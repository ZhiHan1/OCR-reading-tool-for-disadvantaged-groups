"""把 Qt 原生选区接到后端 ScreenshotService 上。

后端 ScreenshotService 硬编码了 tkinter 的 RegionSelector，且 select_region()
会在**后台线程**被调用（功能2/功能3/漫画/游戏的扫描线程）。Qt 控件必须在主线程
创建和跑事件循环。

本适配器解决这两点，让后端完全不用改：
1. 实现与 RegionSelector 相同的 ``select() -> Optional[(x,y,w,h)]`` 接口；
2. 若 select() 在非主线程被调用，用信号 + 等待事件把「框选」调度到主线程，
   阻塞当前线程直到用户框选完成，再把结果带回去。

用法（Qt 层初始化时）：
    from qt_ui.selector_adapter import QtSelectorAdapter
    app.screenshot_service.selector = QtSelectorAdapter(qt_main_window)
"""
import threading
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal, Slot


class _SelectorBridge(QObject):
    """在主线程执行框选，并把结果传回等待线程。"""

    # 请求框选信号（主线程槽触发）
    _do_select = Signal()
    # 框选完成信号（携带结果；None 表示取消）
    _finished = Signal(object)

    def __init__(self, selector_factory, parent=None):
        super().__init__(parent)
        self._selector_factory = selector_factory
        self._result = None
        self._done = threading.Event()
        self._do_select.connect(self._run_select)

    @Slot()
    def _run_select(self):
        try:
            selector = self._selector_factory()
            self._result = selector.select()
        except Exception as e:
            print(f"[选区] 框选失败: {e}")
            self._result = None
        finally:
            self._done.set()


class QtSelectorAdapter:
    """后端 ScreenshotService.selector 的替身，线程安全。"""

    def __init__(self, selector_factory):
        """
        Args:
            selector_factory: 无参可调用，返回一个带 select() 的选区窗口实例
                             （例如 lambda: QtRegionSelector()）。必须在主线程调用。
        """
        self._selector_factory = selector_factory
        self._bridge = _SelectorBridge(selector_factory)

    def select(self) -> Optional[Tuple[int, int, int, int]]:
        """框选屏幕区域，返回 (x, y, w, h) 或 None。

        主线程调用则直接执行；后台线程调用则调度到主线程并阻塞等待。
        """
        # 主线程：直接跑
        if threading.current_thread() is threading.main_thread():
            try:
                selector = self._selector_factory()
                return selector.select()
            except Exception as e:
                print(f"[选区] 框选失败: {e}")
                return None

        # 后台线程：调度到主线程，阻塞等待结果
        self._bridge._result = None
        self._bridge._done.clear()
        self._bridge._do_select.emit()
        self._bridge._done.wait()  # 阻塞直到用户框选完成
        return self._bridge._result
