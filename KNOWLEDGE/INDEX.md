# 项目知识库 · 索引

> 本目录是 OCR 朗读工具项目的**长期维护知识库**，供人和 Codex 检索引用。
> 所有路径相对项目根目录；所有文件为 UTF-8（无 BOM）。
> 条目 ID 规则：`KB-<域>-<三位序号>`，域见下表。引用时写 ID + 文件 + 小节，
> 例如「见 KB-SEL-003（screenshot-selector.md §4）」。

## 一、怎么用这份知识库

**给 Codex 的开场提示词**：`KNOWLEDGE/PROMPT.md`
（2026-11-05 起唯一有效副本；原根目录 `PROMPT_OCR_READER_KB.md` 已归档到
`KNOWLEDGE/archive/PROMPT_OCR_READER_KB_根目录旧副本.md`，内容过时，勿引用）。
它给出角色、架构骨架、10 条硬约束和检索顺序；细节来本目录按 ID 查。

**检索策略**

| 想知道什么 | 先看 |
| --- | --- |
| 某个功能按钮点下去走哪条路 | `architecture.md` §4 调用链 + `backend-api.md` 对应动作 |
| 某个前端页面怎么组织 | `frontend.md` |
| 某个 `do_xxx` 干什么、返回什么 | `backend-api.md` |
| 框选 / 多屏 / 混合 DPI 问题 | `screenshot-selector.md` |
| 语音识别漏字、幻听、模式 | `asr.md` §1–8 |
| 休眠唤醒后语音「未检测到说话」/ 麦克风没声音 | `asr.md` §7.2（KB-ASR-012） |
| 发布包为什么这么大 / 想缩体积 / 哪些 DLL 能删 | `performance.md` §13（KB-PERF-013，2026-11-15 最新账单） |
| CUDA 到底有没有在用、`gpu_runtime` 里哪些文件能删 | `performance.md` §11（KB-PERF-011） |
| 要不要把模型量化成 INT8 ONNX、能省多少、会丢什么 | `asr.md` §8（KB-ASR-008/009，本机实测） |
| 识别错了专有名词，怎么加/排查热词 | `asr.md` §9.8（KB-ASR-010）+ `core/hotwords.py` |
| 业界（微信/豆包/输入法）用什么方案、我们差在哪 | `asr.md` §9（KB-ASR-007，外部调研） |
| 翻译怎么调参、为什么翻错、能不能用 GPU | `translation.md`（KB-TR-001~006） |
| 内存占用、启动速度、线程数 | `performance.md` |
| 模型放哪个文件夹、为什么不进安装包、换盘符会不会失效 | `gotchas.md` §9（KB-GOT-012） |
| 改了代码/测试「没生效」、反向验证结果自相矛盾 | `gotchas.md` §6.2.1（KB-GOT-013，先清 `__pycache__`） |
| 删了一个方法/接口，怎么确认没有漏掉的调用方 | `gotchas.md` §6.2.2（KB-GOT-014，打包自检是测试死角） |
| 反向验证脚本中途崩溃，把源文件留在了破坏状态 | `gotchas.md` §6.2.3（KB-GOT-015，必须 `try/finally` 还原） |
| 哪个 Qt DLL 能删、`opengl32sw`/`cufft`/`cv2` 为什么必须留 | `performance.md` §13（KB-PERF-013）+ `WEB_RELEASE.md` |
| 两个控制器/两个工具看着像重复，能不能合并 | `performance.md` §14（KB-PERF-014，实际合并了哪几处、哪几处不该动） |
| 「这里为什么这么写，看着像 Bug」 | `gotchas.md`（默认答案：不是 Bug） |

## 二、文档清单

