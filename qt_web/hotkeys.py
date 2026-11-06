"""Web 快捷键值规范化工具。键盘捕获由原有网页逻辑负责。"""


def normalize_hotkey(value):
    value = str(value or "").strip().lower()
    if not value or value.startswith("mouse:"):
        return value
    aliases = {"control": "ctrl", "win": "windows", "meta": "windows"}
    parts = [aliases.get(part.strip(), part.strip()) for part in value.split("+")]
    if any(not part for part in parts):
        raise ValueError("组合键格式不正确")
    modifiers = ("ctrl", "alt", "shift", "windows")
    base = [part for part in parts if part not in modifiers]
    if len(base) != 1:
        raise ValueError("请使用修饰键加一个主键")
    return "+".join([part for part in modifiers if part in parts] + base)
