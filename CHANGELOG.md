# 更新日志

本项目从 2026-11 起使用本文件记录版本变更，格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **模型权重完全外置**：安装包只包含程序本体（双 EXE + `_internal/` + 说明 + 图标），
  `.onnx` / `.pt` / `.gguf` 一律不进包。模型放在 **EXE 同级的** `模型/`（语音、翻译）与
  `models/`（OCR / VAD / 标点 / 目录簿）下，路径全部相对 `sys.executable` 解析，
  整个目录换盘符、换机器都能直接用。程序本体 3.28 GiB（不含模型），
  含模型共 4.18 GiB，其中模型约 0.9 GiB 在外置目录。
- **发布包瘦身 0.27 GiB**（4.45 → 4.18 GiB），逐项见 KB-PERF-013：
  - PySide6 **449 → 305 MiB**：PyInstaller 原本把整个 Qt 收进来，
    现在按「从 QWebEngine 出发的 DLL 依赖闭包」只留 22 个必需模块，
    3D / 图表 / PDF / 多媒体 / 虚拟键盘 / Quick 控件样式全部剔除；
    Chromium 本地化包 216 → 4 个（只留中英文）；Qt 自身的 `.qm` 翻译全部删除
    （程序从未安装 `QTranslator`）；开发者工具资源（11 MiB）不进发布版。
  - funasr 的评测依赖 `sklearn` / `numba` / `llvmlite` **约 127 MiB** 不再进 worker
    （这三者只用于 EER 评测、声纹聚类、语音分离计时，本项目不涉及）。
  - **明确保留**：`cufft64_11.dll`（被 CUDA EP 与 `torch_cpu.dll` 硬引用，删了
    GPU 推理运行期才崩）、`onnxruntime_providers_cuda.dll`、`torch`（funasr 导入时
    强制拉起 922 个模块）、`cv2`（`RapidOCR()` 初始化即 `import cv2`）。
- **启动自动检测模型**：`WebApi._check_model_health()` 在启动 1.2 秒后检查
  `模型/语音` 与 `models/asr/{fsmn-vad,ct-punc}`，缺什么就以 `models-missing` 推送到前端，
  界面右下角弹出「缺少模型文件」并列出本机绝对路径，照着放即可。
- `tools/sync_external_models.py`：把本机权重同步到任意目标目录（如从旧包拷模型到新包），
  只补缺失文件，不覆盖用户自己放的模型，同步后重写相对路径的 `models/asr/registry.json`。
- `tools/stage_external_models.py`：扫描外置模型目录并写 `external-models-manifest.json`，
  缺文件时列出清单而不是直接失败。
- `tools/model_audit.py`：只读体检模型目录，报告必需 / 多余 / 重复 / 缺失。
- `--sync-models` 参数：`py -3.14 build_web.py --sync-models` 打包后顺带把本机权重摆到产物目录。

### 修复

- **发布版自检失败（`ModelRegistry` 没有 `remove`）**：上一轮删掉
  `ModelRegistry.remove()` 时漏了 `qt_web/release_gui_check.py` 仍在调用它，
  导致 `py -3.14 build_web.py` 在最后一步 `gui` 检查抛 `AttributeError`。
  已改为按 id 过滤 `records()` 后落盘，并补两条守门测试（静态扫描全仓库
  是否还调用已删方法 + 断言自检脚本的清理写法）。
- **休眠唤醒后语音无法识别**：PortAudio 只在导入时 `Pa_Initialize()` 一次，
  设备表固化；Windows 休眠复位音频驱动后端点重新枚举，导致录音流「打开成功
  但采到纯静音」，界面只显示「未检测到说话」，重试无效、只有重启软件才恢复。
  现新增三层防护：`qt_web/power_monitor.py` 监听 `WM_POWERBROADCAST` 并在唤醒时
  重启语音工作进程；`VoicePipeline` 静音自检（连续 5 秒无电平即报可操作错误）；
  唤醒时正在录音则不打断，等本轮结束补做重连。见 KB-ASR-012。
- **OCR 模型缺失时静默回退到包内副本**：spec 的 `no_weights()` 会剥掉
  `rapidocr/models/`，发布版本来就没有包内副本，一旦回退就会「开发机通过、发布版崩」。
  现在只认外置 `models/ocr`，缺文件抛出带操作指引的 `FileNotFoundError`。

### 变更

