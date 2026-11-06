"""Web 发布版首次启动：只创建目录和配置，不复制/下载任何权重。"""
import sys
import os
from utils.paths import app_root, model_drop_root

def initialize():
    root = app_root()
    for kind in ('voice', 'translation'):
        model_drop_root(kind).mkdir(parents=True, exist_ok=True)
    for relative in ('models/asr', 'models/local', 'models/ocr', 'logs'):
        (root/relative).mkdir(parents=True, exist_ok=True)
    if getattr(sys, 'frozen', False):
        from utils.paths import resource_root
        gpu = resource_root()/'gpu_runtime'
        if gpu.is_dir():
            os.environ['PATH'] = str(gpu)+os.pathsep+os.environ.get('PATH','')
            if hasattr(os,'add_dll_directory'):
                global _gpu_dll_handle
                _gpu_dll_handle=os.add_dll_directory(str(gpu))
        # 无控制台版 stdout/stderr 为 None，日志库和本地推理库仍需要可写流。
        log = (root/'logs/application.log').open('a', encoding='utf-8', buffering=1)
        if sys.stdout is None: sys.stdout = log
        if sys.stderr is None: sys.stderr = log
    from core.config_manager import get_config
    config = get_config()
    if not config.config_path.exists(): config.save()
