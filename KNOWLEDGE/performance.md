# 性能（PERF）

> 域编号：`KB-PERF-*`。权威原始记录：`KNOWLEDGE/archive/PERF_PLAN_2026-11-04.md`
> （含全部实测表格；2026-11-05 从项目根目录归档到这里）。
> 本文只提炼结论、取舍与复测方法；数字以该归档文件第 6 节为准。

## 1. 内存都花在哪

| 占用方 | 量级 | 能否释放 |
| --- | --- | --- |
| ASR 模型（funasr/torch，子进程） | 峰值 WS 2369 MB / 提交 2846 MB，稳态 1837 / 2216 | 只能靠进程退出（闲置 300 s 自动退） |
| 翻译 GGUF（llama.cpp） | ~1.4 GB | 可 unload，有效 |
| OCR（RapidOCR/onnxruntime，GUI 进程内） | 加载后 +207 MB 提交，卸载只回收 47 MB | 按需加载；进程内卸载无效 |
| NVIDIA 驱动 DLL（Qt 窗口合成） | 232.7 MB（映射，非独占） | 关不掉，见 §7 |
| Qt WebEngine（主进程 + 渲染进程） | 主约 196 MB 共享 + 渲染约 90 MB | 不可控，Qt WebEngine 固有 |
| BLAS 线程栈/缓冲 | 与线程数成正比 | 限制线程数 |

实测稳态（开发版，未用语音）：主进程 WS 304 MB / 提交 436 MB，
渲染进程 WS 90 MB / 提交 29 MB。

## 2. 优化总账（KB-PERF-001）

| 分项 | 做法 | 收益 | 代价 |
| --- | --- | --- | --- |
| P0-1 BLAS 限 4 线程 | `utils/env_limits.py` | import numpy 后提交内存 501→117 MB | 识别慢约 4% |
| P0-2 语音 worker 闲置退出 | 300 s 无命令即退出 | 闲置后归零（原约 2 GB） | 再用需重加载 11~14 s |
| P0-3 OCR 预热按需 | 默认 `ocr.warmup: lazy` | 启动省约 65 MB、快约 0.3 s | 首次识别多 0.8~1.2 s |
| P1-4 翻译 GGUF 闲置卸载 | `translation.idle_unload_seconds: 60` | RSS 216→49 MB | 再次翻译需重加载 |
| P2-5c 懒构造控制器 | 13 个 `@property` | 构造 1.34 s → 0.28 s；未用 OCR 时提交内存约 560→124 MB | 首次点功能多几百 ms |
| P2-5d 模型目录轮询降频 | 3 s ↔ 30 s 按页面 | 减少空转磁盘扫描 | 非相关页面的模型变动最多晚 30 s |
| 延迟导入 | `rapidocr`、`sounddevice` 挪进函数 | 启动导入 0.92 s → 更短 | 首次调用有导入开销 |
| 前端合并压缩 | **不做** | — | 保留 19 JS + 6 CSS 可读性 |

## 3. BLAS 线程（KB-PERF-002）

实测（同一台机器、同一张图、同一版模型）：

| 线程数 | 峰值内存 | 耗时 |
| --- | --- | --- |
| 16（默认） | 2863 MB | 0.245 s |
| **4（采用）** | **2089 MB** | **0.256 s** |
| 2 | — | 0.318 s（明显变慢） |

结论：4 线程是内存与延迟的拐点；2 线程省不了多少内存却慢 24%。

实现要点：

- `apply_runtime_limits()` 必须**在导入 onnxruntime / numpy 之前**调用
  （`web_entry.py`、`voice_worker_entry.py`、`qt_web/main.py`、`qt_web/voice_worker.py`）。
- 优先级：环境变量 `OCR_BLAS_THREADS` > `config.yaml` 的 `performance.blas_threads` > 默认 4。
- 设置 `OPENBLAS_NUM_THREADS` 与 `OMP_NUM_THREADS` 两个键。
- **不要动 torch 的线程数**（另有其调优，改了会牵连 ASR 推理）。

## 4. 懒构造（KB-PERF-003）

`controllers/app_controller.py`

```python
self._lazy = {}
self._build_lock = threading.RLock()      # 后台线程可能先于 UI 首次访问

def _lazy_get(self, name):
    obj = self._lazy.get(name)
    if obj is None:
        with self._build_lock:
            obj = self._lazy.get(name)     # 双重检查
            if obj is None:
                obj = getattr(self, "_make_" + name)()
                self._lazy[name] = obj
                setattr(self, "_" + name, obj)   # 便于调试查看
    return obj

@property
def func3(self):
    return self._lazy_get("func3")
```

- 对外接口不变：`app.func3` 第一次读时构造，此后同一实例。
- 生命周期方法（`shutdown()` / `stop_all()` / `reload_config()`）
  **只处理已构造对象**，不能为了遍历而实例化。
- `WebApi._mode_flags` 直接读 `_lazy`，未构造即视为 `False`
  （同理：没构造过就不可能正在扫描）。

## 5. 进程内卸载无效（KB-PERF-004）

| 模型 | 卸载方式 | 效果 |
| --- | --- | --- |
| ASR（funasr / torch） | `del model` + `gc.collect()` | 只回收约 125 MB，2 GB 权重仍在 |
| 翻译 GGUF（llama.cpp） | `model.unload()` | RSS 216→49 MB（有效） |

原因：torch 的权重常驻、CUDA/内存分配器缓存、onnxruntime arena 都不会随对象释放。
所以 ASR 只能**结束进程**；GGUF 走的是 C++ 侧显式 `unload`，所以能回收。

**推论**：以后想省 ASR 内存，优先考虑「换更小的模型」或「闲置退进程」，
不要再尝试进程内手动卸载。

## 6. 按需加载与轮询节流（KB-PERF-005）

- **OCR**：`_warmup_ocr()` 读 `ocr.warmup`，`startup` 才在启动时后台加载；
  默认 `lazy`，首次识别才建引擎（`core/ocr_engine.py::_get_ocr` 内延迟导入 rapidocr）。
- **模型目录轮询**：`WebApi._model_scan_timer` 固定 3 s 触发，
  `_set_model_scan_active(bool)` 决定本次是否真的扫描：
  语音页/设置页 3 s，其它页 30 s。`NavBridge` 暴露为 QWebChannel 的 `nav` 对象，
  前端 `router.js` 切页时调用，`bridge.js` 连接时上报当前页。
- **扫描节流**（功能3/漫画/游戏）：`utils/screenshot.py::frame_signature` 把画面缩成
  128×32 灰度指纹，画面没变直接跳过 OCR，`ocr.skip_unchanged` 开关控制。

## 7. 显卡渲染与内存（KB-PERF-007）

本机有**外接 RTX 3060 Ti + 核显 AMD 780M**，Qt 默认选中 3060 Ti 驱动窗口。

| 方案 | 主进程 WS | 主进程提交 | NVIDIA DLL | 闲置 CPU | 结论 |
| --- | --- | --- | --- | --- | --- |
| 默认（D3D11 / NVIDIA） | 304 MB | 436 MB | 232.7 MB | 32% | **采用** |
| `QSG_RHI_PREFER_SOFTWARE_RENDERER=1`（WARP） | 311 MB | 266 MB | 0 | **114%** | 不可用 |
| `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu-compositing` | 307 MB | 346 MB | 1.0 MB | **100%** | 不可用 |
| `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` | 300 MB | 317 MB | 191.3 MB | — | 不彻底 |
| `QT_OPENGL=software` | 306 MB | 438 MB | 232.7 MB | — | **完全无效** |
| `QSG_RHI_BACKEND=opengl` | 426 MB | 624 MB | 285.2 MB | — | **更差** |

关键结论：

- **`QT_OPENGL` 在 Qt 6 上无效**：Windows 下 Qt Quick 默认走 D3D11（RHI），
  根本不经过 OpenGL。Qt 6.5.1 起 WebEngine 也改用 D3D11 默认后端。