- `build_web.py` 的 staging 步骤只保留 CUDA 运行库；`stage_voice_models.py` 与
  `stage_ocr_runtime.py` 退出打包流程（脚本保留，供需要时手工使用）。
- 发布自检 `voice_models_bundled` → `voice_models_external`，语义改为「EXE 同级目录里能找到完整模型」；
  `translation_weights_external` 现在同时禁止 `.onnx/.pt/.pth/.bin`，但排除外置的 `模型/` 与 `models/`，
  以及 PySide6 的 V8 快照与 chardet 的编码模型（它们是运行库，不是权重）。
- **删除死代码（364 行 + 2 个文件）**：`grab_all_screens`、`get_ocr_device`、
  `clear_cache`、`estimate_audio_duration`、`is_chinese_voice`、`get_all_texts`、
  `_find_common_prefix_length`、`phonetic_variants`、`mine_from_existing`、
  `get_model_path`、`_get_center_y_x`、`is_edge_available`、`synthesize_wav`、
  `ScanModeController.toggle`，以及整个 `qt_web/release_check.py`（入口早已切走）
  与 `pyinstaller_hooks/hook-customtkinter.py`。
- `requirements.txt` 移除 `customtkinter`、`pynput`（光标输入实际用 ctypes `SendInput`）；
  spec 同步移除 `customtkinter` 排除项。
- **清理冗余模型文件**：两处 HuggingFace `.cache` 下载残留、`registry.json.bak`、
  `models/LocalSubs/`、`models/Qwen2.5/`。`models/ocr` 保留（发布版唯一来源）。
- **删除语音模型手工导入/移除接口**：`do_asr_import_model`、`do_asr_remove_model`
  前端从未调用（语音页只有下拉框、没有导入按钮），功能已由 `ModelRegistry.scan()`
  自动扫描 `模型/语音/` 覆盖。连带删除 `ModelRegistry.remove()` 与 `bridge.js` 里
  对应的超时豁免。语音页文案改为「把语音模型文件夹放进程序目录的 模型/语音/ 下，会自动识别」。
- **删除其余死接口与残留模块**：`do_hotwords_clear_candidates`、`do_set_ocr_device`、
  `do_save_config` 三个 handler（前端零调用），以及整个 `utils/hotkey.py`（134 行，
  全仓库零导入，热键实际由 `qt_web/main.py` 用 `keyboard` 库实现）。
  底层 `clear_candidates()`、`reload_device()` 仍有其它调用方，予以保留。

- **麦克风输入源选择**：语音输入页可在启动时枚举出的所有输入设备中选择，
  支持手动刷新，按设备名持久化到 `asr.device_name`。
  同一支麦克风在 MME / DirectSound / WASAPI / WDM-KS 下的重复端口自动归并
  （本机实测 55 个端口 → 15 个设备）。
- `tools/download_asr_models.py`：一键下载语音识别、VAD、标点模型。
- 发布版自检新增 `microphone_listing` 项（25 项检查）。

### 修复

- **设置页页签向左切换时「闪一下旧页面」**：轨道 pane 改为按阅读顺序排列，
  视口位置只由 `translateX` 控制；`onfinish` 中先提交 DOM 再取消动画。
- 发布自检中 `radios` 页的假阴性（`{animate:false}` + `router.refresh()`）。
- 打包脚本对 SenseVoice 模型的硬编码，改为自适应（至少 1 个识别模型 + vad/punc）。

### 变更

- 闲置退出改为固定档位下拉：1 / 2 / 5 / 10 / 30 分钟 + 常驻，默认 5 分钟。
- 语音页「识别设置」面板默认展开。
- 界面文字不可选中，仅 `input` / `textarea` 内容可复制。
- 前端资源版本号统一为 `?v=20261105c`（24 处）。

### 文档

- 新增 `README.md`、`LICENSE`、`CHANGELOG.md`、`.gitattributes`。
- 知识库新增：`KB-WEB-009`（设置页切换闪旧页）、`KB-ASR-011`（麦克风选择）、
  `KB-GOT-009`（动画 bug 三帧采样法）。

### 构建

- 打包入口改为 `build_web.py`（6 步流程），`build_web.ps1` 降为薄封装。
  支持 `--skip-tests` 与 `--tag`。

---

## 说明

- 2026-11-05 之前的开发历史没有逐版本记录，完整细节见 `KNOWLEDGE/`。