| 文件 | 域 | 内容 |
| --- | --- | --- |
| [architecture.md](architecture.md) | ARC | 分层、目录职责、调用链、线程模型、启动流程 |
| [frontend.md](frontend.md) | WEB | 19 JS / 6 CSS、路由、状态、动画、交互约定 |
| [backend-api.md](backend-api.md) | API | `WebApi` 全部 `do_*`、推送通道、配置动作、模型管理 |
| [screenshot-selector.md](screenshot-selector.md) | SEL | 截图语义、多屏框选、坐标换算、踩坑记录 |
| [asr.md](asr.md) | ASR | 语音进程、VAD 分段、首字与幻觉修复、三种模式；§9 业界方案对照（外部调研） |
| [translation.md](translation.md) | TR | 翻译后端架构、官方推理参数、提示词/采样实测、`max_tokens` 截断、GPU 现状与路径 |
| [performance.md](performance.md) | PERF | 内存优化实测、懒构造、闲置退出、BLAS 线程、显卡渲染 |
| [gotchas.md](gotchas.md) | GOT | 已知取舍、历史事故、验证纪律 |
| [PROMPT.md](PROMPT.md) | — | 提示词（唯一有效副本，2026-11-05 起） |
| archive/ | — | 已归档的一次性文档：`PERF_PLAN_2026-11-04.md`、`QT_MIGRATION.md`、`Logo图标最终修复方案_2026.md`、`PROMPT_OCR_READER_KB_根目录旧副本.md`（过时，勿引用） |

## 三、条目总览

### ARC · 架构

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-ARC-001 | 分层总览（UI / 壳层 / 控制器 / 内核 / 工具） | architecture.md §2 |
| KB-ARC-002 | 目录职责对照表 | architecture.md §3 |
| KB-ARC-003 | 一次点击的完整调用链 | architecture.md §4 |
| KB-ARC-004 | 线程模型（主线程 / 扫描线程 / 语音子进程 / 翻译线程） | architecture.md §5 |
| KB-ARC-005 | 启动流程与懒构造装配 | architecture.md §6 |
| KB-ARC-006 | 结果路由（`QtResultRouter`）与翻译接管规则 | architecture.md §7 |

### WEB · 前端

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-WEB-001 | 文件清单与加载顺序 | frontend.md §2 |
| KB-WEB-002 | 页面注册与侧边栏自动生成 | frontend.md §3 |
| KB-WEB-003 | `bridge.call` / `bridge.on` 与作用域释放 | frontend.md §4 |
| KB-WEB-004 | 状态同步模型（`APP.state`） | frontend.md §5 |
| KB-WEB-005 | 路由过渡动画（退场反转 / 分两阶段弹入） | frontend.md §6 |
| KB-WEB-006 | 主题圆形扩散与设置页滚动切换 | frontend.md §7 |
| KB-WEB-007 | 版本号 `?v=` 与缓存 | frontend.md §8 |
| KB-WEB-008 | 文本可选性：只有文本框可复制（`user-select` 规则与例外） | frontend.md §10 |
| KB-WEB-009 | 设置页向左切换「闪旧页」：轨道按阅读顺序铺、视口只由 `translateX` 控制 | frontend.md §7 |

### API · 后端接口

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-API-001 | 唯一入口 `request()` 与动作分发 | backend-api.md §2 |
| KB-API-002 | 动作清单（按功能分组） | backend-api.md §3 |
| KB-API-003 | 推送通道（后端 → 前端事件） | backend-api.md §4 |
| KB-API-004 | 状态快照 `_build_state()` 字段 | backend-api.md §5 |
| KB-API-005 | 配置写入、导入导出与恢复默认（整份替换 + 热词保护 + 55 项覆盖审计） | backend-api.md §6 |
| KB-API-006 | 模型管理动作 | backend-api.md §7 |

### SEL · 截图与框选

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-SEL-001 | 坐标语义：物理像素与 `(x1,y1,x2,y2)` | screenshot-selector.md §2 |
| KB-SEL-002 | 后端线程 → 主线程的框选调度 | screenshot-selector.md §3 |
| KB-SEL-003 | 多屏遮罩：每屏一窗 + 逐屏抓图 | screenshot-selector.md §4 |
| KB-SEL-004 | 物理原点的获取（禁止推算） | screenshot-selector.md §5 |
| KB-SEL-005 | 事件循环收尾顺序 | screenshot-selector.md §6 |
| KB-SEL-006 | 框选踩坑与验证方法 | screenshot-selector.md §7 |

