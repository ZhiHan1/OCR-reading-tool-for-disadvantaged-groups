# -*- coding: utf-8 -*-
"""量化可行性验证：把 Paraformer-Large 导出为 ONNX 并做 INT8 动态量化。

只读原模型，产物写到独立目录，不改动 模型/语音/paraformer-large/。

用法（项目根目录）：
    py -3.14 tools/export_onnx_probe.py
    py -3.14 tools/export_onnx_probe.py --model 模型/语音/SenseVoiceSmall

产物：
    <输出目录>/model.onnx           FP32 导出
    <输出目录>/model_quant.onnx     INT8 动态量化（只量化 MatMul）
    <输出目录>/probe_result.json   体积与耗时实测
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# 与主程序一致的线程限制：必须在 import numpy/torch 之前
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np


def _force_legacy_onnx_exporter():
    """让 torch.onnx.export 走旧版 TorchScript 导出器（dynamo=False）。

    背景：torch>=2.6 默认 dynamo=True，走 torch.export 路线；FunASR 的 CIF
    predictor 含数据相关的动态控制流（cif_predictor.py cif_export），
    torch.export 会抛 GuardOnDataDependentSymNode 而失败。
    funasr 调用 torch.onnx.export 时没传 dynamo，这里补默认值，
    不改第三方源码。
    """
    import torch.onnx

    _orig = torch.onnx.export

    def export(*args, **kwargs):
        kwargs.setdefault("dynamo", False)
        return _orig(*args, **kwargs)

    torch.onnx.export = export


def size_mb(path):
    p = Path(path)
    return round(p.stat().st_size / 1024 / 1024, 1) if p.is_file() else None


def _infer_shapes_path_works() -> bool:
    """探测当前 onnx 的 infer_shapes_path 是否真的会写文件。"""
    import tempfile
    import onnx

    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.onnx")
        dst = os.path.join(d, "out.onnx")
        m = onnx.helper.make_model(
            onnx.helper.make_graph(
                [onnx.helper.make_node("Identity", ["x"], ["y"])],
                "probe",
                [onnx.helper.make_tensor_value_info("x", onnx.TensorProto.FLOAT, [1])],
                [onnx.helper.make_tensor_value_info("y", onnx.TensorProto.FLOAT, [1])],
            ),
            opset_imports=[onnx.helper.make_opsetid("", 14)],
        )
        onnx.save(m, src)
        try:
            onnx.shape_inference.infer_shapes_path(src, dst)
        except Exception:
            return False
        return os.path.isfile(dst)


def _patch_infer_shapes_path():
    """用 in-memory infer_shapes 实现 infer_shapes_path 的落盘语义。"""
    import onnx

    def patched(model_path, output_path=None, **kwargs):
        model = onnx.load(str(model_path))
        inferred = onnx.shape_inference.infer_shapes(model)
        target = output_path if output_path is not None else model_path
        onnx.save(inferred, str(target))

    onnx.shape_inference.infer_shapes_path = patched
    print("      已修补 onnx.shape_inference.infer_shapes_path（onnx>=1.23 空操作）")


def _prepare_model_path(model_dir: Path) -> Path:
    """规避中文路径下 sentencepiece 的 NOT_FOUND。

    只复制/链接很小的配置与词表，不复制 model.pt：在 TEMP 下建一个 ASCII
    目录，把除 model.pt 外的文件复制过去、model.pt 用符号链接指向原文件
    （Windows 无权限时退回复制）。
    """
    if str(model_dir).isascii():
        return model_dir
    import shutil

    # 注意：USERPROFILE/TEMP 里含中文用户名（如「籽寒」），不是 ASCII，
    # 所以不能用 TEMP。复用项目里挑 ASCII 目录的逻辑。
    from core.asr_service import _pick_ascii_cache_dir

    work = Path(_pick_ascii_cache_dir()) / ("asr_export_" + model_dir.name)
    work.mkdir(parents=True, exist_ok=True)
    for item in model_dir.iterdir():
        if not item.is_file():
            continue
        dst = work / item.name
        if dst.exists():
            continue
        if item.name == "model.pt":
            try:
                os.symlink(item, dst)
                continue
            except (OSError, NotImplementedError):
                pass
        shutil.copy2(item, dst)
    return work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="模型/语音/paraformer-large")
    ap.add_argument("--out", default="")
    ap.add_argument("--opset", type=int, default=14)
    ap.add_argument("--skip-export", action="store_true",
                    help="复用已有 model.onnx，只跑量化与检查")
    args = ap.parse_args()

    model_dir = (ROOT / args.model).resolve()
    if not (model_dir / "model.pt").is_file():
        sys.exit(f"模型目录无效: {model_dir}")

    # SenseVoice 的 bpe 分词器在 Windows 中文路径下会让 sentencepiece 的
    # C++ 加载器报 NOT_FOUND。改用 ASCII 临时目录加载（只复制小文件）。
    load_dir = _prepare_model_path(model_dir)
    if load_dir != model_dir:
        print(f"      中文路径规避：改用 {load_dir}")

    tag = model_dir.name + "-onnx"
    out_dir = (ROOT / args.out) if args.out else (model_dir.parent / tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 加载模型: {model_dir}")
    _force_legacy_onnx_exporter()
    from funasr import AutoModel

    t0 = time.perf_counter()
    model = AutoModel(
        model=str(load_dir),
        device="cpu",
        disable_update=True,
        disable_pbar=True,
        disable_log=True,
        trust_remote_code=False,
    )
    load_s = time.perf_counter() - t0
    print(f"      加载耗时 {load_s:.1f}s  参数量 "
          f"{sum(p.numel() for p in model.model.parameters()) / 1e6:.1f}M")

    print(f"[2/4] 导出 ONNX（opset={args.opset}）→ {out_dir}")
    if args.skip_export:
        if not (out_dir / "model.onnx").is_file():
            sys.exit(
                f"--skip-export 需要 {out_dir / 'model.onnx'}，但它不存在。\n"
                f"（FP32 中间产物通常在验证后已清理，去掉该参数重新导出即可。）"
            )
        export_dir = str(out_dir)
        export_s = 0.0
        print("      跳过导出，复用已有 model.onnx")
    else:
        t0 = time.perf_counter()
        export_dir = model.export(
            output_dir=str(out_dir),
            quantize=False,
            opset_version=args.opset,
            device="cpu",
            type="onnx",
        )
        export_s = time.perf_counter() - t0
        print(f"      导出耗时 {export_s:.1f}s  → {export_dir}")

    fp32_path = Path(export_dir) / "model.onnx"
    if not fp32_path.is_file():
        cands = [p for p in Path(export_dir).glob("*.onnx") if "_quant" not in p.name]
        if not cands:
            sys.exit("未找到导出的 onnx 文件")
        fp32_path = cands[0]
    fp32_mb = size_mb(fp32_path)
    print(f"      FP32 ONNX: {fp32_mb} MB")

    print("[3/4] INT8 动态量化（仅 MatMul，与 funasr 官方一致）")
    t0 = time.perf_counter()
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quant_path = Path(str(fp32_path).replace(".onnx", "_quant.onnx"))
    # onnx>=1.23 的 shape_inference 不再写中间文件，导致 onnxruntime 的
    # load_model_with_shape_infer 找不到 model-inferred.onnx。这里手动做
    # shape inference 并落盘，再交给 quantize_dynamic（语义与官方一致）。
    import onnx

    # onnx>=1.23 的 onnx.shape_inference.infer_shapes_path 是空操作（实测不写文件），
    # 导致 onnxruntime 的 load_model_with_shape_infer 读不到中间文件。
    # 这里用 in-memory 版本顶上，返回语义一致（同样落盘到目标路径）。
    if not _infer_shapes_path_works():
        _patch_infer_shapes_path()

    model_proto = onnx.load(str(fp32_path))

    quantize_dynamic(
        model_input=model_proto,
        model_output=str(quant_path),
        op_types_to_quantize=["MatMul"],
        per_channel=True,
        reduce_range=False,
        weight_type=QuantType.QUInt8,
    )
    quant_s = time.perf_counter() - t0
    quant_mb = size_mb(quant_path)
    print(f"      量化耗时 {quant_s:.1f}s  INT8 ONNX: {quant_mb} MB")

    print("[4/4] 推理连通性检查（同一段音频，两个模型各跑一次）")
    audio = np.zeros(16000 * 2, dtype=np.float32)
    audio[8000:24000] = np.random.RandomState(0).randn(16000).astype(np.float32) * 0.05

    result = {
        "model": model_dir.name,
        "original_pt_mb": size_mb(model_dir / "model.pt"),
        "fp32_onnx_mb": fp32_mb,
        "int8_onnx_mb": quant_mb,
        "ratio_vs_pt": round(fp32_mb / size_mb(model_dir / "model.pt"), 3) if fp32_mb else None,
        "int8_vs_pt_ratio": round(quant_mb / size_mb(model_dir / "model.pt"), 3) if quant_mb else None,
        "load_seconds": round(load_s, 1),
        "export_seconds": round(export_s, 1),
        "quantize_seconds": round(quant_s, 1),
    }

    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(quant_path), providers=["CPUExecutionProvider"])
        result["onnx_inputs"] = [
            {"name": i.name, "shape": [str(d) for d in i.shape], "type": i.type}
            for i in sess.get_inputs()
        ]
        result["onnx_outputs"] = [
            {"name": o.name, "shape": [str(d) for d in o.shape], "type": o.type}
            for o in sess.get_outputs()
        ]
        print("      ONNX 输入:", [i["name"] for i in result["onnx_inputs"]])
        print("      ONNX 输出:", [o["name"] for o in result["onnx_outputs"]])
    except Exception as e:
        result["onnx_probe_error"] = str(e)
        print(f"      ONNX session 检查失败: {e}")

    out_json = out_dir / "probe_result.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out_json}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
