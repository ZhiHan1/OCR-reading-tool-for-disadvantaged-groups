# 语音识别（ASR）

> 域编号：`KB-ASR-*`。核心文件：`qt_web/voice_session.py`、`qt_web/voice_worker.py`、
> `qt_web/voice_pipeline.py`、`qt_web/voice_modes.py`、`core/asr_service.py`。

## 1. 设计目标

- 麦克风采集 + VAD + 识别不能卡界面 → 全部放进**独立子进程**。
- 模型常驻很贵（1.7~2 GB），不录音时要能把内存还给系统 → **闲置退出**。
- 用户最常抱怨的两件事：**首字被吞**、**凭空多出文字（幻觉）** → 各有专门修复。

## 2. 进程架构（KB-ASR-001）

```
GUI 进程                        语音子进程（python -m qt_web.voice_worker）
VoiceSession                    main(): 读 stdin 命令行 → pipeline → stdout 回写
  _spawn(): Popen(..., -u)  ──▶  命令：{command: start|stop|load, session, ...}
  _read(): 逐行 JSON 解析    ◀──  事件：{type: recording|level|segment|result|idle|error}
```

要点：

- 协议是**一行一个 JSON**，`ensure_ascii=False`，stdout 专用于协议（`sys.stdout` 被换掉，
  日志走 stderr）。
- 子进程启动时注入 `PYTHONIOENCODING=utf-8` 和 `PYTHONPATH=项目根`，
  这是中文环境必须的（见 KB-GOT-002）。
- `VoiceSession.phase ∈ {idle, recording, ...}`，`snapshot()` 给 UI 用。
- 每次会话带 `session` 编号，迟到的 `stop` 会被丢弃，避免串场。

**GUI 进程为什么不建 ASR 服务**：`AppController.voice_input` / `asr_service`
只服务旧 tkinter 版，构造它们还会拉起麦克风与音频后端。Web 版刻意不在
`_setup_callbacks` 里触碰它们（见 `qt_web/main.py` 注释）。

## 3. 音频窗口与 VAD 分段（KB-ASR-002）

```python
class AudioWindow:            # 保留最近一段音频，按采样点索引裁剪
    append / slice(start, end) / trim(before)
```

- 采样率 16 kHz，`1 ms = 16 samples`（代码里到处出现的 `*16` / `/16` 就是它）。
- 输入设备来自 `asr.device_name`（空 = 系统默认），见 KB-ASR-011。
- FSMN-VAD 流式喂入，返回成对边界 `(start_ms, end_ms)`：
  - `start_ms >= 0` 记下 `pending_start`；
  - `end_ms >= 0` 且有 `pending_start` → `_submit(start, end)` 送去识别线程。
- 识别在独立线程 `_recognize()` 里跑，结果进 `self.completed` 队列。
- 没有 VAD 模型时**直接报错**：`缺少本地 FSMN-VAD，无法进行停顿分段`（不做静默降级）。
- 缓冲溢出 `capture.overflow` 会主动停止，避免丢字后继续产出错误结果。
- 超长无停顿语音每 480000 samples（30 秒）强制切段，但**不据此结束整轮发言**。

## 4. 首字被吞（KB-ASR-003）

**现象**：说「今天天气」只识别出「天天气」或「气」。

**原因**：FSMN-VAD 的起点判定偏晚，语音真正的起始几十~一两百毫秒
被划到 VAD 边界之外。

**修法**：提交识别时向前多留预滚。

```python
PREROLL_MS = 300
preroll = self.PREROLL_MS * 16
self.pending_start = max(0, int(start_ms * 16) - preroll)
```

300 ms 是折中：再大容易把上一段的尾音带进来，再小修不掉。

## 5. 幻觉过滤（KB-ASR-004）

`core/asr_service.py::filter_hallucination(text, duration_ms, voice_ms)`，
配合 `active_ms(audio)` 估算「真正有声」时长。

`active_ms`：按 20 ms 一帧算 RMS，阈值取 `max(0.008, 0.15 × 整段RMS)`，
统计有声帧数换算毫秒。真实说话有声占比通常 >50%，
键盘声/碰麦/呼吸这类瞬态噪声只有一两帧。

过滤规则（保守，只拦明确非语音产物）：