### ASR · 语音识别

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-ASR-001 | 语音进程架构（独立子进程 + JSON 行协议） | asr.md §2 |
| KB-ASR-002 | 音频窗口与 VAD 分段 | asr.md §3 |
| KB-ASR-003 | 首字被吞的修复（预滚） | asr.md §4 |
| KB-ASR-004 | 幻觉过滤的 6 条规则 | asr.md §5 |
| KB-ASR-005 | 三种识别模式与轮次判定 | asr.md §6 |
| KB-ASR-006 | 闲置退出与模型加载成本；UI 6 档下拉（常驻在最后）、5 秒热重载 | asr.md §7 |
| KB-ASR-007 | 业界手机语音方案对照（微信 / 豆包 / 输入法，外部调研） | asr.md §9 |
| KB-ASR-008 | INT8 ONNX 量化实测：体积、精度 6/6、特征/CIF 三个坑、会丢热词 | asr.md §8 |
| KB-ASR-009 | SenseVoice-Small 量化对比（CTC 解码、中文路径坑、体积几乎相同） | asr.md §8.10 |
| KB-ASR-010 | 热词已实施：L1 文本层（三个坑、0.80 阈值、能力边界） | asr.md §9.8 |
| KB-ASR-011 | 麦克风设备选择：按名字、四套 host API 归并、启动枚举 + 手动刷新、失败不回退 | asr.md §10 |
| KB-ASR-012 | 休眠唤醒后「未检测到说话」：PortAudio 设备表固化、电源事件重建、静音自检 | asr.md §7.2 |

### PERF · 性能

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-PERF-001 | 优化总账（做了 / 没做 / 原因） | performance.md §2 |
| KB-PERF-002 | BLAS 线程实测与取舍 | performance.md §3 |
| KB-PERF-003 | 懒构造控制器（启动 1340ms → 278ms） | performance.md §4 |
| KB-PERF-004 | 进程内卸载无效的证据 | performance.md §5 |
| KB-PERF-005 | 按需加载与轮询节流 | performance.md §6 |
| KB-PERF-006 | 测量方法（mem_report / smoke_gui） | performance.md §8 |
| KB-PERF-007 | 显卡渲染与内存（外接显卡、WARP 取舍） | performance.md §7 |
| KB-PERF-008 | 磁盘占用与 ct-punc 词表裁剪（282→20 MB）；翻译 Q5_K_M 实测弃用 | performance.md §9 |
| KB-PERF-009 | 代码精简：删 `legacy/`（−8145 行）、合并三个下载脚本、归档 4 份根目录文档；哪些「无 import」模块其实在用；UI 层 3 套→1 套（再删 1739 行）；`smoke_gui` `radios` 假阴性 | performance.md §10 |
| KB-PERF-010 | 后端整体审查：去重（base_controller、`_read_stable_frames`、13 个 property、TTS 等待循环、翻译后端表）、`CSafeLoader` 快 7~8 倍、删 20 个无用 import；8 条 pyflakes 为何保留；全量回归结果 | performance.md §10.9 |
| KB-PERF-011 | 发布包 CUDA 运行时裁剪：**不能用 torch 是 CPU 版推断 CUDA 没用**、必须用真实模型枚举已加载 DLL、删 5 个省 354 MB、`stage_gpu_runtime.py` 白名单化 | performance.md §11 |
| KB-PERF-012 | 模型目录体检：`tools/model_audit.py` 四类结果、可删的缓存/`.bak`/空目录、`models/ocr` 与 rapidocr 包内 30.3 MB 重复副本、`do_*`/`_make_*` 动态分发的粗筛陷阱 | performance.md §12 |
| KB-PERF-013 | 发布包体积账单与瘦身：CUDA 不能瞎删、torch 无法移除、PySide6 按 DLL 依赖闭包裁剪（449→305 MiB）、`opengl32sw` 必须留、`sklearn/numba/llvmlite` 可去、`cv2` 不能去 | performance.md §13 |
| KB-PERF-014 | 重复实现审查：AST 指纹 + 归一化相似度 + 名词干三种扫描、809 个函数只合了 4 组、合并时发现两处异常捕获范围不同、死代码里藏着的回调签名 bug、**9 类看似重复但不该合并**及 4 条判断标准 | performance.md §14 |

### TR · 翻译

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-TR-001 | 本地模型与官方推荐参数（HY-MT1.5-1.8B，四项取值与官方一致） | translation.md §2 |
| KB-TR-002 | 提示词模板：官方 5 类模板与产品实际使用情况；措辞差异不影响结果 | translation.md §3 |
| KB-TR-003 | 已确认缺陷：`max_tokens` 在 447 字截断（**已修复**，真机 888 字从 length→stop）、`n_ctx=2048` 余量约 1600 字、日→英用错模板、`n_gpu_layers` 硬编码 | translation.md §4 |
| KB-TR-004 | 准确度 A/B 实测：**套对话模板会让质量下降**，不要改成 `create_chat_completion` | translation.md §5 |
| KB-TR-005 | 术语固定错误清单（镀锡→镀镍等）；术语干预方案，**用户已决定暂缓** | translation.md §6 |
| KB-TR-006 | GPU 加速：本机为纯 CPU 版；官方无 CUDA wheel，仅 Vulkan；**用户已决定后续再议** | translation.md §7 |

