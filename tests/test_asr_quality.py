"""语音识别（ASR）质量回归测试。

覆盖三件事：

1. 幻觉过滤规则（纯函数，秒级，不需要模型）；
2. **首字不被吞**：VAD 起点偏晚 300~400ms 时，`PREROLL_MS` 前滚能不能救回
   「你好」的「你」；
3. **幻觉不落地**：静音 / 瞬态噪声（键盘声、碰麦）不能变成文字，同时正常语音
   与轻声说话不能被误杀。

用法（在项目根目录执行）：

    py -3.14 -m unittest tests.test_asr_quality -v
    py -3.14 -m unittest tests.test_asr_quality.FilterRules -v      # 只跑纯函数
    py -3.14 -m unittest tests.test_asr_quality.ModelTests -v       # 跑模型相关

本地模型缺失时，模型相关用例自动 skip（不算失败）。

参考音频由 Windows SAPI（Microsoft Huihui）合成，16kHz 单声道 16bit，
内容分别是「你好，今天天气很不错」「打开设置页面并保存配置」。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
from scipy.io import wavfile

from core.asr_service import (
    SUPPORT_MODEL_DIRS,
    ASRService,
    active_ms,
    filter_hallucination,
)
from utils.paths import resolve_path

DATA = Path(__file__).resolve().parent / "data"
WAV_A = DATA / "asr_zh_a.wav"
WAV_B = DATA / "asr_zh_b.wav"
PHRASE_A = "你好，今天天气很不错"
PHRASE_B = "打开设置页面并保存配置"

# 统计「有效字」：去掉标点/空格，只留中英文与数字。
_CORE_RE = re.compile(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def load_wav(path):
    """读 wav -> 16k 单声道 float32。"""
    sr, data = wavfile.read(str(path))
    data = data.astype(np.float32) / 32768.0
    if data.ndim > 1:
        data = data[:, 0]
    if sr != 16000:
        from scipy.signal import resample_poly

        data = resample_poly(data, 16000, sr).astype(np.float32)
    return data


def core_len(text):
    return len(_CORE_RE.sub("", text or ""))


def models_ready():
    """本地模型是否齐全；返回 (ok, 原因)。"""
    vad = resolve_path(SUPPORT_MODEL_DIRS["fsmn-vad"])
    if not (vad / "model.pt").is_file() or not (vad / "config.yaml").is_file():
        return False, "缺少 FSMN-VAD 模型：%s" % vad
    try:
        from qt_web.asr_models import ModelRegistry

        ModelRegistry().get("paraformer_large")
    except Exception as exc:  # 模型目录/注册表缺失
        return False, "缺少 Paraformer-Large 模型：%s" % exc
    if not WAV_A.is_file() or not WAV_B.is_file():
        return False, "缺少参考音频：%s" % DATA
    return True, ""


MODELS_OK, MODELS_REASON = models_ready()


class _Resources:
    """VAD + ASR 只加载一次，模型相关用例共用。"""

    def __init__(self):
        self.vad = None
        self.service = None


_RES = _Resources()


def resources():
    if _RES.service is not None:
        return _RES
    from funasr import AutoModel
    from qt_web.asr_models import ModelRegistry, configure_service

    try:
        vad_dir = resolve_path(SUPPORT_MODEL_DIRS["fsmn-vad"])
        _RES.vad = AutoModel(
            model=str(vad_dir),
            device="cpu",
            ncpu=2,
            disable_update=True,
            trust_remote_code=False,
            disable_pbar=True,
            disable_log=True,
        )
        svc = ASRService()
        configure_service(svc, ModelRegistry().get("paraformer_large"))
        if svc._load_local_model() is None:
            raise RuntimeError(svc.load_error() or "模型加载失败")
        _RES.service = svc
    except unittest.SkipTest:
        raise
    except Exception as exc:
        raise unittest.SkipTest("本地 ASR 模型不可用：%s" % exc)
    return _RES


class FilterRules(unittest.TestCase):
    """幻觉过滤规则（纯函数，不需要模型）。"""

    def test_drops_observed_hallucinations(self):
        """真实观测到的幻觉输出必须被丢弃。"""
        cases = [
            ("嗯，的是的。", 1000, 0),                       # 纯静音
            ("对对，有没有没有。", 2000, 0),                  # 纯静音
            ("没有没有没有没有没有意见。", 3000, 0),           # 纯静音（复读）
            ("好的好的。", 840, 200),                       # 200ms 瞬态噪声
            ("对不对？", 850, 200),                         # 200ms 瞬态噪声
            ("。。。", 800, 0),                            # 只有标点
            ("啊啊啊啊啊啊", 900, 0),                       # 单字复读
            ("请不吝点赞订阅转发打赏支持明镜与点点栏目", 3000, 0),  # 样板幻觉句
        ]
        for text, dur, voice in cases:
            with self.subTest(text=text):
                self.assertEqual(filter_hallucination(text, dur, voice), "")

    def test_keeps_real_speech(self):
        """正常语音（含很短的回答、口头重复）不能被误杀。"""
        cases = [
            ("你好，今天天气很不错。", 3455, 1820),
            ("打开设置页面并保存配置。", 3410, 2060),
            ("好。", 600, 260),
            ("好的。", 700, 320),
            ("快点快点快点", 1200, 780),
            ("今天的天气真的非常非常不错啊", 3000, 1800),
        ]
        for text, dur, voice in cases:
            with self.subTest(text=text):
                self.assertEqual(filter_hallucination(text, dur, voice), text)

    def test_active_ms(self):
        """active_ms：静音为 0，真实语音远大于 0，轻声说话仍算有声。"""
        self.assertEqual(active_ms(np.zeros(16000, dtype=np.float32)), 0.0)
        speech = load_wav(WAV_A)
        self.assertGreater(active_ms(speech), 1000)
        quiet = (speech * 0.12).astype(np.float32)
        # 轻声说话的有声时长会变短，但必须仍明显高于 350ms 的噪声阈值。
        self.assertGreater(active_ms(quiet), 300)

    def test_preroll_offsets_submission(self):
        """VAD 报起点后，实际提交识别的切片必须向前多留 PREROLL_MS。

        用桩 VAD 精确断言切片起点，不需要模型；删掉 `PREROLL_MS` 或不再前滚
        都会让这条失败。
        """
        from qt_web.voice_modes import TurnDetector
        from qt_web.voice_pipeline import AudioWindow, VoicePipeline

        class StubVad:
            def generate(self, **kwargs):
                return [{"value": [(500, 900)]}]  # 500ms 起、900ms 止

        submitted = []
        pipe = VoicePipeline(lambda *a, **k: None)
        pipe.vad = StubVad()
        pipe.window = AudioWindow()
        pipe.pending_start = None
        pipe.processed_samples = 0
        pipe.silence_ms = 800
        pipe.detector = TurnDetector("auto", 2000, 15000)
        pipe.cache = {}
        pipe._submit = lambda start, end: submitted.append((start, end))

        pipe.feed(np.zeros(16000, dtype=np.float32))

        preroll = VoicePipeline.PREROLL_MS
        self.assertGreater(preroll, 0, "前滚时长必须为正")
        self.assertEqual(submitted, [(500 * 16 - preroll * 16, 900 * 16)])


@unittest.skipUnless(MODELS_OK, MODELS_REASON)
class ModelTests(unittest.TestCase):
    """需要本地模型（首次约 5~15s 加载，全部用例约 30~60s）。"""

    @classmethod
    def setUpClass(cls):
        cls.res = resources()

    def feed_vad(self, audio, preroll_ms=None):
        """按 200ms 一块喂给真实 FSMN-VAD，返回实际提交识别的音频片段。"""
        from qt_web.voice_modes import TurnDetector
        from qt_web.voice_pipeline import AudioWindow, VoicePipeline

        segments = []
        pipe = VoicePipeline(lambda *a, **k: None)
        pipe.vad = self.res.vad
        pipe.window = AudioWindow()
        pipe.pending_start = None
        pipe.processed_samples = 0
        pipe.silence_ms = 800
        pipe.detector = TurnDetector("auto", 2000, 15000)
        pipe.cache = {}
        pipe.PREROLL_MS = VoicePipeline.PREROLL_MS if preroll_ms is None else preroll_ms

        def submit(start, end):
            end = min(end, pipe.window.end)
            segments.append(pipe.window.slice(max(0, start), end).copy())

        pipe._submit = submit
        chunk = 3200  # 200ms
        for i in range(0, len(audio), chunk):
            pipe.feed(audio[i:i + chunk], final=(i + chunk >= len(audio)))
        return segments

    def test_preroll_recovers_first_char(self):
        """VAD 起点偏晚 300/400ms 时，前滚 300ms 仍能识别出完整句子。

        前滚时长直接取生产代码的 `VoicePipeline.PREROLL_MS`（不另写常量），
        所以删掉/改小前滚都会让这条失败——这正是「你好」被识别成「好」的
        回归防线。
        """
        from qt_web.voice_pipeline import VoicePipeline

        preroll = VoicePipeline.PREROLL_MS
        for wav, phrase in ((WAV_A, PHRASE_A), (WAV_B, PHRASE_B)):
            speech = load_wav(wav)
            for late_ms in (300, 400):
                start = max(0, int(late_ms * 16) - preroll * 16)
                text = self.res.service._recognize_local(speech[start:])
                with self.subTest(phrase=phrase, late_ms=late_ms):
                    self.assertIn(phrase, text)

    def test_silence_produces_nothing(self):
        """纯静音不能产出任何文字。"""
        for samples in (16000, 32000):
            text = self.res.service._recognize_local(np.zeros(samples, dtype=np.float32))
            with self.subTest(ms=samples / 16):
                self.assertEqual(text, "")

    def test_noise_does_not_produce_sentences(self):
        """瞬态噪声（键盘声/碰麦）不能变成成句文字。

        极端情况下模型可能蹦出单个语气词（如「嗯」），这里允许最多 1 个
        有效字；但「好的好的。」这类编造的句子必须被拦住。
        """
        rs = np.random.RandomState(11)
        for dur_ms, level in ((200, 0.05), (200, 0.15), (300, 0.15)):
            burst = (rs.randn(int(dur_ms * 16)) * level).astype(np.float32)
            audio = np.concatenate([
                np.zeros(8000, dtype=np.float32),
                burst,
                np.zeros(16000, dtype=np.float32),
            ])
            for seg in self.feed_vad(audio):
                text = self.res.service._recognize_local(seg)
                with self.subTest(dur_ms=dur_ms, level=level, text=text):
                    self.assertLessEqual(core_len(text), 1, "瞬态噪声产生了成句文字：%r" % text)

    def test_speech_through_vad_is_complete(self):
        """正常语音经真实 VAD 分段后仍完整。"""
        for wav, phrase in ((WAV_A, PHRASE_A), (WAV_B, PHRASE_B)):
            speech = load_wav(wav)
            audio = np.concatenate([
                np.zeros(4000, dtype=np.float32),
                speech,
                np.zeros(14400, dtype=np.float32),
            ])
            texts = [self.res.service._recognize_local(seg) for seg in self.feed_vad(audio)]
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, "".join(t for t in texts if t))

    def test_quiet_speech_not_rejected(self):
        """轻声说话（RMS 约 0.005）不能被过滤规则误杀。"""
        for wav, phrase in ((WAV_A, PHRASE_A), (WAV_B, PHRASE_B)):
            quiet = (load_wav(wav) * 0.12).astype(np.float32)
            text = self.res.service._recognize_local(quiet)
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_pipeline_thread_filters_and_dedups(self):
        """生产识别线程：正常语音保留、相同长句只留一次。"""
        from qt_web.voice_pipeline import VoicePipeline

        pipe = VoicePipeline(lambda *a, **k: None)
        pipe.asr = self.res.service       # 复用已加载模型，避免二次加载
        pipe.session = 1
        pipe.mode = "manual"
        pipe.pending = 0
        pipe.ending = False
        pipe.output = []
        pipe._last_text = None

        speech = load_wav(WAV_A)
        for sid in (1, 2):                # 同一句送两次：模拟重复幻觉
            pipe.jobs.put((sid, speech))
            pipe.pending += 1

        results = [pipe.completed.get(timeout=180) for _ in (1, 2)]
        for _sid, text, error in results:
            self.assertIsNone(error)
            self.assertIn(PHRASE_A, text)

        for item in results:              # 再走一遍 tick 的收集/去重
            pipe.completed.put(item)
        pipe.pending = len(results)
        pipe.tick()
        self.assertEqual(len(pipe.output), 1, "重复长句未去重：%r" % (pipe.output,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
