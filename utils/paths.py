"""共享路径规则：外置数据相对项目/EXE；打包资源相对 bundle。"""
from pathlib import Path
import sys


def app_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def models_root():
    return app_root() / 'models'


def model_drop_root(kind, base=None):
    return Path(base or app_root()) / '模型' / {'voice': '语音', 'translation': '翻译'}[kind]


def resource_root():
    return Path(getattr(sys, '_MEIPASS', app_root()))


def resolve_path(value, base=None):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(base or app_root()) / path).resolve()


def store_path(value, base=None):
    """项目内路径存相对路径；外部路径原样保留，不强制重定向。"""
    root = Path(base or app_root()).resolve()
    path = resolve_path(value, root)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
