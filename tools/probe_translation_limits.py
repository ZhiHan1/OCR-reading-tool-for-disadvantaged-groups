# -*- coding: utf-8 -*-
"""探测翻译的两条真实上限：max_tokens 截断点、n_ctx 溢出点。

产品当前（core/gguf_translation_model.py）：
    max_tokens = min(256, max(32, len(text) * 2))
    n_ctx      = 2048

这两个值都很可疑：
- `len(text)*2` 对中文→英文是**低估**（1 个汉字常对应 1.5~3 个英文 token）；
- `min(256, ...)` 又给了一个**硬上限**，长句必被截断；
- n_ctx=2048 时，OCR 一屏约 1000~2000 字，prompt 本身就吃掉 1500+ token，
  剩下给输出的空间不够，llama.cpp 会直接报错或输出空。

用法（项目根目录）：
    py -3.14 tools/probe_translation_limits.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "模型" / "翻译" / "HY-MT1.5-1.8B-Q6_K.gguf"

# 用真实工业段落，按重复倍数构造不同长度
BASE = (
    "连续连轧生产线的工艺控制要点如下：首先，铜液温度应稳定在摄氏一千一百五十度左右，"
    "波动范围不超过正负五度；其次，铸坯的冷却水压力应保持在零点三到零点四兆帕之间，"
    "流量约为每小时一百二十立方米；再次，轧制过程中必须实时监测乳液浓度，正常范围是"
    "百分之八到百分之十，浓度偏低会导致轧辊磨损加快、表面质量下降。"
)


def product_max_tokens(text: str) -> int:
    return min(256, max(32, len(text) * 2))


def main() -> int:
    from llama_cpp import Llama

    start = time.time()
    llm = Llama(model_path=str(MODEL), n_ctx=2048, n_threads=4, n_gpu_layers=0, verbose=False)
    print(f"加载 {time.time() - start:.1f}s  n_ctx=2048\n", flush=True)

    SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05}

    print("=" * 78)
    print("实验 1：现有时长公式 max_tokens=min(256,max(32,len*2)) 在什么长度开始截断")
    print("=" * 78)
    for mult in (1, 2, 3):
        text = BASE * mult
        prompt = f"将以下文本翻译为英语，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
        limit = product_max_tokens(text)
        prompt_tok = len(llm.tokenize(prompt.encode("utf-8"), add_bos=True))
        t0 = time.time()
        try:
            r = llm(prompt, max_tokens=limit, stop=["</s>"], echo=False, **SAMPLING)
            out = r["choices"][0]["text"].strip()
            finish = r["choices"][0]["finish_reason"]
            used = r["usage"]["completion_tokens"]
            tail = out[-40:].replace("\n", " ")
            print(f"  原文 {len(text):4d} 字 | prompt {prompt_tok:4d} token | max_tokens={limit:3d} "
                  f"| 实出 {used:3d} | {finish:<5} | {time.time()-t0:5.1f}s")
            print(f"       结尾: …{tail}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  原文 {len(text)} 字 -> 异常: {type(exc).__name__}: {exc}", flush=True)

    print("\n" + "=" * 78)
    print("实验 2：n_ctx=2048 能否容纳一屏 OCR 文本（输出空间是否被挤光）")
    print("=" * 78)
    for mult in (3, 5, 8):
        text = BASE * mult
        prompt = f"将以下文本翻译为英语，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
        prompt_tok = len(llm.tokenize(prompt.encode("utf-8"), add_bos=True))
        free = 2048 - prompt_tok
        print(f"\n  原文 {len(text):4d} 字 -> prompt {prompt_tok:4d} token，"
              f"n_ctx=2048 只剩 {free} token 给输出", flush=True)
        if free <= 16:
            print("       → 输出空间不足，产品里会失败", flush=True)
            continue
        t0 = time.time()
        try:
            # 模拟「理想情况下给足输出空间」
            r = llm(prompt, max_tokens=min(free, 1024), stop=["</s>"], echo=False, **SAMPLING)
            out = r["choices"][0]["text"].strip()
            finish = r["choices"][0]["finish_reason"]
            used = r["usage"]["completion_tokens"]
            print(f"      → 实出 {used:4d} token | {finish:<5} | {time.time()-t0:5.1f}s | "
                  f"输出 {len(out)} 字符", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"      → 异常: {type(exc).__name__}: {exc}", flush=True)

    print("\n" + "=" * 78)
    print("实验 3：提示词里的换行数量对输出的影响（官方模板是 1 个空行，产品是 1 个换行）")
    print("=" * 78)
    text = "无氧铜杆的含氧量控制在百万分之十以下"
    head = "将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释："
    variants = [
        ("单换行 + 产品措辞", head.format(target="英语") + "\n" + text),
        ("空行 + 产品措辞", head.format(target="英语") + "\n\n" + text),
        ("空行 + 官方措辞(多一个的)", head.format(target="英语") + "\n\n" + text),
        ("空行 + 英文名(English)", head.format(target="English") + "\n\n" + text),
    ]
    for label, prompt in variants:
        r = llm(prompt, max_tokens=128, stop=["</s>"], echo=False, **SAMPLING)
        print(f"  {label:<26} -> {r['choices'][0]['text'].strip()}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
