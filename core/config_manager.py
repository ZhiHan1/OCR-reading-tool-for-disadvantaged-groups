"""
配置管理器 - 从 YAML 文件加载配置
"""
import os
import copy
import math
import functools

import yaml
from utils.paths import resolve_path, resource_root

# libyaml 的 C 加速加载器比纯 Python 版快 7~8 倍（config.yaml 实测 6.2ms -> 0.75ms）。
# 未装 libyaml 时回退纯 Python 版，行为完全一致。
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@functools.lru_cache(maxsize=1)
def _load_defaults_cached() -> dict:
    """解析 defaults.yaml（只读文件，进程内解析一次即可）"""
    path = resource_root() / 'defaults.yaml'
    data = yaml.load(path.read_text(encoding='utf-8'), Loader=_Loader)
    if not isinstance(data, dict):
        raise ValueError('默认配置文件格式错误')
    return data


def load_defaults():
    """默认配置的独立副本（调用方可以随意改，不会污染缓存）

    每次 validate_config() 都要拿默认配置做模板校验，保存一次配置会连调
    好几次；过去每次都要重新解析 YAML，现在解析结果缓存，只做 deepcopy。
    """
    return copy.deepcopy(_load_defaults_cached())


def validate_config(data):
    defaults = load_defaults()
    if not isinstance(data, dict) or not data or not any(key in defaults for key in data):
        raise ValueError('请选择应用导出的配置 YAML 文件')
    def check(value, template, path=''):
        if isinstance(template, dict):
            if not isinstance(value, dict):
                raise ValueError(f'{path} 必须为配置对象')
            for key, val in value.items():
                if not isinstance(key, str):
                    raise ValueError('配置键必须是字符串')
                if key in template:
                    check(val, template[key], path + '.' + key)
        elif isinstance(template, bool):
            if not isinstance(value, bool): raise ValueError(f'{path} 必须为开关值')
        elif isinstance(template, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{path} 必须为有限数值')
            if isinstance(template, int) and not isinstance(value, int):
                raise ValueError(f'{path} 必须为整数')
        elif not isinstance(value, type(template)):
            raise ValueError(f'{path} 类型不正确')
    check(data, defaults)
    limits = {('tts','rate'):(-10,10), ('tts','pitch'):(-10,10), ('tts','volume'):(0,1),
              ('asr','segment_silence_ms'):(400,2000), ('asr','turn_silence_ms'):(2000,10000),
              ('asr','idle_exit_seconds'):(0,86400)}
    for section in ('func3','manga_mode','game_mode'):
        limits[('ocr',section,'scan_interval')]=(0.05,60)
        limits[('ocr',section,'stable_frames')]=(1,10)
        limits[('ocr',section,'history_size')]=(1,20)
    limits.update({('ocr','func3','similarity_threshold'):(0,1), ('ocr','func3','short_text_max_len'):(0,10),
                   ('ocr','func3','min_length_for_similarity'):(0,10000), ('ocr','func3','repeat_cooldown'):(0,3600)})
    for keys, (low,high) in limits.items():
        value = data
        for key in keys:
            value = value.get(key) if isinstance(value,dict) else None
        if value is not None and not low <= value <= high:
            raise ValueError('.'.join(keys) + f' 必须在 {low}～{high} 之间')
    for section,key,choices in [('asr','input_mode',('manual','realtime','auto')),
                                ('ui','web_theme',('dark','light')),('tts','backend',('sapi','edge'))]:
        value=data.get(section,{}).get(key)
        if value is not None and value not in choices: raise ValueError(f'{section}.{key} 不受支持')
    device_name=data.get('asr',{}).get('device_name')
    if device_name is not None and len(str(device_name))>200:
        raise ValueError('asr.device_name 过长')
    providers=data.get('translation',{}).get('online_providers',[])
    if any(not isinstance(p,dict) or not isinstance(p.get('name'),str) for p in providers):
        raise ValueError('在线服务商配置格式不正确')
    for key,value in data.get('hotkeys',{}).items():
        if not isinstance(value,str): raise ValueError('快捷键必须为字符串')
    return copy.deepcopy(data)


class Config:
    """配置管理器"""

    def __init__(self, config_path="config.yaml"):
        self.config_path = resolve_path(config_path)
        if self.config_path == (resource_root() / 'defaults.yaml').resolve():
            raise ValueError('默认配置只读，不能作为当前配置')
        self.data = self._load_config()
        self._persisted = copy.deepcopy(self.data)

    def _load_config(self):
        """加载配置文件"""
        if not self.config_path.exists():
            print(f"[配置] 配置文件不存在: {self.config_path}")
            return self._default_config()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.load(f, Loader=_Loader)
                print(f"[配置] 已加载配置文件: {self.config_path}")
        except Exception as e:
            print(f"[配置] 加载配置文件失败: {e}")
            return self._default_config()

        # 空文件或写坏的 YAML（safe_load 返回 None / 非字典）：
        # 若原样返回，后面 set() 会直接 TypeError，导致设置完全保存不了。
        if not isinstance(data, dict):
            print(f"[配置] 配置文件内容无效（{type(data).__name__}），使用默认配置")
            return self._default_config()
        return data

    def _default_config(self):
        return load_defaults()

    def get(self, *keys, default=None):
        """获取配置值

        Examples:
            config.get("tts", "rate") -> 200
            config.get("hotkeys", "func1_trigger") -> "f7"
        """
        value = self.data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def save(self):
        """保存配置到文件（原子写入）

        先写临时文件再替换，避免写入过程中程序被中断时把 config.yaml
        截断成半截（那样下次启动读不到配置，用户全部设置会退回默认值）。
        """
        tmp_path = self.config_path.parent / (self.config_path.name + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    self.data,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_path)
            self._persisted = copy.deepcopy(self.data)
            print(f"[配置] 已保存配置文件: {self.config_path}")
            return True
        except Exception as e:
            print(f"[配置] 保存配置文件失败: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            self.data.clear()
            self.data.update(copy.deepcopy(self._persisted))
            raise OSError(f'当前配置保存失败，已回滚：{e}') from e

    def replace(self, data):
        replacement = validate_config(data)
        self.data.clear()
        self.data.update(replacement)
        self.save()

    def export(self, path):
        destination = resolve_path(path)
        if destination in (self.config_path, (resource_root()/'defaults.yaml').resolve()):
            raise ValueError('请导出到新文件，不能覆盖当前配置或默认配置')
        temp = destination.with_name(destination.name+'.tmp')
        try:
            temp.write_text(yaml.safe_dump(self.data,allow_unicode=True,sort_keys=False),encoding='utf-8')
            os.replace(temp,destination)
        finally:
            if temp.exists(): temp.unlink()
        return str(destination)

    def set(self, *keys, value):
        """设置配置值

        Examples:
            config.set("tts", "rate", value=250)
            config.set("hotkeys", "func1_trigger", value="f1")
        """
        if not keys:
            return False

        data = self.data
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]

        data[keys[-1]] = value
        return True


# 全局配置实例
_config = None


def get_config():
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config():
    """重新加载配置

    注意：必须原地刷新同一个 Config 实例。
    因为主窗口、各功能控制器、翻译管理器等都持有它的引用，
    若替换成新对象，这些引用会变成"旧配置"，导致热重载不生效、
    甚至把旧数据整份写回文件（覆盖刚保存的设置）。
    """
    global _config
    if _config is None:
        _config = Config()
    else:
        _config.data = _config._load_config()
        _config._persisted = copy.deepcopy(_config.data)
    return _config


def save_config(config_data=None):
    """
    保存配置到文件

    Args:
        config_data: 配置数据字典，如果为None则保存当前全局配置

    Returns:
        bool: 是否保存成功
    """
    global _config
    if config_data is not None:
        # 更新全局配置的数据
        if _config is None:
            _config = Config()
        _config.data = config_data

    if _config is None:
        _config = Config()

    return _config.save()
