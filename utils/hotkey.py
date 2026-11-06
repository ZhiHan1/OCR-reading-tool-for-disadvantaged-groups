# utils/hotkey.py
import keyboard
import threading
import atexit
import time

try:
    import mouse
except ImportError:
    mouse = None

# 快捷键映射（键名与 config.yaml 的 hotkeys 段保持一致）
HOTKEY_MAP = {
    'func1_trigger': 'f7',      # 功能1: 框选朗读一次
    'func2_select': 'f9',       # 功能2: 选择区域（第一步）
    'func2_read': 'f8',         # 功能2: 按键朗读（第二步）
    'func3_toggle': 'f4',       # 功能3: 启动/暂停
    'func3_reselect': 'f5',     # 功能3: 重新选择
    'stop_speak': 'f6',         # 停止朗读
}

# 快捷键触发时间戳（用于去重）
_last_trigger_time = {name: 0 for name in HOTKEY_MAP}

# 快捷键按下标志（简单的布尔标志）
_key_flags = {name: False for name in HOTKEY_MAP}

_lock = threading.Lock()
_registered = False
_cooldown = 0.3  # 防抖时间（秒）


def _register_hotkeys():
    """注册全局快捷键"""
    global _registered
    # 先清理旧的钩子
    if _registered:
        try:
            keyboard.unhook_all()
        except Exception:
            pass

    # 逐个注册：单个键名非法（用户录到奇怪的组合、配置里是 None）时
    # 只跳过那一个，不能让整体抛异常——此时旧钩子已经被清掉了，
    # 一旦整体失败就是「所有快捷键突然都不好使，必须重启」。
    for func_name, key in HOTKEY_MAP.items():
        if key is None:
            continue
        key = str(key).strip()
        if not key:
            continue

        try:
            if key.startswith("mouse:"):
                if mouse is None:
                    print("[快捷键] 未安装 mouse，跳过鼠标快捷键")
                    continue
                mouse.on_button(
                    lambda event, fn=func_name: _on_key_press(fn),
                    buttons=_normalize_mouse_button(key.split(":", 1)[1]),
                    types=(mouse.DOWN,),
                )
            else:
                keyboard.add_hotkey(
                    key, lambda fn=func_name: _on_key_press(fn), trigger_on_release=False
                )
        except Exception as e:
            print(f"[快捷键] {func_name} 注册失败（{key!r}）: {e}")

    _registered = True
    print("[快捷键] 已注册全局快捷键")


def _on_key_press(func_name):
    """快捷键按下回调"""
    now = time.time()
    with _lock:
        # 防抖：如果距离上次触发时间太短，忽略
        if now - _last_trigger_time.get(func_name, 0) < _cooldown:
            return

        _last_trigger_time[func_name] = now
        _key_flags[func_name] = True


def _normalize_mouse_button(button):
    """统一 mouse 库对两枚侧键的命名。"""
    return {
        "xbutton1": "x", "xbutton2": "y",
        "button4": "x", "button5": "y",
        "side1": "x", "side2": "y",
    }.get(str(button or "").strip().lower(), str(button or "").strip().lower())


def _check_press(key_name):
    """检查快捷键是否被按下（一次性消费）"""
    with _lock:
        if _key_flags.get(key_name, False):
            _key_flags[key_name] = False
            return True
    return False


def consume(action: str) -> bool:
    """消费一次快捷键触发

    Args:
        action: HOTKEY_MAP 中的动作名（如 "func1_trigger"）

    Returns:
        bool: 该动作在本轮是否被触发
    """
    return _check_press(action)


def cleanup():
    """清理快捷键钩子"""
    global _registered
    try:
        keyboard.unhook_all()
        if mouse is not None:
            mouse.unhook_all()
        _registered = False
        print("[快捷键] 已清理")
    except Exception as e:
        print(f"[快捷键] 清理失败: {e}")


# 注册退出时的清理函数
atexit.register(cleanup)


# 初始化时注册快捷键
_register_hotkeys()