| # | 规则 |
| --- | --- |
| 1 | 没有任何文字/数字（只剩标点、emoji、标签） |
| 2 | 整段是同一个字重复 ≥5 次 |
| 3 | 整段等于已知样板幻觉句（`_BOILERPLATE`） |
| 4 | 音频 <400 ms 却输出 >12 字 |
| 5 | 有效字 ≥7 且字种多样性 <0.45（复读型） |
| 6 | 真正有声 <350 ms 却输出 ≥3 字 |

**刻意不处理**：「嗯。」这类单字幻觉无法与真实的单字应答区分，
强行过滤会误杀正常内容，所以保留（用户已知晓）。

回归测试：`tests/test_asr_quality.py`，10 例，覆盖首字与六条规则。

## 6. 三种识别模式（KB-ASR-005）

配置项：`asr.input_mode`、`asr.segment_silence_ms`、`asr.turn_silence_ms`。

| 模式 | 行为 |
| --- | --- |
| `manual` | 点一次开始持续录音，再点一次停止并统一输出 |
| `realtime` | 每说完一段就追加文字，继续监听下一段 |
| `auto` | 连续说多句，短暂停顿不断；较长静音后整轮结束 |

**两级静音阈值是核心**

- `segment_silence_ms`（默认 800）只决定**分段**，不结束整轮。
- `turn_silence_ms`（默认 3000）决定**整轮结束**。
- `auto` 模式下若用户一直没开口，等待 `initial_silence_ms = 15000` 后暂停。

判定逻辑在 `voice_modes.TurnDetector.should_stop(processed_ms)`：

```python
if mode != 'auto' or in_speech: return False
if not heard_speech:            return processed_ms >= initial_ms
return last_end_ms is not None and processed_ms - last_end_ms >= silence_ms
```

## 7. 闲置退出与加载成本（KB-ASR-006）

`qt_web/voice_worker.py::main()`

```python
idle_exit = _idle_exit_seconds()          # asr.idle_exit_seconds，默认 300
...
elif idle_exit and pipeline.is_idle():
    if time.monotonic() - idle_since >= idle_exit:
        break                              # 进程退出，内存还给系统
```

- 为什么必须退进程：进程内 `del model + gc.collect()` 实测只回收约 125 MB，
  torch/onnxruntime 的权重常驻与分配器缓存拿不回来（见 KB-PERF-004）。
- 代价：闲置 5 分钟后再用，需要重新加载模型，实测 11~14 秒；
  界面显示「正在准备本地识别模型…」。
- 设为 `0` 即常驻（不退出），供需要低延迟的场景手动选择。

### 7.1 配置项与 UI（2026-11-05）

- 配置键 `asr.idle_exit_seconds`，单位秒；`0 = 常驻`；默认 `300`。
- `validate_config` 校验范围 `0~86400`；`config.yaml` / `defaults.yaml` 均已写入。
- UI 在「语音输入 → 识别设置 → 模型」分组，下拉固定 6 档：
  `1 / 2 / 5（默认） / 10 / 30 分钟`，**最后一项是「常驻（不退出）」**。
- 识别设置面板**默认展开**（`voice.js::setSettingsOpen(true)`），标题仍可点收起。
- 语音 worker **每 5 秒重读一次配置**（`IDLE_REFRESH_SECONDS = 5.0`）：
  改下拉无需重启语音子进程；读盘失败或值非法时保留旧值，不会误退出。
- 注意：`_build_state()` 的 `asr` 字段已包含 `idle_exit_seconds`，前端直接显示。

## 8. 模型加载细节（`core/asr_service.py`）

- 懒加载 + 线程锁，只加载一次。
- 模型种类（sensevoice / paraformer_large）决定 `generate` 参数，
  由 `qt_web.asr_models.configure_service` 依据目录内容设定。
- SenseVoice 的 bpe 分词器在 Windows 中文路径下会被 sentencepiece 的 C++ 加载器
  报 NOT_FOUND，所以复制到 ASCII 缓存路径后再传入（`_ascii_bpe_path`）。
- VAD / 标点模型本地缺失时**从参数里剔除**而不是报错：
  标点只影响可读性，VAD 缺失由上层单独报错。

---

# 一之二、INT8 ONNX 量化实测（KB-ASR-008）

> 状态：**可行性已验证，未接入产品**。全部数据为本机实测，脚本见文末。
> 结论用一句话：**能做，且字面结果与原生完全一致，但要自己重写一段推理后处理，
> 并且会丢掉热词和标点能力。**

