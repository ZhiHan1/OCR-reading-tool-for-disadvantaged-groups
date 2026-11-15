# 坑、取舍与纪律（GOT）

> 域编号：`KB-GOT-*`。看到「像 Bug」的代码先查这里。

## 1. 使用方式

本文记录**有意为之的取舍**和**已经踩过的坑**。维修前请回答三个问题：

1. 这条在本文里吗？在 → 不是 Bug，除非用户明确要求改。
2. 我改的是契约（动作名 / 返回结构 / 坐标语义 / 配置键）吗？是 → 必须同步更新
   `KNOWLEDGE/` 对应条目。
3. 我有办法证明修好了吗（守门测试 + 反向验证）？没有 → 先补验证再动手。

## 2. 已知取舍（KB-GOT-001）

| 现象 | 为什么这样 | 位置 |
| --- | --- | --- |
| 闲置 5 分钟后语音要等 11~14 s | worker 闲置退出换回 2 GB | `qt_web/voice_worker.py` |
| 首次识别慢 0.8~1.2 s | OCR 懒加载换启动省 65 MB | `core/ocr_engine.py` |
| 闲置 1 分钟后翻译要重加载 | GGUF 闲置卸载 | `core/translation_service.py` |
| 「嗯。」这类单字被保留 | 无法与真实单字应答区分 | `filter_hallucination` |
| 前端 19 个 JS 不合并 | 保留可读性与局部刷新 | `qt_web/web/` |
| 设置页「翻译未找到模型配置」 | 翻译 GGUF 未在 ModelManager 注册 | 用户要求暂不修 |
| 非语音/设置页模型变动最多晚 30 s | 轮询降到 30 s | `WebApi._set_model_scan_active` |
| 扫描模式同一时刻只跑一个 | 启动前先停其它模式，避免多线程抢 OCR | `do_toggle_scan` |
| 结果区只追加不替换 | `_RESULT_TARGETS` 全部 `replace=False` | `qt_ui/bridge.py` |

## 3. 文件编码事故与写入纪律（KB-GOT-002）

### 3.1 事故

