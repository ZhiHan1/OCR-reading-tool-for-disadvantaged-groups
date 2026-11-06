# AGENTS.md

本文件是给 AI 编程助手与贡献者的**第一手约定**。动手前先读完，再查 `KNOWLEDGE/`。

## 项目

面向阅读障碍与低视力用户的桌面辅助工具：框选屏幕任意区域 → OCR 识别 → 朗读或翻译。
Windows 桌面应用，Python 3.14 + PySide6（QWebEngineView + QWebChannel）+ 原生 JS 前端。

## 仓库

| 项 | 值 |
| --- | --- |
| 远程 | `https://github.com/ZhiHan1/OCR-reading-tool-for-disadvantaged-groups.git` |
| 分支 | `main` |
| 许可证 | MIT |

## 硬性约束

1. **分层纪律**：`core/` 与 `controllers/` 不感知界面；界面适配只允许放在 `qt_web/`。
   改动优先落在 GUI 层，`core/` 只做最小兼容修改，不重构。
2. **编码**：所有文本文件 UTF-8 **无 BOM**；仓库内统一 LF（`.gitattributes` 管理）。
3. **写入方式**：用 Python 字节级写文件（读 bytes → utf-8 解码 → 替换 → 无 BOM 写回）。
   禁止用 PowerShell `Set-Content` / 默认编码重写源文件——历史上已损坏过 `index.html`。
4. **路径**：优先相对路径（相对项目根目录）。绝对路径含中文时极易在编码/转义环节出错。
5. **前端版本号**：改任何 JS/CSS 后，`qt_web/web/index.html` 里 **24 处** `?v=` 一起 bump，
   否则 QWebEngine 继续用缓存。当前 `?v=20261105c`。
6. **验证**：修 Bug 必须补守门测试并做**反向验证**（回退修复后测试要失败）。
   动画类问题要采样「起始 / 中间 / 结束」三帧，只看结束状态会漏掉闪帧。

## 不要提交到仓库

| 内容 | 原因 |
| --- | --- |
| `模型/翻译/*.gguf`、`模型/语音/*/`、`models/asr/*/`、`models/ocr/*.onnx` | 约 2.3 GB，GitHub 单文件上限 100 MB |
| `config.yaml` | 含本机热词、模型路径、在线服务 API Key |
| `models/asr/registry.json` | 记录本机模型名，换机失效 |
| `dist/`、`build/`、`logs/`、`__pycache__/`、`.cache/` | 构建产物与运行时数据 |

**禁止 `git add -f` 绕过**：大文件一旦进历史，`.git` 永久膨胀。

## 提交规范

中文 Conventional Commits：`type(scope): 中文描述`

- type：`feat` `fix` `docs` `refactor` `test` `perf` `build` `chore`
- scope：`voice` `ocr` `tts` `trans` `settings` `web` `api` `build` `knowledge`
- 示例：`feat(voice): 支持选择麦克风输入设备`

⚠ **提交信息文件必须 UTF-8 无 BOM**。PowerShell 5.1 的 `-Encoding utf8` 会写 BOM，
导致提交信息首字符异常。用 Python 写 bytes，或 `git commit --amend -F` 修正。

## 本机环境注意

- 目录 Owner 是旧 Windows SID，Git 需要：
  `git config --global --add safe.directory '<本目录>'`
- GitHub 需要代理，已配在仓库本地：`git config http.proxy http://127.0.0.1:7890`

## 常用命令

```powershell
py -3.14 web_entry.py                            # 启动开发版
py -3.14 -m unittest discover -s tests -v        # 单元测试
py -3.14 tools\smoke_gui.py                      # GUI 冒烟
py -3.14 build_web.py                            # 打包 + 25 项自检
git status --short                               # 提交前必看
```

## 知识库

`KNOWLEDGE/INDEX.md` 是入口。摘要如下：

| 文件 | 内容 |
| --- | --- |
| `PROMPT.md` | AI 助手的角色、约束与检索顺序（唯一有效副本） |
| `architecture.md` | 分层架构与调用链 |
| `frontend.md` | 页面组织、路由、动画（含 KB-WEB-009 页签切换） |
| `backend-api.md` | `do_xxx` 动作清单与返回约定 |
| `asr.md` | 语音识别（含 KB-ASR-011 麦克风选择） |
| `translation.md` | 翻译服务与调参 |
| `performance.md` | 内存、启动耗时、线程模型 |
| `gotchas.md` | 已知取舍、验证纪律、打包、Git（KB-GOT-010/011） |
| `screenshot-selector.md` | 框选与多屏 DPI |
