# 架构与调用链（ARC）

> 域编号：`KB-ARC-*`。所有路径相对项目根目录。

## 1. 一句话

桌面应用 = **本地 Web 页面（前端）** + **PySide6 壳层（Qt）** + **Python 后端服务（core）**。
前端只负责展示与交互，所有能力通过与 Python 之间唯一的 QWebChannel 通道取得。

```
QWebEngineView 渲染 qt_web/web/index.html
        │  QWebChannel 注入对象 "api" 与 "nav"
        ▼
qt_web/api.py  WebApi.request(action, payloadJson, cb)
        │  动作名 → "_dispatch" → do_<action>(payload)
        ▼
controllers/app_controller.py  AppController 的 13 个 @property（懒构造）
        │
        ▼
controllers/* 控制器（func1/2/3 · manga · game · translation · voice）
        │
        ▼
core/* 服务（ocr · asr · tts · translation · model_manager · config …）
        │
        ▼
系统与模型：onnxruntime / RapidOCR、funasr、SAPI+edge-tts、llama.cpp、Win32 API
```

## 2. 分层总览（KB-ARC-001）

| 层 | 位置 | 职责 | 允许依赖 |
| --- | --- | --- | --- |
| 表现层 | `qt_web/web/` | 页面渲染、动画、交互 | 只能经 `bridge` 调后端 |
| 壳层 | `qt_web/main.py`、`qt_web/api.py`、`qt_web/voice_*` | 窗口、通道、API 面、语音子进程 | 可依赖下层 |
| 编排层 | `controllers/` | 把一个业务流程串起来（框选→识别→显示→朗读→翻译） | 依赖 core |
| 内核层 | `core/` | 单一能力，尽量不依赖 UI | 依赖 utils + 第三方 |
| 原生控件 | `qt_ui/` | 选区器、结果路由（**只剩 3 个模块**） | 不依赖 qt_web |
| 工具层 | `utils/` | 路径、截图、热键、线程限制、托盘 | 不依赖上层 |

**约束**：`core/` 不得 import `qt_ui/` 或 `qt_web/`；`qt_ui/` 不得 import `qt_web/`。
历史遗留的 tkinter 界面（原 `legacy/`，8145 行）**已于 2026-11-05 整体删除**；
`ScreenshotService` 的选区实现现在只能由调用方注入（Web/Qt 入口都注入
`QtSelectorAdapter`），未注入时提示并返回 `None`，不再有任何 tkinter 回退。

## 3. 目录职责对照表（KB-ARC-002）

| 目录 | 文件数 | 关键文件 |
| --- | --- | --- |
| `core/` | 26 | `ocr_engine` `asr_service` `tts`（真实实现）`tts_service`（兼容层）`translation_service` `model_manager` `config_manager` `deduplication_service` `history_manager` `hotwords` `screenshot_service` `device_manager` `audio_capture` |
| `controllers/` | 11 | `app_controller`（13 个懒构造属性）`base_controller`（所有控制器的根骨架）`func1_controller` `func2_controller` `func3_controller` `manga_controller` `game_mode_controller` `scan_mode_controller`（继承 `base_controller`）`translation_manager`（翻译队列）`translation_controller`（只切后端）`voice_input_controller` |
| `qt_web/` | 15 py + web | `main.py` `api.py` `bootstrap` `hotkeys` `power_monitor`（电源事件）`voice_session` `voice_worker` `voice_pipeline` `voice_modes` `cursor_input` `tts_export` `asr_models` `release_check_common` + 两个 `release_*_check` |
| `qt_ui/` | 3（+`__init__`） | `selector` `selector_adapter` `bridge`（旧的 `settings_panel` / `theme` / `widgets` 共 1409 行已于 2026-11-05 删除） |
| `utils/` | 5 | `paths` `screenshot` `env_limits` `system_tray` `model_discovery`（`hotkey` 已于 2026-11-15 删除，改由 `qt_web/hotkeys.py` 负责） |
| `tests/` | 11 | `test_selector` `test_backend_refactor` `test_external_models` `test_package_size` `test_sleep_resume` `test_no_duplicate_impl` `test_asr_quality` `test_hotwords` `test_hotword_learning` `test_audio_devices` `test_clean_text` |
| `tools/` | 23 | `smoke_gui` `mem_report` `verify_web_release` `model_audit` `model_dirs` `stage_*` `sync_external_models` `stage_external_models` `download_asr_models` `quantize_gguf` `trim_punc_vocab` |

### 3.1 控制器继承关系（2026-11-15 起）

```
BaseController                    ← 回调骨架：_update_status / _show_result / _report_error
├── Func1Controller               ← 一次性朗读（框选）
├── Func2Controller               ← 一次性朗读（固定区域）
└── ScanModeController            ← 扫描类骨架：后台线程 / 去重 / 状态通知
    ├── Func3Controller
    ├── MangaController
    └── GameModeController
```

**三个回调只在 `BaseController` 里实现一份**。历史上 `ScanModeController` 把
`_update_status` / `_show_result` / `_report_error` 又写了一遍（AST 完全一致），
2026-11-15 合并后改为继承。改动原因与反向验证见 KB-PERF-014（performance.md §14）。

⚠ `ScanModeController` 独有的只有 `_notify_state_changed`（运行状态变化），
别顺手删掉。守门测试 `tests/test_no_duplicate_impl.py` 用 AST 检查
「子类不得重新定义基类的那三个方法」，行为测试抓不到这种重复。

