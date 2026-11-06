# -*- coding: utf-8 -*-
"""SenseVoice-Small INT8 ONNX 端到端验证（CTC 解码）。

与 Paraformer 的区别：SenseVoice 是 CTC 结构，输出 ctc_logits，
解码规则是「按帧 argmax -> 去掉连续重复 -> 去掉 blank」，不是 CIF。

用法（项目根目录）：
    py -3.14 tools/verify_sensevoice_onnx.py
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

from tools.verify_onnx_e2e import extract_feats, load_wav_16k, synth_audio

CASES = [
    "今天天气不错，我们出去走走吧",
    "请把这份文档翻译成英文",
    "会议定在明天下午三点半",
]


def ctc_decode(logits, out_len, tokens, blank=0):
    """CTC 贪心解码：去掉重复与 blank。"""
    lg = np.asarray(logits)[0]
    n = int(np.asarray(out_len).reshape(-1)[0]) if out_len is not None else lg.shape[0]
    ids = lg[:n].argmax(-1)
    out = []
    prev = -1
    for i in ids:
        i = int(i)
        if i != prev and i != blank and 0 <= i < len(tokens):
            out.append(tokens[i])
        prev = i
    return "".join(out)


def load_tokens(model_dir: Path):
    """SenseVoice 用 BPE；优先读 tokens.json，没有就从 sentencepiece 取。"""
    p = model_dir / "tokens.json"
    if p.is_file():
        t = json.loads(p.read_text(encoding="utf-8"))
        return [t[str(i)] for i in range(len(t))]
    import sentencepiece as spm

    from core.asr_service import _ascii_bpe_path

    bpe = next(model_dir.glob("*.bpe.model"))
    sp = spm.SentencePieceProcessor()
    sp.load(_ascii_bpe_path(str(bpe)))
    return [sp.id_to_piece(i) for i in range(sp.get_piece_size())]


def main():
    import onnxruntime as ort
    import yaml

    model_dir = (ROOT / "模型/语音/SenseVoiceSmall").resolve()
    onnx_path = (ROOT / "模型/语音/SenseVoiceSmall-onnx/model_quant.onnx").resolve()
    conf = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))
    fconf = conf.get("frontend_conf", {})
    tokens = load_tokens(model_dir)
    print(f"词表 {len(tokens)} 个 token")

    from core.asr_service import _clean_text

    from funasr import AutoModel

    # 中文路径会让 sentencepiece 报 NOT_FOUND，改用 ASCII 目录加载。
    from tools.export_onnx_probe import _prepare_model_path

    load_dir = _prepare_model_path(model_dir)
    native = AutoModel(
        model=str(load_dir), device="cpu", disable_update=True,
        disable_pbar=True, disable_log=True,
    )
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    tmp = Path(tempfile.gettempdir()) / "sv_e2e"
    tmp.mkdir(exist_ok=True)

    rows, ok = [], 0
    for i, text in enumerate(CASES, 1):
        wav = tmp / f"s{i}.wav"
        synth_audio(text, wav)
        audio = load_wav_16k(wav)

        t0 = time.perf_counter()
        nat = native.generate(input=audio, language="zh", use_itn=True,
                              batch_size_s=60, disable_pbar=True)
        nat_s = time.perf_counter() - t0
        nat_text = _clean_text(nat[0].get("text", ""))

        t0 = time.perf_counter()
        feats = extract_feats(audio, fconf, model_dir / "am.mvn")
        feat_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        outs = sess.run(None, {
            "speech": feats[None].astype(np.float32),
            "speech_lengths": np.array([feats.shape[0]], dtype=np.int32),
            "language": np.array([0], dtype=np.int32),
            "textnorm": np.array([15], dtype=np.int32),
        })
        onnx_s = time.perf_counter() - t0
        res = dict(zip([o.name for o in sess.get_outputs()], outs))
        raw_text = ctc_decode(res["ctc_logits"], res["encoder_out_lens"], tokens)
        onnx_text = _clean_text(raw_text.replace("▁", " "))
        # 原生带标点、且做了 ITN（"3点半" vs "三点半"），比较前统一掉标点。
        import re
        norm = lambda s: re.sub(r"[，。！？、；：,.!?;: ]", "", s)
        same = norm(nat_text) == norm(onnx_text)
        ok += same
        rows.append({
            "ref": text, "native": nat_text, "onnx_int8": onnx_text,
            "same": same, "native_s": round(nat_s, 3),
            "feat_s": round(feat_s, 3), "onnx_s": round(onnx_s, 3),
        })
        print(f"\n[{i}] {text}")
        print(f"    原生 : {nat_text}")
        print(f"    ONNX : {onnx_text}")
        print(f"    一致 : {same}   耗时 原生 {nat_s:.2f}s / 特征 {feat_s*1000:.0f}ms / ONNX {onnx_s:.3f}s")

    out = onnx_path.parent / "e2e_result.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n一致 {ok}/{len(rows)}")
    print(f"结果: {out}")


if __name__ == "__main__":
    main()
