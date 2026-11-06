# Web 版打包入口（兼容旧用法）：实际逻辑在 build_web.py。
#   .\build_web.ps1              完整打包 + 自检
#   .\build_web.ps1 -SkipTests   只打包
#   .\build_web.ps1 -Tag nightly 自定义产物名后缀
param(
    [switch]$SkipTests,
    [string]$Tag = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @((Join-Path $root 'build_web.py'))
if ($SkipTests) { $arguments += '--skip-tests' }
if ($Tag) { $arguments += @('--tag', $Tag) }
Push-Location $root
try {
    $env:PYTHONIOENCODING = 'utf-8'
    py -3.14 @arguments
    if ($LASTEXITCODE -ne 0) { throw "build_web.py exited with $LASTEXITCODE" }
} finally { Pop-Location }