## 8.1 体积与速度（Paraformer-Large，CPU）

| 项 | 数值 |
|---|---|
| 原始 `model.pt` | 859.0 MB |
| FP32 ONNX（对照） | 843.9 MB |
| **INT8 ONNX** | **234.1 MB（27.3%，约 3.67 倍）** |
| 参数量 | 225.1 M |
| 导出耗时 / 量化耗时 | 9.0 s / 55.0 s |
| 单次推理 | 0.07~0.18 s（4~15 s 音频，纯 CPU） |

配套模型合计：`paraformer-large + ct-punc + fsmn-vad` 约 1.14 GB → 约 0.36 GB。

## 8.2 关键事实：ONNX 不吃波形，只吃 FBank

这不是「换个后缀」，接口语义完全不同：

```
输入  speech          [batch, feats_length, 560]  float32   ← LFR 后的 FBank，不是音频
      speech_lengths  [batch]                     int32     ← 帧数（LFR 后）
输出  logits          [batch, token_num, 8404]    float32   ← log_softmax 后的分数
      token_num       [batch]                     int32
      us_alphas / us_cif_peak  [batch, alphas_length]        ← 画时间戳用
```

**要走 ONNX 路线，必须自己补三样**：FBank+LFR+CMVN 特征提取、CIF 后处理、词表还原。

## 8.3 三个必须对齐的特征细节（错一个就输出乱码）

复现 `funasr.frontends.wav_frontend.WavFrontend` 时：

1. **波形 × 32768**（`upsacle_samples=True`）。用 0~1 浮点直接算 FBank，
   整体偏约 16 dB，识别结果全是 `</s>`。
2. **`dither=0`**。训练配置是 1.0（加随机噪声），推理必须关掉才可复现。
3. **LFR 必须用 funasr 自己的 `apply_lfr`**（`as_strided` 实现）。
   手写「逐帧复制前几帧」会错位：官方只左填 `(lfr_m-1)//2 = 3` 帧，
   而步长从填充后的起点算，导致第 0 帧等价于原始第 -3 帧。

> 磁盘上必须同时有 `tokens.json` 和 `am.mvn`：量化只压模型权重，
> 特征归一化参数和词表不在 ONNX 里。

## 8.4 CIF 解码：不要拿 `us_cif_peak` 当索引（踩过的坑）

`us_cif_peak` 来自 `cif_wo_hidden_export`，是 alphas 的**累计积分值**，
长度是上采样后的时间轴（`token_num 的源长度 × upsample_times(3)`），
**与 `logits` 的行数无关**，只用于画时间戳。

正确解码极简：`logits` 每一行已经是 CIF 对齐后的一个 token。

```python
lg = logits[0][:token_num[0]]
text = "".join(tokens[argmax(row)] for row in lg)   # 去掉 </s> / <blank>
```

错误写法（拿 `us_cif_peak >= 1.0` 的位置索引 `logits`）会把所有行夹到末尾，
输出一片 `</s>`——因为 peak 索引最大到 163，而 logits 只有 14 行。

## 8.5 精度验证：INT8 与原生 6/6 一致

同一段 SAPI 合成语音，原生 FunASR 与 INT8 ONNX 各识别一次：

| 用例 | 结果 |
|---|---|
| 今天天气不错，我们出去走走吧 | 一致 |
| 请把这份文档翻译成英文 | 一致 |
| 会议定在明天下午三点半 | 一致 |
| 这个功能需要支持 offline 模式和 online 模式 | 一致（需合并 `@@`） |
| 第一季度营收同比增长百分之十七点五 | 一致 |
| 长句（40+ 字，含资源占用/维护成本） | 一致 |

**INT8 量化没有带来可见的字面精度损失。** 唯一差异是英文：
原生输出的 `offline` 在 ONNX 侧是 `off@@line`，因为
`AutoModel.generate` 在 `funasr/utils/postprocess_utils.py` 里合并了 `@@`，
ONNX 链路没有这层，必须自己补 `ch.replace("@@", "")`。

## 8.6 会丢什么（这是决定值不值得的关键）

`AutoModel.generate` 这一层提供的后处理，ONNX 路线**全部拿不到**：