- **软件渲染省内存但烧 CPU**：WARP 让 NVIDIA DLL 归零、提交省 170 MB，
  代价是闲置 CPU 从 32% 涨到 114%（多占一个核）。**不划算**。
- **NVIDIA DLL 关不干净**：`--disable-gpu` 后仍有 191 MB 常驻，因为 Qt 自身
  窗口合成也走 D3D11。
- 用 `QSG_INFO=1` 可看到 Qt 选中的适配器（`using this adapter`）。

外接显卡断开时的已知风险与处置：

- 显卡拔掉/切换后 D3D11 设备丢失，WebEngine 可能黑屏；Qt 会尝试自动回退软件渲染，
  但官方文档明确「自动回退不总是可行」。
- 打包产物 `_internal/` 里**没有** `d3dcompiler_47.dll`（系统目录有）。
  分发到缺该 DLL 的机器会黑屏，发布前应补上。
- 检测脚本/配置改动前先读本节，不要重复试错。

## 8. 测量方法（KB-PERF-006）

```powershell
# 内存（WS + 提交 + 峰值，支持 watch/json/dev/exe）
py -3.14 tools/mem_report.py --dev
py -3.14 tools/mem_report.py --dev --watch 2
py -3.14 tools/mem_report.py --exe

# GUI 冒烟：真实窗口 + 6 页面 + 状态 + 轮询降频 + 选区 + OCR + TTS + 翻译
py -3.14 -m tools.smoke_gui

# 回归
py -3.14 -m unittest tests.test_selector tests.test_asr_quality -v
```

要点：

- 内存看两个数：**WS（任务管理器「内存」）** 与 **提交（「提交大小」，决定系统还能不能再分配）**。
- **不要用 `psutil.memory_info().private`**：psutil 7.x 上它等于 `vms`（虚拟地址空间），
  会随启动预读一路涨，看着像泄漏其实不是。`tools/mem_report.py` 已改用 Win32
  `GetProcessMemoryInfo`。测量时至少稳定采样两次（间隔 3 s），差值 <5 MB 才算稳定。
- `QtWebEngineProcess` 是 GUI 的子进程，**统计内存时必须算进去**，
  按进程名过滤会漏掉 90~150 MB。
- 判定闲置退出生效：先把 `asr.idle_exit_seconds` 打桩成 3，观察子进程约 3.5 s 后 `rc=0` 退出；
  期间持续发命令时不能误退。实测默认 300 s 时约 332 s 退出，2216 MB 完整归还。
- 判定懒构造生效：未点 OCR 时 `mem_report` 的提交应约 124 MB；
  点过 OCR 后约 212 MB。
- 打包版验证：`py -3.14 tools/verify_web_release.py <dist>`。

## 9. 磁盘占用与模型裁剪（KB-PERF-008）

### 9.1 占用分布（2026-11-04 实测，清理后）

项目总计 **2328.5 MB**，其中 99% 是模型权重：

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| `模型/翻译/HY-MT1.5-1.8B-Q6_K.gguf` | 1406.5 MB | 翻译，**腾讯官方 Q6_K 原版**（Q5_K_M 实测后弃用，见 §9.5） |
| `模型/语音/paraformer-large/model.pt` | 859.0 MB | ASR 主模型（fp32，不可动） |
| `models/asr/ct-punc/model.pt` | **20.3 MB** | 标点模型（已裁剪） |
| `models/ocr/*.onnx` | 30.4 MB | OCR 三件套 |
| 其余全部（代码/日志/缓存） | < 2.5 MB | — |

累计从起始 2824.7 MB 降到 2328.5 MB（**省 496.2 MB**，见 §9.6）。

### 9.2 ct-punc 裁剪（已实施，282.47 → 20.37 MB）

**为什么不是量化**：model.pt 里 `embed.weight` 一项就占 266.3 MB（95.6%），
其余网络层只有 12.1 MB。量化嵌入表会让近义词向量混淆，而标点模型靠词向量
判断断句位置，**风险不可控**。只量化计算层则仅省 8.7 MB（3%），没有意义。

**做法**：词表 272727 条里 99.3% 是英文单词（`aachen` / `aadvantage` …），
中文场景用不到。删掉这些行即**无损裁剪**，网络结构与数值完全不变。

| 项 | 前 | 后 |
| --- | --- | --- |
| 词条 | 272727 | 8392（保留 3.1%） |
| model.pt | 278.45 MB | 20.31 MB |
| 标点精度 | 22 句样例逐字一致 | 同左 |

工具：`tools/trim_punc_vocab.py`（支持 `--dry-run` / `--out` / `--inplace`）。

**备份已删除**（2026-11-04）。回滚方法：从 ModelScope
`iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` 拉回 278.5 MB 原始模型，
放进 `models/asr/ct-punc/` 后重新执行脚本。

**能力边界**：裁剪版只适合中文/中英混排。若要整段英文标点，须换回原版。

**测试陷阱**：必须喂**无标点纯文本**（真实 ASR 输出格式）。直接喂带标点的文本
会出现 `怎么办？？`、`牌号，，性能` 等重复标点，容易被误判为模型缺陷。

### 9.3 OCR 模型不需要量化

`models/ocr` 只有 30.4 MB，且本来就是 PP-OCRv6 的 **small** 版。
量化最多省 21 MB（占项目总量 0.8%），却可能把形近字认错（`未`/`末`、`己`/`已`）。
**结论：不做。**

### 9.4 已删除的实验产物

`模型/语音/paraformer-large-onnx/`（234.1 MB）——INT8 量化验证的中间产物，
业务代码不引用，仅 `tools/` 下验证脚本使用。实验 JSON 已归档到
`KNOWLEDGE/artifacts/paraformer-onnx-2026-11-04/`，需要复现时用
`tools/export_onnx_probe.py` 重新导出。

### 9.5 翻译模型 Q5_K_M 实测：**弃用**（2026-11-04）

腾讯官方 `tencent/HY-MT1.5-1.8B-GGUF` 只发布三档：Q4_K_M（1080.6 MB）、
Q6_K（1406.5 MB）、Q8_0（1820.1 MB），**没有 Q5_K_M**，需要自己量化。

#### 实测数据

| 项 | 值 |
| --- | --- |
| 量化源 | 官方 Q8_0（8.50 BPW） |
| 产物 | Q5_K_M（5.78 BPW） |
| 体积 | 1238.6 MB，**只省 168 MB（-11.9%）** |
| 量化耗时 | 17 s（16 线程） |

**估算修正**：先前按「Q5_K_M ≈ Q6_K × 80%」估算省 281 MB，**高估了 113 MB**。
Q6_K 已是 6.56 BPW，压到 5.78 BPW 只剩边际收益；从 Q8_0 出发的 68% 比例
不能直接套到 Q6_K 基线上。

#### A/B 质量对比：9/16 完全一致，弃用原因

用 `tools/compare_translation_models.py`（16 句，中英日互译 + 铜材术语，
完全复刻产品推理参数）对比，Q5_K_M 在三处明显变差：

| 原文 | Q6_K | Q5_K_M | 问题 |
| --- | --- | --- | --- |
| 无氧铜**杆**的含氧量…（英译中回译） | 无氧铜**棒** | 无氧铜**线** | **行业术语错**（杆≠线） |
| 无氧铜杆→英 | `the无氧铜杆` | `the无氧 copper rod` | **漏译「杆」** |
| 请把…发给我→日 | 正确 | `どうぞ、…` | **多余语气词** |

只省 168 MB（项目总量 -6.4%）却让铜材术语准确度下降，**不划算，保留 Q6_K**。

#### 顺带发现的模型固有缺陷（与量化无关）

HY-MT1.5-1.8B 在两个档位上**都**译错：

