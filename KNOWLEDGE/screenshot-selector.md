# 截图与多屏框选（SEL）

> 域编号：`KB-SEL-*`。核心文件：`qt_ui/selector.py`、`qt_ui/selector_adapter.py`、
> `utils/screenshot.py`、`core/screenshot_service.py`。

## 1. 为什么不直接用系统截图

框选需要：暗化遮罩、框内聚光灯、实时尺寸提示、Esc/右键取消。
系统截图 API 都没有，必须自绘一份全屏遮罩。

## 2. 坐标语义：物理像素 + bbox（KB-SEL-001）

**唯一约定**：截图区域一律是 **Windows 物理像素** 的 `(x1, y1, x2, y2)`。

```python
# utils/screenshot.py
def grab_region(region):
    """region: (x1, y1, x2, y2)"""
    return ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")
```

- 与 `PIL.ImageGrab` 的 `bbox` 语义完全一致，**不是** `(x, y, w, h)`。
- 所有调用方（`func1/2/3`、漫画、游戏、翻译）都把该元组原样传给 `grab_region`。
- 负坐标合法：副屏在主屏左侧时 `x1` 可以是负数。
- 修改任何选区相关代码前，先确认自己是「物理」还是「逻辑」，二者不可混用。

## 3. 后端线程 → 主线程的调度（KB-SEL-002）

框选会被**后台扫描线程**调用（功能3/漫画/游戏的 `_scan_loop` 里 `select_region()`），
而 Qt 控件只能在主线程创建。中间层是 `QtSelectorAdapter`：

```
后台线程                        主线程
select()
  ├─ QThread.currentThread() == app.thread() ?
  │     是 → 直接调 selector.select()          （同步）
  └─ 否 → 设置 _result=None
          emit _do_select  ───────────────▶ _run_select()
                                              selector.select()  ← 阻塞式局部事件循环
          event.wait() 阻塞                   用户框完返回 bbox
          等待结果        ◀─────────────── emit _finished(result)
          返回 result
```

实现要点：

- 适配器对上层暴露与旧 tkinter 版一致的 `select() -> Optional[bbox]`，
  所以 `ScreenshotService` 与全部控制器都不需要改动。
- 「已在主线程」必须走同步分支：跨线程信号从主线程发出时是**直接调用**，
  会当场阻塞调用线程，形成自锁。
- 超时/取消/异常统一返回 `None`，上层按「用户取消」处理。

## 4. 多屏遮罩：每屏一窗（KB-SEL-003）

### 4.1 为什么不能用一个大窗口铺满虚拟桌面

Qt6 下 `showFullScreen()` 会设置 `_NET_WM_STATE_FULLSCREEN`，把窗口限制在单块屏上；
即使绕过窗口管理器，窗口 geometry 也会被按「窗口主要所在屏的 DPR」重新解释。
实测：请求 `setGeometry(-1920, 0, 3456, 960)`，实际落成 `(-2160, 0, 2160, 600)`
——遮罩根本没有铺满屏幕，用户看到的就是「画面局部放大」。

### 4.2 现行方案

```
for screen in QGuiApplication.screens():
    shade = _ScreenShade(screen, selector)   # 每块屏一个独立顶层窗口
    shade.show()                             # 先让窗口落到该屏
for shade in shades:
    shade.resolve_physical_origin()          # 再查真实物理原点
    shade.capture(dim_factor)                # 最后逐屏抓图
```

- 每个遮罩窗口：`FramelessWindowHint | WindowStaysOnTopHint | Tool`，
  `geometry = screen.geometry()`（单屏范围内 Qt 几何是可靠的）。
- 底图：`screen.grabWindow(0)` **不带坐标**抓整屏，pixmap 的 DPR 等于该屏 DPR，
  绘制 1:1 无缩放。带坐标参数时高 DPI 下返回尺寸会与请求不一致，所以不用。
- 这是 Qt 官方推荐用法，也是业界截图工具（flameshot，PR #4535「Qt6 多屏 + 混合 DPI」）
  的做法：**per-monitor grab + native 像素坐标合成**。

### 4.3 与业界做法对照

