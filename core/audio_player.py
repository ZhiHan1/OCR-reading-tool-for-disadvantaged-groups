"""
Windows 原生音频播放器（MCI）——用于播放在线语音返回的 MP3 与本机 SAPI 的 WAV。

为什么不用 pygame / sounddevice：
- 在线语音（edge-tts）返回的是 MP3 字节流，本机语音（SAPI）是 WAV 字节流；
- 项目本身走「进程内、零额外依赖」的路线，而 Windows 自带的 MCI
  （winmm.dll）可以直接解码播放 MP3 / WAV，且支持异步播放与随时停止，
  正好满足「朗读可以被立即打断」的需求。

线程模型：
- MCI 命令（open / play / status / stop / close）在同一线程内调用最稳妥，
  本类不做跨线程保护，由调用方保证同一实例只在同一线程使用。
"""
import ctypes
import os
import tempfile
from ctypes import wintypes

# MP3 码率固定为 48 kbps（在线语音与本机语音转码都按这个码率），
# 用于估算音频时长（超时判断用）
_MP3_BITRATE_BPS = 48000


class MciMp3Player:
    """基于 Windows MCI 的异步音频播放器（MP3 / WAV，无第三方依赖）。"""

    def __init__(self):
        # mciSendStringW(LPCWSTR command, LPWSTR ret, UINT ret_len, HWND hwnd)
        self._winmm = ctypes.WinDLL("winmm")
        self._winmm.mciSendStringW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.UINT,
            wintypes.HWND,
        ]
        self._winmm.mciSendStringW.restype = wintypes.UINT
        try:
            self._winmm.mciGetErrorStringW.argtypes = [
                wintypes.UINT,
                wintypes.LPWSTR,
                wintypes.UINT,
            ]
            self._winmm.mciGetErrorStringW.restype = wintypes.BOOL
        except Exception:
            pass

        self._alias = None
        self._path = None
        self._counter = 0

    # ===== 内部工具 =====

    def _send(self, command, buf_size=0):
        """发送一条 MCI 命令，返回 (错误码, 文本结果)"""
        if buf_size:
            buf = ctypes.create_unicode_buffer(buf_size)
            err = self._winmm.mciSendStringW(command, buf, buf_size, None)
            return err, buf.value
        err = self._winmm.mciSendStringW(command, None, 0, None)
        return err, ""

    def _error_text(self, err_code) -> str:
        if not err_code:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(err_code, buf, 256)
            return buf.value
        except Exception:
            return str(err_code)

    # ===== 播放控制 =====

    def play(self, audio_bytes) -> bool:
        """把音频字节写入临时文件并开始异步播放，返回是否成功开始。

        支持 MP3（在线语音）与 WAV（本机 SAPI 未装 lameenc 时的原声），
        按文件头自动选择 MCI 设备类型。
        """
        self.stop()
        if not audio_bytes:
            return False

        is_wav = (
            len(audio_bytes) >= 12
            and audio_bytes[:4] == b"RIFF"
            and audio_bytes[8:12] == b"WAVE"
        )
        suffix = ".wav" if is_wav else ".mp3"

        try:
            fd, path = tempfile.mkstemp(prefix="ocr_tts_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
        except Exception as e:
            print(f"[音频] 写入临时文件失败: {e}")
            return False

        self._counter += 1
        alias = f"ocrtts{self._counter}"

        # 先按明确类型打开；个别系统上显式类型会失败，再退回自动识别。
        if is_wav:
            commands = (
                f'open "{path}" type waveaudio alias {alias}',
                f'open "{path}" alias {alias}',
            )
        else:
            commands = (
                f'open "{path}" type mpegvideo alias {alias}',
                f'open "{path}" alias {alias}',
            )

        err = 1
        for command in commands:
            err, _ = self._send(command)
            if not err:
                break
        if err:
            print(f"[音频] 打开失败: {self._error_text(err)}")
            try:
                os.remove(path)
            except OSError:
                pass
            return False

        self._alias = alias
        self._path = path

        err, _ = self._send(f"play {alias}")
        if err:
            print(f"[音频] 播放失败: {self._error_text(err)}")
            self.stop()
            return False
        return True

    def is_playing(self) -> bool:
        """当前是否正在播放"""
        if not self._alias:
            return False
        err, mode = self._send(f"status {self._alias} mode", 64)
        if err:
            return False
        return mode.strip().lower() == "playing"

    def stop(self):
        """停止并释放当前音频（可随时调用）"""
        if self._alias:
            try:
                self._send(f"stop {self._alias}")
            except Exception:
                pass
            try:
                self._send(f"close {self._alias}")
            except Exception:
                pass
            self._alias = None

        if self._path:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._path = None

    def close(self):
        """释放资源（工作线程退出时调用）"""
        self.stop()


def estimate_duration(byte_count: int) -> float:
    """按固定码率估算 MP3 播放时长（秒），用于等待超时。"""
    return max(0.0, byte_count * 8.0 / _MP3_BITRATE_BPS)


def estimate_audio_duration(audio_bytes) -> float:
    """估算音频字节的播放时长（秒）

    WAV 直接读文件头（精确）；MP3 按固定 48 kbps 估算
    （在线语音与 lameenc 转码都用这个码率）。
    """
    if (
        len(audio_bytes) >= 12
        and audio_bytes[:4] == b"RIFF"
        and audio_bytes[8:12] == b"WAVE"
    ):
        try:
            import io
            import wave

            with wave.open(io.BytesIO(audio_bytes), "rb") as w:
                rate = w.getframerate() or 0
                if rate:
                    return max(0.0, w.getnframes() / float(rate))
        except Exception:
            pass

    return estimate_duration(len(audio_bytes or b""))
