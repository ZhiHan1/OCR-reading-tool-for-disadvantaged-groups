"""OCR 文字朗读工具 —— WebEngine 前端入口。

用法：python -m qt_web.main

这是**唯一**的 UI 入口。旧入口 `main_modular_win11.py`（tkinter）与
`qt_main.py`（原生 Qt 控件）已于 2026-11-05 随 `legacy/` 一并删除，
代码里保留下来的同名注释只是历史对照，文件已不存在。
"""
import os
import sys
import time

# 保证以模块方式直接运行也能 import 项目根目录的包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须早于 numpy/scipy 导入：限制 OpenBLAS 线程缓冲（详见 utils/env_limits.py）
from utils.env_limits import apply_runtime_limits
apply_runtime_limits()

from PySide6.QtCore import QTimer, QUrl, Qt, QPoint, Signal, QEvent, QRectF, QObject, Slot
from PySide6.QtGui import QIcon, QPainter, QPen, QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QToolButton, QLabel, QVBoxLayout

from core.config_manager import get_config
from controllers.app_controller import AppController
from controllers.translation_manager import TranslationManager
from qt_ui.bridge import QtResultRouter, SignalBridge
from qt_ui.selector import QtRegionSelector
from qt_ui.selector_adapter import QtSelectorAdapter
from qt_web import api as api_module
from qt_web.api import WebApi
from utils.system_tray import SystemTray
from qt_web.power_monitor import PowerMonitor
from qt_web.voice_session import VoiceSession
from qt_web.cursor_input import CursorInput
from utils.paths import resource_root

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# 页面模式 -> AppController 属性名
SCAN_ATTRS = {"func3": "func3", "manga_mode": "manga", "game_mode": "game"}


