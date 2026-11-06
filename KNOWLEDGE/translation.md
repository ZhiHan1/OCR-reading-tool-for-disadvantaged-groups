# 翻译（TR）

> 域编号：`KB-TR-*`。核心文件：`core/gguf_translation_model.py`（本地 GGUF 后端）、
> `core/translation_service.py`（多后端统一入口）、`controllers/translation_manager.py`（队列与后台线程）。
> 本文只记录**实测结论**，每条都注明脚本与日期；未经实测的推测一律标「未验证」。

## 1. 当前架构

```
qt_web/api.py do_translate / 自动翻译
        ↓
controllers/translation_manager.py   请求队列 + 后台线程 + 结果队列
        ↓
core/translation_service.py          TranslationService：懒加载、闲置卸载、后端切换
        ↓
core/<backend>_translation_model.py  gguf / google / opus / custom_openai
```

后端注册是**延迟导入**的（`TranslationService.AVAILABLE_MODELS`），缺依赖只会打印
「XX模型不可用」，不影响启动。配置在 `config.yaml` 的 `translation` 段，
`model: gguf` 指向本地模型。

## 2. 本地模型与官方推荐参数（KB-TR-001）

模型：`HY-MT1.5-1.8B-Q6_K.gguf`（1406.5 MB），腾讯混元翻译模型 1.5 版 1.8B。

**官方推荐推理参数**（摘自 HY-MT1.5-1.8B-GGUF 的 README，2026-11-05 核对）：

```json
{ "top_k": 20, "top_p": 0.6, "repetition_penalty": 1.05, "temperature": 0.7 }
```

README 原文还有一句关键提示：

> Note that our model does **not have the default system_prompt**.

**产品目前这四项取值与官方一致**（`core/gguf_translation_model.py` 的 `translate()`），
不需要改。

## 3. 提示词模板（KB-TR-002）

官方给了 4 类模板（README 的 Prompts 节）：

| 模板 | 用途 | 产品是否在用 |
| --- | --- | --- |
| ZH⇄XX | 中英互译 | ✅ 用了基础版（措辞少一个「的」字） |
| XX⇄XX | 中英以外的方向（如日→英） | ❌ 一律套中文模板 |
| 术语干预 | `参考下面的翻译：{源词} 翻译成 {目标词}` | ❌ 未用 |
| 上下文翻译 | 提供上文信息 | ❌ 未用 |
| 格式化翻译 | 保留 `<sn>` 标签 | ❌ 未用 |

官方 ZH⇄XX 模板原文：

```
将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：

{source_text}
```

**实测：措辞差异不影响结果。** 见 §5 实验 3——单换行 / 空行 / 多一个「的」字 /
目标语言写英文名（`English`），四种输出完全一样。**不必为此改措辞。**

## 4. 已确认的实现缺陷（KB-TR-003）

### 4.1 `max_tokens` 会在 447 字左右截断（已实测，**已修复**）

```python
# core/gguf_translation_model.py translate()
max_tokens=min(256, max(32, len(text) * 2))
```

`min(256, ...)` 给了一个**硬上限**，长文本必被截断。实测（`tools/probe_translation_limits.py`，
2026-11-05，中→英）：

| 原文 | prompt tokens | `max_tokens` | 实际输出 | `finish_reason` |
| --- | --- | --- | --- | --- |
| 149 字 | 104 | 256 | 142 | `stop` ✅ |
| 298 字 | 191 | 256 | 132 | `stop` ✅ |
| **447 字** | 278 | **256** | **256** | **`length` ❌ 截断** |

给足输出空间后，同样的 447 字实际需要 **420 tokens**，比公式允许的多 64%。

**输出/原文 token 比实测**：

| 原文 | 输出 tokens | 比率 |
| --- | --- | --- |
| 447 字 | 420 | 0.94 |
| 745 字 | 564 | 0.76 |
| 1192 字 | 846 | 0.71 |

**结论**：`len(text) * 2` 本身不算低估，问题在于 `min(256, ...)` 这个封顶。

**✅ 2026-11-05 已修复**：改为 `_max_tokens_for(prompt)`，按**真实 token 数**计算：

```python
prompt_tokens = len(self.model.tokenize(prompt.encode("utf-8")))
wanted = max(64, min(1024, prompt_tokens * 2))   # 余量按 token 数给 2 倍
return max(32, min(wanted, n_ctx - prompt_tokens - 8))  # 不超出上下文
```

真机验证（同一 Q6_K、888 字原文、prompt 563 tokens）：

| 实现 | max_tokens | 实出 | finish_reason | 译文末尾 |
| --- | --- | --- | --- | --- |
| 旧 | 256 | 256 | **`length` ❌** | `...no less than`（截断） |
| 新 | 1024 | 502 | **`stop` ✅** | `...not less than 101%.`（完整） |

