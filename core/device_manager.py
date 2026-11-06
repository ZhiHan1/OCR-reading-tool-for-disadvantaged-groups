"""
计算设备管理器 - 探测可用的 CPU / GPU，供设置页选择推理设备

职责：
- 枚举本机所有可用的推理设备（CPU + 每块 GPU）
- 通过 nvidia-smi 拿到显卡型号、显存等展示信息（不依赖 onnxruntime 是否为 GPU 版）
- 检测 CUDA 推理能力是否真正可用（onnxruntime-gpu + CUDAExecutionProvider）
- 提供稳定可控的设备标识（"cpu" / "cuda:0" / "cuda:1" ...）

设计说明：
nvidia-smi 只能列出 NVIDIA 显卡，而 onnxruntime 的 CUDA provider 也只支持
NVIDIA。因此本模块把「机器上装了什么显卡」和「Python 侧能不能真的用」分开报告，
以便设置页如实告诉用户：为什么某块显卡不能选、需要装什么。

AMD / Intel 核显等无法被 onnxruntime CUDA 使用，只作为信息展示，不进入可选列表，
避免用户选中后程序静默回退到 CPU 却不知道原因。
"""
import logging
import shutil
import subprocess
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 设备类型
KIND_CPU = "cpu"
KIND_GPU = "gpu"

# 设备标识前缀
CUDA_PREFIX = "cuda:"

# 探测结果缓存（探测涉及子进程调用，只需一次）
_probe_cache: Optional[Dict] = None
_probe_lock = threading.Lock()

# 是否已把 nvidia pip 包的 bin 目录注入进程 PATH（幂等标记）
_nvidia_path_injected = False

# 子进程超时（秒）：显卡驱动异常时 nvidia-smi 可能长时间不返回
_NVSMI_TIMEOUT = 6