用 PowerShell `Set-Content`（默认 cp936 写回）重写过 `index.html`，
导致整个文件中文变乱码、`<script>` 顺序错位。最后只能从打包版
（`APP\OCR_Reader_Web\_internal\`）**逐字节**恢复，再人工比对确认除 `?v=` 外无差异。

### 3.2 纪律

```python
# 唯一推荐的改文件方式
from pathlib import Path
p = Path("qt_web/web/index.html")
text = p.read_bytes().decode("utf-8")          # 显式 utf-8
text = text.replace("20261104ad", "20261104ae")
p.write_bytes(text.encode("utf-8"))            # 无 BOM
```

- 读写一律显式 `encoding="utf-8"`；Windows 默认编码是 cp936，不是 UTF-8。
- 不要用 `Set-Content` / `>` 重定向 / 默认编码 `WriteAllText` 改写源文件。
- 改完核对：行数、字节数、`class`/`def` 数量是否符合预期（防止替换范围失控）。
- 控制台脚本设 `PYTHONIOENCODING=utf-8` + `[Console]::OutputEncoding=UTF8`，
  否则中文日志乱码或抛 `UnicodeEncodeError`。

### 3.3 路径

- 文档、脚本、测试、命令参数**优先用相对路径**（相对项目根）。
- 本项目路径含中文，绝对路径在编码/转义/粘贴环节极易出错，
  已因此损坏过文件；只有跨机器引用才用绝对路径并注明原因。
- 运行时路径统一经 `utils/paths.py`：`app_root()` / `resolve_path()` /
  `store_path()`（项目内路径存相对、外部路径原样保留）。

## 4. 版本号与缓存（KB-GOT-003）

- `index.html` 里 24 个 JS/CSS 引用共享一个 `?v=20261105a`。
- QWebEngine 缓存本地文件，**改 JS/CSS 不 bump 就等于没改**。
- bump 用字节级替换，**全文件统一**替换成同一新值（24 处一起换）。
- `js/vendor/qwebchannel.js` 不带版本号，属特例。

## 5. 配置文件乱码（KB-GOT-004）

`config.yaml` 与 `defaults.yaml` 里存在历史乱码字符（例如 `tts.test_text`）。

- **禁止**读成 dict 改完整体写回：会把乱码扩散、注释丢失。
- 只做**定点**修改：`config.set(section, key, value=...)` + `config.save()`。
- 恢复默认 / 导入配置都是**整份替换**（见 KB-API-005），但会保留 `asr.hotwords`。

### 5.1 运行中手改 `config.yaml` 会被覆盖（KB-GOT-007）

应用运行期间**不要直接改磁盘上的 `config.yaml`**：

- 每次 `set_config` 都会 `config.save()` 整份重写；
- 窗口关闭时 `qt_web/main.py::closeEvent` 还会把**内存里的整份配置**再写一次。

所以运行中改盘 → 用户关窗即被内存值覆盖。要改配置，先完全退出应用。
（同理：`yaml.dump` 会丢注释，`config.yaml` 已无注释属正常现象。）

## 6. 验证纪律（KB-GOT-005）

### 6.1 守门测试必须走真实入口

反面教材：框选器的所有测试都手工构造遮罩、直接调 `on_press/on_release`，
从未调用 `select()`。于是 `select()` 里少一个 `QApplication` 导入、
抛出的 `NameError` 被适配器吞成 `None`，测试全绿、实机「框完没反应」。

**规则**：为每个对外入口函数写至少一个直接调用它的用例。

### 6.2 反向验证

修完 Bug 后，把修复临时回退，确认测试**失败**，再恢复。
这是证明「测试确实能拦住这个问题」的唯一办法。

例：回退 `from PySide6.QtWidgets import QApplication, QWidget` 后，
`tests/test_selector.py` 有 4 个用例 FAIL。

### 6.2.1 反向验证必须清 `__pycache__`（KB-GOT-013）

2026-11-15 实测踩到：反向验证脚本在**同一秒内**先写坏文件、跑测试、
再写回原文件。结果源文件与 `.pyc` 的 mtime 落在同一秒，Python 判定
缓存有效，**导入的仍是坏版本**，出现「文件已还原、测试却还在报错」的假象。

当时的错误输出：`AssertionError: 537 != 536`（文件里明明是 `0x0218`）。

**规则**：任何改写源文件后立即运行测试的脚本，都必须显式清理缓存：

```python
for pyc in (path.parent / '__pycache__').glob(path.stem + '*.pyc'):
    pyc.unlink(missing_ok=True)
