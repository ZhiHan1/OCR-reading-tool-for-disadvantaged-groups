"""Qt 原生屏幕选区工具（替换 tkinter 版 ui/selector.py）。

视觉与交互对齐原 tkinter 版：整屏截图按 ``selector.dim_factor`` 亮度缩放做暗化，
框内贴回原亮度图（聚光灯效果），边框用 ``selection_color``/``selection_width`` 自绘，
每次鼠标移动整幅重绘（不用 QRubberBand，避免 XOR 残影）。

多显示器 / 混合 DPI（关键设计）
------------------------------
混合 DPI 下**不能**用一个窗口铺满整个虚拟桌面：Qt 会按窗口主要所在屏的 DPR
重新解释 geometry，窗口根本不会落在预期位置（实测 setGeometry(-1920,0,3456,960)
变成 (-2160,0,2160,600)），表现为「画面局部放大、选区错位」。

正确做法是**每块屏一个无边框窗口**：
- 窗口 geometry = 该屏 ``QScreen.geometry()``（单屏范围内 Qt 几何完全可靠）；
- 底图 = 该屏 ``QScreen.grabWindow(0)``（物理像素，dpr=该屏 DPR），
  绘制时 Qt 自动按 DPR 缩放到窗口逻辑尺寸，1:1 无变形；
- 鼠标坐标是本屏局部逻辑坐标 → 乘 DPR 加上本屏物理原点 = 物理坐标；
- 返回值用 Win32 ``GetCursorPos`` 的物理坐标（与 PIL ``ImageGrab`` 同语义，
  天然支持负坐标副屏），只有取不到时才用上述换算兜底。

返回语义与后端 ``utils.screenshot.grab_region`` 一致：物理 bbox ``(x1, y1, x2, y2)``。
"""
import ctypes
import ctypes.wintypes
from typing import List, Optional, Tuple

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from core.config_manager import get_config


def _physical_cursor_pos() -> Optional[Tuple[int, int]]:
    """当前光标的 Windows 物理屏幕坐标（ImageGrab 同语义）。"""
    try:
        point = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
    except Exception:
        pass
    return None


