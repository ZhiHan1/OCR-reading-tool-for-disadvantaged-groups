# 程序内部模型资源与目录簿

- 用户投放模型请使用项目根目录的 `模型/翻译/` 和 `模型/语音/`，程序分别自动扫描。
- `asr/`：FSMN-VAD、标点辅助模型及 `registry.json`，语音主模型已移至 `模型/语音/`。
- `local/`：翻译模型目录簿 `models_config.json`，GGUF 文件已移至 `模型/翻译/`。
- `ocr/`：OCR 检测、方向分类、识别 ONNX 权重。

项目内登记的模型路径相对项目根目录保存；发布后相对 EXE 所在目录解析。
整个应用文件夹搬动时，把 `模型/`、`models/`、`config.yaml`、EXE 和 `_internal/` 一起移动。
外部导入保留用户选择的绝对路径，不强制复制到本目录；外部目录自己移动后需重新导入。

“导入路径”仅登记路径，“移除”仅删除语音模型列表记录，不删除权重。
VAD 和标点的可选覆盖路径分别为 `asr.vad_path`、`asr.punc_path`；OCR 覆盖路径为
`ocr.det_model_path`、`ocr.cls_model_path`、`ocr.rec_model_path`，均支持项目相对路径。

`build_web.ps1` 构建 Web UI 和独立语音工作进程并复制外置模型到发布目录。
旧 `build_qt.ps1` 仍是原生 Qt 版入口，不能用它代替 Web UI 打包。
当前路径迁移以开发运行和模拟 EXE 目录测试验证，完整 EXE 构建需另行执行。