- 「拉丝机」→ `ラージングマシン`（Q6_K，音译照搬）/ `ラチェットマシン`（Q5，意为棘轮）。
  正确应为 `伸線機`。
- 「无氧铜杆」在英译时原样输出中文 `无氧铜杆`。

说明该模型对铜材冶炼术语覆盖弱。若要做好，应使用 HY-MT 官方的
**术语干预模板**（`参考下面的翻译：{source_term} 翻译成 {target_term}`），
而不是靠换量化档位。

#### 踩过的坑（量化工具链）

1. **不要用 `llama_cpp.llama_model_quantize()`**（Python 包自带的 C API）。
   llama-cpp-python 0.3.36 的预编译库在 Windows 上必然报
   `ios_base::failbit set: iostream stream error`，连 `only_copy=True`
   （只复制、不量化）也失败。已排除路径中文、输出目录缺失、磁盘空间等因素。
2. 改用官方 `llama-quantize.exe`（b12098）。GitHub 直连不通时用
   `gh-proxy.com` 代理下载 release 压缩包（winget 走 GitHub 也会失败）。
3. **从 Q8_0 量化要加 `--allow-requantize`**，否则报
   `requantizing from type q8_0 is disabled`。Q8_0 虽属量化格式，但 8-bit
   舍入近乎无损，作为源比 Q6_K 干净（避免两层量化误差叠加）。
4. `llama-quantize` 不会创建输出目录。

工具：`tools/quantize_gguf.py`（统一封装，只调官方 exe）。

### 9.6 磁盘清理总账（2026-11-04）

| 项 | 节省 | 精度影响 |
| --- | --- | --- |
| 删除 paraformer-large-onnx 验证产物 | 234.1 MB | 无 |
| ct-punc 词表裁剪（282.47→20.37） | 262.1 MB | 无（22 句逐字一致） |
| 删除 ct-punc 原始备份 | 278.5 MB | 无（可重新下载） |
| **合计：2824.7 → 2328.5 MB** | **496.2 MB（-17.6%）** | — |

本轮翻译量化的临时产物（Q8_0 源 1820.1 MB + Q5_K_M 产物 1238.6 MB，共 3058.7 MB）
是在项目目录内产生后又删除的，**不计入上表**，但操作期间峰值占用曾达 5669.6 MB。

**不可再压的部分**：翻译 Q6_K 1406.5 MB + paraformer 859.0 MB = 2265.5 MB，
占总量 97.3%。两者都已是「质量/体积」的合理折中点：翻译再压会错术语
（§9.5 已实测），ASR 是 fp32 主模型（INT8 量化虽可省 625 MB，但需重做 ONNX 流程，
且验证样本仅 6/6，不足以支撑长期结论）。

## 10. 代码精简：删除 legacy 与归档一次性文档（KB-PERF-009）

> 日期：2026-11-05。改动前全量备份在
> `D:\Project_File\语音朗读工具\OCR_备份_20261105-000103`（172 文件 / 1.8 MB，项目外）。
> 本节只讲**代码行数**，磁盘模型占用见 §9。

### 10.1 规模变化

| 项 | 精简前 | 精简后 | 变化 |
| --- | --- | --- | --- |
| `.py` 文件数 | 109 | 90 | −19 |
| 总行数 | 22740 | 约 14700 | **−8040（−35.4%）** |
| 根目录 `.md` | 6 | 1（`WEB_RELEASE.md`）+ `CONFIGURATION.md` | 4 份归档 |

### 10.2 删掉的死代码：整个 `legacy/`

`legacy/` 是旧 tkinter 界面的归档区（15 个 `.py` / 8145 行 / 363 KB）。
确认无运行时依赖后整体删除：

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `main_modular_win11.py` | 3494 | 旧 tkinter 主界面 |
| `qt_main.py` | 1265 | 旧 Qt 入口 |
| `ui/mixins/*`（9 个） | 2861 | 旧 UI 混入类 |
| `ui/selector.py` | 241 | tkinter 选区器（唯一曾有引用的文件） |
| `ui/animated_switch.py` / `dialog.py` | 283 | 旧控件 |

**删除前必须做的一步**：`core/screenshot_service.py` 原先在「未注入选区器」时
惰性 `from legacy.ui.selector import RegionSelector` 兜底。实际命令行永远走不到
（`qt_web/main.py:328` 与 `qt_ui/selector_adapter.py` 都主动注入 `QtSelectorAdapter`），
现已改为直接提示「未注入选区实现，请通过 selector 参数传入 QtSelectorAdapter」并返回 `None`，
**注入接口 `ScreenshotService.selector` 未变**，调用方无需改动。

删除后验证：

| 验证项 | 结果 |
| --- | --- |
| 全项目搜 `from legacy` / `import legacy` | 0 处 |
| `py -3.14 -m unittest tests.test_hotwords tests.test_selector tests.test_asr_quality -v` | 24/24 OK |
| `tools/smoke_gui.py` 的 `screenshot_selector` 步骤 | `["QtSelectorAdapter", true]`，ok |
| `tools/verify_web_release.py` 的 `legacy_main_excluded` / `no_legacy_main_modules` | 检查项仍存在且语义成立（这两个名字指旧入口，不再指目录） |

### 10.3 根目录三个一次性下载脚本合并

`_download_asr_models.py` / `_download_asr_models_hf.py` / `_download_asr_retry.py`
（154 行）是同一件事的三个版本，已合并为 `tools/download_asr_models.py`：

```
py -3.14 tools/download_asr_models.py --list          # 看目标清单
py -3.14 tools/download_asr_models.py                 # 默认 ModelScope
py -3.14 tools/download_asr_models.py --source hf     # hf-mirror 镜像
py -3.14 tools/download_asr_models.py --only ct-punc  # 单拉某个模型
```

目录已改为项目实际使用的 `模型/语音/`（原脚本写的是 `models/asr`，与产品不符）。
重装或换机时用它把 1.8 GB 权重拉回来；ct-punc 拉回的是官方 272727 词原版，
要恢复裁剪后的发布版需再跑 `tools/trim_punc_vocab.py`（见 §9.2）。

### 10.4 归档到 `KNOWLEDGE/archive/`

| 原位置 | 归档名 | 理由 |
| --- | --- | --- |
| `PERF_PLAN.md` | `PERF_PLAN_2026-11-04.md` | 内容已提炼进本文件，原文只当原始数据留档 |
| `PROMPT_OCR_READER_KB.md` | `PROMPT_OCR_READER_KB_根目录旧副本.md` | 与 `KNOWLEDGE/PROMPT.md` 重复，后者为唯一有效副本 |
| `QT_MIGRATION.md` | `QT_MIGRATION.md` | Qt 迁移已完成 |
| `Logo图标最终修复方案.md` | `Logo图标最终修复方案_2026.md` | 一次性修复记录 |

连带修正的引用：`performance.md` 表头、`KNOWLEDGE/INDEX.md` §1、
`KNOWLEDGE/PROMPT.md` 的目录树与检索顺序。

### 10.5 排查过但**不删**的「无 import」模块

静态扫「谁 import 了我」会把这批标成死代码，逐一核实后全部属于**在用**：

