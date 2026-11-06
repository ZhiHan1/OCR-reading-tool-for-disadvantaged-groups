# PyInstaller hook for customtkinter
# customtkinter 内置主题 json（blue.json / dark-blue.json / green.json / gold.json）
# 及字体/图标资源，需要打包进 _internal，否则程序启动时找不到主题文件。
#
# 注意：PyInstaller 6.22 的 collect_data_files 的 includes 参数会错误过滤文件，
#       必须去掉 includes 直接收集全部数据文件。
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter")

print("[Hook] customtkinter theme data files: {}".format(len(datas)))