## 4. 一次点击的完整调用链（KB-ARC-003）

以首页「功能2 · 朗读」为例：

```
1. js/pages/home.js     按钮 click → A.bridge.call("func2_read", {})
2. js/core/bridge.js    api.request("func2_read", "{}", cb)   ← 20s 超时
3. qt_web/api.py        WebApi.request() → json.loads → _dispatch()
4. qt_web/api.py        do_func2_read(p)
5. controllers/app_controller.py  self.app.func2  ← 首次访问才构造
6. controllers/func2_controller.py  截当前选区 → ocr.recognize()
7. core/ocr_engine.py   RapidOCR 推理
8. 结果回调链：_show_result → on_result → SignalBridge.emit_result
9. qt_ui/bridge.py      QtResultRouter.route_result：复制 / 翻译判定 / 显示
10. qt_web/api.py       push_result / push_translate_result → event 信号
11. js/core/bridge.js   收到推送 → emit 事件 → 页面更新结果区
12. 若未启用翻译：core/tts.py TTSManager.add(text, dedup=False) 入队朗读
```

扫描类（功能3 / 漫画 / 游戏）的入口是 `do_toggle_scan`，它调
`AppController.stop_scan_modes(except_mode=...)` 保证同时只有一个扫描在跑，
再调控制器的 `start()`；`start()` 在无选区时先**同步框选**，这一环会从后台线程
跨回主线程（见 KB-SEL-002）。

## 5. 线程模型（KB-ARC-004）

| 执行体 | 拥有者 | 干什么 | 约束 |
| --- | --- | --- | --- |
| 主线程 | Qt | 窗口、QWebEngine、**一切 Qt 控件** | 耗时操作必须挪走 |
| 扫描线程 | `ScanModeController.worker_thread` | 截图 → 变化检测 → OCR → 回调 | 不能直接建 Qt 控件 |
| 翻译线程 | `TranslationManager._worker` | 队列化翻译 | 结果由 120ms QTimer 在主线程取出 |
| 语音子进程 | `VoiceSession` → `qt_web.voice_worker` | 麦克风采集 + VAD + ASR | 独立进程，闲置退出 |
| TTS 线程 | `TTSManager._worker` | 朗读队列 | 需 `pythoncom.CoInitialize()` |

**跨线程到主线程的两条通道**

1. Qt 信号：`SignalBridge` 声明 `Signal`，后台线程 emit，主线程自动排队执行。
2. 适配器：`QtSelectorAdapter` 用信号 + `threading.Event` 阻塞后台线程直到主线程完成框选。

> 曾经的反例：`SelectorBridge._do_select.emit()` 从主线程发出时是**直接调用**
> 而非排队，导致调用线程被自己阻塞。所以适配器对「已在主线程」的情况要单独走同步分支。

## 6. 启动流程与懒构造装配（KB-ARC-005）

```
qt_web/main.py::main()
  ├─ utils.env_limits.apply_runtime_limits()   ← 必须在 import 重依赖之前
  ├─ QApplication / 高 DPI 设置
  └─ WebMainWindow.__init__()
       ├─ AppController()                     ← 只建配置与锁，其它全部懒构造
       ├─ api_module._CURRENT_APP = app       ← 供 _mode_flags 读已构造控制器
       ├─ get_config()
       ├─ VoiceFloatWindow / VoiceSession / CursorInput
       ├─ TranslationManager(config, tts_service, translation_service)
       ├─ SignalBridge + QtResultRouter
       ├─ app.screenshot_service.selector = QtSelectorAdapter(...)
       ├─ _enabled / _queue_mode 从配置读入
       ├─ QWebEngineView + WebApi + QWebChannel
       │    registerObject("api", self.api)
       ├─ view.load(web/index.html)
       ├─ _setup_callbacks()                  ├─ 注册 NavBridge 为 "nav"
       ├─ apply_hotkeys()
       ├─ TranslationManager.start_worker() + 120ms drain timer
       └─ SystemTray.start()
```

前提：`apply_runtime_limits()` 必须在导入 `onnxruntime` / numpy 之前执行，
否则 BLAS 线程数已经定了，改环境变量无效。

## 7. 结果路由：谁负责显示、谁负责读（KB-ARC-006）

所有功能最终走 `qt_ui/bridge.py::QtResultRouter.route_result(mode, text, status, display)`：

```
if status 或空文本 → 只显示，返回 False
if 模式属于 func1/func2 且配置开启复制 → 复制到剪贴板（信号转发到主线程）
if mode != "voice_input" 且翻译「可用」且该模式翻译开关开着
        → auto_translate_if_enabled() 返回 True → 返回 True（调用方不再读原文）
否则 → 显示，返回 False（调用方朗读原文）
```

**两条容易踩的规则**

- 返回 `True` 的语义是「翻译已接管，别读原文」，由调用方决定是否朗读，
  路由本身不发朗读指令。
- 「翻译可用」不只看开关：`WebMainWindow` 把 `api.translation_usable` 注入为
  `router.translation_available`。没有可用模型时退回「显示 + 读原文」，
  避免开关开着却什么都不发生。注入钩子不存在时（原生 Qt 版）默认 True。

结果目标控件固定映射（`_RESULT_TARGETS`）：func1/2/3/manga/game → `result_text`，
voice_input → `voice_result_text`，均为追加模式（replace=False）。
