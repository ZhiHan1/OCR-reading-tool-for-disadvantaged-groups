# Qt UI 迁移

新的 Qt 入口是 `qt_main.py`，旧的 `main_modular_win11.py` 保留不变，便于回退和对照。

## 安装

在项目实际运行的 Python 环境中安装依赖：

```powershell
pip install PySide6
```

完整依赖仍可使用：

```powershell
pip install -r requirements.txt
```

## 启动

```powershell
python -m qt_main
```

或运行 `run_qt.ps1`。

## 已完成的迁移范围

- Qt 主窗口、标题栏、侧边栏和 `QStackedWidget` 页面栈（六页全部可切换）
- 首页五种 OCR 功能卡片：按功能开关与区域状态联动启用/禁用按钮并显示状态文案
- Qt 信号桥接后端线程回调（状态/结果/运行状态），跨线程安全转发到主线程
- 共用识别结果区、状态栏、停止按钮
- Qt 原生屏幕选区（`QScreen` + `QRubberBand`，多屏虚拟坐标）
- 语音输入页：FunASR 模型切换、按住说话、热键显示与剪贴板开关
- 翻译页：手动翻译、语言选择、翻译结果队列轮询与自动翻译结果路由
- TTS 页：语音后端、音色/语速/音量、试听
- 设置页六个页签：功能设置、语音设置、按键设置、翻译设置、识别设置、主题
  （含热键捕获与冲突检测、恢复默认、主题保存后重建界面）

## 回归测试

```powershell
.\run_regression.ps1
```

依次执行：`py_compile` 编译检查 → 离屏冒烟（六页切换/结果路由/主题重载/退出清理）
→ 设置页交互（六页签/保存/热键捕获与冲突/主题重建）→ 框选层（拖拽/ESC/右键/后端 bbox 契约）。
全部通过时输出 `REGRESSION_OK`。

脚本说明：`_qt_smoke.py`（离屏）、`_qt_settings_test.py`（离屏）、`_qt_selector_test.py`（真实桌面，
需窗口系统）、`_qt_live.py`（真实桌面逐页展示后自动退出）。

## 迁移注意事项

- **选区坐标是 bbox**：`qt_ui/selector.py` 返回 `(x1, y1, x2, y2)`，不是 `(x, y, w, h)`。
  后端 `utils/screenshot.py: grab_region()` 直接把它当 `ImageGrab` 的 bbox 使用，
  传错格式会静默截错区域（把宽度当成 x2）。`capture_region()` 的 docstring 写的是
  `(x, y, width, height)`，那是过时注释，以 `grab_region` 的实现为准。
- **后端回调要同步返回 bool**：`on_result` 的返回值表示「是否已交给翻译链路」，
  必须同步返回，不能放进 Qt 信号槽（跨线程异步会丢失返回值）。
- **`ScreenshotService` 硬编码了 tkinter 选区器**：Qt 层通过 `QtSelectorAdapter`
  替换 `screenshot_service.selector` 实例来接管，后端零改动。
- **改主题必须重建界面**：颜色在控件构建时写进样式表，改色后走 `_reload_ui_theme()`
  重建 central widget，并清空 `_cached_pages` / `_widgets` / `_home_state`。
- **`ui.*_enabled` 与 `translation.*_enabled` 不是一回事**：前者是功能是否启用（首页卡片
  按钮可用性），后者是识别后是否自动翻译。