class VoiceGlyph(QWidget):
    """抽象麦克风图标：待机带斜线，录音时按输入电平跳动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recording = False
        self.phase = 0
        self.level = 0.0
        self.target_level = 0.0
        self.last_level_at = 0.0
        self.level_history = [0.0] * 5
        self.phase_name = 'idle'
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _tick(self):
        self.phase = (self.phase + 1) % 24
        if self.recording:
            target = self.target_level if time.monotonic() - self.last_level_at < 0.25 else 0.0
            self.level += (target - self.level) * (0.65 if target > self.level else 0.3)
            self.level_history = self.level_history[1:] + [self.level]
        self.update()

    def set_state(self, phase):
        self.phase_name = phase
        self.recording = phase == 'recording'
        if phase in ('recording', 'starting', 'stopping', 'recognizing', 'loading'):
            if not self.timer.isActive():
                self.timer.start(33)
        else:
            self.level = 0.0
            self.target_level = 0.0
            self.level_history = [0.0] * 5
            self.timer.stop()
        self.update()

    def set_level(self, level):
        self.target_level = max(0.0, min(1.0, float(level)))
        self.last_level_at = time.monotonic()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        accent = QColor("#ef766b")
        idle = QColor("#aab6bf")
        p.setPen(QPen(accent if self.recording else idle, 2.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        if self.phase_name in ('starting', 'stopping', 'recognizing', 'loading'):
            p.setPen(QPen(QColor('#63c9b1'), 2.8, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(14, 14, w - 28, h - 28), self.phase * 15 * 16, 240 * 16)
        elif not self.recording:
            p.drawRoundedRect(QRectF(23, 10, 12, 25), 6, 6)
            p.drawArc(QRectF(17, 19, 24, 25), 180 * 16, 180 * 16)
            p.drawLine(29, 44, 29, 49)
            p.drawLine(23, 49, 35, 49)
            p.setPen(QPen(QColor('#293139'), 6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(13, 13, 45, 45)
            p.setPen(QPen(idle, 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(13, 13, 45, 45)
        else:
            # 整个图标变成声波；每根来自真实电平历史，静音收为五个点。
            p.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap))
            for i, value in enumerate(self.level_history):
                amplitude = max(0.0, (value - 0.025) / 0.975) ** 0.55
                height = 2 + 30 * amplitude
                x = 13 + i * 8
                p.drawLine(QPoint(x, int(h / 2 - height / 2)), QPoint(x, int(h / 2 + height / 2)))


class VoiceFloatWindow(QWidget):
    """置顶的紧凑麦克风窗，点击一次录音，再点一次停止识别。"""

    status_signal = Signal(str)
    level_signal = Signal(float)

    def __init__(self, main_window):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.main_window = main_window
        self._drag_offset = None
        self._press_pos = None
        self._dragging = False
        self._positioned = False
        self.setObjectName("voiceFloat")
        self.setFixedSize(88, 100)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 7)
        layout.setSpacing(3)

        self.button = QToolButton(self)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.setObjectName("voiceFloatButton")
        self.button.setText("")
        self.button.setToolTip("点击开始录音，再点一次停止并识别")
        self.button.setFixedSize(58, 58)
        self.button.clicked.connect(main_window.toggle_voice_float_recording)
        self.glyph = VoiceGlyph(self.button)
        self.glyph.setGeometry(0, 0, 58, 58)
        layout.addWidget(self.button, 0, Qt.AlignCenter)

        self.label = QLabel("点按开始", self)
        self.label.setObjectName("voiceFloatLabel")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        self.setStyleSheet("""
            QWidget#voiceFloat { background: #20252b; border: 1px solid #414a53; border-radius: 18px; }
            QToolButton#voiceFloatButton { background: #293139; border: 1px solid #4d5a62; border-radius: 29px; }
            QToolButton#voiceFloatButton:hover { background: #35424a; }
            QLabel#voiceFloatLabel { color: #e5e9ec; font-family: 'Microsoft YaHei UI', 'Microsoft YaHei'; font-size: 11px; }
        """)
        self.status_signal.connect(self.set_status)
        self.level_signal.connect(self.set_level)
        for target in (self, self.button, self.label):
            target.installEventFilter(self)
        self.button.setToolTip('点击开始/停止；按住麦克风拖动位置')

    def show_near_corner(self):
        screen = QApplication.primaryScreen()
        if screen and not self._positioned:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 80)
            self._positioned = True
        self.show()
        self.raise_()

    def set_recording(self, recording):
        self.glyph.set_state('recording' if recording else 'idle')
        self.label.setText("录音中 · 点停" if recording else "点按开始")

    def set_enabled(self, enabled):
        # 悬浮窗可见时按后端真实录音状态刷新图标，避免与占用来源不一致。
        self.glyph.set_state(self.main_window.voice.phase)

    def apply_state(self, state):
        phase = state['phase']
        self.glyph.set_state(phase)
        if phase == 'recording':
            text = ({'manual': '录音中 · 点停', 'realtime': '实时听写中', 'auto': '说完自动停'}.get(state.get('mode'), '录音中 · 点停')
                    if state['source'] == 'float' else '主窗口录音中')
        else:
            text = {'idle': '点按开始', 'starting': '启动中…', 'stopping': '结束中…',
                    'recognizing': '识别中…', 'loading': '加载模型…'}.get(phase, '按住说话重试')
        self.label.setText(text)
        self.button.setToolTip(state['text'] + '\n按住图标可拖动；主窗口「按住说话」可接管')

    def set_level(self, level):
        self.glyph.set_level(level)

    def set_status(self, text):
        if "正在录音" in text or "录音中" in text:
            self.set_recording(True)
        elif "识别中" in text:
            self.set_recording(False)
            self.label.setText("识别中…")
        elif text:
            self.set_recording(False)
            self.label.setText(text[:8])

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.pos()
            self._dragging = False
            self.button.setDown(watched is self.button)
            watched.grabMouse()
            return True
        if kind == QEvent.MouseMove and self._press_pos is not None:
            pos = event.globalPosition().toPoint()
            if (pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.button.setDown(False)
            if self._dragging:
                self.move(pos - self._drag_offset)
            return True
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._press_pos is not None:
            click = not self._dragging and watched is self.button
            self._press_pos = self._drag_offset = None
            self._dragging = False
            self.button.setDown(False)
            watched.releaseMouse()
            if click:
                self.button.click()
            return True
        return super().eventFilter(watched, event)


def resource_path(relative_path: str) -> str:
    return str(resource_root() / relative_path)



def _ensure_qwebchannel_js():
    """把 Qt 内置的 qwebchannel.js 导出到 web/js/vendor/（页面按相对路径加载）。"""
    out = os.path.join(WEB_DIR, "js", "vendor", "qwebchannel.js")
    if os.path.exists(out):
        return
    try:
        import PySide6.QtWebChannel  # noqa: F401
        from PySide6.QtCore import QFile, QIODevice

        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.ReadOnly):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(bytes(f.readAll().data()))
            f.close()
    except Exception as e:  # noqa: BLE001
        print(f"[WebUI] 导出 qwebchannel.js 失败: {e}")


class NavBridge(QObject):
    """前端路由 → 后端：告诉后端当前停在哪一页（用于调节模型目录轮询频率）。"""

    _HOT_PAGES = ("voice", "settings")

    def __init__(self, api):
        super().__init__()
        self.api = api

    @Slot(str)
    def page(self, name: str):
        self.api._set_model_scan_active(str(name or "") in self._HOT_PAGES)


class WebMainWindow(QMainWindow):
    """主窗口：一个 QWebEngineView + 全部后端接线（与 qt_main 等价）。"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR 文字朗读工具")

        # 窗口尺寸按屏幕可用区域自适应：默认偏紧凑，绝不超出桌面
        # （高 DPI 缩放下 1180x780 这种尺寸会顶到屏幕边缘，所以按比例收敛）
        desired_w, desired_h = 1100, 680
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            win_w = min(desired_w, int(avail.width() * 0.92))
            win_h = min(desired_h, int(avail.height() * 0.92))
        else:
            win_w, win_h = desired_w, desired_h

        self.resize(win_w, win_h)
        self.setMinimumSize(860, 560)

        if screen is not None:
            avail = screen.availableGeometry()
            self.move(
                avail.x() + max(0, (avail.width() - win_w) // 2),
                avail.y() + max(0, (avail.height() - win_h) // 2),
            )

        # ---------- 后端（与 qt_main 完全一致的接线） ----------
        self.app = AppController()
        api_module._CURRENT_APP = self.app  # 供 WebApi._mode_flags 读取已构造的控制器
        self.config = get_config()
        self.voice_float = VoiceFloatWindow(self)
        self.voice = VoiceSession(self)
        self.cursor_input = CursorInput(self)
        self.voice.level.connect(self.voice_float.set_level)
        self.voice.changed.connect(self.voice_float.apply_state)

        # 休眠唤醒后 PortAudio 设备表过期，录音会「打开成功但采到静音」。
        # 这里监听 Windows 电源广播，唤醒时重启语音工作进程以重建设备表。
        self.power_monitor = PowerMonitor(self)
        self.power_monitor.resumed.connect(self.voice.handle_system_resume)

        self.translation_manager = TranslationManager(
            self.config,
            self.app.tts_service,
            translation_service=self.app.translation_service,
        )

        self.bridge = SignalBridge()
        self.router = QtResultRouter(
            self.config,
            self.translation_manager,
            self.app.tts_service,
            self._get_noop_widget,
        )
        self.bridge._router = self.router

        # 注入 Qt 选区（替换后端 tkinter selector）
        self.app.screenshot_service.selector = QtSelectorAdapter(lambda: QtRegionSelector())

        # 功能开关 / 队列模式
        self._enabled = {
            m: bool(self.config.get("ui", f"{m}_enabled", default=True)) for m in ("func1", "func2", "func3", "manga", "game")
        }
        self._queue_mode = {
            "func1": bool(self.config.get("tts", "func1_queue_mode", default=True)),
            "func2": bool(self.config.get("tts", "func2_queue_mode", default=True)),
        }
        self._closing = False

        # ---------- Web UI ----------
        _ensure_qwebchannel_js()
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        self.api = WebApi(self)
        self.voice.changed.connect(self.api._push)
        self.voice.changed.connect(self._on_voice_session_state)
        self.voice.result.connect(self._on_voice_result)
        self.cursor_input.status.connect(self.api._push)
        # 翻译可用性兜底：自动翻译开关开着但没有任何可用模型时，
        # 后端翻译会静默失败（无原文也无译文），这里退回「显示 + 朗读原文」。
        self.router.translation_available = self.api.translation_usable
        self.channel = QWebChannel(self)
        self.channel.registerObject("api", self.api)
        self.view.page().setWebChannel(self.channel)

        index = os.path.join(WEB_DIR, "index.html")
        self.view.load(QUrl.fromLocalFile(index))

        # ---------- 后端回调 → 前端推送 ----------
        self._setup_callbacks()
        if bool(self.config.get("asr", "floating_window", default=False)):
            self.voice_float.show_near_corner()

        # ---------- 快捷键 ----------
        self._hotkey_hooks = []
        self._hotkey_paused = False
        self.apply_hotkeys()

        # ---------- 翻译队列 ----------
        self.translation_manager.start_worker()
        self._translation_timer = QTimer(self)
        self._translation_timer.setInterval(120)
        self._translation_timer.timeout.connect(self._drain_translations)
        self._translation_timer.start()

        # ---------- 托盘 ----------
        self.tray = SystemTray(
            app_name="OCR朗读工具",
            on_quit=self._on_quit,
            on_show=self._on_show,
        )
        self.tray.start()

        try:
            icon = resource_path("Logo.ico")
            if os.path.exists(icon):
                self.setWindowIcon(QIcon(icon))
        except Exception as e:  # noqa: BLE001
            print(f"[UI] 设置窗口图标失败: {e}")

    # ---------- 结果路由需要的一个占位 get_widget ----------
    def _get_noop_widget(self, _attr):
        return None

    def nativeEvent(self, event_type, message):
        """接收 Windows 原生消息，把休眠唤醒转给 PowerMonitor。

        只有子类化 QMainWindow 才能拿到 WM_POWERBROADCAST；不处理的消息
        必须交回 Qt 默认逻辑，否则窗口会失去拖拽、DPI 等原生行为。
        """
        handled, result = self.power_monitor.handle_native_event(event_type, message)
        if handled:
            return True, result
        return super().nativeEvent(event_type, message)

    # ---------- 后端回调接线（与 qt_main._setup_callbacks 等价） ----------
    def _setup_callbacks(self):
        self.bridge.status_updated.connect(
            lambda s: self.api.push_status(s)
        )
        self.router.result_to_display.connect(self._on_result_to_display)
        self.router.copy_requested.connect(self._copy_to_clipboard)

        # 页面前端路由落定后通知后端：只有语音页/设置页需要高频盯着模型目录，
        # 其余页面把模型扫描降到 30 秒一次（见 WebApi._set_model_scan_active）。
        self.nav = NavBridge(self.api)
        self.channel.registerObject("nav", self.nav)

        self.app.func1.on_status_update = self.bridge.emit_status
        self.app.func1.on_result = self.bridge.emit_result

        self.app.func2.on_status_update = self.bridge.emit_status
        self.app.func2.on_result = self.bridge.emit_result
        self.app.func2.on_region_changed = lambda has: self.api.push_region("func2", bool(has))

        from qt_web.main import SCAN_ATTRS  # 本模块映射

        for attr, mode in SCAN_ATTRS.items():
            controller = getattr(self.app, attr, None)
            if controller is None:
                continue
            controller.on_status_update = self.bridge.emit_status
            controller.on_result = self.bridge.emit_result
            controller.on_state_changed = (
                lambda running, m=mode: self.api.push_scan_state(m, bool(running))
            )

        # 说明：翻译结果不经控制器回调，而是走 TranslationManager 的队列
        # （main.py 下方 drain_results → api.push_translate_result）。
        # TranslationController 没有 on_result / on_status_update，别再接回来——
        # 它的签名与 bridge.emit_result 不匹配，接了会推空白消息。

    # 说明：Web UI 的语音识别跑在独立进程（qt_web.voice_session），
    # GUI 进程里不构造 voice_input / asr_service —— 它们只服务旧 tkinter 版，
    # 构造后还要拉起麦克风与音频后端，纯属浪费内存。这里只接语音会话的状态。

    def _on_voice_session_state(self, state):
        if state['phase'] == 'starting' and self.cursor_input.session != state['session']:
            self.cursor_input.begin(state['session'], bool(self.config.get('asr', 'auto_input', default=True)))
        elif self.cursor_input.session != state['session']:
            self.cursor_input.cancel()

    def _on_voice_result(self, text):
        # 保留既有结果框与可选剪贴板路由，只输入原始识别文本，不带 UI 前缀。
        self.cursor_input.backend.keep_clipboard = bool(self.config.get('asr', 'copy_to_clipboard', default=True))
        self.bridge.emit_result(text, display=f'[语音输入] {text}')
        if self.config.get('asr', 'auto_input', default=True):
            self.cursor_input.submit(text, self.voice.session)

    def show_voice_float(self):
        self.voice_float.set_enabled(True)
        self.voice_float.show_near_corner()
        return True

    def hide_voice_float(self):
        self.voice_float.set_enabled(False)
        self.voice_float.hide()
        return True

    def toggle_voice_float_recording(self):
        if self.voice.owner == 'float' and self.voice.phase in ('starting', 'recording'):
            return self.voice.stop('float')['ok']
        if self.voice.phase == 'idle':
            result = self.voice.start('float')
            if not result['ok']:
                self.voice_float.label.setText('请先选择模型')
                self.voice_float.button.setToolTip(result.get('error', '启动失败'))
                self.api.push_status(result.get('error', '启动失败'))
            return result['ok']
        else:
            self.voice_float.label.setText('主窗口可接管')
            return False

    # ---------- 前端访问器（api.py 用） ----------
    def enabled(self, mode):
        return bool(self._enabled.get(mode, True))

    def queue_mode(self, mode):
        return bool(self._queue_mode.get(mode, True))

    def set_mode_enabled(self, mode, enabled):
        self.config.set("ui", f"{mode}_enabled", value=bool(enabled))
        self.config.save()
        self._enabled[mode] = bool(enabled)

    def hotkey_conflict(self, key, value):
        """检查候选热键是否与其他项冲突，返回冲突的键名或 None。

        语音输入已移除快捷键，仅检查 OCR 与朗读快捷键。
        """
        try:
            from qt_web.hotkeys import normalize_hotkey

            target = normalize_hotkey(value) if str(value).strip() else ""
        except ValueError:
            return None
        if not target:
            return None
        current = {
            "func1_trigger": self.config.get("hotkeys", "func1_trigger", default=""),
            "func2_select": self.config.get("hotkeys", "func2_select", default=""),
            "func2_read": self.config.get("hotkeys", "func2_read", default=""),
            "func3_toggle": self.config.get("hotkeys", "func3_toggle", default=""),
            "func3_reselect": self.config.get("hotkeys", "func3_reselect", default=""),
            "stop_speak": self.config.get("hotkeys", "stop_speak", default=""),
        }
        for k, v in current.items():
            if k == key:
                continue
            try:
                normalized = normalize_hotkey(v) if str(v).strip() else ""
            except ValueError:
                normalized = str(v).strip().lower()
            if normalized == target:
                return k
        return None

    def apply_hotkeys(self):
        """按配置注册全局热键（先清理旧的）。"""
        self.pause_hotkeys(True, clear_only=True)
        self._hotkey_paused = False
        self.pause_hotkeys(False)

    def pause_hotkeys(self, paused, clear_only=False):
        """暂停/恢复全局热键（录制按键时暂停，避免触发功能）。"""
        import keyboard as _kb

        if paused:
            self._hotkey_paused = True
            for h in self._hotkey_hooks:
                try:
                    _kb.remove_hotkey(h)
                except Exception:
                    pass
            self._hotkey_hooks = []
            return
        if self._hotkey_paused is False and self._hotkey_hooks:
            return
        self._hotkey_paused = False

        mapping = {
            "func1_trigger": lambda: self.api.request("trigger_func1", "{}"),
            "func2_select": lambda: self.api.request("func2_select", "{}"),
            "func2_read": lambda: self.api.request("func2_read", "{}"),
            "func3_toggle": lambda: self.api.request("toggle_scan", '{"mode":"func3"}'),
            "func3_reselect": lambda: self.api.request("reselect_scan", '{"mode":"func3"}'),
            "stop_speak": lambda: self.api.request("stop_speak", "{}"),
        }
        for name, fn in mapping.items():
            value = str(self.config.get("hotkeys", name, default="")).strip()
            if not value or value.startswith("mouse:"):
                continue
            try:
                self._hotkey_hooks.append(_kb.add_hotkey(value, fn))
            except Exception as e:  # noqa: BLE001
                print(f"[快捷键] 注册 {name}={value} 失败: {e}")

    # ---------- 翻译队列轮询 ----------
    def _drain_translations(self):
        if self._closing:
            return
        try:
            results = self.translation_manager.drain_results()
        except Exception:
            return
        if not results:
            return
        from controllers.translation_manager import MANUAL_MODE

        for result, mode in results:
            if mode == MANUAL_MODE:
                # 翻译页：只回填到译文框，不进结果区（与旧 UI _show_manual_translation_result 一致）
                self.api.push_translate_result(result or "")
            else:
                self.api.push_result(f"翻译: {result}", append=True)
                self.api.push_status("自动翻译完成", "ok")
                # 朗读翻译结果（与 qt_main 路由行为一致）
                try:
                    if self.config.get("translation", f"{mode}_enabled", default=False):
                        self.app.tts_service.speak(result)
                except Exception:
                    pass

    # ---------- Qt 槽 ----------
    def _on_result_to_display(self, widget_attr, text, replace):
        # 语音输入有自己的结果框，单独推送，不要混进共用结果区
        if widget_attr == "voice_result_text":
            self.api.push_asr_result(text)
            return
        # 其他 widget_attr 在 Web UI 里没有对应控件，统一进结果区
        self.api.push_result(text, append=not replace)

    def _copy_to_clipboard(self, text):
        try:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(text)
        except Exception as e:  # noqa: BLE001
            print(f"[剪贴板] 复制失败: {e}")

    def _on_show(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.activateWindow()

    def _on_quit(self):
        if self._closing:
            return
        self._closing = True
        self.api._model_scan_timer.stop()
        self.cursor_input.timer.stop()
        self.cursor_input.cancel()
        self.voice.close()
        try:
            self.config.save()
        except Exception:
            pass
        try:
            self._translation_timer.stop()
        except Exception:
            pass
        try:
            self.pause_hotkeys(True)
        except Exception:
            pass
        try:
            # keyboard 的监听线程可能独立于已注册的 hotkey 句柄存在。
            # 与旧版 hotkey.cleanup() 的语义一致，退出时彻底摘掉本进程钩子。
            import keyboard as _kb

            _kb.unhook_all()
        except Exception:
            pass
        try:
            self.translation_manager.stop_worker()
        except Exception:
            pass
        try:
            self.app.shutdown()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        QApplication.quit()
        # QWebEngine、keyboard 和部分音频后端可能仍有非 Qt 线程。旧版
        # qt_main.py 也采用此兜底，确保点击退出后不留下后台 Python 进程。
        os._exit(0)


def main():
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    app = QApplication(sys.argv)
    app.setApplicationName("OCR 文字朗读工具")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口 → 最小化到托盘

    win = WebMainWindow()
    win.show()

    # 右上角叉号与旧 tkinter / 原生 Qt 版一致：直接完整退出。
    def close_event_filter(event):
        if not win._closing:
            event.ignore()
            win._on_quit()
            return
        event.accept()

    win.closeEvent = close_event_filter

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