| 能力 | 位置 | ONNX 侧 |
|---|---|---|
| SentencePiece `@@` 合并 | `postprocess_utils.py` | 需自写（一行） |
| 标点恢复 | ct-punc 独立模型 | 需自行串联 |
| **热词 `postprocess_hotwords`** | `generate` 参数层 | **需自行实现**（见 KB-ASR-007 的 L1 热词） |
| VAD 串联 / batch 拼接 | `inference_with_vad` | 需自写 |

## 8.7 工程障碍（已绕过，未改第三方源码）

1. **torch 2.15 默认导出器导不出 FunASR**：`torch.onnx.export` 默认
   `dynamo=True` 走 `torch.export`，而 `cif_predictor.py cif_export` 含
   数据相关动态控制流，抛 `GuardOnDataDependentSymNode`。
   解法：`dynamo=False` 退回旧 TorchScript 导出器。
   另需装 `onnxscript`（torch 2.15 新导出路径的硬依赖）。
2. **onnx 1.23.2 的 `infer_shapes_path` 是空操作**：实测调用后不产出任何文件
   （返回 `None`），导致 onnxruntime `load_model_with_shape_infer` 必定
   `FileNotFoundError`。解法：用 in-memory `infer_shapes` 打补丁顶上。
3. `export_dir` 默认取 `init_param` 目录，**会把产物写进现有模型文件夹**，
   必须显式传 `output_dir`。

## 8.8 判断

| 方案 | 体积收益 | 工程量 | 风险 |
|---|---|---|---|
| 维持 FP32 PyTorch | — | 0 | 内存 1.7~2 GB，靠闲置退进程缓解 |
| INT8 ONNX | 省 625 MB | **大**：特征提取 + CIF 解码 + 后处理全套自写 | 热词/标点能力需重建 |
| 换 SenseVoice-Small | 936 MB → 约 250 MB | 中（funasr 内部仍是 FP32） | 无体积收益，除非同样导出 |

**体积收益是真实的，精度损失几乎为零，但省下来的是磁盘空间，
而真正的痛点是内存（见 KB-PERF-004）和热词能力。**
若只解决内存，ONNX 有用；若要保住热词，ONNX 反而多一道重建成本。

## 8.9 复现脚本

| 脚本 | 用途 |
|---|---|
| `tools/export_onnx_probe.py` | 导出 + INT8 量化 + 体积/耗时（`--skip-export` 复用已有） |
| `tools/verify_onnx_e2e.py` | 单句端到端：波形 → 特征 → ONNX → 文本 |
| `tools/compare_onnx_native.py` | 批量对照原生 FunASR |
| `tools/verify_sensevoice_onnx.py` | SenseVoice（CTC）端到端验证 |

产物默认在 `模型/语音/<模型名>-onnx/`，**不写原模型目录**。

## 8.10 SenseVoice-Small 量化对比（KB-ASR-009）

| 项 | Paraformer-Large | SenseVoice-Small |
|---|---|---|
| 原始 `model.pt` | 859.0 MB | 892.9 MB |
| FP32 ONNX | 843.9 MB | 894.2 MB |
| **INT8 ONNX** | **234.1 MB** | **230.0 MB** |
| 压缩率 | 27.3%（3.67×） | **25.8%（3.88×）** |
| 导出 / 量化耗时 | 9.0 s / 55.0 s | 9.6 s / 58.1 s |
| 单句推理（4~5 s 音频） | 0.07~0.18 s | **0.07~0.09 s** |

**两者量化后体积几乎相同（234 MB vs 230 MB）**——因为参数量接近。
所以「换 SenseVoice 省空间」这个假设**不成立**，它的价值只在速度和准确率特性上。

### 结构差异：SenseVoice 是 CTC，不是 CIF

```
输入  speech [batch, len, 560]  speech_lengths [batch]
      language [batch] int32     ← 0 = 中文
      textnorm [batch] int32     ← 15 = 默认
输出  ctc_logits [batch, T, 25055]
      encoder_out_lens [batch]
```

解码是标准 CTC 贪心：逐帧 argmax → 去连续重复 → 去 blank。
比 Paraformer 的 CIF 简单得多（用不上 `us_cif_peak` 那套时间戳数据）。

词表 25055（BPE），token 里带 `▁` 前缀，需替换成空格。

### 字面验证：3/3 内容一致

| 用例 | 原生 | INT8 ONNX |
|---|---|---|
| 今天天气不错… | 今天天气不错，我们出去走走吧。 | 今天天气不错我们出去走走吧 |
| 请把这份文档翻译成英文 | …英文。 | …英文 |
| 会议定在明天下午三点半 | 会议定在明天下午**3**点半。 | 会议定在明天下午**三点**半 |