```

清理后立即正确。这条同样适用于手工验证——怀疑「改了没生效」时，
先删 `__pycache__` 再判断，不要急着改代码。

### 6.2.2 删了方法要全局搜调用方（KB-GOT-014）

**真实事故**：2026-11-15 删除 `ModelRegistry.remove()`（手工导入模型接口的
配套方法）时，只搜了 `qt_web/api.py` 与前端 JS，漏了
`qt_web/release_gui_check.py`。后果是**单元测试 115 项全绿，打包最后一步才炸**：

```
AttributeError: 'ModelRegistry' object has no attribute 'remove'
```

（在 `release_gui_check.py` 的 `auto_discovery` 清理逻辑里）

教训有两条：

1. **打包自检脚本是「测试的死角」**：`release_gui_check.py` /
   `release_worker_check.py` 只在 `py -3.14 build_web.py` 时执行，
   `unittest discover` 完全覆盖不到。删公共方法时必须把它们算进调用方。
2. 改用**静态扫描全仓库**的守门测试兜底，而不是靠人肉记忆：
   `tests/test_external_models.py::test_no_code_calls_removed_registry_methods`
   用 `ast` 找出所有 `<registry 变量>.remove(...)` 调用。

**删方法的正确顺序**：先全局 `grep` 方法名（含 `dist/`、`tools/` 之外的
所有 `*.py`），确认零调用后再删；删完立刻补一条扫描型守门测试。

### 6.3 端到端验证优先于单元验证

实际跑一遍 `api.do_func2_select()` → 截图 → OCR，看能不能出字，
比只验证坐标换算更有说服力（曾靠它发现「能返回 bbox 但下游没反应」）。

### 6.4 不要以「看起来能跑」代替验证

- 数值类改动必须给前后对比数据（内存、耗时、像素）。
- 视觉类改动必须截图确认（遮罩是否真的铺满两块屏）。
- **动画类改动必须采样三个阶段**：起始、中间、结束各取一次快照。
  只测「点完之后是什么页」会漏掉闪帧——见 6.5。

### 6.5 动画 bug 的采样方法（KB-GOT-009）

设置页「向左切换闪一下旧页」的教训：断言只看结束状态，`end === target`
恒成立，Bug 完全测不出来。正确做法是取三帧：

```
点击 → 约 30ms 后取 start  → 约 150ms 后取 mid → 动画时长 + 缓冲后取 end
```

判据：

| 阶段 | 期望 |
| --- | --- |
| `start` | 视口里是**旧页**（不能是目标页） |
| `mid` | 是中间某一页（大步长时不是首尾） |
| `end` | 视口里是**目标页**，且 DOM 里只剩这一个 pane |

取「视口里是哪一页」用 `translateX` 反算：`Math.round(-m41 / 容器宽)`，
比读 `dataset` 更接近用户实际看到的内容。

## 7. 本机环境与测试技巧（KB-GOT-006）

| 项 | 值 |
| --- | --- |
| 主屏 | `\.\DISPLAY1`，逻辑 1536×960 @125%，物理 1920×1200，原点 (0,0) |
| 副屏 | `Zako HDR`，逻辑 960×600 @200%，物理 1920×1200，原点 (-1920,0) |
| Win32 虚拟桌面 | `-1920,0 3840×1200` |
| Qt `virtualGeometry` | `-1920,0 3456×960`（混合坐标的产物，别拿它算物理尺寸） |

技巧：

- **光标注入**：用户同时在用电脑，`SetCursorPos` 会被覆盖 →
  monkeypatch `qt_ui.selector._physical_cursor_pos` 返回指定物理坐标。
- **HDR 色彩差异**：Qt `grabWindow` 与 PIL `ImageGrab` 有稳定映射差（MAD≈20），
  比较图用**去均值相关系数**（阈值 0.85），不要用逐像素绝对差。
- **跨屏比对**：按每块屏取交集分别算相关度再加权，否则基准图只覆盖一半会误判。
- **GUI 进程**：前台运行会被超时杀掉 → 后台启动；
  PowerShell 管道截断会提前结束 python → 输出重定向到文件再读。
- **打包版对照**：`C:\Users\籽寒\APP\OCR_Reader_Web\_internal\` 是完好的旧构建，
  可用于比对前端文件原始内容。

## 8. 其他易踩点

| 坑 | 说明 |
| --- | --- |
| `SignalBridge` 信号从主线程 emit | 是直接调用而非排队，别指望它「异步化」 |
| `_CURRENT_APP` 全局 | `WebApi._mode_flags` 依赖它，窗口构造时必须赋值 |
| `apply_runtime_limits()` 晚于 import | 无效，必须在导入重依赖前调用 |
| 扫描间隔 ≤0 | `_read_scan_interval` 兜底默认值，否则忙等占满一核 |
| 扫描循环异常 | `stop_event.wait(interval)` 放在 `finally`，保证不空转 |
| TTS `dedup` 参数 | 手动触发（功能1/2）必须传 `dedup=False`，否则连点同一句会「点了没反应」 |
| 翻译接管返回 True | 调用方据此**不朗读原文**，不要在此处再发朗读 |

## 9. 打包与发布（KB-GOT-008）

入口是仓库根目录的 `build_web.py`（`build_web.ps1` 只是薄封装）：

```powershell
py -3.14 build_web.py                # 打包 + 发布版自检
py -3.14 build_web.py --skip-tests   # 只打包
```

流程固定 6 步：依赖预检 → PyInstaller 双 EXE → 复制 README/图标 →
staging（OCR 权重 / 语音模型 / CUDA 运行库）→ `verify_web_release.py` → 汇总。
产物在 `dist/Web-<yyyyMMdd-HHmmss>/OCR_Reader_Web/`，路径写入
`build/latest-web-release.txt`；失败时现场全部保留（`build.log` 等）。

关键约定：

- **权重一律外置（KB-GOT-012）**：`OCR_Reader_Web.spec` 的 `no_weights()` 挡住
  `.onnx/.pt/.gguf/...`，`build_web.py` 的 `check_weights()` 再兜一道底，
  安装包本体里不许出现任何权重。模型放在 **EXE 同级**的 `模型/`（语音、翻译）与
  `models/`（OCR / VAD / 标点 / 目录簿）下，由 `utils.paths.app_root()`
  以 `sys.executable` 为锚按相对路径解析——**不要改成 `sys.argv[0]` 或 `__file__`**，
  打包后它们指向 `_internal/`，会导致找不到模型。
- **打包只产出程序本体**：`build_web.py` 只跑 `stage_gpu_runtime.py`。
  `stage_voice_models.py` / `stage_ocr_runtime.py` 已退出打包流程（脚本保留备用）。
  需要把本机权重摆到产物旁边时用 `py -3.14 tools/sync_external_models.py <产物目录>`，
  或打包时加 `--sync-models`。
- **外置目录要排除在「本体」之外**：`check_weights()` 与
  `verify_web_release.py` 都按 `模型/`、`models/` 前缀跳过，
  否则外置方案永远无法通过检查（这两个目录本来就该有 `.pt/.onnx`）。
- **启动检测**：`WebApi._check_model_health()` 在启动 1.2 秒后检查
  `模型/语音` 与 `models/asr/{fsmn-vad,ct-punc}`，缺什么推 `models-missing`，
  前端右下角列出本机绝对路径。守门测试 `tests/test_external_models.py`
  （11 项，含 5 条反向验证）。
- **语音模型是自适应的**：`models/asr/registry.json` 只写相对路径；
  `ModelDiscovery.scan()` 会稳定两轮后自动登记用户新放进 `模型/语音/` 的模型，
  注意 `model.pt` **小于 2 MiB 会被当成假文件跳过**（`utils/model_discovery.py:47`）。
- **翻译 GGUF 永远外置**：`模型/翻译/` 由程序首次启动创建，用户自行放入。
- 构建机需要 `py -3.14`、PyInstaller、以及 `nvidia-*` 运行库包；
  `stage_gpu_runtime.py` 按**精确白名单**把 cublas / cudart / cufft / cuDNN 的
  11 个 DLL 复制到 `_internal/gpu_runtime`，缺任何一个都失败。
  **不要改回通配符**：`cudnn*64_9.dll` 会多带 5 个 OCR 用不到的库（+354 MB），
  删法、验证方法与为什么不能整删，见 `performance.md` §11（KB-PERF-011）。
  另外 `onnxruntime_providers_cuda.dll`（298 MB）由 PyInstaller 收在
  `_internal/onnxruntime/capi/`，不在 `gpu_runtime`，对账时别当成缺失。
- 自检项含：6 个页面、GPU 实际推理、OCR 推理、GGUF 外置推理、
  worker IPC 录音启停、双 EXE 依赖隔离。`release-test.json` 的 `ok=true` 才算通过。
- 2026-11-05 的改动：`stage_voice_models.py` 与 `verify_web_release.py`
  去掉 SenseVoiceSmall 硬编码，改为按实际模型自适应（此前缺该目录会直接打包失败）。
- 2026-11-05 新增自检项 `microphone_listing`：由 worker 自检报告 `input_devices`，
  要求非空。GUI 进程不加载 sounddevice，真正打开设备的永远是语音子进程。
- 2026-11-15：权重完全外置（KB-GOT-012），自检共 25 项，其中
  `voice_models_bundled` 更名为 `voice_models_external`。
- 2026-11-15：体积瘦身（KB-PERF-013），`no_weights()` 同时承担 Qt 裁剪：
  用 `_QT_KEEP` 白名单只留 22 个 Qt DLL，删掉全部 `.qm`、非中英文 `.pak`、
  devtools 资源与 `plugins/qmltooling/`。**`_QT_KEEP` 是白名单，漏一个 DLL
  就会让程序起不来**，改动后务必跑一次完整打包看 `gui` 自检项。
  体积从 4.45 GiB 降到 4.18 GiB。
- **`release-test.json` 的 `size_bytes.total` 不是最终目录大小**，
  它是打包中段统计的快照，要看真实体积请自行遍历目录（去重硬链接），
  详见 `performance.md` §13.6.4。

## 10. 仓库与 Git（KB-GOT-010）

### 10.1 基本信息

| 项 | 值 |
| --- | --- |
| 远程 | `https://github.com/ZhiHan1/OCR-reading-tool-for-disadvantaged-groups.git` |
| 分支 | `main` |
| 许可证 | GPL v3 |
| 首次提交 | 2026-11-06，159 个文件 / 26904 行 |
| 约定 | 中文 Conventional Commits、UTF-8 无 BOM、仓库内 LF |