| 模块 | 真实入口 |
| --- | --- |
| `tools/stage_ocr_runtime.py` / `stage_voice_models.py` / `stage_gpu_runtime.py` | `build_web.ps1:29/31/33` 命令行调用 |
| `pyinstaller_hooks/hook-*.py` | PyInstaller 按文件名自动加载，不由 import 触发 |
| `qt_web/release_check.py` / `release_gui_check.py` / `release_worker_check.py` | 打包版 `--self-test` 显式调用 |
| `core/mp3_encoder.py` / `core/sapi_wav.py` | 旧 TTS 导出链路；Web 版已改走 `qt_web/tts_export.py`（内联 lameenc/SpVoice），**当前无调用方**——留着是因为 SAPI 离线合成逻辑只此一份，属「功能实现，UI 未接」 |
| `utils/hotkey.py` | 旧 tkinter 版热键；Web 版用 `keyboard` 直接注册（`qt_web/main.py:516`），当前无调用方 |
| `qt_web/web/_serve.py` | 前端本地预览用的小 http server（无缓存头），开发辅助 |
| `tools/export_qwebchannel.py` | 生成 `qt_web/web/js/vendor/qwebchannel.js`（已生成在仓库里），换 Qt 版本时才需重跑 |
| `tools/split_reference_css.py` | 原读 `legacy/ui/ui_template.html`；该文件已随 `legacy/` 删除，**工具暂不可用**。CSS 产物 `qt_web/web/css/*.css` 已在仓库内，产物不受影响 |

`core/mp3_encoder.py`、`core/sapi_wav.py`、`utils/hotkey.py` 三份若确认不再要，
可再删 263 行；本轮**按「不确定就留」处理**，因为删除会丢功能实现而不只是省行数。

### 10.6 复测方法

```
# 行数盘点（排除 __pycache__ / _internal / dist / build）
Get-ChildItem . -Recurse -Include *.py | ? { $_.FullName -notmatch "__pycache__|_internal|dist|build" } |
  % { (Get-Content $_.FullName -Encoding UTF8 | Measure-Object -Line).Lines } | Measure-Object -Sum

py -3.14 -m unittest tests.test_hotwords tests.test_selector tests.test_asr_quality -v
$env:QT_QPA_PLATFORM="offscreen"; py -3.14 tools/smoke_gui.py
```

### 10.7 UI 层由 3 套精简为 1 套（同日补充）

删除前项目里有 **3 套 UI 源码**，其中 2 套的入口早已不存在：

| # | UI | 入口 | spec | 构建脚本 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | tkinter 版 | `main_modular_win11.py` | `OCR_Reader.spec` | `build_one_click.ps1` | ❌ 入口已随 `legacy/` 删除 |
| 2 | Qt 原生版 | `qt_main.py` | `OCR_Reader_Qt.spec` | `build_qt.ps1` | ❌ 同上 |
| 3 | **Web 版** | `web_entry.py` | `OCR_Reader_Web.spec` | `build_web.ps1` | ✅ 唯一在用 |

# 同批删除（共 1739 行）

| 文件 | 行数 | 理由 |
| --- | --- | --- |
| `qt_ui/settings_panel.py` | 1240 | 旧 Qt 设置面板，Web 设置页已取代 |
| `qt_ui/widgets.py` | 129 | 只被 `settings_panel` 用 |
| `qt_ui/theme.py` | 40 | 只被上面两个用 |
| `OCR_Reader.spec` | 124 | `MAIN_SCRIPT` 指向已删除的 `main_modular_win11.py` |
| `OCR_Reader_Qt.spec` | 86 | `MAIN_SCRIPT` 指向已删除的 `qt_main.py` |
| `build_qt.ps1` | 10 | 只构建上一条的 spec |
| `build_one_click.ps1` | 110 | 只构建 `OCR_Reader.spec` |

**一个真 bug（已修）**：`qt_web/main.py` 曾有一行

```python
from qt_ui.settings_panel import HOTKEY_DEFAULTS
```

全文件 674 行里**再无第二次出现**。为了一个从未使用的常量，把 1240 行的旧设置面板
连同 `widgets` / `theme`（合计 1409 行）加载进内存。已删除该导入。

**构建脚本现状（2026-11-05 更新）**：已按用户要求补上 `build_web.py`
（`build_web.ps1` 变薄封装），流程与坑见 KB-GOT-008（gotchas.md §9）。
遗留待清理项：`tools/verify_web_release.py:114` 与 `OCR_Reader_Web.spec:9` 仍写着
`qt_main` / `main_modular_win11` 的名字（作为 exclude 名单，不会报错，属无害冗余）；
`qt_web/release_check.py` 是无人引用的旧副本（实际生效的是
`release_gui_check.py` + `release_worker_check.py`）。

### 10.8 `smoke_gui` 的 `pages.radios` 曾长期假阴性（已修）
`tools/smoke_gui.py` 原来用一个同步 IIFE 连续 `A.router.go(n)`，最后统计
`[name=asr-input-mode]` 的数量，断言 `===3`。实测总是 `false`，但**不是前端 bug**：

```js
// qt_web/web/js/router.js router.go()
if (prev && animate) animateLeave(run);   // 退场动画走 setTimeout(…, 200/160ms)
else run();                                // 只有首次进入才同步
```

`router.go()` 在非首次进入时**异步提交**，同一段 JS 里紧接着查询，DOM 还是旧页面。
诊断脚本实测：导航后立刻查 `0`，隔一轮事件循环再查 `3`。

**修法**（`tools/smoke_gui.py::do_pages`）：每一页都传 `{animate:false}`（同步提交），
最后一页额外调 `A.router.refresh()`（同步重绘）再统计。修后 `radios: true`。

> ⚠️ 以后写前端冒烟脚本时，**不要在同一段 JS 里 `router.go()` 后立刻断言 DOM**。
> 要么传 `{animate:false}`，要么用 `router.refresh()`，要么分两次 `runJavaScript`。

**2026-11-05 补充**：同一个假阴性在**发布版自检**里也存在——
`qt_web/release_gui_check.py`（EXE 实际调用的那个）和无人引用的
`qt_web/release_check.py` 都还写着旧的同步 IIFE，导致首次打包自检
`pages.radios=false`、`gui=false`（其余 20 项全 true）。两处已同步改为
`{animate:false}` + `router.refresh()`。教训：改冒烟脚本时，
要同时搜所有 `runJavaScript` 调用点，不要只改 `tools/smoke_gui.py`。

### 10.9 后端整体审查与压缩（KB-PERF-010，2026-11-05，第三批）

用户要求「对整个后端代码检查，看有什么可以压缩和优化」。用 pyflakes + 自写 AST
重复块扫描过了一遍 `core/` `controllers/` `utils/` `qt_ui/` `qt_web/`（约 9700 行），
结论是**代码本身质量尚可，没有大块复制粘贴**（6 行以上的重复块只有 2 处，
且都在 `release_check.py` 的测试辅助里）。

#### 实际改动

| 类别 | 内容 | 效果 |
| --- | --- | --- |
| 真 bug 修复 | `max_tokens` 的 256 硬上限（KB-TR-003） | 888 字文本从 `finish=length` 变为 `stop` |
| 真 bug 修复 | `Llama.n_ctx` 是方法不是属性 | 第一版修复会 `TypeError`，真机测出 |
| 去重 | `func1` / `func2` 抽出 `controllers/base_controller.py` | −32 行，`_speak_or_translate` 一处决策 |
| 去重 | `_read_stable_frames` 上提到 `ScanModeController` | func3 / 漫画 / 游戏三份逐字相同 → 一份 |
| 去重 | `AppController` 13 个 `@property` 生成 | 339 → 327 行，新增成员不会再漏写 |
| 去重 | `TTSManager` 两个等待循环合并为 `_wait_active` | SAPI 与在线播放共用一套轮询 |
| 去重 | `TranslationService` 4 个 `_init_*` → 一张 `_BACKENDS` 表 | 减少 4 个方法 |
| 去重 | `switch_model` / `unload_model` 的释放逻辑 → `_release_model_nonblocking` | 同一段非阻塞释放只写一次 |
| 性能 | `defaults.yaml` 解析结果 `lru_cache` | 保存配置时 `validate_config` 连调多次，不再重复解析 |
| 性能 | YAML 改用 `CSafeLoader` | config.yaml 6.17ms → 0.75ms（**7~8 倍**） |
| 清理 | 删除 20 个未使用的 import | pyflakes 从 55 条降到 8 条 |
| 清理 | 去掉 24 个无占位符的多余 `f` 前缀 | 纯文字改动，无语义变化 |

