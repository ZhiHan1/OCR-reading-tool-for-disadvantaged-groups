# 后端接口（API）

> 域编号：`KB-API-*`。位置：`qt_web/api.py`（约 1100 行，前端唯一后端入口）。

## 1. 为什么全部集中在一个类

前端只能看到 QWebChannel 注入的一个对象。把所有动作做成同一个
`WebApi` 上的方法，既省掉多对象注册的复杂度，也让 `_build_state()`
可以一次性拿到全部后端状态。代价是这个文件偏大，所以约定：
**动作方法只做「校验 + 转发」，业务逻辑留在控制器/内核。**

## 2. 唯一入口与分发（KB-API-001）

```python
@Slot(str, str, result=str)
def request(self, action, payload_json) -> str:
    payload = json.loads(payload_json) if payload_json else {}   # 解析失败 → {}
    result = self._dispatch(action, payload)                     # 异常 → {ok:False, error}
    return json.dumps(result or {"ok": True}, ensure_ascii=False, default=str)

def _dispatch(self, action, p):
    handler = getattr(self, "do_" + action, None)
    if handler is None:
        return {"ok": False, "error": f"未知动作: {action}"}
    return handler(p)
```

**契约**

- 前端 `action` 字符串 = 方法名去掉 `do_`。
- 返回 `None` 会被补成 `{"ok": True}`；抛异常一律被捕获成 `{"ok": False, "error": ...}`。
- 因此前端永远拿到合法 JSON，**不会因为后端异常而卡在超时**。
- 新增动作 = 在 `WebApi` 上新增 `do_xxx`，前端即可调用，无需注册表。

## 3. 动作清单（KB-API-002）

按功能分组，括号内是前端调用点。

### 3.1 状态与主题
| 动作 | 说明 |
| --- | --- |
| `state` | 返回 `_build_state()` 全量快照（app.js 启动时拉一次） |
| `set_theme` / `set_frost` | 写 `ui.web_theme` / `ui.web_frost` 并推送 |
| `clear_result` | 清空结果区 |
| `stop_speak` | 停止朗读 |

### 3.2 识别
| 动作 | 说明 |
| --- | --- |
| `trigger_func1` | 框选朗读（一次） |
| `func2_select` / `func2_read` / `func2_clear` | 功能2：选区域 / 读当前区域 / 清区域 |
| `toggle_scan` `{mode}` | 功能3 / 漫画 / 游戏：启动或暂停扫描 |
| `reselect_scan` `{mode}` | 重新框选 |
| `clear_scan` `{mode}` | 停止扫描并清区域 |
| `set_mode_enabled` | 功能开关（写 `ui.<mode>_enabled`） |

`toggle_scan` 关键行为：启动前先 `stop_scan_modes(except_mode=当前)`，
保证同一时刻只有一个扫描线程；然后补推 `regions` 与 `running`，
因为首次 `start()` 会同步触发框选，前端必须知道结果。

### 3.3 翻译 / TTS
| 动作 | 说明 |
| --- | --- |
| `translate` | 手动翻译当前文本，结果走 `push_translate_result` |
| `speak` | 朗读指定文本（页面内试听用参数，不改全局配置） |
| `copy_text` | 复制到剪贴板 |
| `tts_preview` / `tts_export` | 试听 / 导出音频（导出在独立线程） |

⚠ **翻译不要调 `TranslationController.translate()`——它已经删了**。
翻译统一走 `TranslationManager.enqueue_manual(text, source, target)`，
结果经 `result_queue` 回到主线程，再由 `api.push_translate_result()` 推前端。

历史坑（KB-PERF-014）：`TranslationController.translate()` 是同步阻塞调用，
没有返回值，结果只通过 `on_result(result)` 送出；而 `bridge.emit_result`
期望 `on_result(mode, text, status, display)` 四个参数。接错时译文被当成
`mode`、文本为空，用户看到空白结果。它在 2026-11-15 被删除，
`TranslationController` 现在只负责 `switch_model` / 查询模型信息。

