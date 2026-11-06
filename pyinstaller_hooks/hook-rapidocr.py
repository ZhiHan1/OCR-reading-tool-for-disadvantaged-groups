# PyInstaller hook for rapidocr
# rapidocr 使用模块级 __getattr__ 惰性导入（PEP 562），例如：
#   from rapidocr import RapidOCR  ->  实际 import_module(".main")
# PyInstaller 的静态分析无法追踪这种动态导入，必须显式收集全部子模块。
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# 收集全部子模块（含 main / ch_ppocr_* / inference_engine.onnxruntime 等）
hiddenimports = collect_submodules('rapidocr', filter=lambda name: not any(
    part in name for part in ('.pytorch', '.paddle', '.tensorrt', '.openvino')))

# 收集数据文件（若有）
datas = collect_data_files("rapidocr")

print("[Hook] rapidocr hidden modules: {}".format(len(hiddenimports)))
print("[Hook] rapidocr data files: {}".format(len(datas)))