class _ScreenShade(QWidget):
    """单块屏幕的遮罩窗口：暗化底图 + 框内聚光灯 + 边框。"""

    def __init__(self, screen, selector: "QtRegionSelector"):
        super().__init__(None)
        self._screen = screen
        self._sel = selector
        self._dimmed: Optional[QPixmap] = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        geo = screen.geometry()
        self._virtual_origin = geo.topLeft()
        self._dpr = screen.devicePixelRatio() or 1.0
        # 本屏物理原点。注意：**不能用「逻辑原点 × DPR」推算**——混合 DPI 的
        # 副屏上两者不成比例（实测副屏逻辑 -1920 @200%，物理原点仍是 -1920）。
        # 物理原点必须由 Win32 GetMonitorInfo 提供（resolve_physical_origin，
        # 在窗口落到该屏之后调用）；没拿到之前先用逻辑原点兜底。
        self._phys_origin = (geo.x(), geo.y())
        self.setGeometry(geo)

    # ---- 物理原点 ----

    def resolve_physical_origin(self):
        """窗口已落在本屏后，用 Win32 API 查询所在显示器的真实物理矩形。"""
        try:
            hmon = ctypes.windll.user32.MonitorFromWindow(
                int(self.winId()), 2  # MONITOR_DEFAULTTONEAREST
            )
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_long),
                            ("rcMonitor", RECT), ("rcWork", RECT),
                            ("dwFlags", ctypes.c_long)]

            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if hmon and ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                self._phys_origin = (int(info.rcMonitor.left), int(info.rcMonitor.top))
        except Exception:
            pass  # 保留逻辑原点兜底

    # ---- 抓屏 ----

    def capture(self, dim_factor: float):
        """抓本屏物理像素，生成暗化底图。pixmap dpr = 屏 DPR，绘制 1:1 无变形。

        按 Qt 官方建议用 ``grabWindow(0)`` 不带坐标抓整屏（带坐标时返回的
        pixmap 尺寸在高 DPI 下会与请求尺寸不一致），这正是 flameshot 在 Qt6
        多屏修复（PR #4535）里采用的 per-monitor 抓图方式。
        """
        shot = self._screen.grabWindow(0)
        factor = max(0.0, min(1.0, float(dim_factor)))
        dimmed = QPixmap(shot.size())
        dimmed.setDevicePixelRatio(shot.devicePixelRatio())
        dimmed.fill(Qt.black)
        painter = QPainter(dimmed)
        painter.setOpacity(factor)
        painter.drawPixmap(0, 0, shot)
        painter.end()
        self._dimmed = dimmed
        self._shot = shot

    # ---- 坐标换算 ----

    def _local_to_physical(self, local: QPoint) -> Tuple[int, int]:
        return (
            self._phys_origin[0] + int(round(local.x() * self._dpr)),
            self._phys_origin[1] + int(round(local.y() * self._dpr)),
        )

    def _physical_to_local(self, phys: Tuple[int, int]) -> QPoint:
        return QPoint(
            int(round((phys[0] - self._phys_origin[0]) / self._dpr)),
            int(round((phys[1] - self._phys_origin[1]) / self._dpr)),
        )

    def _local_rect(self) -> QRect:
        """本屏局部逻辑坐标系的选区矩形（框不落在本屏时返回空矩形）。"""
        phys = self._sel.phys_rect()
        if phys is None:
            return QRect()
        a = self._physical_to_local((phys[0], phys[1]))
        b = self._physical_to_local((phys[2], phys[3]))
        return QRect(a, b).normalized()

    # ---- 事件（全部转发给 selector 统一处理） ----

    def mousePressEvent(self, event):
        self._sel.on_press(event)

    def mouseMoveEvent(self, event):
        self._sel.on_move(event)

    def mouseReleaseEvent(self, event):
        self._sel.on_release(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._sel.cancel()

    # ---- 绘制 ----

    def paintEvent(self, event):
        if self._dimmed is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        # dpr 与窗口一致，直接 1:1 画满
        painter.drawPixmap(0, 0, self._dimmed)

        rect = self._local_rect()
        if rect.isValid() and rect.width() > 0 and rect.height() > 0:
            self._draw_spotlight(painter, rect)
            self._draw_border(painter, rect)
            if self._sel._cfg.get("show_info"):
                self._draw_info(painter, rect)
        painter.end()

    def _draw_spotlight(self, painter: QPainter, rect: QRect):
        """框内还原亮度。源矩形用物理像素（QPixmap 源坐标是设备像素）。"""
        shot = self._shot
        dpr = self._dpr
        src = QRect(
            int(round(rect.x() * dpr)),
            int(round(rect.y() * dpr)),
            int(round(rect.width() * dpr)),
            int(round(rect.height() * dpr)),
        ).intersected(QRect(0, 0, shot.width(), shot.height()))
        if src.isEmpty():
            return
        dst = QRect(
            int(round(src.x() / dpr)),
            int(round(src.y() / dpr)),
            int(round(src.width() / dpr)),
            int(round(src.height() / dpr)),
        )

        brightness = float(self._sel._cfg.get("spotlight_brightness", 1.0))
        if brightness <= 0:
            painter.fillRect(dst, Qt.black)
            return
        if abs(brightness - 1.0) < 0.01:
            painter.drawPixmap(dst, shot, src)
            return
        if brightness < 1.0:
            # 黑底 + 半透明原图 = 原图 × brightness
            painter.fillRect(dst, Qt.black)
            painter.setOpacity(brightness)
            painter.drawPixmap(dst, shot, src)
            painter.setOpacity(1.0)
            return
        # > 1.0：先画原图，再以 Plus 模式叠加补足亮度
        painter.drawPixmap(dst, shot, src)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.setOpacity(min(1.0, brightness - 1.0))
        painter.drawPixmap(dst, shot, src)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setOpacity(1.0)

    def _draw_border(self, painter: QPainter, rect: QRect):
        cfg = self._sel._cfg
        pen = QPen(QColor(cfg.get("selection_color", "#FFD400")))
        pen.setWidth(int(cfg.get("selection_width", 2)))
        pen.setJoinStyle(Qt.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _draw_info(self, painter: QPainter, rect: QRect):
        cfg = self._sel._cfg
        painter.setPen(QColor(cfg.get("selection_color", "#FFD400")))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        phys = self._sel.phys_rect() or (0, 0, 0, 0)
        text = f"X:{phys[0]}  Y:{phys[1]}  W:{phys[2] - phys[0]}  H:{phys[3] - phys[1]}"
        ty = rect.y() - 8 if rect.y() > 24 else rect.y() + rect.height() + 18
        painter.drawText(rect.x(), ty, text)


class QtRegionSelector(QWidget):
    """多屏选区协调器。

    用法：
        selector = QtRegionSelector()
        region = selector.select()   # 阻塞直到用户框选完成或取消
    """

    # 选区完成信号（内部用事件循环等待，也可连接此信号异步处理）
    region_selected = Signal(tuple)

    def __init__(self):
        super().__init__(None)
        self._shades: List[_ScreenShade] = []
        self._press_physical: Optional[Tuple[int, int]] = None
        self._current_physical: Optional[Tuple[int, int]] = None
        self._result: Optional[Tuple[int, int, int, int]] = None
        self._selection_loop: Optional[QEventLoop] = None
        self._cfg = {}

    # ---- 配置 ----

    def _load_config(self):
        c = get_config()
        self._cfg = {
            "dim_factor": float(c.get("selector", "dim_factor", default=0.3)),
            "spotlight_brightness": float(
                c.get("selector", "spotlight_brightness", default=1.0)
            ),
            "selection_color": c.get("selector", "selection_color", default="#FFD400"),
            "selection_width": int(c.get("selector", "selection_width", default=2)),
            "show_info": bool(c.get("selector", "show_info", default=False)),
        }

    # ---- 主入口 ----

    def select(self) -> Optional[Tuple[int, int, int, int]]:
        """阻塞式框选，返回物理 bbox (x1, y1, x2, y2) 或 None（取消）。"""
        self._result = None
        self._press_physical = None
        self._current_physical = None
        self._load_config()

        # 每块屏一个遮罩窗口；先 show 让窗口落到各自屏上，
        # 才能用 MonitorFromWindow 查到真实物理原点，最后再抓屏。
        self._shades = []
        for screen in QGuiApplication.screens():
            shade = _ScreenShade(screen, self)
            self._shades.append(shade)
        for shade in self._shades:
            shade.show()
            shade.raise_()
        for shade in self._shades:
            shade.resolve_physical_origin()
            shade.capture(self._cfg["dim_factor"])
        active = QApplication.activeWindow() or (
            self._shades[0] if self._shades else None)
        if active is not None:
            active.activateWindow()
            active.setFocus(Qt.OtherFocusReason)

        # 局部事件循环：保持 Qt 事件分发，不忙轮询、不占用 CPU
        loop = QEventLoop(self)
        self._selection_loop = loop
        loop.exec()
        self._selection_loop = None
        self._close_shades()

        return self._result

    def _hide_shades(self):
        """隐藏遮罩（不销毁），让循环退出后画面立刻恢复。"""
        for shade in self._shades:
            shade.hide()

    def _close_shades(self):
        for shade in self._shades:
            shade.close()
            shade.deleteLater()
        self._shades = []

    # ---- 选区状态（物理坐标） ----

    def phys_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """当前选区的物理 bbox，未按下时为 None。"""
        if self._press_physical is None or self._current_physical is None:
            return None
        x1, x2 = sorted((self._press_physical[0], self._current_physical[0]))
        y1, y2 = sorted((self._press_physical[1], self._current_physical[1]))
        return (x1, y1, x2, y2)

    # ---- 鼠标事件（由各屏遮罩转发） ----

    def on_press(self, event):
        if event.button() == Qt.RightButton:
            self.cancel()
            return
        if event.button() == Qt.LeftButton:
            # 光标物理坐标是唯一可信来源（跨屏比例不一致时逻辑坐标不可换算）
            self._press_physical = _physical_cursor_pos()
            self._current_physical = self._press_physical
            self._repaint_all()

    def on_move(self, event):
        if self._press_physical is None:
            return
        self._current_physical = _physical_cursor_pos()
        self._repaint_all()

    def on_release(self, event):
        if event.button() != Qt.LeftButton or self._press_physical is None:
            return
        release_physical = _physical_cursor_pos()
        if release_physical is None:
            # 兜底：用事件所在屏换算
            shade = self.sender() if hasattr(self, "sender") else None
            if isinstance(shade, _ScreenShade):
                release_physical = shade._local_to_physical(event.position().toPoint())
        if release_physical is not None:
            self._current_physical = release_physical
            rect = self.phys_rect()
            if rect is not None:
                self._finish(*rect)
                return
        self.cancel()

    def _repaint_all(self):
        for shade in self._shades:
            shade.update()

    # ---- 完成/取消 ----

    def _finish(self, x1: int, y1: int, x2: int, y2: int):
        if x2 - x1 < 5 or y2 - y1 < 5:
            # 过小视为误触，取消（与原版阈值一致）
            self.cancel()
            return
        # utils.screenshot.grab_region 需要 bbox (x1, y1, x2, y2) 物理像素，
        # 用原始按下/松开点计算，避免 QRect 含端点带来的 1px 偏差。
        self._result = (int(x1), int(y1), int(x2), int(y2))
        self._press_physical = None
        self._current_physical = None
        self.region_selected.emit(self._result)
        self._stop_loop()

    def cancel(self):
        self._result = None
        self._press_physical = None
        self._current_physical = None
        self._stop_loop()

    def _stop_loop(self):
        # 顺序很重要：先退出局部事件循环，再隐藏窗口。
        # 反过来的话，关闭窗口 → destroyed → loop.quit() 会在鼠标松开事件
        # 的处理途中把事件循环拆掉，select() 提前返回 None（曾因此漏掉框选）。
        loop = self._selection_loop
        self._selection_loop = None
        if loop is not None and loop.isRunning():
            loop.quit()
        self._hide_shades()
