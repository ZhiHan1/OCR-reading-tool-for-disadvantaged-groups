"""Windows 电源事件监听：休眠唤醒后通知上层重建音频设备表。

为什么需要这个模块：

PortAudio（sounddevice）在**导入的那一刻**调用一次 `Pa_Initialize()`，设备表
从此固化。Windows 休眠时音频驱动会被卸载/复位，唤醒后端点重新枚举，设备索引
整体平移或指向已失效的端点，而 PortAudio 不会自动重建这张表。

后果（本项目的真实故障）：休眠唤醒后录音流能正常打开，但采到的是纯静音，
VAD 检测不到语音，界面显示「未检测到说话，已暂停」——用户重试无效，
只能重启软件。

修法：监听 `WM_POWERBROADCAST` 的 `PBT_APMRESUMEAUTOMATIC` / `PBT_APMRESUMESUSPEND`，
唤醒后让语音工作进程重启（它是独立进程，重启即重新 `Pa_Initialize()`）。

实现注意：
- Qt 主窗口通过 `nativeEvent` 收 Windows 消息，不能另开窗口（会抢消息）。
- 唤醒事件可能连发多次，用节流避免连环重启。
- 用 `time.monotonic()` 而不是 `time.time()`：休眠期间 time.time() 会跳变。
"""
import ctypes
import ctypes.wintypes as wintypes
import sys
import time

from PySide6.QtCore import QObject, Signal

WM_POWERBROADCAST = 0x0218
WM_POWERBROADCAST_VALUES = {
    0x0007: 'PBT_APMSUSPEND',       # 即将休眠
    0x0012: 'PBT_APMRESUMEAUTOMATIC',  # 唤醒（自动，无用户参与）
    0x0004: 'PBT_APMRESUMESUSPEND',    # 唤醒（用户操作）
    0x0013: 'PBT_APMRESUMECRITICAL',   # 从严重错误恢复
}
RESUME_MESSAGES = {0x0012, 0x0004, 0x0013}

# 唤醒事件常连发两三次（RESUMEAUTOMATIC + RESUMESUSPEND），
# 在这个窗口内的重复事件只处理一次。
_THROTTLE_SECONDS = 3.0


class _MSG(ctypes.Structure):
    """与 Windows MSG 结构对齐（仅取 wParam 需要的前三个字段）。"""
    _fields_ = [
        ('hwnd', wintypes.HWND),
        ('message', wintypes.UINT),
        ('wParam', wintypes.WPARAM),
        ('lParam', wintypes.LPARAM),
        ('time', wintypes.DWORD),
        ('pt_x', wintypes.LONG),
        ('pt_y', wintypes.LONG),
    ]


class PowerMonitor(QObject):
    """把 Windows 电源广播翻译成 Qt 信号。

    用法：
        monitor = PowerMonitor(parent=window)
        window.nativeEvent = monitor.handle_native_event  # 或在窗口里转发
        monitor.resumed.connect(...)
    """

    resumed = Signal()   # 系统从休眠/睡眠唤醒（已节流）
    suspended = Signal()  # 系统即将进入休眠

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_resume = 0.0
        self._enabled = sys.platform == 'win32'

    def handle_native_event(self, event_type, message_ptr):
        """在主窗口的 nativeEvent 里调用。

        返回 (handled, result)；`handled=False` 表示交给 Qt 默认处理。
        """
        if not self._enabled:
            return False, 0
        # PySide6 传进来的是 VoidPtr 包装，对它做真值判断不可靠
        # （某些情况下非空指针也会判成 False），必须先转成整数。
        try:
            address = int(message_ptr)
        except (TypeError, ValueError):
            return False, 0
        if not address:
            return False, 0
        # Qt6 的 Windows native event type 是 0x0100 附近的 win32 消息值，
        # 实际用 struct 里的 message 字段判断更稳。
        try:
            msg = _MSG.from_address(address)
        except (TypeError, ValueError, OSError):
            return False, 0
        if msg.message != WM_POWERBROADCAST:
            return False, 0

        code = int(msg.wParam)
        if code == 0x0007:
            self.suspended.emit()
            return True, 0
        if code in RESUME_MESSAGES:
            now = time.monotonic()
            if now - self._last_resume >= _THROTTLE_SECONDS:
                self._last_resume = now
                self.resumed.emit()
            return True, 0
        return False, 0
