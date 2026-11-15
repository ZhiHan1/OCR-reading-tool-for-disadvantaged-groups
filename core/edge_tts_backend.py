"""
微软 Edge 在线语音（edge-tts）后端。

说明：
- 使用微软 Edge 浏览器的在线神经网络语音接口（开源库 edge-tts），
  无需 Azure Key / Region，也无需付费；代价是必须联网、有网络延迟。
- 按照需求，本模块只提供**中文**语音（普通话 / 粤语 / 台湾国语）。
- 合成失败（断网、接口变更等）时由 TTSManager 自动回退到本机 SAPI，
  保证离线时朗读功能仍然可用。

线程模型：
- 合成是异步的，但 edge-tts 的 Communicate 只能在同一个事件循环里用；
  这里为 TTS 工作线程维护一个常驻事件循环，避免每句都新建/销毁。
"""
import asyncio
import importlib.util

# ===== 中文语音列表 =====
# name  : edge-tts 的短名称（写入配置 tts.edge_voice）
# label : 中文显示名
# gender: 性别（用于展示）
# locale: 语言/方言（用于展示）
EDGE_VOICES = [
    {"name": "zh-CN-XiaoxiaoNeural", "label": "晓晓", "gender": "女声", "locale": "普通话"},
    {"name": "zh-CN-XiaoyiNeural", "label": "晓伊", "gender": "女声", "locale": "普通话"},
    {"name": "zh-CN-YunxiNeural", "label": "云希", "gender": "男声", "locale": "普通话"},
    {"name": "zh-CN-YunjianNeural", "label": "云健", "gender": "男声", "locale": "普通话"},
    {"name": "zh-CN-YunyangNeural", "label": "云扬", "gender": "男声", "locale": "普通话"},
    {"name": "zh-CN-YunxiaNeural", "label": "云夏", "gender": "男声·少年", "locale": "普通话"},
    {"name": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北", "gender": "女声", "locale": "辽宁话"},
    {"name": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮", "gender": "女声", "locale": "陕西话"},
    {"name": "zh-HK-HiuMaanNeural", "label": "曉曼", "gender": "女声", "locale": "粤语"},
    {"name": "zh-HK-HiuGaaiNeural", "label": "曉佳", "gender": "女声", "locale": "粤语"},
    {"name": "zh-HK-WanLungNeural", "label": "雲龍", "gender": "男声", "locale": "粤语"},
    {"name": "zh-TW-HsiaoChenNeural", "label": "曉臻", "gender": "女声", "locale": "台湾国语"},
    {"name": "zh-TW-HsiaoYuNeural", "label": "曉雨", "gender": "女声", "locale": "台湾国语"},
    {"name": "zh-TW-YunJheNeural", "label": "雲哲", "gender": "男声", "locale": "台湾国语"},
]

DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"

# 合成过程中检查「是否被打断」的间隔（秒）
_INTERRUPT_POLL = 0.05

_VOICE_NAMES = {v["name"] for v in EDGE_VOICES}

# edge-tts 是否安装（只探测一次）
_available_cache = None


def is_available() -> bool:
    """edge-tts 是否可用（已安装）"""
    global _available_cache
    if _available_cache is None:
        try:
            _available_cache = importlib.util.find_spec("edge_tts") is not None
        except Exception:
            _available_cache = False
    return _available_cache


def get_voices():
    """返回中文语音列表（副本，避免调用方改动内部常量）"""
    return [dict(v) for v in EDGE_VOICES]


# ===== 参数换算：SAPI 的 -10~10 / 0.0~1.0 → SSML 字符串 =====

def _clamp_level(level) -> int:
    try:
        return int(max(-10, min(10, int(round(float(level))))))
    except (TypeError, ValueError):
        return 0


def rate_to_ssml(rate) -> str:
    """语速：SAPI 的 -10~10 映射为 -100%~+100%"""
    return f"{_clamp_level(rate) * 10:+d}%"


def pitch_to_ssml(pitch) -> str:
    """音调：SAPI 的 -10~10 映射为 -50Hz~+50Hz"""
    return f"{_clamp_level(pitch) * 5:+d}Hz"


def volume_to_ssml(volume) -> str:
    """音量：0.0~1.0 映射为 -100%~+0%（+0% 为原始音量）"""
    try:
        value = float(volume)
    except (TypeError, ValueError):
        value = 1.0
    pct = int(round((max(0.0, min(1.0, value)) - 1.0) * 100))
    return f"{pct:+d}%"


class EdgeTTSClient:
    """edge-tts 的同步封装（由 TTS 工作线程调用）

    维护一个常驻事件循环，把异步的流式合成转成同步的字节返回。
    """

    def __init__(self):
        self._loop = None

    def _ensure_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    @staticmethod
    async def _consume(communicate, chunks):
        async for message in communicate.stream():
            if message.get("type") == "audio":
                chunks.append(message["data"])

    async def _run(self, text, voice, rate, volume, pitch, should_stop):
        """消费音频流；被打断时取消任务并返回 (音频, 是否被打断)"""
        import edge_tts

        communicate = edge_tts.Communicate(
            text, voice, rate=rate, volume=volume, pitch=pitch
        )
        chunks = []
        cancelled = False

        task = asyncio.ensure_future(self._consume(communicate, chunks))
        # 边收边轮询打断信号；取消任务会让异常从最内层 await 抛出，
        # 逐层解开 async with，把 WebSocket / HTTP 会话正常关闭（不留残留连接）。
        while not task.done():
            await asyncio.sleep(_INTERRUPT_POLL)
            if should_stop is not None and should_stop():
                task.cancel()
                cancelled = True
                break

        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if not cancelled:
                raise

        return b"".join(chunks), cancelled

    def synthesize(self, text, voice, rate, volume, pitch, should_stop=None):
        """合成一段文本，返回 MP3 字节；被打断/无音频时可能返回 b""。

        Args:
            text: 待合成文本
            voice: 语音短名称
            rate/volume/pitch: 已换算好的 SSML 字符串
            should_stop: 无参回调，返回 True 时尽快中止合成
        """
        text = str(text).strip()
        if not text:
            return b""

        loop = self._ensure_loop()
        audio, cancelled = loop.run_until_complete(
            self._run(text, voice, rate, volume, pitch, should_stop)
        )
        if cancelled:
            # 被取消的流式生成器需要收尾（关闭底层连接），否则会残留会话
            self._finalize_asyncgens(loop)
            return b""
        return audio

    @staticmethod
    def _finalize_asyncgens(loop):
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass

    def close(self):
        loop = self._loop
        self._loop = None
        if loop is not None and not loop.is_closed():
            self._finalize_asyncgens(loop)
            try:
                loop.close()
            except Exception:
                pass
