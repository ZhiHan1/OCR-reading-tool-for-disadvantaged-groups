"""语音结果整段粘贴到外部光标，不发送 Enter，按开关保留/恢复剪贴板。"""
import ctypes
from ctypes import wintypes as W
from collections import deque
import os
import time
from PySide6.QtCore import QObject, QTimer, Signal, QMimeData
from PySide6.QtGui import QGuiApplication


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', INPUTUNION)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', W.DWORD), ('flags', W.DWORD), ('hwndActive', W.HWND),
                ('hwndFocus', W.HWND), ('hwndCapture', W.HWND), ('hwndMenuOwner', W.HWND),
                ('hwndMoveSize', W.HWND), ('hwndCaret', W.HWND), ('rcCaret', W.RECT)]


class WindowsInput:
    def __init__(self):
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.keep_clipboard = False
        self._clipboard_original = None
        self._clipboard_sequence = None
        self._restore_timer = QTimer()
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self.restore_clipboard)
        for name, args, result in [
            ('GetForegroundWindow', [], W.HWND),
            ('GetWindowThreadProcessId', [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            ('GetGUIThreadInfo', [W.DWORD, ctypes.POINTER(GUITHREADINFO)], W.BOOL),
            ('IsWindow', [W.HWND], W.BOOL),
            ('SetForegroundWindow', [W.HWND], W.BOOL),
            ('GetAsyncKeyState', [ctypes.c_int], ctypes.c_short),
            ('SendInput', [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int], W.UINT),
            ('GetClipboardSequenceNumber', [], W.DWORD),
        ]:
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result

    def current(self):
        hwnd = self.api.GetForegroundWindow()
        if not hwnd:
            return None
        pid = W.DWORD()
        thread = self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        focus = info.hwndFocus if self.api.GetGUIThreadInfo(thread, ctypes.byref(info)) else None
        return (hwnd, pid.value, focus)

    def alive(self, target):
        if not target or not self.api.IsWindow(target[0]):
            return False
        pid = W.DWORD()
        self.api.GetWindowThreadProcessId(target[0], ctypes.byref(pid))
        return pid.value == target[1]

    def activate(self, target):
        return bool(self.api.SetForegroundWindow(target[0]))

    def keys_down(self):
        return any(self.api.GetAsyncKeyState(k) & 0x8000 for k in (1, 2, 16, 17, 18, 91, 92))

    def write(self, text):
        text = ' '.join(text.splitlines()).replace(chr(9), ' ')
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        sequence = self.api.GetClipboardSequenceNumber()
        if not self.keep_clipboard and (self._clipboard_original is None or sequence != self._clipboard_sequence):
            original = QMimeData()
            source = clipboard.mimeData()
            if source is not None:
                for fmt in source.formats():
                    original.setData(fmt, source.data(fmt))
            self._clipboard_original = original
        clipboard.setText(text)
        self._clipboard_sequence = self.api.GetClipboardSequenceNumber()
        events = (INPUT * 4)()
        for event, vk, flags in zip(events, (0x11, 0x56, 0x56, 0x11), (0, 0, 2, 2)):
            event.type = 1
            event.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        sent = self.api.SendInput(len(events), events, ctypes.sizeof(INPUT))
        self._restore_timer.start(700)
        if sent != len(events):
            raise RuntimeError('系统未完成文字输入（目标程序权限可能更高）；请从结果框复制剩余文字，避免重复输入')

    def restore_clipboard(self):
        original, self._clipboard_original = self._clipboard_original, None
        if original is not None and not self.keep_clipboard and self.api.GetClipboardSequenceNumber() == self._clipboard_sequence:
            QGuiApplication.clipboard().setMimeData(original)


class CursorInput(QObject):
    status = Signal(dict)

    def __init__(self, parent=None, backend=None):
        super().__init__(parent)
        self.backend = backend or WindowsInput()
        self.last_external = None
        self.target = None
        self.session = None
        self.queue = deque()
        self.enabled = True
        self.next_send = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def observe(self):
        current = self.backend.current()
        if current and current[1] != os.getpid():
            self.last_external = current
        return current

    def begin(self, session, enabled=True):
        self.observe()
        self.session = session
        self.enabled = enabled
        self.target = self.last_external
        self.queue.clear()
        if enabled and not self.backend.alive(self.target):
            self._report(False, '请先在目标应用点击输入光标，再点悬浮麦克风；本次仍保留识别文字')

    def cancel(self):
        self.queue.clear()
        self.target = None
        self.session = None

    def submit(self, text, session):
        if not self.enabled or not text or session != self.session:
            return
        self.queue.append(dict(text=text, deadline=time.monotonic()+2.5, restore=False))
        self._tick()

    def _report(self, ok, text):
        self.status.emit(dict(type='asr-delivery', ok=ok, text=text))

    def _tick(self):
        current = self.observe()
        if not self.queue:
            return
        if time.monotonic() < self.next_send:
            return
        item = self.queue[0]
        if not self.backend.alive(self.target):
            self.queue.popleft()
            self._report(False, '原输入窗口已关闭或未选定；文字已保留在识别结果中')
            return
        if time.monotonic() > item['deadline']:
            self.queue.popleft()
            self._report(False, '未能恢复输入焦点；请从结果框复制文字')
            return
        if self.backend.keys_down():
            return
        if not current or current[0] != self.target[0]:
            if current and current[1] == os.getpid() and not item['restore']:
                item['restore'] = True
                self.backend.activate(self.target)
                return
            if current and current[1] != os.getpid():
                self.queue.popleft()
                self._report(False, '输入窗口已切换，本段未自动输入；文字已保留')
            return
        if self.target[2] and current[2] != self.target[2]:
            self.queue.popleft()
            self._report(False, '输入控件已切换，本段保留在结果框中；重新开始录音可更新目标')
            return
        self.queue.popleft()
        try:
            self.backend.write(item['text'])
            self.next_send = time.monotonic() + 0.25
            self._report(True, '已向目标光标发送文字')
        except Exception as exc:
            self._report(False, str(exc))
