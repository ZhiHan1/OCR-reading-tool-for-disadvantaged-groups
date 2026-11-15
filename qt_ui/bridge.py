"""后端回调 → Qt 信号的桥接层。

后端控制器（core/controllers）的回调是「函数指针」风格，且都在后台线程中被调用
（扫描线程 / OCR 线程 / 翻译线程 / 录音线程）。Qt 要求所有界面更新必须在主线程执行。

本模块提供两个关键能力：
1. ``SignalBridge``：把后端的函数指针回调包装成 Qt 信号，后端线程里直接调用
   包装函数即可，内部用 ``QMetaObject.invokeMethod``（QueuedConnection）把调用
   安全地转发到 Qt 主线程。
2. ``QtResultRouter``：对应旧 ui/mixins/result_display.py 的 ``_show_result`` 路由，
   负责把识别结果分发到结果框、判定是否交给翻译链路、以及剪贴板复制。

这样后端代码完全不用改，Qt 层拿到的是线程安全的、信号驱动的接口。
"""
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot


class SignalBridge(QObject):
    """把函数指针回调转成 Qt 信号，并做跨线程安全转发。

    用法：
        bridge = SignalBridge()
        bridge.status_updated.connect(ui_slot)          # ui_slot 在主线程执行
        controller.on_status_update = bridge.emit_status  # 后端线程直接调用

    后端线程调用 emit_status(status) 时，SignalBridge 内部通过 QueuedConnection
    把 status_updated 信号的发射排到主线程事件循环，从而线程安全。
    """

    # 状态更新（str）
    status_updated = Signal(str)
    # 结果回调，签名与后端 _show_result(mode, text, status, display) 一致
    result_received = Signal(str, str, bool, str)
    # 运行状态变化（bool）
    state_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = self  # 接收方对象（默认自身）

    # ---- 后端可赋值的函数指针（线程安全转发） ----

    def emit_status(self, status: str):
        """后端线程调用：on_status_update(status)"""
        self._safe_invoke("status_updated", str(status))

    def emit_result(self, *args, **kwargs):
        """兼容 OCR 与语音输入两种后端回调签名。

        OCR 控制器调用 ``on_result(mode, text, status, display)``；
        VoiceInputController 调用 ``on_result(text, display=...)``。

        注意：这里**必须同步返回** bool（表示「是否已交给翻译链路」），
        后端依赖返回值决定是否还要朗读原文。因此翻译判定不能在信号槽里做，
        必须在当前线程同步完成——见 QtResultRouter.route_result 的实现。
        """
        if len(args) == 1 and "display" in kwargs:
            mode = "voice_input"
            text = args[0]
            status = False
            display = kwargs.get("display")
        else:
            mode = args[0] if len(args) > 0 else kwargs.get("mode", "")
            text = args[1] if len(args) > 1 else kwargs.get("text", "")
            status = args[2] if len(args) > 2 else kwargs.get("status", False)
            display = args[3] if len(args) > 3 else kwargs.get("display")
        return self._route_sync_result(str(mode), str(text), bool(status), display)

    def emit_state_changed(self, is_running: bool):
        """后端线程调用：on_state_changed(is_running)"""
        self._safe_invoke("state_changed", bool(is_running))

    # ---- 内部 ----

    def _safe_invoke(self, signal_name: str, *args):
        """把信号发射安全转发到主线程（当前已在主线程时直接发，否则排队）。"""
        # 复用 QMetaObject.invokeMethod 的 QueuedConnection 语义：
        # 从任意线程调用，信号槽连接（queued）会在主线程执行。
        signal = getattr(self, signal_name)
        signal.emit(*args)

    def _route_sync_result(self, mode, text, status, display):
        """同步路由结果：翻译判定在调用方线程同步完成（保证返回值正确）。"""
        router = getattr(self, "_router", None)
        if router is not None:
            return router.route_result(mode, text, status, display)
        # 没有路由器时退化为纯显示
        self._safe_invoke("result_received", str(mode), str(text), bool(status), str(display or ""))
        return False


