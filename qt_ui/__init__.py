"""Qt (PySide6) UI 层。

只保留 Web 版**真正在用的三样**：
- ``bridge.py``       控制器回调 → Qt 信号（跨线程安全转发）
- ``selector.py``     屏幕选区（QScreen.grabWindow + QRubberBand）
- ``selector_adapter.py``  后端线程 → 主线程的选区调度

旧的 Qt 原生界面（``settings_panel.py`` / ``widgets.py`` / ``theme.py``，
共 1409 行）已于 2026-11-05 删除——Web 设置页早已取代它们，且
``qt_web/main.py`` 对 ``settings_panel`` 的导入只是用一个从未被引用的常量。
旧 tkinter 层（``main_modular_win11.py`` + ``ui/``）同理，随 ``legacy/`` 一并删除。

后端 core/controllers/services 完全复用，不做任何改动。
"""