# PyInstaller hook for rapidocr_onnxruntime
# rapidocr 通过 Path(__file__).resolve().parent 定位 models/*.onnx 和 config.yaml，
# 这些 .onnx / .yaml 不会被 PyInstaller 自动收集，必须显式打包到 _internal。
#
# 注意：PyInstaller 6.22 的 collect_data_files 的 includes 参数会错误过滤文件，
#       必须去掉 includes 直接收集全部数据文件。
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 收集包内所有数据文件（models/*.onnx、各子模块的 config.yaml）
datas = collect_data_files("rapidocr_onnxruntime")

# 收集所有子模块（rapid_ocr_api / ch_ppocr_v3_det / v3_rec / v2_cls），防止动态 import 遗漏
hiddenimports = collect_submodules("rapidocr_onnxruntime")

print("[Hook] rapidocr_onnxruntime data files: {}".format(len(datas)))
print("[Hook] rapidocr_onnxruntime hidden modules: {}".format(len(hiddenimports)))
