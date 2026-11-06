"""WAV → MP3 转码（可选依赖 lameenc）。

为什么需要它：
- 微软在线语音（edge-tts）直接返回 MP3 字节，无需转码；
- 本机 SAPI 只能输出 WAV，而本页的导出约定是 MP3，
  所以用 lameenc（自带 LAME 的 Python 绑定，约 120 KB）转一次码。

未安装 lameenc 时 wav_to_mp3() 返回 None，由调用方明确提示安装，
不会悄悄改成导出 WAV——保持「导出即 MP3」的约定。
"""
import io
import wave


def is_available() -> bool:
    """lameenc 是否可用"""
    try:
        import lameenc  # noqa: F401
        return True
    except Exception:
        return False


def wav_to_mp3(wav_bytes, bit_rate: int = 48):
    """把 16-bit PCM WAV 转成 MP3 字节

    Args:
        wav_bytes: WAV 文件完整字节
        bit_rate: 目标码率（kbps）。在线语音固定 48 kbps，
                  这里保持一致，时长估算才通用。

    Returns:
        MP3 字节；失败或缺少 lameenc 时返回 None
    """
    if not wav_bytes:
        return None

    try:
        import lameenc
    except Exception:
        return None

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels = w.getnchannels()
            sample_rate = w.getframerate()
            sample_width = w.getsampwidth()
            pcm = w.readframes(w.getnframes())
    except Exception as e:
        print(f"[MP3] 解析 WAV 失败: {e}")
        return None

    if not pcm:
        return None
    if sample_width != 2:
        # lameenc 只接受 16-bit PCM；其它位深（很罕见）直接放弃
        print(f"[MP3] 不支持的位深: {sample_width * 8} bit")
        return None

    try:
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(int(bit_rate))
        encoder.set_in_sample_rate(int(sample_rate))
        encoder.set_channels(int(channels))
        encoder.set_quality(2)
        data = encoder.encode(pcm)
        data += encoder.flush()
        return bytes(data) if data else None
    except Exception as e:
        print(f"[MP3] 编码失败: {e}")
        return None
