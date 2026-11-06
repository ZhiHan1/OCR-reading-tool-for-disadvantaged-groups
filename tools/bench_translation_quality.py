# -*- coding: utf-8 -*-
"""HY-MT1.5 翻译准确度实验：当前实现 vs 官方推荐用法。

产品现状（A 组，core/gguf_translation_model.py）：
    直接把中文提示词当原始文本喂给模型，不套对话模板，不显式加 BOS。

本实验对比 5 组配置，全部用同一个 Q6_K 模型、同一批铜材领域句子：
    A 现状：原始文本 + 无 BOS + 0.7 采样（产品当前行为，逐比特复刻）
    B 套模板：apply_chat_template + 0.7 采样（官方 llama.cpp 用法）
    C 贪心：套模板 + temperature=0（每次结果完全可复现）
    D 贪心无模板：原始文本 + temperature=0（隔离「模板」与「采样」的各自贡献）
    E 模板+低温度：套模板 + temperature=0.2（保留一点采样多样性）

同时检查一个产品缺陷：翻译超长文本时 max_tokens 会被截断。

用法（项目根目录）：
    py -3.14 tools/bench_translation_quality.py --out result.json
    py -3.14 tools/bench_translation_quality.py --only A B     # 只跑部分配置
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL = ROOT / "模型" / "翻译" / "HY-MT1.5-1.8B-Q6_K.gguf"

# 官方推荐参数（HY-MT1.5-1.8B-GGUF README）
OFFICIAL_SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05}

# 与产品完全一致的提示词（core/gguf_translation_model.py translate()）
PROMPT_ZH = "将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
# 官方 ZH<=>XX 模板（比产品的多了「的」字，且用空行分隔）
PROMPT_OFFICIAL = (
    "将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：\n\n"
    "{source_text}"
)

CASES = [
    # (源文本, 目标语言代码, 说明)
    ("无氧铜杆的含氧量控制在百万分之十以下", "en", "行业术语-中译英"),
    ("阳极泥送去提纯以后要检测品位和回收率", "en", "行业流程-中译英"),
    ("退火温度控制在四百到五百度之间", "ja", "工艺参数-中译日"),
    ("拉丝机的配模参数需要重新调整", "ja", "设备-中译日"),
    ("电解铜的电流密度和槽电压是多少", "en", "提问句-中译英"),
    ("我们用的是TP2和TU1两种牌号，性能差别明显", "en", "牌号专名-中译英"),
    ("请把镀锡铜线的抗拉强度数据发给我", "ja", "请求句-中译日"),
    ("铜精矿品位下降导致冶炼成本上升，公司正在优化配料方案", "ja", "长句-中译日"),
    ("The oxygen content of oxygen-free copper rod must be kept below 10 ppm", "zh", "英译中-行业"),
    ("Copper prices rose three percent today and may continue to climb", "zh", "英译中-通用"),
    ("銅線の引張強度は規格値を満たしています", "zh", "日译中-质量"),
    ("キャストロールの表面温度と冷却水流量を確認してください", "zh", "日译中-设备"),
    # 长文本：用来暴露 max_tokens = min(256, max(32, len*2)) 的截断问题
    ("连续连轧生产线的工艺控制要点如下：首先，铜液温度应稳定在摄氏一千一百五十度"
     "左右，波动范围不超过正负五度；其次，铸坯的冷却水压力应保持在零点三到零点四"
     "兆帕之间，流量约为每小时一百二十立方米；再次，轧制过程中必须实时监测乳液"
     "浓度，正常范围是百分之八到百分之十，浓度偏低会导致轧辊磨损加快、表面质量"
     "下降；最后，收线环节要控制张力在合理区间，张力过大会使线材出现缩颈甚至断裂。"
     "以上各项参数需由班组每小时记录一次，发现异常立即上报工艺部门并留存数据备查。",
     "en", "长文本-中译英-截断测试"),
]

CONFIGS = {
    "A": {"label": "现状：原始文本+无BOS+0.7", "chat": False, "sampling": OFFICIAL_SAMPLING, "bos": False},
    "B": {"label": "套模板+0.7", "chat": True, "sampling": OFFICIAL_SAMPLING, "bos": True},
    "C": {"label": "套模板+贪心", "chat": True, "sampling": {"temperature": 0.0}, "bos": True},
    "D": {"label": "原始文本+贪心", "chat": False, "sampling": {"temperature": 0.0}, "bos": False},
    "E": {"label": "套模板+温度0.2", "chat": True,
          "sampling": {"temperature": 0.2, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05},
          "bos": True},
}


def product_max_tokens(text: str) -> int:
    """产品当前的 max_tokens（core/gguf_translation_model.py）"""
    return min(256, max(32, len(text) * 2))


def lang_name(code: str) -> str:
    from core.language_utils import language_name

    return language_name(code)


def build_prompt(case_text: str, target: str) -> str:
    return PROMPT_ZH.format(target=lang_name(target), text=case_text)


def run_config(letter: str, llm) -> dict:
    spec = CONFIGS[letter]
    rows = []
    for idx, (text, target, note) in enumerate(CASES, 1):
        prompt = build_prompt(text, target)
        tokens_limit = product_max_tokens(text)

        if spec["chat"]:
            messages = [{"role": "user", "content": PROMPT_OFFICIAL.format(
                target_language=lang_name(target), source_text=text)}]
            t0 = time.time()
            resp = llm.create_chat_completion(
                messages=messages, max_tokens=tokens_limit,
                stop=["</s>"], **spec["sampling"])
            dt = time.time() - t0
            out = (resp["choices"][0]["message"].get("content") or "").strip()
            finish = resp["choices"][0].get("finish_reason")
            n_tok = resp.get("usage", {}).get("completion_tokens")
        else:
            t0 = time.time()
            resp = llm(prompt, max_tokens=tokens_limit, stop=["</s>"], echo=False,
                       **spec["sampling"])
            dt = time.time() - t0
            out = (resp["choices"][0]["text"] or "").strip()
            finish = resp["choices"][0].get("finish_reason")
            n_tok = resp.get("usage", {}).get("completion_tokens")

        rows.append({
            "idx": idx, "note": note, "text": text, "target": target,
            "output": out, "seconds": round(dt, 2),
            "max_tokens": tokens_limit, "finish_reason": finish,
            "completion_tokens": n_tok,
            "truncated": finish == "length",
        })
        flag = " [截断]" if finish == "length" else ""
        print(f"  [{idx:2d}] {dt:5.2f}s {out[:110]}{flag}", flush=True)

    return {"config": letter, "label": spec["label"], "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--only", nargs="+", choices=sorted(CONFIGS), default=sorted(CONFIGS))
    args = ap.parse_args(argv)

    from llama_cpp import Llama

    print(f"模型: {MODEL.name}  {MODEL.stat().st_size / 1048576:.1f} MB", flush=True)
    t0 = time.time()
    llm = Llama(model_path=str(MODEL), n_ctx=2048, n_threads=4, n_gpu_layers=0, verbose=False)
    print(f"加载 {time.time() - t0:.1f} s\n", flush=True)

    results = []
    for letter in args.only:
        print(f"===== {letter}: {CONFIGS[letter]['label']} =====", flush=True)
        results.append(run_config(letter, llm))
        print("", flush=True)

    del llm
    gc.collect()

    # 汇总：各配置之间的逐句一致率 + 截断统计
    print("=" * 72, flush=True)
    print("汇总", flush=True)
    for r in results:
        trunc = sum(1 for row in r["rows"] if row["truncated"])
        total_s = sum(row["seconds"] for row in r["rows"])
        print(f"  {r['config']}  {r['label']:<26} 总耗时 {total_s:6.1f}s  "
              f"截断 {trunc}/{len(r['rows'])}", flush=True)

    if len(results) > 1:
        print("\n逐句一致率（相对第一组）", flush=True)
        base = results[0]
        for r in results[1:]:
            same = sum(1 for a, b in zip(base["rows"], r["rows"]) if a["output"] == b["output"])
            print(f"  {base['config']} vs {r['config']}: {same}/{len(base['rows'])}", flush=True)

    if args.out:
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