### 10.2 哪些东西不进仓库

| 内容 | 原因 |
| --- | --- |
| `模型/翻译/*.gguf`、`模型/语音/*/` | 约 2.3 GB，GitHub 单文件上限 100 MB |
| `models/asr/*/`、`models/ocr/*.onnx` | 同上，由 `tools/download_asr_models.py` 获取 |
| `config.yaml` | 含本机热词（139 条）、模型路径、在线服务 API Key |
| `models/asr/registry.json` | 记录本机模型名，换机器就失效 |
| `dist/`、`build/`、`logs/`、`__pycache__/`、`.cache/` | 构建产物与运行时数据 |

**不要用 `git add -f` 绕过**：这会把 1.4 GB 的 GGUF 提交进历史，
即使后来删除，`.git` 体积也永久膨胀，需要 `git filter-repo` 才能清理。

### 10.3 两个本机环境坑

**（1）dubious ownership**

`D:\Project_File` 整个目录的 Owner 是旧 Windows 账户的 SID
（`S-1-5-21-3435860081-...`），与当前用户
（`S-1-5-21-3107991029-...`）不一致，Git 会拒绝一切操作并提示
`detected dubious ownership`。

```powershell
# 只对该目录加例外，不要用 --global --add safe.directory '*'
git config --global --add safe.directory 'D:/Project_File/语音朗读工具/OCR模块化项目'
```