### 3.4 文件与目录
| 动作 | 说明 |
| --- | --- |
| `pick_folder` / `pick_file` | 原生对话框（前端不设超时） |
| `open_folder` | 资源管理器打开 |

### 3.5 语音输入
| 动作 | 说明 |
| --- | --- |
| `asr_start` / `asr_stop` / `asr_state` | 会话控制 |
| `asr_load_model` | 预热模型（子进程 `load` 命令） |
| `asr_models` / `asr_import_model` / `asr_remove_model` | 模型列表与增删 |
| `asr_devices` | 重新枚举麦克风（返回 `devices` + 当前 `selected`，并推 `asr-devices`） |

### 3.6 配置
| 动作 | 说明 |
| --- | --- |
| `set_config` / `set_config_nested` | 单点写入（立即落盘） |
| `save_config` | 手动落盘（一般不需要） |
| `reset_defaults` | 恢复默认（整份替换，保留热词；需 `confirmed`） |
| `import_config` / `export_config` | 导入（整份替换）/ 导出（全量快照） |
| `set_hotkey` / `reset_hotkeys` | 热键 |
| `hotkey_capture_start` / `hotkey_capture_end` | 录制热键时暂停全局热键 |

### 3.7 模型管理
| 动作 | 说明 |
| --- | --- |
| `enable_local_model` / `disable_local_model` | 启停本地 GGUF |
| `add_local_model` / `remove_local_model` | 增删 |
| `enable_online_model` / `remove_online_model` / `add_online_model` | 在线服务商 |
| `test_online_model` / `test_online_config` | 连通性测试 |
| `scan_model_folders` / `open_model_folder` | 扫描与打开模型目录 |

## 4. 推送通道（KB-API-003）

后端 → 前端只有一条物理通道：`WebApi.event` 信号携带 JSON 字符串，
`bridge.js` 解析后按 `type` 分发。

| 方法 | type | 主要字段 |
| --- | --- | --- |
| `push_status` | `status` | text, kind |
| `push_result` | `result` | text, status, append |
| `push_translate_result` | `translate` | text, error |
| `push_asr_result` | `asr` | text |
| `push_scan_state` | `scan` | mode, running |
| `push_region` | `region` | mode, has_region |
| `_push_running` / `_push_regions` | 批量状态 | 各模式快照 |

**注意**：`scan` / `region` 是**逐模式增量**推送，不是整体替换；
前端更新 `APP.state.running[mode]` 即可，不要覆盖整个对象。

## 5. 状态快照字段（KB-API-004）

`_build_state()` 返回：

| 键 | 内容 |
| --- | --- |
| `theme` / `frost` | 界面外观 |
| `enabled` | 五个功能开关（来自 `win._enabled`） |
| `queueModes` | tts 队列模式（func1/func2） |
| `regions` / `running` | **只统计已构造的控制器**（见下） |
| `hotkeys` | 六个快捷键文本 |
| `asr` | backend / local_model / 三个布尔 / input_mode / 两个静音阈值 / **idle_exit_seconds** / **device_name + devices** / models / model_status |
| `translation` | 源/目标语言 + 五个模式的自动翻译开关 |
| `tts` | backend / voice_name / edge_voice / rate / volume / pitch / test_text |
| `voices` | `{sapi: [...], edge: [...]}` |
| `models` | 本地 GGUF（ModelManager）+ 在线服务商 |
| `recognition` | 功能1/2 的复制与朗读开关 |
| `ocr` | 设备、可选设备列表、五个模式的扫描参数 |

**`_mode_flags` 的关键设计**：只读 `app._lazy` 里**已构造**的控制器。
懒构造下没构造过的控制器不可能正在扫描或已有选区，没必要为拼一个 `False`
把它实例化（那会让状态查询变慢）。

