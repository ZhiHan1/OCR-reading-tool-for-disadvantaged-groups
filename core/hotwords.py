"""识别后热词纠错（L1 文本层）

职责：
- 把模型识别错的专有名词（人名、产品名、术语）按拼音相似度纠回正确写法。
- 只作用于**已清理过的纯文本**，不碰模型、不换权重、不增加推理耗时。

为什么是「文本层」而不是「声学层」：
- 声学热词（ContextualParaformer / SeACo）需要换模型权重，工程量大；
- 文本层用拼音模糊匹配，通用、零模型依赖，对专有名词这类
  「听音相似、用字不同」的错误最有效（如 纸朴青言 -> 智谱清言）。

**顺序很重要（踩过的坑）**：
FunASR 的 `generate()` 返回的 `text` 是**带空格的分字形式**
（`用 纸 朴 青 言 帮 我`），而拼音匹配器按连续字符串匹配，
直接套会把本该纠正的句子全部漏掉。所以必须：
    先 `asr_service._clean_text()` 合并空格 -> 再应用热词。
本项目在 `qt_web/voice_pipeline.py` 的 `_recognize` 里按这个顺序调用。

配置（`config.yaml` 的 `asr` 段）：
    hotwords:
      - 昆仑万象
      - 智谱清言
      - 欧拉玛=Ollama      # 显式：把「欧拉玛」替换为「Ollama」
    hotword_threshold: 0.85  # 可选，拼音相似度阈值，默认 0.85
    hotword_fuzzy: true      # 可选，false 则只做显式替换

依赖缺失（pypinyin / rapidfuzz 未装）时**静默降级为不纠错**，
不让热词把整条识别链路带崩。
"""
from __future__ import annotations

import re
import threading
from typing import List, Sequence, Tuple

_LOCK = threading.Lock()
_CACHE: dict = {}          # 配置指纹 -> matcher（或 False 表示不可用）
_WARNED = False


def _hotword_cfg() -> dict:
    """从全局配置读出热词相关字段。

    注意：`get_config()` 返回的是 `Config` 对象，不是 dict，
    必须用 `Config.get("asr", "键", default=...)` 这种分段取值。
    """
    try:
        from core.config_manager import get_config

        cfg = get_config()
        return {
            "hotwords": cfg.get("asr", "hotwords", default=None),
            "hotword_threshold": cfg.get("asr", "hotword_threshold", default=0.80),
            "hotword_fuzzy": cfg.get("asr", "hotword_fuzzy", default=True),
        }
    except Exception:
        return {}


# funasr 只认这三种显式分隔符
_FUNASR_SEPS = ("=>", "->", "→")
# 用户更习惯写等号，这里统一转成 funasr 认识的写法
_USER_SEPS = ("=", "＝")


def _normalize_entry(entry: str) -> str:
    """把用户写的 `错=对` 转成 funasr 认识的 `错->对`。

    funasr 的 `_EXPLICIT_SEPARATORS` 只有 `=>` `->` `→`，不认等号；
    不转换的话整行会被当成一个「模糊目标词」，显式替换静默失效。
    """
    text = entry.strip()
    if not text:
        return text
    # 注释行与 Markdown 说明（`#`、`>`）不是词，直接丢弃。
    # 曾经踩过：从词表文件整段导入时把 `>` 开头的说明行也当成了热词。
    if text.startswith(("#", ">", "//")):
        return ""
    if any(sep in text for sep in _FUNASR_SEPS):
        return text
    for sep in _USER_SEPS:
        if sep in text:
            wrong, _, right = text.partition(sep)
            wrong, right = wrong.strip(), right.strip()
            if wrong and right:
                return f"{wrong}->{right}"
    return text


def _norm_hotwords(raw) -> Tuple[str, ...]:
    """把配置里的热词统一成字符串元组（用于缓存指纹）。"""
    if not raw:
        return ()
    if isinstance(raw, str):
        items = [ln for ln in raw.splitlines()]
    elif isinstance(raw, dict):
        items = [f"{k}={v}" if k != v else str(k) for k, v in raw.items()]
    else:
        try:
            items = [str(x) for x in raw]
        except TypeError:
            return ()
    return tuple(x for x in (_normalize_entry(s) for s in items) if x)


