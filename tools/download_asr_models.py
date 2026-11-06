# -*- coding: utf-8 -*-
"""下载 ASR 相关模型到 <项目根>/模型/语音/。

合并自原根目录的三份一次性脚本（_download_asr_models.py /
_download_asr_models_hf.py / _download_asr_retry.py），功能等价，行为不变：
只下载模型权重，不修改应用配置。

为什么需要它：
- 项目本身不随包发布权重以外的东西，重装/换机时需要把模型拉回来；
- ct-punc 词表已裁剪到 8392 词条（见 KNOWLEDGE/performance.md §9.2），
  若要从头重建，需要先拉官方 272727 词版本再跑 tools/trim_punc_vocab.py。

用法（项目根目录）：
    py -3.14 tools/download_asr_models.py            # 默认走 ModelScope
    py -3.14 tools/download_asr_models.py --source hf # 走 hf-mirror 镜像
    py -3.14 tools/download_asr_models.py --only ct-punc
    py -3.14 tools/download_asr_models.py --list

注意：
- ModelScope 更适合国内网络（实测 2.2–2.5 MB/s）；hf-mirror 实测仅 39–141 KB/s。
- 已存在的文件会跳过，不会重复下载；中断后直接重跑即可续传。
- 模型总大小约 1.8 GB（paraformer 859 MB + SenseVoice 893 MB + 其余）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_ROOT = os.path.join(ROOT, "模型", "语音")

# (key, model_id, 目标文件夹, 仅 hf 源使用的 allow_patterns)
TARGETS = [
    (
        "sensevoice",
        "iic/SenseVoiceSmall",
        "SenseVoiceSmall",
        ["config.yaml", "configuration.json", "model.pt", "am.mvn",
         "*.bpe.model", "tokens.json", "tokens.txt"],
    ),
    (
        "paraformer",
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "paraformer-large",
        ["config.yaml", "configuration.json", "model.pt", "am.mvn",
         "tokens.json", "seg_dict"],
    ),
    (
        "vad",
        "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "fsmn-vad",
        ["config.yaml", "configuration.json", "model.pt", "am.mvn"],
    ),
    (
        "ct-punc",
        "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        "ct-punc",
        ["config.yaml", "configuration.json", "model.pt", "tokens.json"],
    ),
]

# hf-mirror 上的等价仓库（个别 repo 名与 ModelScope 不同）
HF_REPO_OVERRIDE = {
    "sensevoice": "FunAudioLLM/SenseVoiceSmall",
    "paraformer": (
        "alextomcat/speech_paraformer-large-vad-punc_asr_nat-zh-cn-"
        "16k-common-vocab8404-pytorch"
    ),
    "vad": "funasr/fsmn-vad",
    "ct-punc": "lucasjin/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
}


def _download_modelscope(model_id: str, dest: str) -> str:
    from modelscope import snapshot_download

    return snapshot_download(model_id, local_dir=dest)


def _download_hf(model_id: str, dest: str, patterns) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        repo_id=model_id,
        local_dir=dest,
        allow_patterns=patterns,
        max_workers=4,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载 ASR 模型到 模型/语音/")
    parser.add_argument(
        "--source", choices=("modelscope", "hf"), default="modelscope",
        help="下载源：modelscope（默认，国内更快）或 hf（hf-mirror.com）",
    )
    parser.add_argument(
        "--only", nargs="+", metavar="KEY",
        help=f"只下载指定模型，可选：{', '.join(k for k, *_ in TARGETS)}",
    )
    parser.add_argument("--list", action="store_true", help="列出所有目标后退出")
    args = parser.parse_args(argv)

    if args.list:
        for key, model_id, folder, _ in TARGETS:
            print(f"  {key:<12} {folder:<18} {model_id}")
        return 0

    if args.source == "hf":
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    selected = TARGETS
    if args.only:
        unknown = set(args.only) - {k for k, *_ in TARGETS}
        if unknown:
            parser.error(f"未知模型: {', '.join(sorted(unknown))}")
        selected = [t for t in TARGETS if t[0] in args.only]

    os.makedirs(DEST_ROOT, exist_ok=True)
    failed = []
    for key, model_id, folder, patterns in selected:
        dest = os.path.join(DEST_ROOT, folder)
        print(f"\n===== {key}: {model_id} -> {dest} =====", flush=True)
        try:
            if args.source == "hf":
                repo = HF_REPO_OVERRIDE.get(key, model_id)
                out = _download_hf(repo, dest, patterns)
            else:
                out = _download_modelscope(model_id, dest)
            print(f"[完成] {out}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failed.append(key)
            print(f"[失败] {type(exc).__name__}: {exc}", flush=True)

    print("\n全部任务结束", flush=True)
    if failed:
        print(f"[有失败] {', '.join(failed)}；可直接重跑续传。", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