**（2）GitHub 需要代理**

直连 `ls-remote` 会 `Recv failure: Connection was reset`。
本机系统代理为 `127.0.0.1:7890`，但 Git 不读系统代理：

```powershell
git config http.proxy "http://127.0.0.1:7890"   # 只在本仓库生效
git config https.proxy "http://127.0.0.1:7890"
```

### 10.4 提交信息不要带 BOM（KB-GOT-011）

PowerShell 的 `-Encoding utf8` **会写 BOM**（Windows PowerShell 5.1 行为），
`git commit -F` 读进去后提交信息首字符异常，`git log --format=%s`
会显示成一个不可见字符。

正确做法（任选）：

```powershell
# 方案 A：用 Python 字节级写无 BOM 文件
py -3.14 -c "from pathlib import Path; Path('msg.txt').write_bytes('feat: 标题'.encode('utf-8'))"
git commit -F msg.txt

# 方案 B：PowerShell 7 显式无 BOM
'feat: 标题' | Set-Content msg.txt -Encoding utf8NoBOM

# 方案 C：已提交但未推送，直接修正
git commit --amend -F msg.txt
```

验证：`git log -1 --format=%s | Format-Hex`，前两字节应为 `66 65`（`fe`），
不是 `EF BB BF`。

### 10.5 日常流程

```powershell
git status --short                 # 先看有没有误加模型/配置
git add -A
git commit -F msg.txt              # msg.txt 必须是 UTF-8 无 BOM
git push                           # 代理已配在仓库本地
```

README 的「快速开始」与「提交规范」是面向贡献者的说明，
改动仓库约定时两边一起更新。

