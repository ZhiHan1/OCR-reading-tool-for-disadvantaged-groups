"""进程级运行时限制：必须在 numpy / scipy 导入之前生效。

为什么需要这个模块
------------------
OpenBLAS 会按「CPU 逻辑核心数」预留线程通信缓冲，每个线程 32MB。
本机 16 核，实测仅 `import numpy` 就提交了 501 MB（RSS 只有 30 MB，
属于「占了提交空间但没真正摸过」）；funasr 再拉进 scipy.signal 时
又会为 scipy 自带的那份 OpenBLAS 再提交 532 MB。

我们的推理主要走 torch 自己的线程池和 kaldi-native-fbank（C++，不走
BLAS），numpy 的 BLAS 只服务零散小矩阵运算。实测（paraformer-large，
3 秒音频单次识别）：

    线程数   模型加载   单次识别   进程提交内存
    16       10.9s     0.245s     2863 MB
    4        10.5s     0.256s     2089 MB
    2        10.7s     0.318s     2090 MB

因此默认取 4：省 773 MB，延迟差异在噪声范围内。

配置优先级
----------
1. 环境变量 ``OCR_BLAS_THREADS``（便于临时排查 / 打包时外部覆盖）
2. ``config.yaml`` 的 ``performance.blas_threads``
3. 默认 4

设为 0 或负数表示不干预，交回 OpenBLAS 自己决定。
"""
import os

DEFAULT_BLAS_THREADS = 4

#: 需要限制的变量：OpenBLAS 读前者；OpenMP 构建的 BLAS 读后者。
_ENV_KEYS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")

_applied = False


def _from_config() -> int:
    """从 config.yaml 读取 performance.blas_threads（失败则用默认值）。"""
    try:
        import yaml
        from utils.paths import app_root, resource_root

        for base in (app_root(), resource_root()):
            path = base / "config.yaml"
            if not path.is_file():
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            value = (data.get("performance") or {}).get("blas_threads")
            if value is not None:
                return int(value)
    except Exception:
        pass
    return DEFAULT_BLAS_THREADS


def apply_runtime_limits(threads: int = None) -> int:
    """限制 BLAS 线程数，返回最终生效值（0 表示未干预）。

    必须在 numpy / scipy / torch 之前调用：OpenBLAS 只在第一次初始化
    线程池时读取环境变量，之后再改就无效了。
    """
    global _applied
    if _applied:
        return int(os.environ.get(_ENV_KEYS[0], "0") or 0)

    if threads is None:
        raw = os.environ.get("OCR_BLAS_THREADS")
        try:
            threads = int(raw) if raw not in (None, "") else _from_config()
        except (TypeError, ValueError):
            threads = DEFAULT_BLAS_THREADS

    _applied = True
    if threads is None or int(threads) <= 0:
        return 0

    value = str(int(threads))
    for key in _ENV_KEYS:
        # setdefault：外部显式设置的环境变量优先，不被我们覆盖。
        os.environ.setdefault(key, value)
    return int(os.environ.get(_ENV_KEYS[0], value))