def _guard_matches(text: str, matches: list) -> List[dict]:
    """过滤掉「窗口对齐错误」造成的误伤。

    funasr 的模糊匹配会在整条文本上滑窗，窗口长度取目标词长 ±1。
    「质朴清颜」既能让 4 字窗口得 0.8462，也能让错位的 3 字窗口
    「朴清颜」得 0.8571 ——后者分数更高被选中，替换后变成
    「质智谱清言」，**把本该保留的「质」吃进了替换范围**。

    单靠长度判断挡不住（3 字原文 vs 4 字目标，长度规则会放行），
    所以这里用更本质的判据：**替换片段的首字必须和目标词首字读音接近**。
    错位窗口的首字（朴 pu）和目标首字（智 zhi）读音不符，直接丢弃。

    跨语言替换（`欧拉玛 -> Ollama`）不比较长度：中英文词长本就不可比。
    """
    kept = []
    for m in matches:
        original = str(m.get("original", ""))
        replacement = str(m.get("replacement", ""))
        if not original or not replacement:
            continue
        same_script_len_ok = True
        if _is_cjk(original[0]) and _is_cjk(replacement[0]):
            # 中文词必须**等长**：窗口比目标词短/长都说明是错位匹配。
            # 例：「铜线已经」中窗口「已经」比「铜线坯」短，靠这条挡住。
            if len(original) != len(replacement):
                same_script_len_ok = False
        if not same_script_len_ok:
            continue
        try:
            head_ok = _similar_head(original[0], replacement[0])
            # 逐字对齐校验：挡住「已经→阴极铜」这类错位窗口。
            typo_ok = _is_plausible_typo(original, replacement)
        except Exception:
            head_ok, typo_ok = True, True
        if head_ok and typo_ok:
            kept.append(m)
    return kept


def _is_cjk(ch: str) -> bool:
    return "\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"


def _per_char_scores(original: str, replacement: str):
    """按字对齐逐字比拼音，返回每个位置 0~1 的相似度；长度不同返回 None。"""
    if len(original) != len(replacement):
        return None
    from pypinyin import lazy_pinyin
    from rapidfuzz import fuzz

    pa, pb = lazy_pinyin(original), lazy_pinyin(replacement)
    if len(pa) != len(pb) or not pa:
        return None
    return [fuzz.ratio(x, y) / 100.0 for x, y in zip(pa, pb)]


def _is_plausible_typo(original: str, replacement: str) -> bool:
    """原文窗口是否可信地就是目标词的「错写」。

    背景（实测踩坑）：热词表从 2 条扩到 300+ 条后，拼音整串相似度 0.80
    几乎能被任何句子撞上，14 句日常话里 13 句被误改，例如
    「已经」→「阴极铜」、「个功」→「铬锆铜」、「银价」→「阴极板」。

    这些**错位窗口**的共同点是：窗口比目标词短，或与目标词只对上开头一两个字。
    而真正会出现的错写（`电解同→电解铜`、`库纳万象→昆仑万象`）特征是：
    **除个别字读音不同，其余每个字都完全同音**。

    判据：长度必须相同，且逐字拼音比对时**最多只有 1 个字对不上**。
    宁可漏纠，不可误纠——漏纠用户自己能看出来，误纠会改坏正常文本。
    """
    scores = _per_char_scores(original, replacement)
    if scores is None:
        return False
    return sum(1 for s in scores if s < 0.6) <= 1


# 模糊匹配只用于这个长度及以上的词。
# 原因：两字词贡献了绝大部分误伤（实测 16/17），且两字同音词在读音层
# 无法与正常用词区分（`件已` 与 `金银` 逐字同音，但都不是彼此）。
# 两字词仍可写成 `错=对` 做**显式**替换，那条路径不受此限制。
FUZZY_MIN_CHARS = 3

# 牌号（纯字母数字，如 TP1 / QSn6.5-0.1 / BFe10-1-1）不进模糊池。
#
# 为什么（实测 bug）：pypinyin 对纯字母数字**原样返回**，
#   lazy_pinyin("BFe10-1-1") -> ["BFe10-1-1"]     （只有一个"字"）
# 于是 `_per_char_scores` 逐字比对时永远通过，模糊匹配退化成字面相似度：
#   BFe10-1-1  <->  BFe30-1-1   只差一个数字，被判为"听错"而互相篡改。
# 实测 27 条牌号里 BFe10-1-1 / BFe30-1-1 会来回误改，比不写更糟。
#
# 牌号想纠错请写显式映射：`达不溜西=WC1`、`替屁一=TP1`。
# 显式路径不经过这里。
_CODE_LIKE = re.compile(r"^[0-9A-Za-z.\-]+$")


def _is_code_like(word: str) -> bool:
    """是否像牌号/型号（纯 ASCII 字母数字与连接符，无中文）。"""
    return bool(word) and bool(_CODE_LIKE.match(word))


def _split_for_fuzzy(hotwords: Sequence[str]):
    """把热词拆成「显式替换项」与「可用于模糊匹配的长词」。"""
    explicit, fuzzy = [], []
    for word in hotwords:
        # 先归一化，否则用户写的 `错=对` 认不出是显式项，
        # 会被当成长词塞进模糊池（两字词进模糊池会误伤）。
        norm = _normalize_entry(word)
        if not norm:
            continue
        if any(sep in norm for sep in ("=>", "->", "→")):
            explicit.append(norm)
            continue
        # 牌号不进模糊池：纯字母数字没有拼音，模糊匹配会退化成
        # 字面比对，导致 BFe10-1-1 与 BFe30-1-1 互相误改。
        if _is_code_like(norm):
            continue
        if len(norm) >= FUZZY_MIN_CHARS:
            fuzzy.append(norm)
    return explicit, fuzzy


