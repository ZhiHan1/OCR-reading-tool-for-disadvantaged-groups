# OCR 文字朗读工具

> 面向阅读障碍与视障人群的桌面辅助工具：框选屏幕任意区域 → OCR 识别 → 朗读或翻译。

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-QtWebEngine-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)](#环境要求)
[![License](https://img.shields.io/badge/License-MIT-lightgrey)](#许可证)

> **无障碍定位**：本项目为阅读障碍（Dyslexia）、低视力及弱视用户设计。
> 核心思路是把「需要费力阅读的文字」转为「可以直接听的内容」，
> 让用户不必逐字辨认屏幕上的小字、密排字或无法复制的文字。

---

## 这个工具解决什么问题

大量文字只存在于**屏幕上、无法选中复制**的地方：游戏字幕、扫描版 PDF、图片公告、
没有文本层的视频、老旧的业务软件。对阅读障碍者、低视力用户和需要长时间阅读的人来说，
这几乎是一道墙。

本工具用一条热键把这段文字「捞」出来，转成可以听、可以复制、可以翻译的文本：

| 能力 | 说明 |
| --- | --- |
| 🔍 **框选识别** | 拖一个框，识别框内文字并朗读 |
| 📌 **固定区域识别** | 框一次，之后同一区域循环监听、自动朗读（游戏字幕、弹窗） |
| 🖼️ **翻译提示** | 漫画/游戏模式的文字气泡识别后送入翻译 |
| 🎤 **语音输入** | 本地 FunASR 模型实时转写，可选定麦克风 |
| 🔊 **朗读** | 系统 SAPI 与微软 Edge 在线音色，失败自动回退 |
| 🌐 **本地翻译** | GGUF 量化模型本地推理，不需要联网 |
| ♿ **可访问性** | 大字号界面、常驻托盘、闲置自动回收内存 |

## 功能一览

### 屏幕识别

- **功能1（F7）**：框选 → OCR → 朗读 → 可选复制到剪贴板
- **功能2（F9 / F8）**：框选固定区域 → 连续监听 → 新文本自动朗读
- **功能3（F4 / F5）**：切换开关 / 重新框选
- **漫画模式、游戏模式**：针对聊天气泡和字幕的专用扫描策略（稳定性判定、历史去重）
- **停止朗读（F6）**：随时中断

### 语音识别

三种输入模式，适配不同说话习惯：

| 模式 | 行为 |
| --- | --- |
| 手动结束 | 点一次开始持续录音，再点一次结束并统一输出 |
| 实时分段 | 每说完一段就追加文字，继续监听下一段 |
| 说完自动结束 | 允许中途短暂停顿，较长静音后整轮结束 |

本地推理使用 FunASR（Paraformer / SenseVoice），VAD 与标点模型独立加载。
识别结果可直接输入到当前光标处。

### 麦克风设备选择

「语音输入 → 识别设置 → 模型 → 麦克风」列出**启动时自动枚举**的全部输入设备：

- 同一支麦克风在 MME / DirectSound / WASAPI / WDM-KS 下的重复条目会被合并（本机实测 55 个端口 → 15 个设备）
- 按**设备名字符串**保存，不存索引（索引随插拔变化）
- 留空即跟随 Windows 默认输入设备
- 插拔后点「刷新」重新枚举
- 选中的设备被拔掉时会**明确报错**，不会静默改用其他麦克风

### 翻译

- 本地 GGUF（HY-MT1.5 量化版）离线推理
- 在线后端可选；在线失败不影响本地朗读
- 翻译模型按需扫描登记，不随包发布

## 界面形态

提供两套自研界面，**Web UI 为当前主线**：

| 形态 | 入口 | 状态 |
| --- | --- | --- |
| Web UI（QWebEngine + HTML/CSS/JS） | `web_entry.py` | ✅ 主线，开发与发布均基于此 |
| 原生 Qt Widgets | `qt_main.py` | 保留作回退参考 |

界面视觉语言：深色磨砂主题、圆角卡片、侧边栏分组导航、页面切换过渡动画。

## 技术栈

**后端**

- Python 3.14
- PySide6（Qt 6 WebEngine + WebChannel）
- RapidOCR（ONNX Runtime，可选 CUDA 加速）
- FunASR / PyTorch（语音识别）
- llama-cpp-python（GGUF 翻译）

**前端**

- 原生 JavaScript（无框架、无构建步骤）
- 模块化 `pages/`、`components/`、`core/` 结构
- CSS 变量驱动的主题系统

**打包**

- PyInstaller 双 EXE：GUI 进程 + 语音工作进程（依赖隔离）
- 自定义 staging 脚本装配运行时资源

## 项目结构

```text
OCR模块化项目/
├── core/                     # 领域服务：OCR、ASR、TTS、翻译、配置
├── controllers/              # 功能编排：框选 → 识别 → 输出
├── qt_ui/                    # 原生 Qt 控件（选择器、悬浮窗）
├── qt_web/                   # Web UI 外壳（主线）
│   ├── main.py               #   主窗口、托盘、页面桥接
│   ├── api.py                #   JS ↔ Python 唯一通道
│   ├── voice_session.py      #   语音会话与子进程管理
│   ├── voice_pipeline.py     #   录音 / VAD / 串行 ASR
│   └── web/                  #   index.html、js/、css/
├── utils/                    # 路径、日志、环境限制等通用工具
├── tools/                    # 开发脚本：验证、基准、模型 staging
├── tests/                    # 单元与质量回归测试
├── KNOWLEDGE/                # 长期维护的知识库（架构、踩坑、实测数据）
├── 模型/                       # 用户模型投放目录（翻译 / 语音）
├── models/                   # 程序内部模型（VAD、标点、OCR、目录簿）
├── defaults.yaml             # 出厂默认配置
├── requirements.txt          # 运行依赖
├── build_web.py              # 一键打包（6 步）
└── WEB_RELEASE.md            # 发布版说明
```

## 快速开始

### 环境要求

- **Windows 10 / 11**（依赖 SAPI、WASAPI、Win32 API）
- **Python 3.14**
- 磁盘空间：模型约占 2.3 GB

### 1. 克隆与安装

```powershell
git clone https://github.com/ZhiHan1/OCR-reading-tool-for-disadvantaged-groups.git
cd OCR-reading-tool-for-disadvantaged-groups

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

> `llama-cpp-python` 在 Windows 上推荐使用预编译 wheel；若 `pip install` 需要本地编译，
> 请先安装 Visual Studio Build Tools。

### 2. 准备模型

模型体积大，**不进仓库**，需要手动放到对应目录（详见下文）。

```powershell
# 自动下载语音识别、VAD、标点模型
py -3.14 tools\download_asr_models.py
```

| 目录 | 内容 | 是否必需 |
| --- | --- | --- |
| `模型/语音/<模型名>/` | Paraformer / SenseVoice 完整文件夹（`config.yaml`、`model.pt`、`tokens.json` 等） | 语音输入必需 |
| `模型/翻译/*.gguf` | 本地 GGUF 翻译模型 | 本地翻译必需 |
| `models/asr/fsmn-vad/` | VAD 端点检测 | 语音输入必需 |
| `models/asr/ct-punc/` | 标点恢复 | 语音输入必需 |
| `models/ocr/*.onnx` | RapidOCR 检测/分类/识别权重 | OCR 必需 |

程序**每 3 秒扫描一次**模型目录；只登记路径，不复制文件、不自动加载。
新增模型后在界面点「重新扫描」即可。

### 3. 启动

```powershell
# Web UI（主线）
py -3.14 web_entry.py

# 或使用脚本
.\run_web.ps1
```

首次启动会在项目根目录自动生成 `config.yaml`。

### 4. 运行测试

```powershell
# 全部单元测试
py -3.14 -m unittest discover -s tests -v

# 只跑纯函数（秒级）
py -3.14 -m unittest tests.test_asr_quality.FilterRules -v

# GUI 冒烟测试（真实启动窗口并检查页面）
py -3.14 tools\smoke_gui.py
```

模型缺失时，依赖模型的用例会自动 skip，不会算作失败。

## 配置

| 文件 | 用途 | 进仓库 |
| --- | --- | --- |
| `defaults.yaml` | 出厂默认配置，程序只读 | ✅ |
| `config.yaml` | 当前配置，随用户修改自动保存 | ❌（含个人热词与路径） |

配置项说明见 [`CONFIGURATION.md`](CONFIGURATION.md)。
设置页支持**导入 / 导出 / 恢复全部默认**，导出内容包含在线服务凭证，请勿公开分享。

## 打包发布

```powershell
py -3.14 build_web.py                    # 完整打包 + 自检
py -3.14 build_web.py --skip-tests       # 跳过自检（快速迭代）
py -3.14 build_web.py --tag v1.0.0       # 带版本标签
```

打包 6 个阶段：环境预检 → PyInstaller 双 EXE → 随包说明与图标 → 运行时资源 staging
→ 发布版自检 → 汇总。产物位于 `dist/Web-<时间戳>/OCR_Reader_Web/`，
以 `release-test.json` 的 `ok: true` 为完整通过标志。

自检覆盖 25 项：页面渲染、OCR 实际识别、CUDA 实际推理、语音模型推理、
工作进程 IPC 与录音启停、麦克风枚举、依赖隔离、翻译 GGUF 外置等。
详见 [`WEB_RELEASE.md`](WEB_RELEASE.md)。

## 开发约定

### 分层纪律

```text
前端 (web/)  ──QWebChannel──▶  qt_web/api.py  ──▶  controllers/  ──▶  core/
```

- `core/`、`controllers/` **不感知界面**；界面相关只允许在 `qt_web/` 内做适配
- JS 侧只有一个入口 `api.request(action, payloadJson, callback)`
- 后端推送统一走 `event` 信号，payload 为 JSON 字符串

### 编码要求

- 所有源文件 **UTF-8 无 BOM**
- 换行由 `.gitattributes` 统一管理（仓库内 LF）
- 路径优先使用相对路径，禁止硬编码绝对路径

### 提交规范

使用 **中文 Conventional Commits**：

```text
feat(voice): 支持选择麦克风输入设备
fix(settings): 修复页签向左切换闪旧页
docs(knowledge): 补充麦克风设备归并说明
refactor(api): 拆分语音设备枚举逻辑
test(settings): 增加页签动画三帧采样用例
```

常用类型：`feat` `fix` `docs` `refactor` `test` `perf` `build` `chore`。
作用域建议：`voice` `ocr` `tts` `trans` `settings` `web` `api` `build` `knowledge`。

### 修改前端资源后

`qt_web/web/index.html` 中所有 JS/CSS 共享同一个版本号 `?v=...`，
**必须全部 24 处一起 bump**，否则 QWebEngine 会继续使用缓存里的旧文件。

## 知识库

`KNOWLEDGE/` 是长期维护的工程知识库，记录架构决策、实测数据与踩坑经验，
供人和 AI 助手检索：

| 文件 | 内容 |
| --- | --- |
| `INDEX.md` | 全部 `KB-xxx-000` 条目索引 |
| `PROMPT.md` | 给 AI 助手的角色与约束提示词 |
| `architecture.md` | 分层架构与调用链 |
| `frontend.md` | 前端页面组织、路由、动画 |
| `backend-api.md` | `do_xxx` 动作清单与返回约定 |
| `asr.md` | 语音识别：模式、模型、热词、麦克风 |
| `translation.md` | 翻译服务与调参 |
| `performance.md` | 内存、启动耗时、线程模型 |
| `gotchas.md` | 「这里为什么这么写」与验证纪律 |

## 许可证

MIT License —— 详见 [`LICENSE`](LICENSE)。

### 第三方组件

本项目依赖多个开源项目与模型，均遵循各自的许可证：

- **PySide6** — LGPLv3
- **RapidOCR / PaddleOCR** — Apache-2.0
- **FunASR**（Paraformer / SenseVoice / FSMN-VAD / CT-Punc）— MIT / Apache-2.0
- **llama-cpp-python** — MIT
- **ONNX Runtime** — MIT
- **HY-MT1.5**（翻译模型）— 见模型发布方的许可证

模型权重不随本仓库分发，请自行获取并遵守其原始许可。