**字面内容全部正确**，差异只有两处，且都是 `generate` 层的后处理：

1. **标点**：原生挂了标点模型，ONNX 侧没有
2. **ITN**（逆文本规整）：原生把「三点半」写成「3点半」，ONNX 保持中文数字

> 注意：`textnorm=15` 参数在 ONNX 里**已经传了**，但 ITN 并未生效。
> 说明 ITN 不在导出的模型里，而在 `AutoModel.generate` 的后处理中。
> 这与 Paraformer 的 `@@` 合并、热词是同一类问题（见 8.6）。

### SenseVoice 的另一个坑：中文路径

`sentencepiece` 的 C++ 加载器在**中文路径**下报 `NOT_FOUND`。
本机 `USERPROFILE` 就是 `C:\Users\籽寒`，所以连 `%TEMP%` 都不安全。
规避方式：加载前复制/链接到 ASCII 目录（`core/asr_service._pick_ascii_cache_dir`）。
Paraformer 不用 BPE，没有这个问题。

## 8.11 量化路线总评

| | Paraformer-Large | SenseVoice-Small |
|---|---|---|
| INT8 体积 | 234 MB | 230 MB |
| 字面精度 | 6/6 一致 | 3/3 一致 |
| 解码复杂度 | 中（CIF） | 低（CTC） |
| 额外输入 | — | `language` / `textnorm` |
| 中文路径问题 | 无 | **有** |

**两个模型量化都可行、精度都能对上。**
选哪个不该看体积（差异仅 4 MB），而应看原本的取舍：
SenseVoice 更快、解码更简单；Paraformer 准确率更高、生态更成熟。

---

# 二、业界口径参照

> 本节是**外部调研结论**，不是本仓库代码。用于回答「为什么手机软件语音识别准确率不低」
> 「我们要往哪个方向补」这类问题。**结论均来自厂商公开文档，未在本地实测。**
> 调研日期：2026-11-04。厂商文档会变，引用前请复核链接。

## 9. 手机软件的通行方案（KB-ASR-007）

### 9.1 一句话

手机端语音输入 = **云端（或端侧）ASR 大模型 + 流式两遍解码 + 热词/上下文注入 + 标点 ITN 后处理**。
准确率高的主因是这套**工程流水线**，不是「模型比我们的大」。

三层解耦的行业默认做法：

```
采集/VAD  ──▶  识别模型（流式 or 非自回归）  ──▶  后处理（标点 / ITN / 热词纠错 / 二遍重打分）
                     │
                     └─ 可换：云端大模型 ⇄ 端侧量化小模型（弱网/隐私时兜底）
```

### 9.2 微信

微信 ASR 不是项目里那种自建推理，走的是腾讯云同一套引擎。

| 事实 | 出处 |
| --- | --- |
| 腾讯云 IM「AI 智能语音」把 ASR + TTS + 翻译嵌进 IM SDK | 腾讯云即时通信 IM《AI 智能语音 · 功能介绍》 |
| 文档明写该能力用于构建「**微信、元宝同款**『AI 语音输入』交互体验」 | 同上 |
| 交互形态：长按输入框录音、点击语音转文字、**按住说话 + 滑动转文字** | 同上 |
| 延迟：语音输入实时识别延迟「毫秒级」，边说边出字 | 同上 |
| 后处理：系统理解上下文语义，**自动断句、自动加标点**，无需二次编辑 | 同上 |
| 语言：普通话 / 英语 / 粤语 / 多地方言 / 多种小语种 | 同上 |
| 「微信、王者荣耀的语音转文字使用一套服务」，字准率 97% | 腾讯云 ASR 产品页 |

另一口径：腾讯云「录音文件识别」约 0.8~1.5 元/小时（按量计费档位，具体以官网价目为准）。

### 9.3 豆包 / 火山引擎

豆包语音输入法背后是火山引擎**豆包语音识别大模型**。