def _run_nvidia_smi(query_fields: str) -> Optional[str]:
    """调用 nvidia-smi，返回原始输出；不可用时返回 None"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        logger.debug("[设备] 未找到 nvidia-smi，无法通过它枚举 NVIDIA 显卡")
        return None

    try:
        completed = subprocess.run(
            [exe, f"--query-gpu={query_fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVSMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("[设备] 调用 nvidia-smi 失败: %s", e)
        return None

    if completed.returncode != 0:
        logger.warning("[设备] nvidia-smi 返回码 %s: %s", completed.returncode,
                       (completed.stderr or "").strip()[:200])
        return None

    return completed.stdout


def _parse_nvidia_smi(output: str) -> List[Dict]:
    """解析 nvidia-smi 的 CSV 输出为显卡信息列表"""
    gpus = []
    if not output:
        return gpus

    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue

        def _as_int(value):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        gpus.append({
            "index": index,
            "name": parts[1],
            "memory_total_mb": _as_int(parts[2]),
            "driver_version": parts[3],
        })
    return gpus


def _ensure_nvidia_dll_path() -> None:
    """把 nvidia pip 包（nvidia-*-cu12/cu13）的 bin 目录加入进程 PATH

    背景：
    onnxruntime-gpu 的 CUDA provider（onnxruntime_providers_cuda.dll）依赖
    cublasLt64_12.dll / cudnn64_9.dll 等，这些 DLL 由 nvidia-cublas-cu12、
    nvidia-cudnn-cu12 等 pip 包提供，装在 site-packages/nvidia/*/bin 下。

    Windows 上，仅靠 os.add_dll_directory() 不能保证「依赖链上的隐式依赖」
    被正确解析（实测 CUDA provider 会静默回退到 CPU）。把这些目录追加到
    PATH 是最可靠的做法，代价是进程内 PATH 变长，无副作用。

    本函数幂等：只追加一次。
    """
    global _nvidia_path_injected
    if _nvidia_path_injected:
        return

    try:
        import glob
        import os
        import sys

        site_packages = [p for p in sys.path if p and "site-packages" in p]
        if getattr(sys,'frozen',False):
            from utils.paths import resource_root
            runtime=resource_root()/'gpu_runtime'
            if runtime.is_dir():
                os.environ['PATH']=str(runtime)+os.pathsep+os.environ.get('PATH','')
                _nvidia_path_injected=True
                return
        if not site_packages:
            _nvidia_path_injected = True
            return

        site = site_packages[0]
        bins = sorted(glob.glob(os.path.join(site, "nvidia", "*", "bin")))
        if not bins:
            _nvidia_path_injected = True
            return

        existing = os.environ.get("PATH", "")
        new_bins = [b for b in bins if b not in existing]
        if new_bins:
            os.environ["PATH"] = os.pathsep.join(new_bins + [existing])
            logger.info("[设备] 已将 %s 个 nvidia 依赖目录加入 PATH", len(new_bins))
    except Exception as e:
        logger.warning("[设备] 注入 nvidia DLL 路径失败: %s", e)
    finally:
        _nvidia_path_injected = True


def _get_onnxruntime_info() -> Dict:
    """读取 onnxruntime 的版本与可用执行提供器（缺失时优雅降级）"""
    info = {
        "available": False,
        "version": "",
        "providers": [],
        "cuda_ready": False,
        "is_gpu_build": False,
    }

    # 在 import onnxruntime 之前注入 nvidia DLL 路径，否则 CUDA provider
    # 会在加载 onnxruntime_providers_cuda.dll 时因找不到依赖而静默失效。
    _ensure_nvidia_dll_path()

    try:
        import onnxruntime as ort
    except Exception as e:
        logger.info("[设备] 未安装 onnxruntime: %s", e)
        return info

    info["available"] = True
    info["version"] = getattr(ort, "__version__", "")
    try:
        info["providers"] = list(ort.get_available_providers())
    except Exception as e:
        logger.warning("[设备] 读取 onnxruntime 提供器失败: %s", e)
        info["providers"] = []

    # CUDAExecutionProvider 只有在装了 onnxruntime-gpu 且 CUDA/cuDNN 就绪时才出现
    info["cuda_ready"] = "CUDAExecutionProvider" in info["providers"]
    # GPU 版的包名是 onnxruntime-gpu，其 __version__ 与 CPU 版一致，
    # 因此以「是否带 CUDA/TensorRT provider」作为判断依据更可靠
    info["is_gpu_build"] = any(
        p in info["providers"] for p in ("CUDAExecutionProvider", "TensorrtExecutionProvider")
    )
    return info


def probe_devices(force: bool = False) -> Dict:
    """探测本机可用设备（结果缓存）

    Returns:
        {
            "cpu": {"id": "cpu", "kind": "cpu", "name": "CPU", "available": True},
            "gpus": [  # 仅包含 onnxruntime 真正可用的 GPU
                {"id": "cuda:0", "kind": "gpu", "index": 0,
                 "name": "NVIDIA GeForce RTX 3060 Ti", "memory_total_mb": 8192,
                 "driver_version": "610.88", "available": True},
                ...
            ],
            "all_gpus": [ ... ],       # nvidia-smi 检出的所有显卡（含不可用的）
            "onnxruntime": {...},
            "cuda_ready": bool,        # 是否有任何 GPU 真正可用于推理
        }
    """
    global _probe_cache

    with _probe_lock:
        if _probe_cache is not None and not force:
            return _probe_cache

        ort_info = _get_onnxruntime_info()
        smi_output = _run_nvidia_smi("index,name,memory.total,driver_version")
        all_gpus = _parse_nvidia_smi(smi_output) if smi_output else []

        cuda_ready = bool(ort_info["cuda_ready"] and all_gpus)

        gpus = []
        if cuda_ready:
            for gpu in all_gpus:
                gpus.append({
                    "id": f"{CUDA_PREFIX}{gpu['index']}",
                    "kind": KIND_GPU,
                    "index": gpu["index"],
                    "name": gpu["name"],
                    "memory_total_mb": gpu["memory_total_mb"],
                    "driver_version": gpu["driver_version"],
                    "available": True,
                })

        result = {
            "cpu": {
                "id": "cpu",
                "kind": KIND_CPU,
                "index": None,
                "name": "CPU",
                "available": True,
            },
            "gpus": gpus,
            "all_gpus": all_gpus,
            "onnxruntime": ort_info,
            "cuda_ready": cuda_ready,
        }

        _probe_cache = result
        logger.info(
            "[设备] 探测完成：检测到 %s 块 NVIDIA 显卡，可用的 GPU 设备 %s 个，CUDA 就绪=%s",
            len(all_gpus), len(gpus), cuda_ready,
        )
        return result


def clear_cache():
    """清空探测缓存（测试或运行中装了驱动后用）"""
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def device_display_name(device: Optional[Dict]) -> str:
    """设备显示名：GPU 带上序号与显存，便于多卡时区分"""
    if not device:
        return "CPU"
    if device.get("kind") == KIND_CPU:
        return "CPU（处理器）"

    name = device.get("name") or "GPU"
    index = device.get("index", 0)
    memory = device.get("memory_total_mb")
    if memory:
        return f"GPU {index} · {name} · {memory} MB"
    return f"GPU {index} · {name}"


def list_selectable_devices(force: bool = False) -> List[Dict]:
    """列出设置页可选设备：CPU 永远第一位，其后是每块可用 GPU"""
    info = probe_devices(force=force)
    devices = [info["cpu"]]
    devices.extend(info["gpus"])
    return devices


def find_device(device_id: str, force: bool = False) -> Optional[Dict]:
    """按设备标识查找设备；找不到（例如拔了显卡）时返回 None"""
    if not device_id:
        return None
    for device in list_selectable_devices(force=force):
        if device["id"] == device_id:
            return device
    return None


def resolve_device(device_id: str, force: bool = False) -> Dict:
    """解析设备标识，非法或不可用时安全回退到 CPU

    回退而不是报错，是为了保证：用户换了显卡、或在没装的 CUDA 的机器上
    打开别人拷过来的配置，程序仍然能用（只是退回 CPU）。
    """
    device = find_device(device_id, force=force)
    if device is not None:
        return device

    if device_id and device_id != "cpu":
        logger.warning("[设备] 配置的设备 %s 不可用，回退到 CPU", device_id)

    return probe_devices(force=force)["cpu"]


def get_ocr_device(force: bool = False) -> Dict:
    """读取配置并解析出 OCR 应使用的设备"""
    from core.config_manager import get_config

    config = get_config()
    device_id = config.get("ocr", "device", default="cpu") or "cpu"
    return resolve_device(device_id, force=force)


def diagnostic_text() -> str:
    """生成一段人类可读的诊断信息（设置页/日志排查用）"""
    info = probe_devices()
    ort = info["onnxruntime"]

    lines = []
    if ort["available"]:
        lines.append(f"onnxruntime {ort.get('version', '?')}")
    else:
        lines.append("onnxruntime 未安装")

    if ort["is_gpu_build"]:
        lines.append("已安装 GPU 版")
    else:
        lines.append("当前为 CPU 版（无 CUDA 提供器）")

    if info["all_gpus"]:
        for gpu in info["all_gpus"]:
            lines.append(f"检测到 {gpu['name']}")
    else:
        lines.append("未检测到 NVIDIA 显卡")

    return " · ".join(lines)