class QtResultRouter(QObject):
    """识别结果路由（对应旧 result_display.py 的 _show_result）。

    职责：
    - 把结果文本追加到结果框（在主线程，通过 result_received 信号）。
    - 判定是否交给翻译链路（在调用方线程同步执行，保证 bool 返回值正确）。
    - 功能1/功能2 的剪贴板复制。
    """

    # 结果落点表：mode -> (文本控件属性名, 是否覆盖)
    _RESULT_TARGETS = {
        "func1": ("result_text", False),
        "func2": ("result_text", False),
        "func3": ("result_text", False),
        "manga": ("result_text", False),
        "game": ("result_text", False),
        "voice_input": ("voice_result_text", False),
    }

    # 翻译结果显示目标表（与旧 _TRANSLATION_TARGETS 一致）
    _TRANSLATION_TARGETS = {
        "manga": ("result_text", "manga_enabled", False, False),
        "game": ("result_text", "game_enabled", False, False),
        "func1": ("result_text", "func1_enabled", False, None),
        "func2": ("result_text", "func2_enabled", False, None),
        "func3": ("result_text", "func3_enabled", False, False),
    }

    # 结果追加信号（主线程槽）
    result_to_display = Signal(str, str, bool)
    # 剪贴板复制请求（主线程槽）
    copy_requested = Signal(str)

    def __init__(self, config, translation_manager, tts_service, get_widget, parent=None):
        """
        Args:
            config: 配置管理器
            translation_manager: 翻译队列管理器
            tts_service: TTS 服务
            get_widget: 回调 (attr_name) -> QWidget，用于取结果文本框等控件
        """
        super().__init__(parent)
        self.config = config
        self.translation_manager = translation_manager
        self.tts = tts_service
        self._get_widget = get_widget

    # ---- 路由（调用方线程同步执行） ----

    def route_result(self, mode: str, text: str, status: bool = False,
                     display: Optional[str] = None) -> bool:
        shown = text if display is None else display

        # 状态消息：只显示，不翻译
        if status or not text or not text.strip():
            widget_attr, replace = self._RESULT_TARGETS.get(mode, ("result_text", False))
            self.result_to_display.emit(widget_attr, shown, replace)
            return False

        # 剪贴板复制（仅功能1/功能2）：复制必须在主线程做，用信号转发
        if self._should_copy(mode):
            self.copy_requested.emit(text)

        # 翻译判定：同步执行，保证返回值正确
        if mode != "voice_input" and self._translation_usable(mode):
            try:
                if self.translation_manager.auto_translate_if_enabled(text, mode):
                    return True
            except Exception as e:
                print(f"[Qt路由] 翻译判定失败({mode}): {e}")

        widget_attr, replace = self._RESULT_TARGETS.get(mode, ("result_text", False))
        self.result_to_display.emit(widget_attr, shown, replace)
        return False

    def _translation_usable(self, mode: str) -> bool:
        """自动翻译是否值得尝试（开关打开 **且** 翻译链路真的可用）。

        ``translation_available`` 是可选的注入钩子（``(mode) -> bool``）。
        没注入时一律返回 True，即完全保持原有行为（原生 Qt 版就是这种情况）。
        """
        hook = getattr(self, "translation_available", None)
        if hook is None:
            return True
        try:
            if hook(mode):
                return True
        except Exception as e:
            print(f"[Qt路由] 翻译可用性判断失败({mode}): {e}")
            return True
        print(f"[Qt路由] {mode} 已开启翻译，但没有可用的翻译模型/服务商，改为显示并朗读原文")
        return False

    @Slot(str)
    def copy_to_clipboard(self, text: str):
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(text)
            print(f"[剪贴板] 已复制: {text[:30]}")
        except Exception as e:
            print(f"[剪贴板] 复制失败: {e}")

    def _should_copy(self, mode: str) -> bool:
        # 语音输入有自己的开关（asr.copy_to_clipboard），与识别设置里的
        # func1/func2 剪贴板复制互不相干 —— 旧 UI 在 voice_input.py 里就是分开的
        if mode == "voice_input":
            return bool(self.config.get("asr", "copy_to_clipboard", default=True))
        if mode not in ("func1", "func2"):
            return False
        return bool(self.config.get("recognition", f"{mode}_copy", default=False))

    # ---- 翻译结果回填（主线程槽） ----

    def handle_translation_results(self, results):
        """处理翻译队列取出的结果（主线程调用）。

        Args:
            results: [(result, mode), ...] 来自 translation_manager.drain_results()
        """
        from controllers.translation_manager import MANUAL_MODE
        for result, mode in results:
            if mode == MANUAL_MODE:
                self._show_manual_result(result)
            else:
                self._display_translation_result(result, mode)

    def _display_translation_result(self, result: str, mode: str):
        target = self._TRANSLATION_TARGETS.get(mode)
        if target is None:
            self._append_result("result_text", f"翻译: {result}")
            return

        widget_attr, enabled_key, replace, fixed_interrupt = target
        self._append_result(widget_attr, f"翻译: {result}", replace=replace)

        if not self.config.get("translation", enabled_key, default=False):
            return
        if not self._should_read_aloud(mode):
            return

        if fixed_interrupt is None:
            queue_mode = self.config.get("tts", f"{mode}_queue_mode", default=True)
            interrupt_mode = not queue_mode
        else:
            interrupt_mode = fixed_interrupt

        print(f"[自动翻译] 开始朗读翻译结果（{mode}, 打断={interrupt_mode}）: {result[:50]}...")
        self.tts.speak(result, interrupt=interrupt_mode)

    def _show_manual_result(self, result: str):
        """手动翻译结果回填到翻译页输出框（不朗读、不进结果框）。"""
        self._append_result("translate_output_text", result, replace=True)

    def _should_read_aloud(self, mode: str) -> bool:
        if mode not in ("func1", "func2"):
            return True
        return bool(self.config.get("recognition", f"{mode}_read", default=True))

    def _append_result(self, widget_attr: str, text: str, replace: bool = False):
        """在主线程向结果框追加/替换文本（通过信号转发）。"""
        self.result_to_display.emit(widget_attr, text, replace)
