# Web UI 便携版

运行 `OCR_Reader_Web.exe`。不要单独移动 EXE，必须保留 `_internal/`、`OCR_VoiceWorker.exe` 和模型目录。
界面、托盘和 EXE 使用项目的 Logo.ico。仅提供 WebEngine 主界面。

本包不含翻译 GGUF，也不含开发者当前配置或 API 密钥。
随包包含：
- `模型/语音/`：实际存在的完整 FunASR 语音识别模型（当前为 paraformer-large；
  若打包机上有 SenseVoiceSmall，会自动一并带上并写入 `models/asr/registry.json`）。
- `models/asr/fsmn-vad/`、`models/asr/ct-punc/`：语音端点检测与标点模型（必需）。
- `models/ocr/`：约 31 MiB 的通用 OCR 检测、方向分类与识别权重。
- `_internal/gpu_runtime/`：ONNX Runtime CUDA 12 / cuDNN 9 运行库，供 OCR 使用 GPU 推理。

随包清单：`voice-models-manifest.json`（语音模型与字节数）、
`ocr-runtime-manifest.json`、`gpu-runtime-manifest.json`。

翻译模型仍按原方式外置读取：
- `模型/翻译/`：放 GGUF 文件；启动及运行时自动扫描登记，登记后需自行选择并启用。
- `模型/语音/`：可继续放入新的完整 SenseVoice/Paraformer 模型文件夹，启动时自动扫描。
- `models/ocr/`：如需替换 OCR 权重，保持与随包文件相同的文件名。

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

构建会验证六个 Web 页面、图标、模型目录自动扫描、系统音色、MP3 编码、
两类 EXE 的依赖隔离、随包语音模型推理、OCR 文字识别、翻译 GGUF 外置推理、
CUDA 实际推理，以及语音工作进程通信/录音启停。
所有构建产物保留，测试失败时也不清理。以 `release-test.json` 的 ok=true 为完整通过标志。
