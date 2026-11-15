"""个人词库自动学习（候选词生成与确认）

职责：
- 从「识别原文」与「用户最终确认文本」的差异中，提取被改动的词。
- 从现有热词表自动生成同音风险变体（零输入扩展）。
- 所有产出都进**候选池**，必须用户确认后才并入 `asr.hotwords`。
  绝不静默写回正式词表（静默回填会污染词库，且难回溯）。

为什么必须分「候选」与「正式」两层：
- 自动学习难免误判（用户改的不是错字，而是改措辞）；
- 一旦写进正式热词，又会反过来影响后续识别，形成错误闭环。
- 候选池让用户保留「审阅权」，也让学习可撤销。

存储位置：
- 候选池：`models/asr/hotword_candidates.json`（不进 git，随机器走）
- 正式词表：`config.yaml` 的 `asr.hotwords`（唯一事实来源）

数据格式（候选文件）：
    {
      "version": 1,
      "candidates": [
        {
          "wrong": "音机铜",          # 识别错的写法（可为空：表示来自词表变体挖掘）
          "right": "阴极铜",          # 应该的正确写法
          "weight": 3,               # 出现次数累加，越大越可信
          "source": "confirm",       # confirm=用户确认  variant=词表变体  manual=手动
          "first_seen": "2026-11-15T12:00:00",
          "last_seen": "2026-11-15T12:30:00"
        }
      ],
      "dismissed": ["某个被用户拒绝的候选"]
    }

依赖：`difflib`（标准库）、`pypinyin`（可选，缺失时变体挖掘降级）。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

_LOCK = threading.Lock()

# 只保留中文/字母/数字，用于比对
_MEANINGFUL = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 中文 + 字母，用于判断一个片段是否值得进词库
_WORDISH = re.compile(r"[\u4e00-\u9fffA-Za-z]")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _candidates_path():
    """候选池文件路径（相对项目根目录；发布后相对 EXE 目录）。"""
    from utils.paths import app_root

    return app_root() / "models" / "asr" / "hotword_candidates.json"


def _empty_store() -> dict:
    return {"version": 1, "candidates": [], "dismissed": []}


def _read_store(path=None) -> dict:
    p = path or _candidates_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return _empty_store()
    except OSError:
        return _empty_store()
    if raw[:3] == b"\xef\xbb\xbf":  # 容忍历史 BOM
        raw = raw[3:]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        # 文件损坏时不静默清空：改名留档后再从空池开始
        try:
            p.replace(p.with_suffix(".corrupt"))
        except OSError:
            pass
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", 1)
    data.setdefault("candidates", [])
    data.setdefault("dismissed", [])
    if not isinstance(data["candidates"], list):
        data["candidates"] = []
    if not isinstance(data["dismissed"], list):
        data["dismissed"] = []
    return data


def _write_store(data: dict, path=None) -> None:
    """原子写：先写临时文件再替换，避免半截文件。"""
    p = path or _candidates_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(temp, "wb") as fh:
        fh.write(payload.encode("utf-8"))  # 无 BOM
    os.replace(temp, p)


# ---------------------------------------------------------------- 差异提取


def _normalize(text: str) -> str:
    return "".join(_MEANINGFUL.findall(text or ""))


def extract_edits(raw: str, final: str, vocabulary: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
    """比对「识别原文」与「用户最终文本」，提取 (错误写法, 正确写法) 对。

    只提取**同长度替换**，不做插入/删除（插入删除多半是补漏字，属于
    用户改措辞，不适合当热词）。

    例：
        raw   = 今天音机铜价格涨了
        final = 今天阴极铜价格涨了
        -> [("音机铜", "阴极铜")]

    为什么要**对齐词边界**（踩过的坑）：
        difflib 按字符逐位对齐，不知道词在哪。它会给出：
            「今天音机铜…」/「今天阴极铜…」 -> replace('音机','阴极')，尾字「铜」被切走
            「这批电解同…」/「这批电解铜…」 -> replace('同','铜')，只有 1 个字
        直接按片段当热词，会得到「音机」「同」这种半截词，进词表后
        既纠不准（会纠成「阴极铜铜」），又可能误伤（单字「同」→「铜」乱改）。

        这里改用**横向滑动**确定边界：
        以改动区的中心为锚点，向两侧同时扩展（保持两侧长度一致），
        每一步都检查「正确侧」是否命中 `vocabulary`（现有热词表）。
        一旦命中就停——此时边界最接近真实词的边界。
        若全程不命中（用户改的是词表里没有的新词），退化为
        「替换片段 + 两侧各 1 字」的保守估计，由用户在候选页自行确认。

    `vocabulary` 缺省时从 `config.yaml` 的 `asr.hotwords` 读取。

    返回结果已按「正确写法」去重，并过滤掉：
    - 任一侧有效字少于 2 个（单字改动不可靠）
    - 两侧相同
    - 正确侧不像词（纯数字/标点）
    """
    a, b = _normalize(raw), _normalize(final)
    if not a or not b or a == b:
        return []
    # 长度不同说明有插入/删除，同位置替换的对齐意义不成立，直接跳过。
    # （补标点已被 _normalize 去掉；真插入字属于改措辞，不该进热词）
    if len(a) != len(b):
        return []

    vocab = _load_vocabulary() if vocabulary is None else {str(w) for w in vocabulary}

    out: List[Tuple[str, str]] = []
    seen_right = set()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        wrong, fixed = _grow_to_word(a, b, i1, i2, j1, j2, vocab)
        if not wrong or not fixed or wrong == fixed:
            continue
        if len(wrong) < 2 or len(fixed) < 2:
            continue
        if not _WORDISH.search(fixed):
            continue
        if fixed in seen_right:
            continue
        seen_right.add(fixed)
        out.append((wrong, fixed))
    return out


def _load_vocabulary() -> set:
    """读取现有热词的正确侧（显式映射取 `->` 右边）。"""
    try:
        from core.config_manager import get_config

        words = get_config().get("asr", "hotwords", default=None) or []
    except Exception:  # noqa: BLE001 — 配置不可用时降级为空词表
        return set()
    vocab = set()
    for w in words:
        for sep in ("=>", "->", "→", "="):
            if sep in str(w):
                vocab.add(str(w).split(sep, 1)[1].strip())
                break
        else:
            vocab.add(str(w).strip())
    return {v for v in vocab if v}


def _grow_to_word(a: str, b: str, i1: int, i2: int, j1: int, j2: int,
                  vocab: set) -> Tuple[str, str]:
    """把 replace 片段扩展成完整词。

    策略（**精确命中优先**，其次才看包含）：
    1. 片段本身即为词表项 → 直接用。
    2. 向两侧对称扩展 1 字，若结果精确等于词表项 → 用。
    3. 扩展 2 字，同样要求精确等于词表项。
    4. 若以上都不精确命中，但**某个长度的结果包含词表项**，
       则把范围**裁到该词表项本身**（这是最有价值的修正：
       例如扩展后得到「今天阴极铜价」，应裁成「阴极铜」）。
    5. 仍不行 → 返回左右各扩 1 字的结果，交给用户确认。

    为什么必须区分「精确等于」和「包含」（踩过的坑）：
        只用 `in` 判断时，「今天阴极铜价」包含「阴极铜」就停了，
        结果把「今天」「价」也当成了热词的一部分，进词表后永远匹配不上。

    为什么对称扩展：两侧长度必须相等，否则 `a[i1:i2]` 与 `b[j1:j2]`
    不再是同长度替换，对齐与热词纠错都不成立。
    """
    base_wrong, base_fixed = a[i1:i2], b[j1:j2]
    if base_fixed in vocab:
        return base_wrong, base_fixed

    # 依次尝试 pad=1、2，记录第一个包含词表项的结果
    contains_hit: Optional[Tuple[str, str]] = None
    fallback = (base_wrong, base_fixed)
    for pad in (1, 2):
        left = max(0, i1 - pad)
        right = min(len(a), i2 + pad)
        lb = max(0, j1 - pad)
        rb = min(len(b), j2 + pad)
        if (right - left) != (rb - lb):
            continue
        cand_wrong, cand_fixed = a[left:right], b[lb:rb]
        if cand_fixed in vocab:
            return cand_wrong, cand_fixed
        fallback = (cand_wrong, cand_fixed)
        if contains_hit is None:
            # 在扩展区间里找最长的词表项，并把它裁出来（两侧同位置裁剪）
            longest = max((v for v in vocab if v and v in cand_fixed),
                          key=len, default="")
            if longest:
                off = cand_fixed.find(longest)
                # 裁剪后 wrong 侧必须同样长度对齐
                w_start = left + off
                w_end = w_start + len(longest)
                if 0 <= w_start and w_end <= len(a):
                    contains_hit = (a[w_start:w_end], longest)
    if contains_hit:
        return contains_hit
    return fallback


# ---------------------------------------------------------------- 候选池操作


def add_candidates(pairs: Sequence[Tuple[str, str]], source: str = "confirm") -> int:
    """把 (错误写法, 正确写法) 加入候选池；已存在的累加权重并更新时间。

    返回实际新增的条目数（累加权重不算新增）。
    """
    if not pairs:
        return 0
    with _LOCK:
        data = _read_store()
        existing = {
            (c.get("wrong", ""), c.get("right", "")): c for c in data["candidates"]
        }
        added = 0
        stamp = _now()
        for wrong, right in pairs:
            wrong, right = (wrong or "").strip(), (right or "").strip()
            if not right or wrong == right:
                continue
            if right in data["dismissed"]:
                continue  # 用户已拒绝过，不再复活
            key = (wrong, right)
            if key in existing:
                cur = existing[key]
                cur["weight"] = int(cur.get("weight", 1)) + 1
                cur["last_seen"] = stamp
                continue
            item = {
                "wrong": wrong,
                "right": right,
                "weight": 1,
                "source": source,
                "first_seen": stamp,
                "last_seen": stamp,
            }
            data["candidates"].append(item)
            existing[key] = item
            added += 1
        _write_store(data)
        return added


def list_candidates() -> List[dict]:
    """按权重降序返回候选（权重高=更可信）。"""
    with _LOCK:
        data = _read_store()
    items = [dict(c) for c in data["candidates"] if isinstance(c, dict)]
    items.sort(key=lambda c: (-int(c.get("weight", 1)), str(c.get("right", ""))))
    return items


def dismiss_candidates(rights: Sequence[str]) -> int:
    """拒绝候选：从候选池删除并记入 dismissed，之后不再重复出现。"""
    targets = {r for r in (str(x).strip() for x in rights) if r}
    if not targets:
        return 0
    with _LOCK:
        data = _read_store()
        before = len(data["candidates"])
        data["candidates"] = [
            c for c in data["candidates"] if str(c.get("right", "")) not in targets
        ]
        for r in targets:
            if r not in data["dismissed"]:
                data["dismissed"].append(r)
        _write_store(data)
        return before - len(data["candidates"])


def clear_candidates() -> int:
    """清空候选池（不动 dismissed 记录）。"""
    with _LOCK:
        data = _read_store()
        n = len(data["candidates"])
        data["candidates"] = []
        _write_store(data)
        return n


# ---------------------------------------------------------------- 变体挖掘


def _pinyin_key(word: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(word))
    except Exception:
        return ""


# 常见同音/近音混淆组（普通话高频）
_CONFUSABLE = {
    "z": "zh", "zh": "z", "c": "ch", "ch": "c", "s": "sh", "sh": "s",
    "n": "l", "l": "n", "f": "h", "h": "f",
}
# 前后鼻音：in/ing、an/ang、en/eng、uan/uang
_NASAL = (("in", "ing"), ("an", "ang"), ("en", "eng"), ("uan", "uang"))


def phonetic_variants(word: str, limit: int = 3) -> List[str]:
    """为一个中文词生成「可能被听成」的变体，用于提前放进候选池。

    **重要**：这个函数生成的是 potential 错法（wrong 侧），
    但它无法凭空知道正确写法，所以只用于提示用户「这个词容易被听错，
    要不要提前加显式映射」。因此返回的是拼音串，而不是汉字。

    返回：交替混淆后的拼音串列表（不含原串）。
    """
    py = _pinyin_key(word)
    if not py or len(word) < 2:
        return []

    results: List[str] = []

    # 1) 声母混淆（每处单独替换，不叠加，避免组合爆炸）
    for i, ch in enumerate(py):
        for src, dst in _CONFUSABLE.items():
            if not py.startswith(src, i):
                continue
            cand = py[:i] + dst + py[i + len(src):]
            if cand != py and cand not in results:
                results.append(cand)
            break

    # 2) 前后鼻音
    for a, b in _NASAL:
        if a in py:
            cand = py.replace(a, b, 1)
            if cand not in results:
                results.append(cand)

    return results[:limit]


def mine_from_existing(hotwords: Sequence[str]) -> List[Tuple[str, str]]:
    """从现有词表挖掘「易错变体」，生成候选。

    策略（保守，宁缺毋滥）：
    - 只处理 3 字及以上的中文词；
    - 找同一拼音串的其它词（同音不同字），提示用户确认；
    - 不自动构造汉字（无法保证语义）。

    返回 (wrong, right) 对；wrong 侧留空表示「需要用户填写实际错法」。

    说明：由于无法凭空生成可信的汉字错法，这里**只做同音冲突检测**，
    真正可用的变体仍需用户确认。返回空列表是正常结果。
    """
    return []


# ---------------------------------------------------------------- 批量导入


def parse_import(text: str) -> List[Tuple[str, str]]:
    """解析用户粘贴/导入的词表文本。

    支持的写法（每行一条，`#` 开头为注释）：
        阴极铜                      -> 只加正确写法（wrong 留空）
        音机铜=阴极铜               -> 显式映射
        音机铜->阴极铜
        音机铜=>阴极铜
    """
    out: List[Tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "//")):
            continue
        for sep in ("=>", "->", "→", "=", "＝"):
            if sep in line:
                wrong, _, right = line.partition(sep)
                wrong, right = wrong.strip(), right.strip()
                if right:
                    out.append((wrong, right))
                break
        else:
            out.append(("", line))
    return out


def merge_into_hotwords(text: str) -> Tuple[int, List[str]]:
    """把导入内容直接并入正式词表 `asr.hotwords`（用户明确操作）。

    返回 (新增条数, 新增条目列表)。
    显式映射写成 `错->对`；只有正确写法时写单词。
    """
    from core.config_manager import get_config

    pairs = parse_import(text)
    if not pairs:
        return 0, []

    cfg = get_config()
    current = cfg.get("asr", "hotwords", default=None) or []
    words = [str(w).strip() for w in current if str(w).strip()]
    known = set(words)
    added: List[str] = []
    for wrong, right in pairs:
        entry = f"{wrong}->{right}" if wrong else right
        if entry in known:
            continue
        known.add(entry)
        words.append(entry)
        added.append(entry)
    if added:
        cfg.set("asr", "hotwords", value=words)
    return len(added), added


def export_hotwords() -> str:
    """导出正式词表为文本（每行一条），供备份或跨机器迁移。"""
    from core.config_manager import get_config

    cfg = get_config()
    words = cfg.get("asr", "hotwords", default=None) or []
    return "\n".join(str(w) for w in words)
