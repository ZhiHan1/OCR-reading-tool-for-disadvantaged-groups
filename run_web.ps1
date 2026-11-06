$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 必须用 Python 3.14：llama-cpp-python（本地 GGUF 翻译）只装在这个解释器里。
# 3.12 虽然有 PySide6，但没有 llama_cpp，翻译会报「未安装llama-cpp-python库」。
Write-Host "启动 WebEngine 版 OCR 文字朗读工具 (Python 3.14)..." -ForegroundColor Cyan
py -3.14 -m qt_web.main
