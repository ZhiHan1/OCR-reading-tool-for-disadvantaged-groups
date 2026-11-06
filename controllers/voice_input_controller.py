"""语音输入控制器：语音转文字

职责：
- 管理录音与识别流程：开始录音 → 停止录音 → 识别 → 回调结果。
- 支持「按住说话」：按下开始录音，松开停止并识别。
- 结果通过 on_result / on_status_update 回调通知 UI（线程安全）。

设计说明：
- 录音与识别都在后台线程执行，UI 线程不阻塞。
- 结果回调直接复用主窗口的 _show_result / _update_status（与 OCR 功能一致）。
- 控制器不直接操作 UI 控件，只通过回调向外传结果，保持分层清晰。
"""
import threading
from typing import Callable, Optional

from core.audio_capture import AudioCapture, is_available as mic_available
from core.asr_service import ASRService


class VoiceInputController:
    """语音输入控制器"""

    MODE = "voice_input"
    LABEL = "语音输入"

    def __init__(self, asr_service: ASRService):
        self.asr = asr_service
        self.capture = AudioCapture()

        # 回调（由 UI 层装配）
        self.on_status_update: Optional[Callable[[str], None]] = None
        self.on_result: Optional[Callable[[str, str], bool]] = None

        self._recording = False
        self._owner = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def recording_owner(self):
        with self._lock:
            return self._owner if self._recording else None

    # ===== 按住说话 =====

    def start_recording(self, source="button") -> bool:
        """开始录音（按住说话）

        Returns:
            bool: 是否成功开始
        """
        if not mic_available():
            self._emit_status("❌ 未安装 sounddevice，无法录音（pip install sounddevice）")
            return False

        with self._lock:
            if self._recording:
                return False
            # 从占用检查到打开音频流保持同一把锁，防止两个入口同时启动。
            if not self.capture.start():
                ok = False
            else:
                self._recording = True
                self._owner = str(source or "button")
                ok = True
        if not ok:
            self._emit_status("❌ 麦克风启动失败，请检查设备")
            return False
        self._emit_status("🎤 正在录音...")
        return True

    def stop_and_recognize(self, source=None) -> bool:
        """停止录音并识别（松开说话）"""
        with self._lock:
            if not self._recording:
                return False
            if source is not None and self._owner != str(source):
                return False
            self._recording = False
            self._owner = None

        audio = self.capture.stop()
        if audio is None or len(audio) == 0:
            self._emit_status("未检测到语音")
            return True

        duration = self.capture.duration(audio)
        if duration < 0.2:
            self._emit_status("语音太短，请重试")
            return True

        self._emit_status("识别中...")
        threading.Thread(
            target=self._recognize_worker,
            args=(audio,),
            daemon=True,
            name="VoiceRecognize",
        ).start()
        return True

    def cancel(self):
        """取消当前录音（不识别）"""
        with self._lock:
            self._recording = False
            self._owner = None
        self.capture.stop()
        self._emit_status("已取消")

    # ===== 内部 =====

    def _recognize_worker(self, audio):
        """后台识别线程"""
        try:
            text = self.asr.recognize(audio)
        except Exception as e:
            print(f"[语音输入] 识别异常: {e}")
            self._emit_status("❌ 识别失败")
            return

        if not text:
            self._emit_status("未识别到内容")
            return

        # 结果回调（复用 _show_result，进入翻译链路判定）
        handled = False
        if self.on_result is not None:
            try:
                handled = bool(self.on_result(text, display=f"[{self.LABEL}] {text}"))
            except Exception as e:
                print(f"[语音输入] 结果回调失败: {e}")

        if not handled:
            self._emit_status("✅ 已识别")

    def _emit_status(self, text: str):
        if self.on_status_update is not None:
            try:
                self.on_status_update(text)
            except Exception as e:
                print(f"[语音输入] 状态回调失败: {e}")
