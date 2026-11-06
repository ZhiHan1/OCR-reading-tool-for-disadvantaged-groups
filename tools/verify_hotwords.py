# -*- coding: utf-8 -*-
"""热词（L1 文本层）端到端验证：真实模型 + 真实清理顺序。

验证两件事：
1. 顺序正确性：热词必须作用在 _clean_text 之后（generate 返回带空格分字）。
2. 实际效果：专有名词听错时能被纠回；不该纠的不会误伤。

用法（项目根目录）：
    py -3.14 tools/verify_hotwords.py
"""
import json
import os
import tempfile
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.verify_onnx_e2e import load_wav_16k, synth_audio

CASES = [
    ("请用酷纳万像帮我整理一下会议纪要", ["昆仑万象"]),
    ("智谱清炎这个模型效果怎么样", ["智谱清言"]),
    ("打开欧拉玛然后拉取模型", ["欧拉玛=Ollama"]),
    ("今天天气不错我们出去走走吧", ["昆仑万象", "智谱清言"]),
]

# 故意不放进配置的对照组：确认不会无中生有
NEGATIVE = ["昆仑山风景很好", "这里是万向集团", "天气真不错"]


def main():
    from core.asr_service import _clean_text, local_generate_kwargs
    from core.hotwords import apply_hotwords, hotword_matches, reload_hotwords
    from core.config_manager import get_config

    from funasr import AutoModel

    model_dir = ROOT / "模型/语音/paraformer-large"
    model = AutoModel(
        model=str(model_dir), device="cpu", disable_update=True,
        disable_pbar=True, disable_log=True,
    )

    cfg = get_config()
    tmp = Path(tempfile.gettempdir()) / "hw_verify"
    tmp.mkdir(exist_ok=True)

    def _set_hotwords(words):
        cfg.set("asr", "hotwords", value=list(words))
        reload_hotwords()

    rows = []
    ok = 0
    for i, (say, hotwords) in enumerate(CASES, 1):
        _set_hotwords(hotwords)

        wav = tmp / f"h{i}.wav"
        if not synth_audio(say, wav):
            print(f"[{i}] 合成失败")
            continue
        audio = load_wav_16k(wav)

        result = model.generate(
            input=audio, batch_size_s=60, disable_pbar=True,
            **local_generate_kwargs("paraformer_large"),
        )
        raw = result[0].get("text", "") if result else ""
        cleaned = _clean_text(raw)
        fixed = apply_hotwords(cleaned)
        matches = hotword_matches(cleaned)

        expect = any(("=" in h and h.split("=")[1] in fixed) or
                     ("=" not in h and h in fixed) for h in hotwords)
        changed = fixed != cleaned
        ok += bool(expect)
        rows.append({
            "say": say, "hotwords": hotwords,
            "generate_raw": raw, "cleaned": cleaned,
            "with_hotwords": fixed, "changed": changed,
            "matches": matches, "expect_hit": bool(expect),
        })
        print(f"\n[{i}] 说：{say}")
        print(f"    hotwords : {hotwords}")
        print(f"    generate : {raw!r}")
        print(f"    清理后   : {cleaned}")
        print(f"    热词后   : {fixed}   {'命中' if expect else '未命中'}")
        for m in matches:
            print(f"      match  : {m['original']} -> {m['replacement']} ({m['score']})")

    # 负例：不应误伤
    print("\n=== 防误伤（模型识别正常文本，热词不该改它）===")
    _set_hotwords(["昆仑万象", "智谱清言"])
    for t in NEGATIVE:
        print(f"  {t:<16} -> {apply_hotwords(t)}")

    out = ROOT / "模型/语音/paraformer-large-onnx" / "hotword_result.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n命中 {ok}/{len(rows)}")
    print(f"结果: {out}")


if __name__ == "__main__":
    main()