> ⚠️ **踩坑**：`Llama.n_ctx` 是**方法**（`n_ctx()`），不是属性。第一版写成
> `int(getattr(model, "n_ctx", 2048))` 直接抛 `TypeError`，而单元测试用假对象
> 恰好把 `n_ctx` 写成属性，**测过了但真机必崩**。`tests/test_backend_refactor.py`
> 现在同时覆盖「方法」与「属性」两种形态。

注：296 字这类中等长度文本新旧两种都给 `stop`（实出 243 token），
**要 800 字以上才能复现旧缺陷**——这也是它一直没被发现的原因。

### 4.2 `n_ctx=2048` 的余量在缩小（已实测）

| 原文 | prompt tokens | 输出 tokens | 合计 | 2048 是否够 |
| --- | --- | --- | --- | --- |
| 447 字 | 278 | 420 | 698 | ✅ |
| 745 字 | 452 | 564 | 1016 | ✅ |
| 1192 字 | 713 | 846 | 1559 | ✅（仅剩 489） |

约 **1600 字**就会逼近上限。OCR 全屏长文档可能超过，届时 llama.cpp 会报错或空输出。
模型训练长度是 262144，`n_ctx` 调大的代价是 KV cache 内存（CPU 推理下主要是内存）。

### 4.3 日→英方向用了错误的中文模板

`translate()` 不管语言方向，一律发中文模板。官方要求 ZH⇄XX 之外的方向用英文模板
（`Translate the following segment into {target}, without additional explanation.`）。
**未做 A/B，影响程度未验证。**

### 4.4 `n_gpu_layers=0` 是硬编码，注释会误导

```python
n_gpu_layers=0,  # CPU模式，如果有GPU可以设置为-1
```

这句注释让人以为「有 GPU 就能用」，实际情况见 §7。翻译模块**没有**任何 GPU 配置项。

## 5. 准确度实验：提示词格式 vs 采样（KB-TR-004）

脚本：`tools/bench_translation_quality.py`（2026-11-05，14 句，5 组配置，同一 Q6_K 模型）。

| 组 | 配置 | 总耗时 | 截断 |
| --- | --- | --- | --- |
| **A** | **原始文本 + 无 BOS + 0.7 采样（产品当前行为）** | 38.0 s | 0/13 |
| B | 套对话模板（`apply_chat_template`）+ 0.7 | 41.1 s | 0/13 |
| C | 套对话模板 + 贪心（温度 0） | 36.1 s | 0/13 |
| D | 原始文本 + 贪心 | 34.9 s | 0/13 |
| E | 套对话模板 + 温度 0.2 | 39.6 s | 0/13 |

**逐句一致率（相对 A）**：B 0/13、C 0/13、D 8/13、E 0/13。

### 5.1 反直觉结论：套对话模板会让质量**下降**

模型自带 `tokenizer.chat_template`（596 字符，`hunyuan-dense` 架构，
BOS token id 120000）。直觉上「按训练格式喂」应该更好，但实测：

| 配置 | 中译英第 1 句 | 日译中第 12 句 |
| --- | --- | --- |
| A（原始文本） | `The oxygen content in the无氧铜杆 is controlled below 10 parts per million` | `请确认キャストロール的表面温度和冷却水流量` |
| B（套模板） | `The oxygen content in the无氧铜杆应控制在每百万份中不超过10份` | 同上 |
| C（套模板+贪心） | 同 B | 同上 |

B/C/E 组在中译英时**后半句直接没翻译**（中英混杂），A 组至少翻完了。

**原因**：官方 README 给的 llama.cpp 用法是
`llama-cli ... -p "Translate the following segment into Chinese, ..."`，
**直接把纯文本喂给 `-p`**，并没有走 chat template。聊天模板里带有
`<｜hy_User｜>` 这类控制标记，而这些标记是给多轮对话准备的；
官方又明确说「模型没有默认 system_prompt」。所以**纯文本直喂才是对的用法**。

> ⚠️ **不要**把 `self.model(prompt)` 改成 `create_chat_completion()`。这是本轮
> 最容易做错的「优化」，实测有害。

### 5.2 采样温度影响很小，但会引入不一致

A（0.7）与 D（贪心）有 8/13 一致。差异体现在用词上，例如日译中的「铜杆」：

- A：`无氧铜棒`　D：`无氧铜棒`　C：`无氧铜棒`

以及「铜精矿」的日译：

- A：`銅精鉱`（正确）　D：`銅鉱石`（不准确）

**结论：保持官方 0.7 即可**，换贪心没有稳定收益，反而在不同输入上各有胜负。

## 6. 术语错误清单（KB-TR-005，待处理）

以下错误**五组配置全部出现**，是模型的固定错误，采样/模板都救不了：

