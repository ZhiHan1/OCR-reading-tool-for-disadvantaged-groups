"""选区入口回归测试（多屏 / 混合 DPI）。

背景：qt_ui/selector.py 曾因缺少 ``QApplication`` 导入，``select()`` 一进到
激活窗口那行就抛 NameError，被 QtSelectorAdapter 吞掉后返回 None——界面上
表现为「框选完没有任何反应」。之前的测试都手工构造遮罩窗口，绕过了
``select()`` 这个真实入口，所以没发现。本测试专门守入口。

运行：py -3.14 -m unittest tests.test_selector -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QPointF, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication


class SelectorEntryTest(unittest.TestCase):
    """必须真的调用 ``select()``，而不是直接驱动内部的 on_press/on_release。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        import qt_ui.selector as selmod

        cls.selmod = selmod
        cls._orig_cursor = selmod._physical_cursor_pos

    @classmethod
    def tearDownClass(cls):
        cls.selmod._physical_cursor_pos = cls._orig_cursor

    def _run_select(self, phys_a, phys_b):
        """跑一次真实 select()，期间用定时器模拟拖动。"""
        selmod = self.selmod
        sel = selmod.QtRegionSelector()
        state = {"pos": phys_a}
        selmod._physical_cursor_pos = lambda: state["pos"]
        got = {}

        def drag():
            shades = [w for w in QApplication.topLevelWidgets()
                      if type(w).__name__ == "_ScreenShade" and w.isVisible()]
            # 框选时必须真的把遮罩显示出来，否则用户看不到选区
            self.assertTrue(shades, "select() 没有显示任何遮罩窗口")
            state["pos"] = phys_a
            sel.on_press(QMouseEvent(QEvent.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            state["pos"] = phys_b
            sel.on_move(QMouseEvent(QEvent.MouseMove, QPointF(1, 1), QPointF(1, 1),
                                    Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            sel.on_release(QMouseEvent(QEvent.MouseButtonRelease, QPointF(1, 1), QPointF(1, 1),
                                       Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

        QTimer.singleShot(120, drag)
        # 兜底：万一卡住，3 秒后强退局部循环
        QTimer.singleShot(3000, lambda: sel.cancel())
        got["value"] = sel.select()   # ← 真实入口，任何异常都会让测试失败
        return got["value"]

    # ---- 用例 ----

    def test_select_entry_returns_bbox(self):
        """select() 本身必须能跑通并返回 bbox（修复 NameError 的守门用例）。"""
        result = self._run_select((100, 100), (400, 350))
        self.assertEqual(result, (100, 100, 400, 350))

    def test_select_normalizes_reverse_drag(self):
        """从右下往左上拖，也要返回左上-右下顺序的 bbox。"""
        result = self._run_select((400, 350), (100, 100))
        self.assertEqual(result, (100, 100, 400, 350))

    def test_select_on_secondary_screen(self):
        """在副屏（负坐标、不同 DPR）上框选，坐标必须原样返回。"""
        from PySide6.QtGui import QGuiApplication

        primary = QGuiApplication.primaryScreen()
        others = [s for s in QGuiApplication.screens() if s is not primary]
        if not others:
            self.skipTest("只有一块屏，跳过副屏用例")
        # 取该屏物理区域中间的一小块
        screen = others[0]
        dpr = screen.devicePixelRatio() or 1.0
        origin_x = int(screen.geometry().x())
        origin_y = int(screen.geometry().y())
        w = int(screen.geometry().width() * dpr)
        h = int(screen.geometry().height() * dpr)
        a = (origin_x + w // 4, origin_y + h // 4)
        b = (origin_x + w // 2, origin_y + h // 2)
        result = self._run_select(a, b)
        self.assertEqual(result, (a[0], a[1], b[0], b[1]))

    def test_select_cancel_returns_none(self):
        """右键/过小拖动 = 取消，返回 None（而不是抛出）。"""
        sel = self.selmod.QtRegionSelector()
        self.selmod._physical_cursor_pos = lambda: (100, 100)
        QTimer.singleShot(120, sel.cancel)
        QTimer.singleShot(3000, lambda: sel.cancel())
        self.assertIsNone(sel.select())

    def test_tiny_drag_is_cancelled(self):
        """3px 的误触应视为取消。"""
        result = self._run_select((100, 100), (103, 103))
        self.assertIsNone(result)

    def test_shades_cleaned_up(self):
        """框选结束后不能残留遮罩窗口。"""
        self._run_select((100, 100), (400, 350))
        # 让 deleteLater 有机会执行
        for _ in range(5):
            self.app.processEvents()
        left = [w for w in QApplication.topLevelWidgets()
                if type(w).__name__ == "_ScreenShade" and w.isVisible()]
        self.assertEqual(left, [], "框选结束后仍有可见遮罩窗口")


if __name__ == "__main__":
    unittest.main(verbosity=2)