#### 有意不动的（pyflakes 剩余 8 条全部属于此类）

| 位置 | 为什么保留 |
| --- | --- |
| `asr_service.py:216` `import funasr` | 探测依赖是否安装（带 `# noqa`） |
| `mp3_encoder.py:18` `import lameenc` | 同上 |
| `sapi_wav.py:28` `import win32com.client` | 同上 |
| `text_corrector.py:70/78/79/80` | 实际是**正则表达式**（含 `\1`、`{|lI}`），不是拼字符串 |
| `main.py:248` `import PySide6.QtWebChannel` | 副作用导入：注册资源后第 354 行要用 `QWebChannel` |

#### 复测结果（全量回归）

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **41 项全通过**（原有 24 + 新增 `tests/test_backend_refactor.py` 17） |
| GUI 冒烟 `smoke_gui` | `EXIT=0`，`"ok": true`，8 个页面检查全 true |
| TTS 真实链路 | 短句发声 1.12s 自然结束；长句打断 0ms；两句串行 1.66s |
| 翻译真实链路 | 888 字：旧 `length` → 新 `stop`，实出 502 tokens |
| 配置读写 | `load_defaults` 返回独立副本；连续 100 次 `validate_config` 正常 |
| pyflakes | 55 → 8（剩余均有意保留） |
| BOM / 行尾 | 所有改动文件无 BOM，行尾保持原样 |

#### 新增守门测试 `tests/test_backend_refactor.py`

覆盖这次重构里**最容易在以后被无意破坏**的约定：

- func1/func2：翻译接管不朗读、开关关闭不朗读、朗读必须 `dedup=False`；
- `_read_stable_frames`：三个扫描子类不得再各自覆盖；取值 1~10 与非法回退；
- `AppController`：13 个成员必须仍是只读 property；
- `_max_tokens_for`：不能回到 256 上限、不得超出 `n_ctx`、`n_ctx` 方法/属性两形态；
- `load_defaults`：缓存后仍返回可自由修改的独立副本。

> ⚠️ 这次最大的教训：**单元测试用假对象能过，真机不一定能过**。`n_ctx`
> 方法 vs 属性就是典型——假对象写成属性就测过去了。凡是要碰第三方库对象属性的
> 修复，必须真机跑一次。

**变更记录**：2026-11-05 删除 `legacy/`（8145 行），合并三个下载脚本进 `tools/`，
归档 4 份根目录文档，`ScreenshotService` 去掉 tkinter 回退。
同日补充：删除旧 Qt 设置面板与两套失效 spec / 构建脚本（1739 行），
UI 层精简为 Web 版一套；修 `smoke_gui` 的 `radios` 假阴性。
第三批：后端去重与清理（见 §10.9），修 `max_tokens` 截断；全量回归 41 项测试通过。
## 11. 发布包 CUDA 运行时裁剪（KB-PERF-011）

> 日期：2026-11-15。改动前备份在
> `D:\Project_File\语音朗读工具\OCR_备份_CUDA裁剪_20261115-125402`（仅 `stage_gpu_runtime.py`；
> 被删的 DLL 都能从 pip 包 `nvidia-*` 原样恢复）。

### 11.1 先回答「CUDA 到底有没有在用」

发布包 4.80 GB 里 `_internal/gpu_runtime` 占 **2197.8 MB（45%）**，是单项最大占用，比
语音模型（867 MB）还大。看到 `torch 2.15.0+cpu`（`cuda.is_available()=False`）很容易
下「这 2.15 GB 是白占的」的结论——**这个结论是错的**。

| 组件 | 跑在哪 | 依据 |
| --- | --- | --- |
| ASR（funasr + torch） | **CPU** | `torch 2.15.0+cpu`，`torch.version.cuda is None` |
| 翻译（llama.cpp） | **CPU** | `n_gpu_layers=0`，见 `translation.md` §7 |
| OCR（onnxruntime） | **CUDA** | `onnxruntime-gpu 1.24.1`，真实模型 session 里 `CUDAExecutionProvider` 生效 |

`core/device_manager.py::_ensure_nvidia_dll_path()` 与 `qt_web/bootstrap.py` 都会把
`gpu_runtime` 塞进 `PATH` + `os.add_dll_directory()`，**只为 onnxruntime 的 CUDA
provider 服务**，和 torch 无关。所以 torch 是 CPU 版并不影响 CUDA 运行库的必要性。

**决定性证据**（不能只看 `get_available_providers()`，它只说明 DLL 注册了，不说明能跑）：

```python
# 创建 session 后必须看 get_providers()，并真的 run 一次
sess = ort.InferenceSession(model, so, providers=['CUDAExecutionProvider','CPUExecutionProvider'])
sess.run(None, {inp.name: data})
print(sess.get_providers())   # ['CUDAExecutionProvider', 'CPUExecutionProvider']
```

只请求 `['CUDAExecutionProvider']` 而缺 DLL 路径时，ORT 会**静默回退**到
`['CPUExecutionProvider']` 且不报错，必须显式检查 `get_providers()` 才知道退没退。

### 11.2 不能整体删除，但能删 354 MB

真正的判据不是「猜哪个库用不上」，而是**枚举进程内实际加载的模块**。用
`CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32)` 遍历
`MODULEENTRY32W.szExePath`，把 `gpu_runtime` 里的文件逐个对账。

结果：真实 OCR 模型一共加载 **11 个** CUDA DLL，其余 5 个从未出现。

| 保留（11 个，1843.8 MB） | 大小 | 说明 |
| --- | --- | --- |
| `cublasLt64_12.dll` | 637.7 MB | 矩阵乘 |
| `cudnn_engines_precompiled64_9.dll` | 503.7 MB | **只有真实模型才加载** |
| `cufft64_11.dll` | 273.8 MB | 卷积 FFT 路径 |
| `cudnn_ops64_9.dll` | 100.8 MB | **只有真实模型才加载** |
| `cublas64_12.dll` | 97.8 MB | 矩阵乘 |
| `cudnn_graph64_9.dll` | 95.3 MB | 计算图引擎 |
| `cudnn_heuristic64_9.dll` | 90.4 MB | **只有真实模型才加载** |
| `cudnn_engines_runtime_compiled64_9.dll` | 43.3 MB | **只有真实模型才加载** |
| `cudnn_engines_tensor_ir64_9.dll` | 0.1 MB | **只有真实模型才加载** |
| `cudart64_12.dll` | 0.6 MB | 运行时不依赖 |
| `cudnn64_9.dll` | 0.3 MB | cuDNN 分发包入口 |

| 删除（5 个，354.0 MB） | 大小 | 理由 |
| --- | --- | --- |
| `cudnn_adv64_9.dll` | 258.4 MB | RNN/LSTM 高级算子，OCR 只用 CNN |
| `nvrtc64_120_0.dll` | 85.7 MB | 运行时编译，仅 TensorRT 用；发布包不含 TensorRT provider |
| `nvrtc-builtins64_129.dll` | 6.9 MB | 同上 |
| `cudnn_cnn64_9.dll` | 2.9 MB | cuDNN 9 已拆成 engines 分包，此接口库无人加载 |
| `cudnn_ext64_9.dll` | 0.1 MB | 扩展算子，无人加载 |

**验证方式**（不是「删了看看软件能不能开」，而是「只留这 11 个，跑真实模型」）：
把 11 个文件复制到临时目录 → 该目录进 `PATH` + `add_dll_directory` →
`PP-OCRv6_det_small.onnx` / `PP-OCRv6_rec_small.onnx` /
`ch_ppocr_mobile_v2.0_cls_mobile.onnx` 三个都建 session 并 `run` 一次
→ **3/3 `MODEL_OK`，provider 仍是 `CUDAExecutionProvider`，11 个 DLL 全部加载，零误判**。