| 原文 | 错误输出 | 说明 |
| --- | --- | --- |
| 无氧铜**杆** | `the无氧铜杆`（中译英）/ `无氧铜棒`（日、中） | 中文原样残留 / 同义替换 |
| **镀锡**铜线 | `ニッケルメッキ`（镀**镍**） | **术语译错，危害最大** |
| cast roll | `キャストロール` | 日译中时片假名未转写，原样残留 |
| 铜**精矿** | `銅鉱石`（铜矿石） | 术语不准 |

**可用的修法**：官方「术语干预」模板

```
参考下面的翻译：
{source_term} 翻译成 {target_term}

将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：
{source_text}
```

项目已有 139 条铜材热词（`config.yaml` 的 `asr.hotwords`），可以复用为术语表。
**⚠️ 用户 2026-11-05 明确决定：专用名词准确度「暂时不用管」，此项挂起。**

## 7. GPU 加速：现状与可行路径（KB-TR-006，未实施）

### 7.1 现状：装的是纯 CPU 版，调用不了任何显卡

```python
from llama_cpp import llama_cpp as L
L.llama_supports_gpu_offload()  # → False
# lib/ 目录只有：ggml-base.dll, ggml-cpu.dll, ggml.dll, llama.dll, mtmd.dll
# 没有 ggml-cuda.dll / ggml-vulkan.dll
```

本机硬件（`Get-CimInstance Win32_VideoController` + `nvidia-smi`）：

| 设备 | 说明 |
| --- | --- |
| NVIDIA GeForce RTX 3060 Ti | 8192 MiB，驱动 610.88 |
| AMD Radeon 780M | 核显 |
| Parsec / Virtual Display Driver 等 | 虚拟显示器，与本问题无关 |

**结论：硬件有 RTX 3060 Ti，但当前 llama-cpp-python 一个都调不动。**

### 7.2 官方没有 CUDA 预编译包，只有 Vulkan（2026-11-05 核实）

| 来源 | 实际情况 |
| --- | --- |
| **PyPI** | `llama-cpp-python` 0.3.36 只有 **1 个文件** `llama_cpp_python-0.3.36.tar.gz`（源码包），无任何 Windows wheel |
| **GitHub Releases** | 逐版本只有 `-vulkan`、`-rocm72`、`-metal` 三种标签，**没有 `-cuda` 标签** |
| Windows 可用 | `v0.3.36-vulkan` → `llama_cpp_python-0.3.36-py3-none-win_amd64.whl` |

要点：

- Windows **没有官方 CUDA wheel**。想上 CUDA 只能自己从源码编译（需要 MSVC + CUDA Toolkit）。
- **Vulkan wheel 是 `py3-none-win_amd64`，不绑定 Python 版本**，本机 Python 3.14.8 可以直接装。
- 3060 Ti 和 780M 都支持 Vulkan，理论上都能跑。
- Vulkan 后端在 NVIDIA 卡上通常慢于 CUDA（社区经验值 60–80%，**无官方数据，未验证**）。

### 7.3 风险与前置条件

换 wheel 后需要同步处理：

1. `pyinstaller_hooks/hook-llama_cpp.py` 收集的是 `collect_dynamic_libs("llama_cpp")`，
   换包后 DLL 集合会变（多出 Vulkan 运行时），**打包产物必须重新验证**。
2. 翻译模块要新增 `n_gpu_layers` 配置（现在是硬编码 0），并加「后端不支持 GPU 时自动降级」。
3. 换包后须跑 `tools/bench_translation_quality.py` 确认输出与 CPU 版**逐字一致**
   （理论上相同，但 Vulkan 后端有浮点累加顺序差异，需实测）。
4. 本机已装 `nvidia-cuda-*` 系列（cu12 运行时），但那是 torch 的依赖，与 llama.cpp 无关。

**收益预期**：只提升速度，不提升准确度。翻译是「短输入、长输出」任务，
瓶颈在逐 token 生成（实测 1192 字需 69.1 s），换 GPU 确实能快数倍。

**⚠️ 用户 2026-11-05 明确决定：换 GPU 版「后续再说」，本轮不实施。**

## 8. 复现方法

```powershell
# 准确度：5 组配置 × 14 句（含长文本截断检查）
py -3.14 tools/bench_translation_quality.py --out bench.json

# 上限探测：max_tokens 截断点、n_ctx 溢出点、提示词格式变体
py -3.14 tools/probe_translation_limits.py

# 确认当前 llama-cpp-python 是否支持 GPU
py -3.14 -c "from llama_cpp import llama_cpp as L; print(L.llama_supports_gpu_offload())"
```

复测前请确认模型路径 `模型/翻译/HY-MT1.5-1.8B-Q6_K.gguf` 存在（1406.5 MB）。

**变更记录**：2026-11-05 新建。记录提示词/采样 A/B 实测（套对话模板有害）、
`max_tokens` 在 447 字截断、`n_ctx=2048` 余量约 1600 字、
llama-cpp-python 无官方 CUDA wheel、本机为纯 CPU 版。
