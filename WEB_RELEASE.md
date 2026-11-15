# Web UI 便携版

运行 `OCR_Reader_Web.exe`。不要单独移动 EXE，必须保留 `_internal/`、`OCR_VoiceWorker.exe` 和模型目录。
界面、托盘和 EXE 使用项目的 Logo.ico。仅提供 WebEngine 主界面。

本包**不含模型权重**，只有程序本体。首次运行前，把模型放到 EXE 同级的这两个目录里：
- `模型/语音/<模型名>/`：完整 FunASR 语音识别模型（`config.yaml` + `model.pt`），
  目录名随意，启动时会自动扫描登记（`model.pt` 小于 2 MiB 会被当成假文件跳过）。
  当前开发机已验证 paraformer-large。
- `models/asr/fsmn-vad/`、`models/asr/ct-punc/`：语音端点检测与标点模型（必需）。
- `models/ocr/`：OCR 检测、方向分类与识别三个 onnx；缺失时会回退到程序包内自带的副本。

缺少模型时程序仍能启动，右下角会弹出提示并列出应该放文件的本机路径。
从旧版本升级：直接在旧包目录里运行 `py -3.14 tools\sync_external_models.py <新包目录>`，
或把旧的 `模型/` 与 `models/` 整个复制到新包的 EXE 同级即可。

路径一律相对 `OCR_Reader_Web.exe` 解析（`utils.paths.app_root()` 以 `sys.executable` 为锚），
**整个目录可以换盘符、换机器、改目录名**，不需要改任何配置。

`_internal/gpu_runtime/`：ONNX Runtime CUDA 12 / cuDNN 9 运行库，供 OCR 使用 GPU 推理（属于程序本体）。

清单：`external-models-manifest.json`（外置模型实际情况与缺失项）、
`gpu-runtime-manifest.json`（CUDA 运行库）。

翻译模型同样外置：
- `模型/翻译/`：放 GGUF 文件；启动及运行时自动扫描登记，登记后需自行选择并启用。
- `模型/语音/`、`models/ocr/`：可继续放入新模型，启动时自动扫描。

## 麦克风选择
「语音输入 → 识别设置 → 模型 → 麦克风」可在启动时列出的所有输入设备中选择；
默认「跟随系统默认输入」。插拔设备后点该行的「刷新」即可重新枚举。
同一支麦克风在不同 Windows 音频接口下的重复条目已合并，只保留一个。
选择的设备名存在 `asr.device_name`；选中的设备被拔掉时会明确报错，不会静默改用其它麦克风。

首次启动自动生成当前 `config.yaml` 和翻译模型目录。模型路径按 EXE 所在目录解析，不依赖启动时的工作目录。
运行日志：`logs/application.log`。构建自检结果：`release-test.json`。

## 运行库
主程序收集 WebEngine、OCR、翻译及 TTS；语音工作进程收集 FunASR/PyTorch。
已排除 WebEngine 调试资源、未使用的 QML、ONNX TensorRT 提供器和 NVIDIA 额外组件；
OCR 的 CUDA 运行库按清单复制到 `_internal/gpu_runtime/`。没有可用 NVIDIA GPU 时，
OCR 会安全回退到 CPU，其他功能不受影响。

### 体积构成与保留原则

发布包总计约 **4.18 GiB**（含外置模型约 0.9 GiB），程序本体约 3.28 GiB：

| 项目 | 体积 | 说明 |
| --- | --- | --- |
| `gpu_runtime/` | 1.80 GiB | CUDA 12 + cuDNN 9，GPU 推理必需 |
| `torch/` | 373 MiB | funasr 导入时强制依赖（922 个模块），无法移除 |
| `onnxruntime/` | 328 MiB | 含 298 MiB 的 CUDA EP |
| `PySide6/` | 305 MiB | 已按依赖闭包裁剪，原为 449 MiB |
| `cv2/` | 112 MiB | `RapidOCR()` 初始化即 `import cv2`，不能删 |

PySide6 只保留从 `QtWebEngineCore` 出发可达的 22 个 DLL；3D、图表、PDF、
多媒体、虚拟键盘、Quick 控件样式均已剔除。Chromium 本地化包只留中英文
（216 → 4 个），Qt 自身的 `.qm` 翻译全部删除（程序未安装 `QTranslator`）。

**以下不要「优化」掉**，删了会造成运行期故障而非打包失败：

- `opengl32sw.dll`：`Qt6Gui` 的隐式软件 OpenGL 回退，不在导入表里，
  删了会让无显卡 / 远程桌面环境黑屏。
- `cufft64_11.dll`：被 `onnxruntime_providers_cuda.dll` 与 `torch_cpu.dll` 引用，
  删了 GPU 推理会崩。
- `cv2`：OCR 引擎初始化硬依赖。

守门测试：`tests/test_package_size.py`（16 项，静态检查 spec，不需要跑完整打包）。

构建会验证 7 个 Web 页面（home / translate / tts / voice_input / settings / about / radios）、
图标、模型目录自动扫描、系统音色、MP3 编码、
两类 EXE 的依赖隔离、外置模型推理（语音 / VAD / 标点 / OCR）、翻译 GGUF 外置推理、
CUDA 实际推理，以及语音工作进程通信/录音启停。自检共 25 项。
所有构建产物保留，测试失败时也不清理。以 `release-test.json` 的 ok=true 为完整通过标志。