### 11.3 两个必须记住的坑

**（1）小测试模型会给出错误结论 —— 必须用真实模型验证**

第一次验证用的是自造的 4×4 `MatMul` ONNX 模型，它只触发 6 个 DLL：
`cublas / cublasLt / cudart / cudnn64 / cudnn_graph / cufft`。

按这个结果去删，会连 `cudnn_engines_precompiled`（503.7 MB）、`cudnn_ops`（100.8 MB）、
`cudnn_heuristic`（90.4 MB）、`cudnn_engines_runtime_compiled`（43.3 MB）、
`cudnn_engines_tensor_ir`（0.1 MB）一起删掉——**合计 738 MB，删完 OCR 直接崩**。
真实卷积网络才需要 cuDNN 9 的 engine 子系统，玩具模型一个 MatMul 走不到那里。

> 结论：涉及「哪些二进制是必需的」，验证用的必须是**线上真实权重**，
> 拿玩具模型跑通只能证明链路通，证明不了依赖完整。

**（2）`onnxruntime_providers_cuda.dll` 不在 `gpu_runtime` 里**

它在 `_internal/onnxruntime/capi/`，298.1 MB，由 PyInstaller 的 hook 自动收集，
`stage_gpu_runtime.py` 不管它。清单对账时如果把它当成「缺失」，会误以为打包漏了文件。
`verify_web_release.py` 也单独有一项 `cuda_provider` 检查它存在、
且**不存在** `onnxruntime_providers_tensorrt.dll`（没有 TensorRT 是 `nvrtc*` 可删的前提）。

### 11.4 构建脚本改成白名单

根因在 `tools/stage_gpu_runtime.py`：原来 cuDNN 用的是通配符 `cudnn*64_9.dll`，
把 pip 包 `nvidia-cudnn-cu12` 里的**所有** DLL 全抄进 `gpu_runtime`，包括那 5 个
用不到的。只删磁盘上的文件治标不治本，下次打包又会带回来。

已改成显式白名单：

- `REQUIRED`：包名 → **精确文件名列表**（不再有通配符），缺任何一个直接 `RuntimeError`；
- `EXCLUDED`：记录 5 个明确排除的文件及理由，将来 pip 包升级出现同名文件也不会被误收；
- 写出的 `gpu-runtime-manifest.json` 只含 11 条，`verify_web_release.py` 的
  `gpu_runtime_assets` 逐条校验大小，清单与磁盘不一致就打包失败。

改动后核对：脚本想要 11 个，磁盘上正好 11 个，**缺失 0、多余 0、命中 `EXCLUDED` 0**。

### 11.5 结果与后续

| 项 | 前 | 后 |
| --- | --- | --- |
| `gpu_runtime` | 2197.8 MB（16 文件） | **1843.8 MB（11 文件）** |
| 发布包总量 | 4.80 GB | **约 4.55 GB** |

**还能再小的部分（均未做，需要先决策）**：

| 候选 | 可省 | 拦路点 |
| --- | --- | --- |
| Paraformer 转 INT8 ONNX | 约 0.5 GB | 会丢热词，且 6/6 样本不足以定论（见 §8） |
| 移除 `cv2` | 111.8 MB | 需确认 OCR 预处理是否真用 |
| 移除 `sklearn` / `llvmlite` | 约 126 MB | 需确认是否只是 funasr 的间接依赖 |
| 移除 PySide6 的 qml/translations 等 | 数百 MB | 需逐目录试删，Qt 插件缺失会静默失效 |

**变更记录**：2026-11-15 CUDA 运行时白名单化，删 5 个未加载 DLL（354.0 MB），
真实 OCR 模型 3/3 复验通过；`tools/stage_gpu_runtime.py` 改精确清单。

---

## 12. 模型目录体检与冗余清单（KB-PERF-012）

`tools/model_audit.py` 只读扫描，给出「必需 / 多余 / 重复 / 缺失」四类结果：

```powershell
py -3.14 tools\model_audit.py            # 项目根目录
py -3.14 tools\model_audit.py dist\Web-xxxx\OCR_Reader_Web   # 发布目录
```

2026-11-15 在本机项目根目录的实测结果：

| 类别 | 内容 | 体积 | 处置 |
| --- | --- | --- | --- |
| 必需 | `模型/语音/paraformer-large`、`models/asr/{fsmn-vad,ct-punc}`、`models/ocr/*.onnx`、`模型/翻译/*.gguf` | 2325.8 MB | 保留 |
| 多余 | `模型/语音/paraformer-large/.cache`、`models/asr/fsmn-vad/.cache`（HuggingFace 下载残留） | ≈0（12+6 个元数据文件） | **已删**（2026-11-15） |
| 多余 | `models/asr/registry.json.bak` | 298 B | **已删**（指向已不存在的 SenseVoiceSmall） |
| 空目录 | `models/LocalSubs/`、`models/Qwen2.5/` | 0 | **已删** |
| 重复 | `models/ocr/*.onnx` 与 `site-packages/rapidocr/models/` 同名同大小 | 30.3 MB | **保留，不要删** |
| 状态文件 | `models/asr/registry.json`、`models/asr/hotword_candidates.json`、`models/local/models_config.json` | < 2 KB | 运行时生成，不必分发 |

**为什么 `models/ocr` 不能删（重要纠正）**：`OCR_Reader_Web.spec` 的 `no_weights()`
会剥掉 `rapidocr/models/`，所以**发布版里没有包内副本**，`models/ocr` 是唯一来源。
曾经一度让 `core/ocr_engine.py` 回退到包内副本，那会「开发机通过、发布版崩」，
已改回只认外置目录，缺文件直接抛 `FileNotFoundError` 并提示放到 `程序目录/models/ocr/`。
守门测试：`test_falls_back_to_external_models_folder`、`test_missing_models_raise_clear_error`。

### 冗余代码候选（粗筛结果，未删除）

结构化粗筛只能定「零文本引用」，本项目有两类动态引用，必须人工排除：

- `do_<action>`：唯一派发点是 `qt_web/api.py` 的 `getattr(self, "do_" + action)`
- `_make_<name>`：`controllers/app_controller.py` 的 `getattr(self, "_make_" + name)`，
  name 来自 `LAZY_MEMBERS`

排除这两类、并与 `qt_web/web/**.js|html` 交叉比对后，2026-11-15 的 5 个死接口已全部删除：
`do_hotwords_clear_candidates`、`do_set_ocr_device`、`do_save_config`（见下方清单），
以及两个语音模型手工接口（见下一节）。前端共调用 52 个动作，后端 handler 从 59 降到 54。

### 2026-11-15 已删除的语音模型手工接口

`do_asr_import_model` / `do_asr_remove_model` 原本是「手工登记模型路径」的入口
（弹选目录对话框 → `registry.add()` → 写 `config.yaml` 的 `asr.local_model`）。
它们**前端从未被调用**：语音页只有「识别模型」下拉框，占位文案写着「请导入本地模型目录」，
却从来没有导入按钮——是孤立接口。

功能并未丢失：`ModelRegistry.scan()` 扫描 `模型/语音/` 下含 `config.yaml` + `model.pt`
的完整模型目录，稳定两轮后自动登记；`WebApi.__init__` 启动即扫，
`_model_scan_timer` 每 3s/30s 轮询。所以「把文件夹放进去就能用」已经覆盖了原设计意图。

连带删除：`ModelRegistry.remove()`（只被移除接口调用）、`bridge.js` 里 `asr_import_model`
的超时豁免条件。`do_pick_folder` **保留**（`tts.js:196` 仍在用）。

前端文案同步改为「把语音模型文件夹放进程序目录的 模型/语音/ 下，会自动识别」。
守门测试：`tests/test_external_models.py::AsrModelInterfaceTest`（5 项，
已做反向验证——还原旧代码后其中 3 项 FAIL）。