def _similar_head(a: str, b: str) -> bool:
    """两个字读音是否足够接近（按整字拼音做模糊比）。"""
    if a == b:
        return True
    from funasr.utils.postprocess_hotwords import _to_pinyin_key

    pa, pb = _to_pinyin_key(a), _to_pinyin_key(b)
    if not pa or not pb:
        return True
    if pa == pb:
        return True
    # 首字母必须相同（zh/ch/sh 这类卷舌音已在拼音串里体现）
    if pa[0] != pb[0]:
        return False
    # 整字拼音的编辑相似度 >= 0.5 才认为是同一个音的误写
    from rapidfuzz import fuzz

    return fuzz.ratio(pa, pb) / 100.0 >= 0.5


def _build(hotwords: Sequence[str], threshold: float, fuzzy: bool):
    """构建匹配器；依赖缺失返回 None（降级）。"""
    global _WARNED
    try:
        from funasr.utils.postprocess_hotwords import build_postprocess_hotword_matcher
    except Exception:
        return None
    try:
        return build_postprocess_hotword_matcher(
            postprocess_hotwords=list(hotwords),
            postprocess_hotword_threshold=threshold,
            enable_fuzzy=fuzzy,
        )
    except ImportError:
        # pypinyin / rapidfuzz 未安装：模糊匹配不可用。
        if not _WARNED:
            print("[热词] 缺少 pypinyin 或 rapidfuzz，热词纠错已跳过；"
                  "安装后可启用：py -3.14 -m pip install pypinyin rapidfuzz")
            _WARNED = True
        if not fuzzy:
            return None
        # 退化：只用显式替换（不需要 pypinyin）
        try:
            return build_postprocess_hotword_matcher(
                postprocess_hotwords=list(hotwords),
                postprocess_hotword_threshold=threshold,
                enable_fuzzy=False,
            )
        except Exception:
            return None
    except Exception:
        return None


def _matcher():
    """按当前配置取匹配器（带缓存，配置变了自动重建）。"""
    asr = _hotword_cfg()
    hotwords = _norm_hotwords(asr.get("hotwords"))
    if not hotwords:
        return None
    try:
        threshold = float(asr.get("hotword_threshold", 0.80))
    except (TypeError, ValueError):
        threshold = 0.80
    fuzzy = bool(asr.get("hotword_fuzzy", True))

    key = (hotwords, threshold, fuzzy)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key] or None
        matcher = _build_cached(hotwords, threshold, fuzzy)
        _CACHE.clear()          # 只保留最新配置，避免无限增长
        _CACHE[key] = matcher if matcher is not None else False
    return matcher


def _build_cached(hotwords: Sequence[str], threshold: float, fuzzy: bool):
    """按「显式项 + 长词模糊」构建匹配器。

    两字词不进模糊池（会大量误伤，见 FUZZY_MIN_CHARS 注释）；
    它们若写成 `错=对` 仍会作为显式替换生效。
    funasr 的同一份输入里，显式项先替换、模糊项后跑，互不影响。
    """
    explicit, long_words = _split_for_fuzzy(hotwords)
    pool = list(long_words) + list(explicit)
    if not pool:
        return None
    return _build(pool, threshold, fuzzy)


def apply_hotwords(text: str) -> str:
    """对**已清理**的识别文本应用热词纠错；未配置或不可用时原样返回。"""
    if not text:
        return text
    matcher = _matcher()
    if matcher is None:
        return text
    try:
        fixed, matches = matcher.apply_text(text)
        if not isinstance(fixed, str) or not fixed:
            return text
        # 逐个校验：若存在对齐异常的替换，退回只应用通过校验的那些。
        applied = _guard_matches(text, [m.as_dict() for m in matches])
        if len(applied) == len(matches):
            return fixed
        out = text
        for m in sorted(applied, key=lambda x: x["start"], reverse=True):
            out = out[: m["start"]] + m["replacement"] + out[m["end"]:]
        return out or text
    except Exception:
        return text


def hotword_matches(text: str) -> List[dict]:
    """返回命中的热词明细，供调试/日志用（已过滤对齐异常的项）。"""
    matcher = _matcher()
    if matcher is None or not text:
        return []
    try:
        _fixed, matches = matcher.apply_text(text)
        return _guard_matches(text, [m.as_dict() for m in matches])
    except Exception:
        return []


def reload_hotwords() -> int:
    """清空缓存并重建，返回热词条数（配置页改动后调用）。"""
    with _LOCK:
        _CACHE.clear()
    asr = _hotword_cfg()
    return len(_norm_hotwords(asr.get("hotwords")))