| 项 | 内容 |
| --- | --- |
| 模型 ID | `volc.seedasr.sauc.duration`（按小时计费）/ `volc.seedasr.sauc.concurrent`（按并发包月） |
| 双向流式 | 逐字输出，**随语义完整度持续修正**；官方定位：会议字幕、直播字幕、智能外呼 |
| 流式输入 | **说完一句才输出该句完整结果**；官方定位：**语音输入法**、语音消息转写、智能体对话 |
| 准确率取向 | 官方明说流式输入准确率**优于**双向流式——语音输入法选的是准确率那一条路 |
| 二遍识别 | 开关 `enable_asr_twopass`：先用流式出草稿，再用整句重解码替换 |
| 周边能力 | 热词、替换词、上下文注入 |

值得抄的一条设计：**同一个模型提供两种延迟/精度档位**，由业务场景选。
输入法场景宁可等一句话说完，也要拿更准的那份结果。

价格参考：火山流式约 2 元/小时（2026 年公开口径）；阿里百炼 Paraformer 约 0.288 元/小时。

### 9.4 输入法与其他软件的两条路线

| 路线 | 做法 | 代表 | 取舍 |
| --- | --- | --- | --- |
| 云端 | 音频上云，大模型识别 + 用户词库热词 | 讯飞 / 搜狗 / 百度等主流输入法默认 | 准、省电，依赖网络与隐私 |
| 端侧 | 手机 NPU 跑量化小模型，离线可用 | 强调隐私/离线的版本 | 隐私好、离线可用，模型小 |

端侧可落地的开源参考：

- **SenseVoiceSmall**（FunASR 出品）：官方 benchmark 中文长音频 CER
  SenseVoice-Small 7.81% / Paraformer-Large 10.18% / Whisper-large-v3 20.02%；
  CPU 上约 17.2 倍实时。
- 端侧部署路径：`gguf` + `llama.cpp`（q8 约 254 MB）、`sherpa-onnx`、
  以及厂商 NPU 移植（如爱芯 AX650N，纯 NPU 推理，支持流式增量输出与时间戳）。

### 9.5 准确率高在哪几条（按贡献排序）

| 手段 | 作用 | 本项目有无 |
| --- | --- | --- |
| VAD 精准分句 | 换气处切开，每句独立解码，避免长音频漂移 | ✅ FSMN-VAD（KB-ASR-002） |
| 热词 / 上下文注入 | 联系人名、App 名、当下话题交给模型，专有名词不再听错 | ❌ 未用 |
| 流式 + 二遍修正 | 先出草稿，整句重解码替换 | ❌ 一次解码定稿 |
| 非自回归模型 | Paraformer / SenseVoice 一次出全句，中文长音频优于自回归 Whisper | ✅ 用的是 Paraformer |
| 标点 / ITN 后处理 | 自动加标点、数字规整，省二次编辑 | ✅ ct-punc（`LOCAL_MODEL_LOAD_ARGS`） |
| 端侧小模型兜底 | 弱网/隐私可用 | ❌ 仅 CPU FP32 |

**关键判断**：本项目的模型世代（Paraformer-Large）不落后；差距集中在
**热词、二遍修正**两项工程能力。「热词 + 上下文」对用户主观准确率的提升最大——
微信听得懂联系人昵称、豆包听得懂刚聊的话题，靠的是这个。

### 9.6 若要缩小差距，性价比顺序
1. **加热词**：~~FunASR `AutoModel.generate` 支持 `hotword` 参数~~ →
   **已完成**，见下方 9.8（KB-ASR-010）。`postprocess_hotwords` 文本层方案。
2. **加二遍重打分**：接入 `ct-transformer`（Paraformer 配套）对一遍结果重打分。
   评估结论：CPU 上不划算，暂缓。
3. 再考虑换成 ONNX/量化推理压低内存（属新工程，已实测，见 KB-ASR-008/009）。

> 注意：第 2、3 项仍为**方案建议**，落地前须按 KB-GOT-005 补守门测试。

### 9.8 热词已实施：L1 文本层（KB-ASR-010）

`core/hotwords.py` + `qt_web/voice_pipeline.py` 接入，配置在 `config.yaml` 的 `asr` 段。

```yaml
asr:
  hotwords:
    - 昆仑万象          # 模糊：按拼音纠相似词
    - 欧拉玛=Ollama     # 显式：强制替换
  hotword_threshold: 0.80
  hotword_fuzzy: true
```

#### 三个实测踩过的坑

**坑 1：顺序错了就全军覆没。**
`generate()` 返回的 `text` 是**带空格分字**（`用 纸 朴 青 言 帮 我`），
拼音匹配器按连续串匹配，先纠错会一个都匹配不上。
**必须先 `_clean_text()` 合并空格，再 `apply_hotwords()`。**