### GOT · 坑与取舍

| ID | 标题 | 位置 |
| --- | --- | --- |
| KB-GOT-001 | 已知取舍清单（不要修） | gotchas.md §2 |
| KB-GOT-002 | 文件编码事故与字节级写入纪律 | gotchas.md §3 |
| KB-GOT-003 | 版本号与缓存 | gotchas.md §4 |
| KB-GOT-004 | 配置文件乱码，勿整文件重写 | gotchas.md §5 |
| KB-GOT-005 | 验证纪律（守门测试 + 反向验证） | gotchas.md §6 |
| KB-GOT-006 | 本机环境与测试技巧 | gotchas.md §7 |
| KB-GOT-007 | 运行中手改 `config.yaml` 会被关窗覆盖 | gotchas.md §5.1 |
| KB-GOT-008 | 打包与发布（`build_web.py`、自适应模型 staging、自检项） | gotchas.md §9 |
| KB-GOT-009 | 动画 bug 的三帧采样法（起始必须是旧页） | gotchas.md §6.5 |
| KB-GOT-010 | 仓库与 Git：远程地址、不入库清单、dubious ownership、代理 | gotchas.md §10 |
| KB-GOT-011 | 提交信息绝不能带 BOM（PowerShell `-Encoding utf8` 陷阱） | gotchas.md §10.4 |
| KB-GOT-012 | 权重完全外置：模型在 EXE 同级 `模型/`+`models/`、按 `sys.executable` 解析、打包只出程序本体、启动检测与 `models-missing` 推送 | gotchas.md §9 |
| KB-GOT-013 | 反向验证必须清 `__pycache__`：同秒 mtime 会复用坏字节码，导致「已还原却仍报错」 | gotchas.md §6.2.1 |
| KB-GOT-014 | 删方法要全局搜调用方：`release_gui_check.py` 是测试死角，单元测试全绿但打包才炸 | gotchas.md §6.2.2 |
| KB-GOT-015 | 反向验证脚本必须 `try/finally` 还原：脚本中途崩溃会把源文件留在破坏状态、PowerShell 打印中文可能抛编码错误 | gotchas.md §6.2.3 |

## 四、维护约定

1. 改代码时若改变了**契约**（动作名、返回结构、坐标语义、配置键），
   必须同步改对应条目，并在条目末尾加「变更记录」一行（日期 + 一句话）。
2. 新增条目一律追加在对应文件末尾，ID 顺延，不重排旧 ID。
3. 每条结论要能落到 `文件:行号` 或实测数据，否则标注「待验证」。
4. 本目录只放结论与方法，不放截图、日志、模型二进制。
5. 仓库：`https://github.com/ZhiHan1/OCR-reading-tool-for-disadvantaged-groups`，
   `main` 分支，GPL v3 许可。提交信息用中文 Conventional Commits，
   编码约定见 KB-GOT-010 / KB-GOT-011。

## 五、仓库信息（速查）

| 项 | 值 |
| --- | --- |
| 远程 | `https://github.com/ZhiHan1/OCR-reading-tool-for-disadvantaged-groups.git` |
| 分支 | `main` |
| 许可证 | GPL v3（`LICENSE`） |
| 面向文档 | `README.md`（功能/安装/开发约定）、`CHANGELOG.md`、`WEB_RELEASE.md`、`CONFIGURATION.md` |
| 仓库政策 | `.gitignore`（排除模型与 `config.yaml`）、`.gitattributes`（统一 LF） |
| 首次提交 | 2026-11-06 · 159 文件 · 26904 行 |

**不入库**：模型权重（2.3 GB）、`config.yaml`、`models/asr/registry.json`、
`dist/`、`build/`、`logs/`、`__pycache__/`。

**本机注意**：
- 目录属旧 SID，需 `git config --global --add safe.directory <本目录>`
- GitHub 需代理 `127.0.0.1:7890`（已配在仓库本地 `http.proxy`）
