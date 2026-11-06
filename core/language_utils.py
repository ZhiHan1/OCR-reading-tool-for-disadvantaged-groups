"""翻译相关的语言代码工具。"""

# HY-MT1.5 官方 prompt 模板要求目标语言用「完整中文语言名」，
# 因此这里统一用官方写法（英语/日语/韩语/法语/德语/西班牙语/俄语），
# 不再使用「英文/日文/韩文」这类可能让模型困惑的旧写法。
LANG_NAMES = {
    "auto": "自动检测", "zh": "中文", "en": "英语", "ja": "日语",
    "ko": "韩语", "fr": "法语", "de": "德语", "es": "西班牙语", "ru": "俄语",
}
LANG_DISPLAY_NAMES = dict(LANG_NAMES)
NAME_TO_CODE = {name: code for code, name in LANG_DISPLAY_NAMES.items()}


def normalize_lang(value: str) -> str:
    value = (value or "").strip()
    return value if value in LANG_DISPLAY_NAMES else NAME_TO_CODE.get(value, value)


def language_name(code: str) -> str:
    return LANG_NAMES.get(normalize_lang(code), code)


def language_display_name(code: str) -> str:
    return LANG_DISPLAY_NAMES.get(normalize_lang(code), code)
