"""裁剪 ct-punc 词表：只保留中文场景需要的字符，去掉 26 万条英文词。

背景（KB-PERF-008）
--------------------
`models/asr/ct-punc/model.pt` 有 278 MB，拆开看：

    embed.weight        266.3 MB  (272727 词 × 256 维)
    其余全部网络层       12.1 MB

也就是说 **95.6% 的体积是一张词嵌入表**，而这张表里 99.3% 是英文单词
（`aachen` / `aadvantage` / `aabid` …）。中文识别结果用不到它们。

因此本脚本不改网络结构、不做数值量化（量化嵌入表会让近义词混淆、
直接导致标点错乱），只做**无损裁剪**：删掉用不到的行。

实测：282.47 MB -> 20.37 MB（-93%），22 句样例标点输出逐字一致。

用法
----
    # 预览（不写盘）
    py -3.14 tools/trim_punc_vocab.py --dry-run

    # 生成到指定目录
    py -3.14 tools/trim_punc_vocab.py --out models/asr/ct-punc-trimmed

    # 直接原地替换（会先备份成 ct-punc-orig-backup）
    py -3.14 tools/trim_punc_vocab.py --inplace

从零恢复原始模型（备份已删除时）
--------------------------------
`ct-punc-orig-backup/` 已于 2026-11-04 删除。需要回滚或重新裁剪时，先从
ModelScope 拉回原始模型（278.5 MB）：

    https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch

把 `model.pt` 与 `tokens.json` 放进 `models/asr/ct-punc/`，再重新执行本脚本。
注意：**不要**对已经裁剪过的 `ct-punc` 再次运行 `--inplace`，它对已裁剪目录
是幂等的（`--dry-run` 显示 8392 -> 8392），但并不会产生新的收益。

注意
----
- 裁剪后模型只适合**中文/中英混排**场景。若将来要支持整段英文标点，
  请用原始备份，不要用裁剪版。
- 保留规则见 `KEEP_RANGES` / `KEEP_ASCII`，改这里就能调整覆盖范围。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "models" / "asr" / "ct-punc"

# 保留的字符区间：中文、日文假名、CJK 符号、全角符号
KEEP_RANGES = (
    (0x4E00, 0x9FFF),   # CJK 统一汉字
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x3040, 0x30FF),   # 日文假名（ASR 偶尔混入）
    (0x3000, 0x303F),   # CJK 符号和标点
    (0xFF00, 0xFFEF),   # 全角符号
)

# 保留的 ASCII 单字符（字母数字 + 常见符号）
KEEP_ASCII = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .,!?;:'\"-()[]{}%&/\\@#$*+=<>_~|`^"
)

# 必须保留的特殊 token
KEEP_SPECIAL = {"<blank>", "<s>", "</s>", "<unk>"}


def should_keep(token: str) -> bool:
    """单字符且在保留集合内，或属于特殊 token。"""
    if token in KEEP_SPECIAL:
        return True
    if len(token) != 1:
        return False
    o = ord(token)
    if any(lo <= o <= hi for lo, hi in KEEP_RANGES):
        return True
    return token in KEEP_ASCII


def build(src: Path, dst: Path) -> dict:
    import torch

    tokens_path = src / "tokens.json"
    model_path = src / "model.pt"
    for p in (tokens_path, model_path):
        if not p.is_file():
            raise SystemExit(f"缺少文件：{p}")

    tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    state = torch.load(model_path, map_location="cpu", weights_only=False)
    embed = state.get("embed.weight")
    if embed is None:
        raise SystemExit("model.pt 里没有 embed.weight，可能不是 ct-punc 模型")

    if embed.shape[0] != len(tokens):
        raise SystemExit(
            f"词表与权重行数不一致：tokens={len(tokens)} embed={embed.shape[0]}"
        )

    keep_idx = [i for i, t in enumerate(tokens) if isinstance(t, str) and should_keep(t)]
    if not keep_idx:
        raise SystemExit("裁剪后没有任何词条，请检查保留规则")

    new_tokens = [tokens[i] for i in keep_idx]
    new_embed = embed[keep_idx].clone()

    dst.mkdir(parents=True, exist_ok=True)
    new_state = dict(state)
    new_state["embed.weight"] = new_embed
    torch.save(new_state, dst / "model.pt")
    (dst / "tokens.json").write_text(
        json.dumps(new_tokens, ensure_ascii=False), encoding="utf-8"
    )
    for name in ("config.yaml", "configuration.json"):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)

    old_mb = model_path.stat().st_size / 1048576
    new_mb = (dst / "model.pt").stat().st_size / 1048576
    return {
        "tokens_before": len(tokens),
        "tokens_after": len(new_tokens),
        "model_mb_before": old_mb,
        "model_mb_after": new_mb,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="裁剪 ct-punc 词表（去掉英文词）")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="原始 ct-punc 目录")
    ap.add_argument("--out", default="", help="输出目录（默认 <src>-trimmed）")
    ap.add_argument("--inplace", action="store_true",
                    help="原地替换，原目录改名为 <src>-orig-backup")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.is_absolute():
        src = (ROOT / src).resolve()

    if args.dry_run:
        tokens = json.loads((src / "tokens.json").read_text(encoding="utf-8"))
        kept = [t for t in tokens if isinstance(t, str) and should_keep(t)]
        emb_mb = (src / "model.pt").stat().st_size / 1048576
        print(f"词条 {len(tokens)} -> {len(kept)}（保留 {100 * len(kept) / len(tokens):.1f}%）")
        print(f"模型 {emb_mb:.2f} MB -> 预计 {emb_mb * len(kept) / len(tokens):.2f} MB")
        return 0

    if args.inplace:
        tmp = src.with_name(src.name + "-tmp-build")
        if tmp.exists():
            shutil.rmtree(tmp)
        stats = build(src, tmp)
        backup = src.with_name(src.name + "-orig-backup")
        if backup.exists():
            shutil.rmtree(backup)
        src.rename(backup)
        tmp.rename(src)
        print(f"已备份原模型：{backup.name}")
        target = src
    else:
        target = Path(args.out) if args.out else src.with_name(src.name + "-trimmed")
        if not target.is_absolute():
            target = (ROOT / target).resolve()
        stats = build(src, target)
        print(f"已生成：{target}")

    print(f"词条 {stats['tokens_before']} -> {stats['tokens_after']}")
    print(f"模型 {stats['model_mb_before']:.2f} MB -> {stats['model_mb_after']:.2f} MB "
          f"（省 {stats['model_mb_before'] - stats['model_mb_after']:.2f} MB）")
    print(f"写入位置：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
