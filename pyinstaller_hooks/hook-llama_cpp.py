# PyInstaller hook for llama-cpp-python
# 收集 llama_cpp 的运行时动态库（ggml.dll / llama.dll 等），
# 避免 onedir 打包后加载 GGUF 模型时报「找不到 DLL」。
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, copy_metadata

# 收集 llama_cpp 包内的动态库（lib/ 下的 .dll）
binaries = collect_dynamic_libs("llama_cpp")

# 收集 llama_cpp 包的元数据（某些版本依赖 metadata 判断版本）
datas = collect_data_files("llama_cpp")

# 收集包元数据（可选，保证版本信息可用）
try:
    datas += copy_metadata("llama-cpp-python")
except Exception:
    pass

# 若 collect_dynamic_libs 未捕获到（新版结构不同），手动兜底
if not binaries:
    import os
    import llama_cpp
    pkg_dir = os.path.dirname(llama_cpp.__file__)
    lib_dir = os.path.join(pkg_dir, "lib")
    if os.path.isdir(lib_dir):
        for name in os.listdir(lib_dir):
            if name.endswith((".dll", ".so", ".dylib")):
                src = os.path.join(lib_dir, name)
                binaries.append((src, "llama_cpp/lib"))

    print("[Hook] llama_cpp dynamic libs collected: {}".format(len(binaries)))