**坑 2：funasr 不认 `=`。**
`_EXPLICIT_SEPARATORS` 只有 `=>` `->` `→`。
写 `欧拉玛=Ollama` 会被整行当成一个「模糊目标词」，**显式替换静默失效**。
`core/hotwords.py` 里做了 `=` → `->` 的转换。

**坑 3：模糊匹配会「多吃一个字」。**
funasr 在整条文本上滑窗，窗口长度取目标词长 ±1。
「质朴清颜」既能整段匹配「智谱清言」（0.8462），
也能让错位窗口「朴清颜」得 0.8571 而被选中，替换后变成
**「质智谱清言」**——把本该保留的「质」吃进了替换范围。
判据：**替换片段首字必须与目标词首字读音接近**（`朴 pu` vs `智 zhi` → 拒绝）。

#### 实测结果

| 场景 | 清理后 | 热词后 | 结论 |
|---|---|---|---|
| 说「酷纳万像」，词表有「昆仑万象」 | 请用库纳万象… | 请用**昆仑万象**… | ✅ 纠正 |
| 说「欧拉玛」，配置 `欧拉玛=Ollama` | 打开欧拉玛… | 打开**Ollama**… | ✅ 纠正 |
| 说「智谱清炎」，录成「质朴清颜」 | 质朴清颜… | 质朴清颜… | ⚠️ 拒绝（防错位） |
| 正常句子 ×3 | — | 一字未改 | ✅ 零误伤 |
| 「昆仑山风景很好」「万向集团」 | — | 不变 | ✅ 零误伤 |

**阈值 0.80** 是根据实测定的：`库纳万象` vs `昆仑万象` 恰好 **0.8462**，
默认的 0.85 会漏掉这类最高频的「漏一个字」错误。

#### 能力边界（要说清楚）

- **只救「听音相似、用字不同」**。把 `智谱` 听成 `质朴`（少韵母 u）这种
  音节级错误，热词救不了——那需要声学热词（L2）。
- **不做声学注入**，所以对模型完全「没听过」的词，只能靠文本层碰运气。
- 依赖 `pypinyin`（拼音）+ `rapidfuzz`（相似度）。缺任一则**静默降级**
  为只用显式替换，不会让识别链路报错。

#### 复现

```powershell
py -3.14 tools/verify_hotwords.py     # 真实音频 + 真实模型对照
```

#### 9.8.1 个人词库：从修改行为学习（候选池）

热词表早先**没有任何前端入口**，只能手改 `config.yaml`。现在补齐两层：

| 层 | 位置 | 谁能改 |
|---|---|---|
| 候选池 | `models/asr/hotword_candidates.json`（不进 git） | 自动学习写入 |
| 正式词表 | `config.yaml` 的 `asr.hotwords`（唯一事实来源） | 只有用户确认才改 |

**为什么必须分两层**：自动学习难免误判（用户改的是措辞不是错字），
一旦写进正式热词会反过来影响后续识别，形成错误闭环。候选池保留审阅权，也
让学习可撤销。

链路：

```
说错 -> 模型输出识别原文
     -> 用户在「语音」页直接编辑结果框，点「💾 保存修改」
     -> extract_edits(raw, final) 提取 (错写, 正写)
     -> 进候选池（只进候选，绝不碰正式词表）
     -> 「设置 → 个人词库」页确认「✅ 加入」或「✕ 忽略」
     -> 确认后才并入 asr.hotwords
```

**坑：difflib 会切出半截词。**
`SequenceMatcher` 按字符逐位对齐，不知道词在哪：

```
今天音机铜…/今天阴极铜…  -> replace('音机','阴极')    尾字「铜」被切走
这批电解同…/这批电解铜…  -> replace('同','铜')        只有 1 个字
```

直接拿片段当热词，会得到「音机」「同」这种半截词，进词表后**既纠不准**
（会纠成「阴极铜铜」）**又可能误伤**（单字「同」→「铜」乱改）。

修法在两个方向上都试过，都失败：

| 尝试 | 结果 | 失败原因 |
|---|---|---|
| 向两侧无限扩展到相同字符 | 「今天音机铜价格涨了」整句 | 相同字符一路相等到句首 |
| 左右各吸收 1 字 | 「天音机铜」「丝摸需」 | 左边那字属于**上一个词**（今天） |

