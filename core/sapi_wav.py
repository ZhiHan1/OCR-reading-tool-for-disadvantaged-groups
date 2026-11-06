"""用 Windows SAPI 把文字合成为 WAV 字节（文字转语音页的「本机语音」用）。

与 core.tts 的朗读路径互不影响：
- 朗读是把 SpVoice 直接接到声卡（异步、可随时 purge）；
- 这里是离线合成：把 SpVoice 的输出接到 SpFileStream，等整段合成完再读出来，
  因此本模块是同步阻塞的，必须放在后台线程调用。

语速/音量与朗读完全一致（直接用 SpVoice 的 Rate/Volume）；
语调同样走 SSML 的 prosody（系统语音不支持单独的 pitch 参数）。

线程模型：
- COM 对象只在调用线程内创建/释放，进入函数时 CoInitialize、退出时 CoUninitialize；
- 所有 COM 对象都在 _synthesize() 里，函数返回时即释放，
  保证释放发生在 CoUninitialize 之前（否则退出会打印 IUnknown 释放异常）。
"""
import os
import tempfile

from core.tts import SPF_IS_XML, clamp_level, clamp_volume

# SpeechStreamFileMode.SSFMCreateForWrite
_SSFM_CREATE_FOR_WRITE = 3


def is_available() -> bool:
    """本机语音是否可用（pywin32 是否安装）"""
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def synthesize_wav(text, voice_name=None, rate=0, volume=1.0, pitch=0) -> bytes:
    """把文字合成为 WAV 字节

    Args:
        text: 要合成的文字
        voice_name: SAPI 语音名称（空则用系统默认）
        rate: 语速 -10 ~ 10（与朗读一致）
        volume: 音量 0.0 ~ 1.0
        pitch: 语调 -10 ~ 10（通过 SSML prosody 换算）

    Returns:
        WAV 文件的完整字节；text 为空时返回 b""

    Raises:
        RuntimeError: 缺少 pywin32 或 SAPI 初始化失败
    """
    text = str(text or "").strip()
    if not text:
        return b""

    try:
        import pythoncom
    except Exception as e:
        raise RuntimeError("本机语音需要 pywin32：pip install pywin32") from e

    pythoncom.CoInitialize()
    try:
        return _synthesize(text, voice_name, rate, volume, pitch)
    finally:
        pythoncom.CoUninitialize()


def _synthesize(text, voice_name, rate, volume, pitch) -> bytes:
    import win32com.client

    voice = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")

    fd, path = tempfile.mkstemp(prefix="ocr_sapi_", suffix=".wav")
    os.close(fd)
    try:
        stream.Open(path, _SSFM_CREATE_FOR_WRITE, False)
        voice.AudioOutputStream = stream
        voice.Rate = clamp_level(rate)
        voice.Volume = clamp_volume(volume)

        if voice_name:
            try:
                # 与朗读用同样的令牌过滤语法
                tokens = voice.GetVoices(f"Name={voice_name}")
                if tokens.Count > 0:
                    voice.Voice = tokens.Item(0)
            except Exception as e:
                print(f"[本机语音] 切换语音失败({voice_name}): {e}")

        pitch = clamp_level(pitch)
        if pitch:
            # 文本必须先转义，否则 < & > 会让 SSML 解析失败
            safe = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            ssml = (
                '<speak version="1.0" '
                'xmlns="http://www.w3.org/2001/10/synthesis">'
                f'<prosody pitch="{pitch * 5:+d}%">{safe}</prosody>'
                "</speak>"
            )
            voice.Speak(ssml, SPF_IS_XML)
        else:
            voice.Speak(text)

        stream.Close()

        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            stream.Close()
        except Exception:
            pass
        try:
            os.remove(path)
        except OSError:
            pass
