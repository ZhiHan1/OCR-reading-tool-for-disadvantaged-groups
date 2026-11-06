# -*- coding: utf-8 -*-
"""量化 ONNX 端到端验证：原始波形 → FBank+LFR+CMVN → ONNX → CIF 解码 → 文本。

目的：证明「INT8 量化模型能不能真正出字」。这是决定要不要走 ONNX 路线的
最后一环——如果这里必须手写大量 FunASR 内部逻辑，说明工程量不可接受。

只读模型，不改任何现有代码。

用法（项目根目录）：
    py -3.14 tools/verify_onnx_e2e.py --text "今天天气不错，我们出去走走吧"
    py -3.14 tools/verify_onnx_e2e.py --audio 录音.wav
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np


def synth_audio(text: str, wav_path: Path, rate: int = 0) -> bool:
    """用 Windows SAPI 生成一段已知文本的语音，作为对照基准。"""
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        print(f"  无法导入 pywin32: {e}")
        return False

    pythoncom.CoInitialize()
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        # 22 SAFT22kHz16BitMono
        stream.Format.Type = 22
        stream.Open(str(wav_path), 3)
        voice.AudioOutputStream = stream
        if rate:
            voice.Rate = rate
        voice.Speak(text)
        stream.Close()
        return True
    except Exception as e:
        print(f"  SAPI 合成失败: {e}")
        return False
    finally:
        pythoncom.CoUninitialize()


def load_wav_16k(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"只支持 16bit PCM，当前 {width * 8}bit")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        # 简单线性重采样，仅用于验证
        n = int(len(data) * 16000 / sr)
        idx = np.linspace(0, len(data) - 1, n)
        data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
    return data


def extract_feats(audio: np.ndarray, conf: dict, cmvn_file: Path) -> np.ndarray:
    """复现 FunASR WavFrontend：波形×32768 → kaldi fbank(80) → LFR(7,6) → CMVN。

    三个容易写错、写错就输出乱码的点（均已实测踩过）：
    1. upsacle_samples=True：波形要先乘 1<<15，否则特征整体偏 ~16dB。
    2. dither 置 0：训练时是 1.0（加随机噪声），推理必须去掉才可复现。
    3. LFR 用 funasr 自己的 apply_lfr（as_strided 实现），手写逐帧复制
       会导致前几帧错位。
    """
    from funasr.frontends.wav_frontend import apply_cmvn, apply_lfr, load_cmvn
    from funasr.utils import fbank as kaldi

    import torch

    n_mels = conf.get("n_mels", 80)
    lfr_m = conf.get("lfr_m", 7)
    lfr_n = conf.get("lfr_n", 6)

    waveform = torch.from_numpy(audio).unsqueeze(0).float() * (1 << 15)
    mat = kaldi.fbank(
        waveform,
        num_mel_bins=n_mels,
        frame_length=conf.get("frame_length", 25),
        frame_shift=conf.get("frame_shift", 10),
        dither=0.0,
        energy_floor=0.0,
        window_type=conf.get("window", "hamming"),
        sample_frequency=16000,
        snip_edges=True,
    )
    if lfr_m != 1 or lfr_n != 1:
        mat = apply_lfr(mat, lfr_m, lfr_n)
    cmvn = load_cmvn(str(cmvn_file)).float()
    mat = apply_cmvn(mat, cmvn)
    return mat.numpy()


def decode_cif(logits, token_num, tokens, eos="</s>"):
    """CIF 解码：logits 每一行已经是 CIF 对齐后的一个 token，直接逐行 argmax。

    注意两个坑（实测踩过）：
    1. 不要拿 us_cif_peak 索引 logits。us_cif_peak 来自
       cif_wo_hidden_export，是 alphas 的「累计积分值」，用于画时间戳，
       长度是上采样后的时间轴（len*upsample_times），与 logits 的 token
       行数无关。拿它当索引会把所有行都夹到 padding 上，输出一片 </s>。
    2. logits 是 log_softmax 后的分数（全为负数），argmax 依然有效，
       但不要拿数值大小当置信度绝对值比较。

    末尾的 </s>（或 <blank>/<unk>）按 FunASR 规则去掉。
    """
    n = int(np.asarray(token_num).reshape(-1)[0])
    lg = np.asarray(logits)[0]
    if n > 0:
        lg = lg[:n]
    chars = []
    for row in lg:
        tid = int(np.argmax(row))
        if not (0 <= tid < len(tokens)):
            continue
        ch = tokens[tid]
        if ch in (eos, "<blank>", "<unk>", "<s>"):
            continue
        # SentencePiece 子词标记：原生 AutoModel.generate 在
        # funasr/utils/postprocess_utils.py 里合并 @@，ONNX 链路没有这层，
        # 必须自己补，否则英文单词会分裂成 off@@line。
        ch = ch.replace("@@", "")
        chars.append(ch)
    return "".join(chars), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="模型/语音/paraformer-large")
    ap.add_argument("--onnx", default="模型/语音/paraformer-large-onnx/model_quant.onnx")
    ap.add_argument("--text", default="今天天气不错，我们出去走走吧")
    ap.add_argument("--audio", default="")
    ap.add_argument("--rate", type=int, default=0, help="SAPI 语速 -10~10")
    args = ap.parse_args()

    model_dir = (ROOT / args.model).resolve()
    onnx_path = (ROOT / args.onnx).resolve()
    if not onnx_path.is_file():
        sys.exit(f"ONNX 不存在: {onnx_path}")

    import yaml
    conf = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))
    fconf = conf.get("frontend_conf", {})
    tokens = json.loads((model_dir / "tokens.json").read_text(encoding="utf-8"))
    if isinstance(tokens, dict):
        tokens = [tokens[str(i)] for i in range(len(tokens))]
    print(f"词表 {len(tokens)} 个 token；frontend: n_mels={fconf.get('n_mels')} "
          f"lfr_m={fconf.get('lfr_m')} lfr_n={fconf.get('lfr_n')}")

    # 1. 准备音频
    if args.audio:
        audio_path = (ROOT / args.audio).resolve()
        ref_text = "(外部音频，无参考文本)"
    else:
        audio_path = Path(os.environ["TEMP"]) / "onnx_e2e.wav"
        ref_text = args.text
        print(f"[1/4] SAPI 合成参考语音: {ref_text}")
        if not synth_audio(args.text, audio_path, args.rate):
            sys.exit("语音合成失败")
    audio = load_wav_16k(audio_path)
    print(f"      音频 {len(audio) / 16000:.2f}s @16k")

    # 2. 特征
    print("[2/4] 提取 FBank + LFR + CMVN")
    t0 = time.perf_counter()
    feats = extract_feats(audio, fconf, model_dir / "am.mvn")
    feat_s = time.perf_counter() - t0
    print(f"      特征 {feats.shape}  耗时 {feat_s * 1000:.0f}ms")

    # 3. ONNX 推理
    print("[3/4] INT8 ONNX 推理")
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    feed = {
        "speech": feats[None, :, :].astype(np.float32),
        "speech_lengths": np.array([feats.shape[0]], dtype=np.int32),
    }
    t0 = time.perf_counter()
    outs = sess.run(None, feed)
    infer_s = time.perf_counter() - t0
    names = [o.name for o in sess.get_outputs()]
    res = dict(zip(names, outs))
    print(f"      推理 {infer_s * 1000:.0f}ms  输出: {list(res)}")

    # 4. CIF 解码
    print("[4/4] CIF 解码")
    text, n_tok = decode_cif(res["logits"], res["token_num"], tokens)
    print(f"      token_num={n_tok}")
    print(f"\n参考文本: {ref_text}")
    print(f"ONNX 识别: {text}")
    if args.text and not args.audio:
        same = text.replace(" ", "") == args.text.replace(" ", "")
        print(f"完全一致: {same}")

    report = {
        "audio_seconds": round(len(audio) / 16000, 2),
        "feat_shape": list(feats.shape),
        "feat_ms": round(feat_s * 1000),
        "infer_ms": round(infer_s * 1000),
        "ref_text": ref_text,
        "onnx_text": text,
        "token_num": n_tok,
    }
    out_json = onnx_path.parent / "e2e_result.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果: {out_json}")


if __name__ == "__main__":
    main()
