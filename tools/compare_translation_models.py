"""翻译模型 A/B 质量对比：同一批中英日句子，比较两个 GGUF 的翻译输出。

完全复刻 core/gguf_translation_model.py 的推理配置：
    n_ctx=2048, n_threads=4, n_gpu_layers=0
    temperature=0.7, top_p=0.6, top_k=20, repeat_penalty=1.05
    max_tokens = min(256, max(32, len(text)*2))
    prompt = "将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"

用法：
    py -3.14 tools/compare_translation_models.py A.gguf B.gguf --out result.json
"""
from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

# 目标语言：中文名（与 core/language_utils.py 的 language_name 保持一致）
LANG_ZH = "中文"
LANG_EN = "英语"
LANG_JA = "日语"

# 测试集：覆盖冶炼/铜材领域 + 通用句 + 长句 + 数字/专有名词
CASES = [
    # (源文本, 目标语言, 说明)
    ("无氧铜杆的含氧量控制在百万分之十以下", LANG_EN, "行业术语-中译英"),
    ("阳极泥送去提纯以后要检测品位和回收率", LANG_EN, "行业流程-中译英"),
    ("退火温度控制在四百到五百度之间", LANG_JA, "工艺参数-中译日"),
    ("拉丝机的配模参数需要重新调整", LANG_JA, "设备-中译日"),
    ("电解铜的电流密度和槽电压是多少", LANG_EN, "提问句-中译英"),
    ("我们用的是TP2和TU1两种牌号，性能差别明显", LANG_EN, "牌号专名-中译英"),
    ("请把镀锡铜线的抗拉强度数据发给我", LANG_JA, "请求句-中译日"),
    ("The oxygen content of oxygen-free copper rod must be kept below 10 ppm", LANG_ZH, "英译中-行业"),
    ("The annealing process significantly improves the ductility of the wire", LANG_JA, "英译日-工艺"),
    ("Copper prices rose three percent today and may continue to climb", LANG_ZH, "英译中-通用"),
    ("銅線の引張強度は規格値を満たしています", LANG_ZH, "日译中-质量"),
    ("この設備のメンテナンスは毎月一度行う必要があります", LANG_EN, "日译英-维护"),
    ("キャストロールの表面温度と冷却水流量を確認してください", LANG_ZH, "日译中-设备"),
    ("第一季度营收同比增长百分之十七点五，主要来自铜加工业务", LANG_EN, "长句+数字-中译英"),
    ("The continuous casting and rolling line operated stably throughout the quarter, "
     "with output reaching 12,500 tons and a yield rate of 98.6 percent", LANG_ZH, "长句-英译中"),
    ("铜精矿品位下降导致冶炼成本上升，公司正在优化配料方案", LANG_JA, "长句-中译日"),
]

PROMPT = "将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"


def translate_all(model_path: Path, tag: str) -> list[dict]:
    from llama_cpp import Llama

    print(f"\n=== 加载 {tag} ===", flush=True)
    print(f"    文件: {model_path.name}", flush=True)
    print(f"    大小: {model_path.stat().st_size / 1048576:.2f} MB", flush=True)

    t0 = time.time()
    llm = Llama(
        model_path=str(model_path),
        n_ctx=2048,
        n_threads=4,
        n_gpu_layers=0,
        verbose=False,
    )
    load_s = time.time() - t0
    print(f"    加载耗时: {load_s:.1f} s", flush=True)

    rows = []
    for i, (text, target, note) in enumerate(CASES, 1):
        prompt = PROMPT.format(target=target, text=text)
        t = time.time()
        resp = llm(
            prompt,
            max_tokens=min(256, max(32, len(text) * 2)),
            temperature=0.7,
            top_p=0.6,
            top_k=20,
            repeat_penalty=1.05,
            stop=["</s>"],
            echo=False,
        )
        dt = time.time() - t
        out = resp["choices"][0]["text"].strip() if resp.get("choices") else ""
        rows.append({
            "idx": i, "text": text, "target": target, "note": note,
            "output": out, "seconds": round(dt, 2),
        })
        print(f"  [{i:2d}] {dt:5.2f}s  {out}", flush=True)

    # 内存峰值参考
    try:
        import psutil
        proc = psutil.Process()
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            wintypes.HANDLE(int(proc.pid)), ctypes.byref(pmc), pmc.cb)
        mem = {
            "peak_ws_mb": round(pmc.PeakWorkingSetSize / 1048576, 1),
            "ws_mb": round(pmc.WorkingSetSize / 1048576, 1),
            "commit_mb": round(pmc.PagefileUsage / 1048576, 1),
        }
    except Exception as exc:
        mem = {"error": str(exc)}

    del llm
    gc.collect()
    return {"tag": tag, "path": str(model_path), "load_seconds": round(load_s, 1),
            "memory": mem, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+", help="要对比的 GGUF 文件（按顺序）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args(argv)

    results = []
    for idx, m in enumerate(args.models, 1):
        p = Path(m)
        if not p.is_file():
            raise SystemExit(f"文件不存在: {p}")
        results.append(translate_all(p, p.stem))

    print("\n" + "=" * 70, flush=True)
    print("汇总", flush=True)
    for r in results:
        print(f"  {r['tag']:<34} {Path(r['path']).stat().st_size/1048576:8.1f} MB  "
              f"加载 {r['load_seconds']:5.1f}s  峰值WS {r['memory'].get('peak_ws_mb', '?')} MB", flush=True)

    if len(results) == 2:
        a, b = results
        print("\n逐句对比（A = %s, B = %s）" % (a["tag"], b["tag"]), flush=True)
        same = 0
        for ra, rb in zip(a["rows"], b["rows"]):
            ok = ra["output"] == rb["output"]
            same += ok
            print(f"\n[{ra['idx']:2d}] {'完全一致' if ok else '不同'}  {ra['note']}", flush=True)
            print(f"     原文: {ra['text']}", flush=True)
            print(f"     A: {ra['output']}", flush=True)
            if not ok:
                print(f"     B: {rb['output']}", flush=True)
        print(f"\n完全一致: {same}/{len(a['rows'])}", flush=True)

    if args.out:
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