| 要点 | flameshot | 本项目 |
| --- | --- | --- |
| 每屏一个遮罩 | ✅ | ✅ `_ScreenShade` |
| 逐屏抓图 | ✅ | ✅ `grabWindow(0)` |
| 物理坐标 | native 像素 | ✅ Win32 `GetCursorPos` |
| 单一全屏窗口 | ❌ 明确不可行 | ❌ 已废弃 |

## 5. 物理原点的获取（KB-SEL-004）

**核心事实**：Qt6 的 `QScreen::geometry()` 是**混合坐标**——
原点是 native（物理）像素，尺寸是逻辑像素（physical / dpr）。

```
主屏   geometry (0,0,1536,960)      dpr 1.25  物理 0..1920
副屏   geometry (-1920,0,960,600)   dpr 2.00  物理 -1920..0
```

因此 **「逻辑原点 × DPR = 物理原点」是错的**：副屏会算成 -3840，实际是 -1920。
正确做法是拿窗口句柄问 Win32：

```python
hmon = user32.MonitorFromWindow(int(self.winId()), MONITOR_DEFAULTTONEAREST)
user32.GetMonitorInfoW(hmon, byref(info))
self._phys_origin = (info.rcMonitor.left, info.rcMonitor.top)
```

查询时机：窗口 `show()` **之后**（此时才真正落到目标屏），抓图之前。
拿不到时退回「逻辑原点」兜底，但本地实测永远能拿到。

局部↔物理换算（仅用于绘制与兜底）：

```python
phys_x = phys_origin[0] + round(local_x * dpr)
local_x = round((phys_x - phys_origin[0]) / dpr)
```

## 6. 事件循环收尾顺序（KB-SEL-005）

```python
loop = QEventLoop(self); self._selection_loop = loop
loop.exec()                 # 阻塞到用户松开鼠标 / 右键 / Esc
self._selection_loop = None
self._close_shades()        # 循环退出后才销毁窗口
return self._result

def _stop_loop(self):
    if loop.isRunning(): loop.quit()   # ① 先退出循环
    self._hide_shades()                # ② 再隐藏窗口
```

**先关窗会出事**：鼠标松开事件由最后一个遮罩窗口派发，在其处理途中 `close()`
+ `deleteLater()` 会拆掉局部事件循环，`select()` 提前返回 `None`，
表现为「框完没反应」。

## 7. 踩坑记录与验证方法（KB-SEL-006）

### 7.1 已经踩过的坑

| 现象 | 根因 | 修法 |
| --- | --- | --- |
| 多屏画面局部放大 | 单窗口铺虚拟桌面，geometry 被重解释 | 每屏一窗 |
| 副屏坐标整体偏移 | 逻辑原点 × DPR 推算物理原点 | Win32 查真值 |
| 框选后毫无反应 | 缺少 `QApplication` 导入，`select()` 抛 `NameError` 被适配器吞成 `None` | 补导入 + 入口守门测试 |
| 框选后返回 None | 先关窗再退事件循环 | 调换顺序 |
| 测试全绿但实机失败 | 测试直接调 `on_press/on_release`，绕过了 `select()` 入口 | 测试必须调真实入口 |

### 7.2 验证方法

**结构测试**（`tests/test_selector.py`，6 例，全部真实调用 `select()`）

- 入口能返回 bbox；反向拖动归一化；
- 副屏（负坐标 + 不同 DPR）；取消返回 None；3px 误触取消；结束后无残留窗口。

**坐标注入式实机验证**

- 用户同时使用电脑时 `SetCursorPos` 不可靠 → monkeypatch
  `qt_ui.selector._physical_cursor_pos` 注入物理坐标。
- 对比「遮罩抓到的画面」与 `grab_region(bbox)`：跨屏时**按每块屏取交集分别比对**，
  再按面积加权，否则跨屏用例会因基准图只覆盖一半而误判。
- 显示器可能开了 HDR：Qt `grabWindow` 与 PIL `ImageGrab` 存在稳定色彩差异（MAD≈20），
  所以比较用**去均值相关系数**，阈值 0.85，不要用逐像素绝对差。

**反向验证**：把 `QApplication` 导入回退，6 个用例应有 4 个 FAIL——证明测试有效。

### 7.3 相关配置

```yaml
selector:
  dim_factor: 0.3            # 遮罩暗化程度
  spotlight_brightness: 1.0  # 框内亮度还原
  selection_color: '#FFD400'
  selection_width: 2
  show_info: false           # 是否绘制尺寸提示
```