最终方案 `_grow_to_word()`：**以现有词表为锚点做横向滑动**，
对每个 replace 片段尝试 pad=1、pad=2 的对称扩展，要求正确侧**精确等于**
词表项；不精确时退而找「包含词表项」并裁剪到该词本身；都不行才用
pad=1 结果交给用户确认。

必须区分「精确等于」和「包含」——只用 `in` 判断时，
「今天阴极铜价」包含「阴极铜」就停了，把「今天」「价」也当成热词。

#### 能力边界（个人词库）

- 只学**同长度替换**。插入/删除（补字、删字、加语气词）一律跳过——
  那是用户润色，不是模型听错。
- 提取出的「正确写法」若命中词表，边界最准；若用户改的是词表外的新词，
  只能用 pad=1 的保守估计，**依赖用户确认**。
- 拒绝过的候选记入 `dismissed`，不会因为再次学习而复活。
- `mine_from_existing()`（从现有词表自动挖同音变体）**保守返回空**：
  无法凭空生成可信的汉字错法，乱生成只会污染候选池。

测试：

```powershell
py -3.14 -m unittest tests.test_hotword_learning -v   # 15 项
py -3.14 -m unittest tests.test_hotwords -v           # 原有防误伤 8 项
```

### 9.7 引用纪律

- 本节标注为「外部调研」，与仓库内实测结论**分开对待**；引用时说明来源厂商文档。
- 厂商文档常改版（价格、模型 ID、参数名），超过半年请重新核对再引用。
- 不要把本节当成「本项目已具备的能力」。9.5 表格里有 ❌ 的，就是没做。

## 10. 麦克风设备选择（KB-ASR-011）

### 10.1 为什么按名字而不是索引

`sounddevice` 的设备索引会随插拔、驱动重装而变化；配置里存名字（Windows 设置里
显示的那个名字）才能跨重启保持有效。`core/audio_capture.py::find_input_device()`
负责把名字翻译成当前索引。

### 10.2 设备列表怎么来

```python
# core/audio_capture.py::input_devices()
sd.query_hostapis(); sd.query_devices()   # 只枚举，不打开设备
```

Windows 上同一支麦克风会出现在 **MME / Windows DirectSound / Windows WASAPI /
Windows WDM-KS** 四个 host API 里，本机实测 55 个端口 → 归并后 **15 个设备**。

- MME 的设备名会被截断到 31 字符（如 `CABLE Output (VB-Audio Virtual`），
  所以用「前缀互为包含」判断是否同一支设备；
- 显示名优先取完整名，避免把截断名写进配置；
- 归并后保留的 `index` 是 MME 的原生端口（与系统默认列表一致）。

### 10.3 生命周期

| 时机 | 行为 |
| --- | --- |
| 软件启动 | `WebApi.__init__` 立即枚举一次并缓存到 `self._input_devices`（不反复打开音频子系统） |
| 页面加载 | `_build_state()['asr']` 直接读缓存，`devices` 随状态下发 |
| 点「刷新」 | `asr_devices` 重新枚举，返回并推送 `asr-devices` |
| 开始录音 | 语音 worker 读 `asr.device_name`，`find_input_device()` 定位端口 |

**不要**把枚举挂到 3 秒一次的模型扫描定时器上：`query_devices()` 要访问音频后端，
反复调用会拖慢启动、在部分声卡上还会触发 WASAPI 初始化。

### 10.4 失败语义

- `device_name` 为空 → 交给 PortAudio 默认输入设备，行为与改造前完全一致。
- 选了具体设备但已拔出 → `AudioCapture.start()` 返回 `False`，worker 抛出
  `麦克风启动失败：找不到或无法打开「xxx」…`，**不会静默回退到默认麦克风**
  （静默回退会让用户以为选错了设备还在工作）。

### 10.5 实测（2026-11-05 本机）

```
系统默认                               -> 打开成功 samples=9152
麦克风 (HECATE G2无线版 HEADSET)        -> 打开成功 samples=9152
麦克风 (High Definition Audio Device)  -> 打开成功 samples=8800 rms=0.12（正在播放声音）
不存在的设备                            -> START_FAILED（明确失败，未回退）
```

前端验证：下拉 15 个选项 + 「跟随系统默认输入」、切换后 `config.yaml`
`asr.device_name` 同步、恢复为空后状态一致。