### 2026-11-15 已删除的其余死接口与残留模块

| 项 | 原用途 | 为什么废 |
| --- | --- | --- |
| `do_hotwords_clear_candidates` | 清空热词候选池 | 前端从无按钮调用；用户只会「接受 / 忽略」单条 |
| `do_set_ocr_device` | 切 OCR 的 cpu/cuda | 设备由 `core/device_manager.py` 自动选，前端不提供切换 |
| `do_save_config` | 保存配置并立即生效 | 设置页统一走 `do_set_config`（逐项保存），批量保存没人用 |
| `utils/hotkey.py` | 旧版全局热键实现（134 行） | 全仓库零导入，热键实际由 `qt_web/main.py` 用 `keyboard` 库实现 |

**只删 handler，不删底层能力**：`core/hotword_learning.clear_candidates()` 仍被
`tests/test_hotword_learning.py:131` 直接测试，予以保留；
`ocr_service.reload_device()` 与 `_apply_current()` 都还有别的调用方。

### 2026-11-15 已删除的死代码（共 364 行 + 2 个文件）

用 `ast` 定位函数边界、按 UTF-8 无 BOM 字节级写回，每项先经引用核查确认
「仅定义、零引用」，删除后全量测试 99 项通过、`tools/smoke_gui.py` ok。

| 文件 | 删除内容 |
| --- | --- |
| `utils/screenshot.py` | `grab_all_screens()` |
| `core/device_manager.py` | `get_ocr_device()`、`clear_cache()` |
| `core/audio_player.py` | `estimate_audio_duration()` |
| `core/edge_tts_backend.py` | `is_chinese_voice()` |
| `core/history_manager.py` | `get_all_texts()`、`_find_common_prefix_length()` |
| `core/hotword_learning.py` | `phonetic_variants()`、`mine_from_existing()` |
| `core/model_manager.py` | `get_model_path()` |
| `core/ocr_engine.py` | `_get_center_y_x()` |
| `core/tts_service.py` | `is_edge_available()` |
| `core/sapi_wav.py` | `synthesize_wav()` |
| `controllers/scan_mode_controller.py` | `toggle()` |
| `qt_web/release_check.py` | 整文件（入口已切到 `release_gui_check` / `release_worker_check`） |
| `pyinstaller_hooks/hook-customtkinter.py` | 整文件（无任何 `import customtkinter`） |
| `requirements.txt` | 移除 `customtkinter`、`pynput`（后者实际用 ctypes `SendInput`） |
| `OCR_Reader_Web.spec` | excludes 移除 `customtkinter` |

**`utils/hotkey.py` 未删但已是死代码**：全仓库无任何导入，热键实际由
`qt_web/main.py` 用 `keyboard` 库实现。保留是因为它无副作用；要删可整体移除 134 行。

**不要删 `translate_batch`**：`core/translation_service.py:339` 的版本被
`:358` 真实调用，各模型的 `translate_batch` 是基类抽象方法体系的一部分。

备份：`.cache/cleanup_backup/`（16 个原始文件，已被 `.gitignore` 覆盖）。

### 权重后缀检查的两个坑（2026-11-15 实际踩到）

1. **`.bin` 不能一刀切**：`_internal/PySide6/resources/v8_context_snapshot.bin`
   与 `_internal/chardet/models/*.bin` 都是运行库。第一版 `check_weights()` 把它们
   当成权重，结果是「装了 PySide6 就永远打包失败」。现在按路径前缀
   `NON_MODEL_BINS` 排除（`build_web.py`），`verify_web_release.py` 同步处理。
2. **`set |=` 而不是 `set +=`**：合并排除前缀时写成 `skip += {...}` 会抛
   `TypeError: unsupported operand type(s) for +=: 'set' and 'set'`，
   守门测试 `test_build_web_ignores_runtime_bins` 立刻抓到了。

## 13. 发布包体积账单与瘦身（KB-PERF-013）

**先纠正一个流传过的错误说法**：模型外置后「体积从 5.15 降到 3.6 GiB」是错的。
实测 4.45 GiB，其中**模型只占约 0.9 GiB**，从来不是主要部分。

2026-11-15 的完整体积构成（`dist/Web-20261115-143041`，4.45 GiB）：

| 项目 | 体积 | 性质 | 处置 |
| --- | --- | --- | --- |
| `gpu_runtime/`（CUDA） | 1.80 GiB | 可选 | 保留（GPU 是可选项，有显卡就该能用） |
| `PySide6/` | 449 MiB | 必需 | **已瘦身到 ~305 MiB** |
| `torch/` | 373 MiB | 必需 | 保留 |
| `onnxruntime/` | 328 MiB | 必需 | 保留（含 298 MiB 的 CUDA EP） |
| `llvmlite/` | 115 MiB | 可删 | **已排除** |
| `cv2/` | 112 MiB | 必需 | 保留 |
| 模型（外置） | ~920 MiB | 外置 | 不随包分发 |

### 13.1 CUDA 不能瞎删（实测结论）

`gpu_runtime/` 单个文件就很大：`cublasLt64_12.dll` 638 MiB、
`cudnn_engines_precompiled64_9.dll` 504 MiB、`cufft64_11.dll` 274 MiB。

曾想把 `cufft`（傅里叶变换，OCR/ASR 用不到）删掉省 274 MiB，
但实测它是**多处硬引用**：

- `onnxruntime/capi/onnxruntime_providers_cuda.dll`（CUDA EP 加载时解析导入）
- `torch/lib/torch_cpu.dll`、`torch/lib/torch_python.dll`

删了会让 GPU 推理在**运行期**崩（不是启动期，很难发现）。已写成守门测试
`test_cufft_not_stripped`，防止后来者「优化」掉。`onnxruntime_providers_cuda.dll`
同理保留。

### 13.2 torch 无法移除（实测 922 个模块）

`import funasr` 会**强制拉起 922 个 torch 模块**（`funasr` → `torch` 是硬依赖，
不是可选）。项目代码里没有任何直接 `import torch`，但这没有意义——
只要用 funasr 就必须带 torch。

### 13.3 PySide6 瘦身：按 DLL 依赖闭包裁剪

PyInstaller 的 PySide6 hook 会把**整个 Qt 安装目录**收进来。实际界面只用
`QtWebEngineWidgets` + `QtWebChannel` + `Widgets`，却连 3D、图表、PDF、
虚拟键盘、各种 Quick 控件样式都带上了。

**推导方式**（`_qt_deps.py` 的原始思路）：读每个 DLL 的 PE 导入表，
从 `qt6webenginecore` / `qt6webenginewidgets` / `qt6core` / `qt6gui` /
`qt6widgets` 出发做可达闭包。137 个 DLL 里 **114 个不可达，共 79 MiB**。

`OCR_Reader_Web.spec` 的 `no_weights()` 里用 `_QT_KEEP` 白名单保留 22 个：

```
Qt6WebEngineCore / WebEngineWidgets / WebEngineQuick / Core / Gui / Widgets /
WebChannel / Network / Qml / QmlModels / QmlMeta / QmlWorkerScript / Quick /
QuickWidgets / OpenGL / OpenGLWidgets / Positioning / PrintSupport / Svg /
Concurrent / pyside6.abi3 / opengl32sw
```

**`opengl32sw.dll` 必须留**：它不在导入表里，是 `qt6gui` 的隐式软件 OpenGL
回退，删了会让无显卡 / 远程桌面环境黑屏。这是纯静态分析抓不到的坑。

另外三项：

| 内容 | 体积 | 为什么能删 |
| --- | --- | --- |
| `translations/*.pak`（除中英文） | ~50 MiB | Chromium 本地化包，216 → 4 个（`zh-CN/zh-TW/en-US/en-GB`） |
| `translations/*.qm`（全部） | ~3 MiB | 程序**从未安装任何 `QTranslator`**，Qt 自身翻译永不加载 |
| `resources/qtwebengine_devtools_resources.pak` | 11 MiB | 开发者工具（F12）资源，发布版不开 Inspector |

