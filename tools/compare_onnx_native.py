# -*- coding: utf-8 -*-
"""ONNX vs 原生 FunASR 批量对照：同一段 SAPI 语音，两条链路各识别一次。

判定标准：ONNX 出字与原生出字一致，才说明整条链打通（含 INT8 量化精度损失）。

用法（项目根目录）：
    py -3.14 tools/compare_onnx_native.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from tools.verify_onnx_e2e import decode_cif, extract_feats, load_wav_16k, synth_audio

CASES = [
    "今天天气不错，我们出去走走吧",
    "请把这份文档翻译成英文",
    "会议定在明天下午三点半",
    "这个功能需要支持 offline 模式和 online 模式",
    "第一季度营收同比增长百分之十七点五",
    "我觉得这个方案在工程实现上风险比较高，需要再评估一下"
    "硬件资源占用和长期维护成本这两方面的问题",
]


def main():
    import yaml
    import onnxruntime as ort

    model_dir = (ROOT / "模型/语音/paraformer-large").resolve()
    onnx_path = (ROOT / "模型/语音/paraformer-large-onnx/model_quant.onnx").resolve()
    conf = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))
    fconf = conf["frontend_conf"]
    tokens = json.loads((model_dir / "tokens.json").read_text(encoding="utf-8"))
    if isinstance(tokens, dict):
        tokens = [tokens[str(i)] for i in range(len(tokens))]

    # 原生对照：只加载一次
    from funasr import AutoModel

    native = AutoModel(
        model=str(model_dir), device="cpu", disable_update=True,
        disable_pbar=True, disable_log=True,
    )
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    tmp = Path(tempfile.gettempdir()) / "onnx_cmp"
    tmp.mkdir(exist_ok=True)

    rows = []
    for i, text in enumerate(CASES, 1):
        wav = tmp / f"c{i}.wav"
        if not synth_audio(text, wav):
            print(f"[{i}] 合成失败，跳过")
            continue
        audio = load_wav_16k(wav)

        t0 = time.perf_counter()
        nat = native.generate(input=audio, batch_size_s=60, disable_pbar=True)
        nat_s = time.perf_counter() - t0
        nat_text = nat[0].get("text", "").replace(" ", "")

        t0 = time.perf_counter()
        feats = extract_feats(audio, fconf, model_dir / "am.mvn")
        feat_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        outs = sess.run(None, {
            "speech": feats[None].astype(np.float32),
            "speech_lengths": np.array([feats.shape[0]], dtype=np.int32),
        })
        onnx_s = time.perf_counter() - t0
        res = dict(zip([o.name for o in sess.get_outputs()], outs))
        onnx_text, n_tok = decode_cif(res["logits"], res["token_num"], tokens)

        same = nat_text == onnx_text
        rows.append({
            "case": i,
            "ref": text,
            "audio_seconds": round(len(audio) / 16000, 2),
            "native": nat_text,
            "onnx_int8": onnx_text,
            "same": same,
            "native_s": round(nat_s, 3),
            "feat_s": round(feat_s, 3),
            "onnx_s": round(onnx_s, 3),
            "token_num": n_tok,
        })
        print(f"\n[{i}] {text}")
        print(f"    原生 : {nat_text}")
        print(f"    ONNX : {onnx_text}")
        print(f"    一致 : {same}   耗时 原生 {nat_s:.2f}s / ONNX {onnx_s:.3f}s")

    out = onnx_path.parent / "compare_result.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in rows if r["same"])
    print(f"\n一致 {ok}/{len(rows)}")
    print(f"结果: {out}")


if __name__ == "__main__":
    main()