## 6. 配置写入、导入导出与恢复默认（KB-API-005）

### 6.1 单点写入（每次立即落盘）

`do_set_config` / `do_set_config_nested` 的固定顺序：

```python
before = deepcopy(config.data)
candidate = deepcopy(before); candidate[section][key] = value
validate_config(candidate)        # 类型 + 范围，不合格直接抛错
config.set(...); config.save()    # 原子写：tmp + fsync + os.replace
_apply_current(before)            # 只对变化项做热应用（热键/设备/浮窗/翻译）
```

- `asr` 的 `local_model` / `input_mode` / `segment_silence_ms` / `turn_silence_ms`
  在录音中拒绝修改（要求先停止）；`idle_exit_seconds` 与 `device_name` 不受限，随时可改。
- `config.save()` 是**整份** `yaml.dump(self.data)` + `os.replace`，失败自动回滚。
  因此注释会丢、未知键会保留。

### 6.2 导出（全量快照）

`do_export_config` → `Config.export(path)`：

- 内容是 `yaml.safe_dump(self.data)`，即**当前内存配置整份**。
- 禁止覆盖 `config.yaml` / `defaults.yaml`；先写 `.tmp` 再 `os.replace`。

### 6.3 导入（整份替换 + 热词保护）

`do_import_config`：

1. 文件必须 `< 2 MB`，`yaml.safe_load` 后必须是 dict；
2. `_keep_hotwords(candidate, current, only_if_missing=True)`
   —— 导入文件**没带** `asr.hotwords` 时沿用当前词表；带了就以文件为准；
3. `_replace_current(candidate)`：
   `validate_config` → 快捷键冲突检查 → `config.replace()`（内部 `save()`）
   → 结束旧语音/框选会话 → `_apply_current(full=True)` → 推全量 state。

### 6.4 恢复默认（整份替换 + 热词保护）

`do_reset_defaults`（必须带 `confirmed`）：

```python
_replace_current(_keep_hotwords(load_defaults(), self.config.data))
```

- 以 `defaults.yaml` 为模板整份替换，不再逐键回填；
- `asr.hotwords` 无前端编辑入口，恢复默认时**强制保留**当前词表。

### 6.5 覆盖范围审计（2026-11-05）

- UI 实际可写的配置项共 **55 项 / 13 组**，全部在 `defaults.yaml` 有对应键，缺失 0。
- 组别：功能开关 5、队列模式 2、功能3 4、漫画 3、游戏 3、推理设备 1、
  语音设置 7、快捷键 6、翻译模型 3、翻译语言/集成 7、识别设置 4、主题 2、语音输入 8。
- 仅后端读取、无 UI 的键：`ocr.warmup`、`performance.blas_threads`、
  `translation.idle_unload_seconds`、`ocr.func3.scan_interval`、
  `ocr.func3.min_length_for_similarity`、`asr.backend`。
- `defaults.yaml` 也必须包含 `asr.hotwords / hotword_threshold / hotword_fuzzy`，
  否则恢复默认会把用户热词清空。

**禁止**：把整个 `config.yaml` 读成 dict 改完整体写回——文件里存在历史乱码字符
（见 KB-GOT-004），整文件重写会把乱码扩散或丢失注释。

## 7. 模型管理（KB-API-006）

两套来源并存：

1. **本地 GGUF**：`core/model_manager.py::ModelManager`，扫描 `models/local`，
   配置写在 `config.yaml` 的 translation 段（模型 id / 启用状态 / 路径）。
2. **在线服务商**：直接存在 `config.yaml` 的 `translation.online_providers`。

`_model_manager()` 惰性构造单例；`_release_models()` 在删除/禁用后释放实例。

`_scan_model_folders()` 由 3 秒定时器驱动，**按当前页面节流**：
语音页/设置页 3 秒，其它页 30 秒；`NavBridge` 切页时立即触发一次扫描。
