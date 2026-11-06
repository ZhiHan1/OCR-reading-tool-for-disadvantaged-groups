"""FunASR 注册表和 torch.jit.script 需要运行时可读取的 Python 源码。"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

module_collection_mode = 'pyz+py'
hiddenimports = collect_submodules('funasr')
datas = collect_data_files('funasr') + copy_metadata('funasr')
