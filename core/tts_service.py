"""
TTS 朗读服务 - 独立模块
职责：提供文字转语音功能
"""
from typing import Optional
from . import edge_tts_backend
from .tts import TTSManager, get_available_voices


class TTSService:
    """TTS朗读服务"""

    def __init__(self, rate: int = None, volume: float = None, voice_index: int = None,
                 voice_name: str = None, pitch: int = None,
                 backend: str = None, edge_voice: str = None):
        """
        初始化TTS服务

        Args:
            rate: 语速 (-10 到 10)
            volume: 音量 (0.0 到 1.0)
            voice_index: 语音索引
            voice_name: 本机语音名称
            pitch: 音色 (-10 到 10)
            backend: 语音来源，"sapi"（本机）或 "edge"（微软在线）
            edge_voice: 在线语音短名称（如 zh-CN-XiaoxiaoNeural）
        未传入的参数一律回退到 config.yaml 中的 tts 配置。
        """
        self.manager = TTSManager(
            rate=rate, volume=volume, voice_index=voice_index,
            voice_name=voice_name, pitch=pitch,
            backend=backend, edge_voice=edge_voice,
        )
        self.manager.start()

    def speak(self, text: str, interrupt: bool = False, dedup: bool = True):
        """
        朗读文本

        Args:
            text: 要朗读的文本
            interrupt: 是否打断当前朗读
            dedup: 是否启用「同一句 2 秒内不重复朗读」的兜底去重。
                   功能1/功能2 手动触发必须传 False，否则重复点同一句会被静默丢弃。
        """
        if text:
            self.manager.add(text, interrupt=interrupt, dedup=dedup)

    def stop_current(self):
        """停止当前朗读"""
        self.manager.stop_current()

    def stop(self):
        """停止TTS服务"""
        self.manager.stop()

    def restart(self):
        """重启TTS服务"""
        self.manager.start()

    def update_settings(self, rate: Optional[int] = None,
                       volume: Optional[float] = None,
                       voice_index: Optional[int] = None,
                       voice_name: Optional[str] = None,
                       pitch: Optional[int] = None,
                       backend: Optional[str] = None,
                       edge_voice: Optional[str] = None):
        """
        更新TTS设置

        Args:
            rate: 新的语速
            volume: 新的音量
            voice_index: 新的语音索引
            voice_name: 新的本机语音名称
            pitch: 新的音色
            backend: 新的语音来源（"sapi" / "edge"）
            edge_voice: 新的在线语音名称
        """
        if rate is not None:
            self.manager.set_rate(rate)
        if volume is not None:
            self.manager.set_volume(volume)
        if pitch is not None:
            self.manager.set_pitch(pitch)
        if voice_name is not None:
            self.manager.set_voice(voice_name)
        if backend is not None:
            self.manager.set_backend(backend)
        if edge_voice is not None:
            self.manager.set_edge_voice(edge_voice)
        if voice_index is not None:
            # 重新初始化
            self.stop()
            self.manager = TTSManager(
                rate=rate if rate is not None else self.manager.rate,
                volume=volume if volume is not None else self.manager.volume,
                voice_index=voice_index,
                voice_name=voice_name if voice_name is not None else self.manager.voice_name,
                pitch=pitch if pitch is not None else self.manager.pitch,
                backend=backend if backend is not None else self.manager.backend,
                edge_voice=edge_voice if edge_voice is not None else self.manager.edge_voice,
            )
            self.manager.start()

    def get_available_voices(self):
        """获取本机可用的语音列表"""
        return get_available_voices()

    @staticmethod
    def get_edge_voices():
        """获取中文在线语音列表"""
        return edge_tts_backend.get_voices()

    @staticmethod
    def is_edge_available():
        """在线语音是否可用（edge-tts 是否已安装）"""
        return edge_tts_backend.is_available()
