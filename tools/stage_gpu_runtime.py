"""补齐 ONNX CUDA 执行依赖，不复制模型，也不打包整个 CUDA SDK。

为什么用**白名单**而不是通配符（踩过的坑）：
    原来 cudnn 用的是 `cudnn*64_9.dll`，会把 pip 包里所有 cudnn DLL 全抄进来，
    其中 5 个 OCR 根本不用，白占 354 MB：
        cudnn_adv64_9.dll                258.4 MB  (RNN/LSTM 高级算子)
        nvrtc64_120_0.dll                 85.7 MB  (运行时编译，TensoRT 才用)
        nvrtc-builtins64_129.dll           6.9 MB
        cudnn_cnn64_9.dll                  2.9 MB  (已被 V9 分包替代)
        cudnn_ext64_9.dll                  0.1 MB

怎么确定白名单的：
    用真实 OCR 模型（PP-OCRv6_det_small / rec_small / cls_mobile）在纯
    gpu_runtime 环境下跑一次 CUDA 推理，再用 CreateToolhelp32Snapshot
    枚举进程内实际加载的模块，只有真正被加载的才算必需。

    **必须用真实模型验证**，小测试模型只会加载 6 个 DLL，
    会误判 cudnn_engines_precompiled(503 MB) / cudnn_ops(100 MB) /
    cudnn_heuristic(90 MB) 等可以删除，删完 OCR 直接崩。

保留清单（11 个，1843.8 MB）与实测加载情况：
    cublas64_12.dll                        97.8 MB  必需
    cublasLt64_12.dll                     637.7 MB  必需
    cudart64_12.dll                         0.6 MB  必需
    cudnn64_9.dll                           0.3 MB  必需
    cudnn_engines_precompiled64_9.dll     503.7 MB  真实模型才用
    cudnn_engines_runtime_compiled64_9.dll 43.3 MB  真实模型才用
    cudnn_engines_tensor_ir64_9.dll         0.1 MB  真实模型才用
    cudnn_graph64_9.dll                    95.3 MB  必需
    cudnn_heuristic64_9.dll                90.4 MB  真实模型才用
    cudnn_ops64_9.dll                     100.8 MB  真实模型才用
    cufft64_11.dll                        273.8 MB  必需

注意：`onnxruntime_providers_cuda.dll` 在 `_internal/onnxruntime/capi/` 下，
由 PyInstaller 自动收集，不在这里。
"""
from pathlib import Path
import hashlib
import json
import shutil
import site
import sys

# 包名 -> 该包下需要复制的 DLL 文件名（精确匹配，不用通配符）
REQUIRED = {
    'cublas': [
        'cublas64_12.dll',
        'cublasLt64_12.dll',
    ],
    'cuda_runtime': [
        'cudart64_12.dll',
    ],
    'cufft': [
        'cufft64_11.dll',
    ],
    'cudnn': [
        'cudnn64_9.dll',
        'cudnn_graph64_9.dll',
        'cudnn_ops64_9.dll',
        'cudnn_heuristic64_9.dll',
        'cudnn_engines_precompiled64_9.dll',
        'cudnn_engines_runtime_compiled64_9.dll',
        'cudnn_engines_tensor_ir64_9.dll',
    ],
}

# 明确排除：即使将来 pip 包里出现同名也不复制
EXCLUDED = {
    'cudnn_adv64_9.dll',        # RNN/LSTM 高级算子，OCR 不用
    'cudnn_cnn64_9.dll',        # 已被 V9 分包替代
    'cudnn_ext64_9.dll',        # 扩展算子
    'nvrtc64_120_0.dll',        # 运行时编译，仅 TensorRT
    'nvrtc-builtins64_129.dll', # 同上
}


def stage(out):
    out = Path(out)
    destination = out / '_internal/gpu_runtime'
    destination.mkdir(parents=True, exist_ok=True)
    site_packages = site.getsitepackages()
    records = []
    for package, names in REQUIRED.items():
        directory = next(
            (Path(s) / 'nvidia' / package / 'bin'
             for s in site_packages
             if (Path(s) / 'nvidia' / package / 'bin').is_dir()),
            None,
        )
        if directory is None:
            raise RuntimeError('Missing NVIDIA runtime package: ' + package)
        for name in names:
            if name in EXCLUDED:
                continue
            src = directory / name
            if not src.is_file():
                raise RuntimeError(f'Missing GPU library: {package}/{name}')
            target = destination / src.name
            if not target.exists() or target.stat().st_size != src.stat().st_size:
                shutil.copy2(src, target)
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            records.append(dict(name=src.name, size=src.stat().st_size, sha256=digest))
    (out / 'gpu-runtime-manifest.json').write_text(
        json.dumps(records, indent=2), encoding='utf-8')
    return records


if __name__ == '__main__':
    result = stage(sys.argv[1])
    print('GPU_RUNTIME_READY', len(result), sum(i['size'] for i in result))