合计 PySide6 从 449 MiB 降到约 305 MiB（**省 143.5 MiB**）。

### 13.4 funasr 的评测依赖不进 worker

`sklearn` / `numba` / `llvmlite` 在 funasr 里只出现在三条路径：

| 文件 | 用途 |
| --- | --- |
| `metrics/compute_eer.py` | `sklearn.metrics.roc_curve`，评测 EER |
| `models/campplus/cluster_backend.py` | 声纹聚类后端 |
| `models/sense_voice/whisper_lib/timing.py` | 语音分离分词的计时（numba） |

本项目只做 ASR 推理，三条都不走。spec 里用 `_ASR_ONLY_EXCLUDES` 排除，
**省约 127 MiB**（sklearn 12 + llvmlite 115）。

**验证方式**：用 `sys.meta_path` 装一个拦截器，在导入阶段直接 raise
ImportError 模拟 excludes，然后实测 `AutoModel` 仍能加载 `paraformer-large`
并正常推理——不是看代码猜的。

### 13.5 `cv2` 不能删（实测）

项目零直接 `import cv2`，看着像可以删，但 `RapidOCR()` **初始化时就会**
`import cv2`（rapidocr 有 10 个文件引用它）。屏蔽后实测直接抛
`ImportError`，OCR 完全不可用。已保留。

### 13.6 工具、方法与守门

| 工具 | 用途 |
| --- | --- |
| `tools/model_audit.py` | 模型目录体检（必需/多余/重复/缺失） |
| `tools/sync_external_models.py` | 把模型同步到发布目录（`--sync-models` 用） |
| `tools/stage_external_models.py` | 写 `external-models-manifest.json` |
| `tests/test_package_size.py` | 16 项静态守门，不需要跑完整打包 |

#### 13.6.1 怎么定位「包为什么这么大」

按体积倒序列出比猜测有效得多：

```python
entries = sorted(INTERNAL.iterdir(), key=lambda p: -size_of(p))
files = sorted(((f.stat().st_size, f) for f in INTERNAL.rglob('*') if f.is_file()),
               reverse=True)[:20]
```

先看顶层目录，再看单个文件 Top 20。这次正是靠它发现
`cublasLt64_12.dll` 一个文件就 638 MiB。

#### 13.6.2 判断「某个 DLL 能不能删」的正确方法

不能靠「项目代码有没有 import 它」，因为依赖链在第三方库里。三档方法，
可靠性递增：

| 方法 | 能发现 | 会漏掉 |
| --- | --- | --- |
| 看项目 `import` | 直接依赖 | 第三方间接依赖（`cv2` 被漏过） |
| 读 PE 导入表做闭包 | 静态链接依赖 | **隐式加载**（`opengl32sw` 被漏过） |
| 真实运行 + 枚举已加载模块 | 全部 | 只在特定分支才加载的（需覆盖到） |

本次三者都用上了：静态闭包砍掉 114 个 DLL，但 `opengl32sw.dll` 是
`Qt6Gui` 的**隐式**软件 OpenGL 回退（不写进导入表），纯静态分析必然误删，
所以它被单独手工加回白名单。

PE 导入表的读法（纯标准库，无需 pefile）：

```python
pe = struct.unpack_from('<I', data, 0x3C)[0]      # e_lfanew
opt = pe + 24
magic = struct.unpack_from('<H', data, opt)[0]    # 0x20B = PE32+
dd = opt + (112 if magic == 0x20B else 96)
imp_rva, _ = struct.unpack_from('<II', data, dd + 8)   # 导入表 RVA
# 再用节表把 RVA 换算成文件偏移，逐项读 name_rva 指向的 DLL 名
```

#### 13.6.3 验证「排除之后还能不能用」：meta_path 拦截器

改 spec 的 excludes 后再打包（10 分钟）才知道结果，太慢。可以在**开发环境**
里用导入钩子模拟同样的效果：

```python
class Blocker:
    BLOCKED = {'sklearn', 'numba', 'llvmlite'}
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in self.BLOCKED:
            raise ImportError(f'{fullname} 已被排除')
sys.meta_path.insert(0, Blocker())
```

然后照常 `from funasr import AutoModel` → 加载真实模型 → `generate()`。
`sklearn`/`numba`/`llvmlite` 用这个方法验证通过；`cv2` 用同样方法立刻暴露
`ImportError`，省下一次 10 分钟的打包。

#### 13.6.4 体积数字的两个陷阱

1. **`release-test.json` 的 `size_bytes.total` 不等于最终目录大小**。
   它统计的是打包中段的状态（含尚未同步的外置模型副本），本次显示
   4.48 GiB，而最终目录实际是 4.18 GiB。要看真实值就自己遍历去重：

   ```python
   seen = set()
   for f in release.rglob('*'):
       if f.is_file():
           st = f.stat()
           if (st.st_dev, st.st_ino) in seen:   # 硬链接去重
               continue
           seen.add((st.st_dev, st.st_ino))
           real += st.st_size
   ```

2. **要区分「含模型」和「不含模型」**。本次不含模型 3.28 GiB、
   含模型 4.18 GiB，讨论「程序本体多大」时必须说清是哪一个。

#### 13.6.5 实测结果

打包 `dist/Web-20261115-160022`，自检 **25/25 通过**，`stray_weights` 为空：

| 项目 | 瘦身前 | 瘦身后 |
| --- | --- | --- |
| PySide6 | 449 MiB | **305 MiB** |
| `llvmlite`/`numba`/`sklearn` | 127 MiB | **0** |
| PySide6 DLL 个数 | 137 | 28（含 VC 运行库等非 Qt 文件） |
| `translations/*.pak` | 216 个 | 4 个 |
| 总体积（含模型） | 4.45 GiB | **4.18 GiB** |
| 程序本体（不含模型） | 3.56 GiB | **3.28 GiB** |

关键自检项确认功能未受影响：`gui` 7 个页面全部渲染、
`worker_inference` 通过、**GPU 推理实测走 `CUDAExecutionProvider`**、
OCR 真跑出 `OCR TEST 123`、外置模型加载与推理正常。

#### 13.6.6 还剩什么（想再缩可以考虑）

| 方向 | 可省 | 代价 |
| --- | --- | --- |
| 去 CUDA（做 CPU 精简版） | ~2.0 GiB | GPU 推理不可用，OCR 变慢 |
| CUDA 改为首次启动下载 | ~1.8 GiB | 需联网与下载逻辑 |
| 继续啃 PySide6 | < 50 MiB | 收益递减，且 `Qt6WebEngineCore` 194 MiB 是刚性的 |

**不建议动的**：`torch`（funasr 硬依赖）、`onnxruntime`（推理后端）、
`cv2`（OCR 初始化必需）、`Qt6WebEngineCore`（整个界面就是它）。

守门测试 `tests/test_package_size.py`（16 项）静态检查 spec，不需要跑完整
打包就能发现问题：权重不进包、WebEngine 必需项保留、`opengl32sw` 保留、
3D/图表/PDF 被剔除、语言包只剩中英文、`_ASR_ONLY_EXCLUDES` 声明、
`funasr`/`torch`/`cufft`/CUDA EP 不被误删。

**反向验证**（10 种破坏方式全部被抓住）：去掉权重过滤、删 `opengl32sw` 保留项、
删 WebChannel 保留项、放行所有 Qt DLL、放行所有语言包、放行 `.qm`、
放行 devtools 资源、worker 不排除 sklearn、误把 funasr 也排除、误删 cufft。


另外 `tools/sync_external_models.py` 的 `sync()` 返回值契约是
`copied_files / copied_bytes / registry / models`，`build_web.py` 依赖它；
改返回值时 `test_sync_returns_documented_keys` 会失败。

