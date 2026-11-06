# 测试

## 语音识别质量回归（`test_asr_quality.py`）

覆盖三类问题：

| 用例 | 内容 | 需要模型 |
|---|---|---|
| `FilterRules` | 幻觉过滤规则、`active_ms` 有声时长估算、前滚切片偏移 | 否（秒级） |
| `ModelTests` | 前滚救首字、静音/噪声不产文字、正常与轻声语音不误杀、识别线程去重 | 是（约 15~60s） |

在项目根目录执行：

```powershell
# 全部
py -3.14 -m unittest tests.test_asr_quality -v

# 只跑纯函数（改过滤规则时用，秒级）
py -3.14 -m unittest tests.test_asr_quality.FilterRules -v

# 只跑模型相关（改 VAD / 前滚 / 识别参数时用）
py -3.14 -m unittest tests.test_asr_quality.ModelTests -v
```

本地模型缺失时 `ModelTests` 会整体 skip，不会算作失败。

### 关键用例说明

- `test_preroll_offsets_submission`：用桩 VAD 让起点固定为 500ms，断言提交识别的
  切片从 500ms − `PREROLL_MS` 开始。不需要模型，秒级。
- `test_preroll_recovers_first_char`：把语音人为向前裁掉 300 / 400ms（等价于
  FSMN-VAD 起点报晚），断言仍能识别出完整句子。前滚时长直接取生产代码的
  `VoicePipeline.PREROLL_MS`，**删掉或改小前滚都会让它失败**——它就是「你好」
  被识别成「好」的回归防线。
- `test_noise_does_not_produce_sentences`：用 200~300ms 的白噪声突发模拟键盘声 /
  碰麦，断言不会变成成句文字（允许模型蹦出单个语气词）。
- `test_quiet_speech_not_rejected`：把语音衰减到 RMS≈0.005（正常说话约 0.04~0.06），
  断言不被幻觉过滤误杀。

### 参考音频

`data/asr_zh_a.wav`、`data/asr_zh_b.wav` 由 Windows SAPI（Microsoft Huihui）
合成为 16kHz 单声道 16bit，内容分别是：

- A：「你好，今天天气很不错」
- B：「打开设置页面并保存配置」

重新生成（语音质量需一致，否则断言里的句子要同步改）：

```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Huihui Desktop')
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  16000,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('tests\data\asr_zh_a.wav', $fmt)
$s.Speak('你好，今天天气很不错')
$s.Dispose()
```
