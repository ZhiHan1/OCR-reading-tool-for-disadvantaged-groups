"""TTS 音频导出（MP3）—— 在 GUI 层补齐，不改动 core / controllers / services。

背景：后端 ``TTSService`` 只提供「朗读」（``speak`` / ``stop_current`` …），
没有导出接口。UI 需要「导出 MP3」，因此把合成+落盘能力放在 Web UI 这一层，
复用后端已有的合成实现，保证导出结果与试听**完全一致**：

- 在线语音 (edge)：直接调用 ``edge_tts_backend.EdgeTTSClient.synthesize``，
  它返回的就是 MP3 字节；参数换算沿用 ``rate_to_ssml`` / ``volume_to_ssml`` /
  ``pitch_to_ssml``，与 ``TTSManager._edge_params`` 同一套规则。
- 本机语音 (sapi)：``SpVoice`` 写入临时 WAV（同步朗读到文件流），再用
  ``lameenc`` 编码成 MP3；语速/音量/语音/音调的处理方式与 ``_SapiVoice``
  保持一致（音调走 SSML 的 prosody，因为 System.Speech 没有 pitch 参数）。

本模块是纯函数式的，不持有状态，可在线程里安全调用。
"""
import os
import re
import tempfile
import wave
from datetime import datetime
from typing import Callable, Optional

# 与 core/tts.py 中的 SAPI 标志位保持一致（只读引用，不改后端）
_SPF_IS_XML = 8
_SSFM_CREATE_FOR_WRITE = 3

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------- 路径与文件名


def default_dir() -> str:
    """默认导出目录：系统「文档」目录，取不到就退回用户主目录。"""
    try:
        from PySide6.QtCore import QStandardPaths

        path = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass

    for candidate in (
        os.path.join(os.path.expanduser("~"), "Documents"),
        os.path.expanduser("~"),
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.getcwd()


def build_filename(pattern: str, ext: str = ".mp3") -> str:
    """把用户填写的文件名模板展开成真实文件名。

    - 支持 ``%Y%m%d`` 等 strftime 占位符
    - 留空则用 ``语音_年月日_时分秒``
    - 自动去掉非法字符、补齐扩展名
    """
    raw = str(pattern or "").strip()
    if not raw:
        raw = "语音_%Y%m%d_%H%M%S"

    try:
        name = datetime.now().strftime(raw)
    except Exception:
        name = raw

    name = _ILLEGAL.sub("_", name).strip().strip(".")
    if not name:
        name = datetime.now().strftime("语音_%Y%m%d_%H%M%S")

    if not name.lower().endswith(ext):
        name += ext
    return name


def _resolve_target(out_dir: str, filename: str) -> str:
    directory = str(out_dir or "").strip() or default_dir()
    directory = os.path.abspath(os.path.expanduser(directory))
    os.makedirs(directory, exist_ok=True)

    target = os.path.join(directory, build_filename(filename))
    # 重名不覆盖：追加 (2)(3)…
    if os.path.exists(target):
        stem, ext = os.path.splitext(target)
        index = 2
        while os.path.exists(f"{stem}({index}){ext}"):
            index += 1
        target = f"{stem}({index}){ext}"
    return target


# ---------------------------------------------------------------- 两个后端


def _export_edge(text, voice, rate, pitch, volume, progress) -> bytes:
    """在线语音：直接拿到 MP3 字节。"""
    from core import edge_tts_backend as edge

    if not edge.is_available():
        raise RuntimeError("在线语音不可用（未安装 edge-tts）")

    voice = str(voice or "").strip() or "zh-CN-XiaoxiaoNeural"
    progress(0.15, "正在合成（在线语音）…")

    client = edge.EdgeTTSClient()
    try:
        audio = client.synthesize(
            text,
            voice,
            edge.rate_to_ssml(rate),
            edge.volume_to_ssml(volume),
            edge.pitch_to_ssml(pitch),
        )
    finally:
        try:
            client.close()
        except Exception:
            pass

    if not audio:
        raise RuntimeError("合成失败：在线语音没有返回音频（请检查网络）")
    progress(0.75, "合成完成，正在写入文件…")
    return bytes(audio)


def _export_sapi(text, voice, rate, pitch, volume, progress) -> bytes:
    """本机语音：SpVoice 写 WAV → lameenc 编 MP3。"""
    import pythoncom
    import win32com.client

    from core.tts import clamp_level, clamp_volume

    pythoncom.CoInitialize()
    tmp_path = None
    try:
        progress(0.15, "正在合成（本机语音）…")

        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Rate = clamp_level(rate)
        speaker.Volume = clamp_volume(volume)

        if voice:
            try:
                tokens = speaker.GetVoices(f"Name={voice}")
                if tokens.Count > 0:
                    speaker.Voice = tokens.Item(0)
            except Exception as e:  # noqa: BLE001
                print(f"[导出] 切换语音失败({voice}): {e}")

        handle, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)

        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(tmp_path, _SSFM_CREATE_FOR_WRITE, False)
        try:
            speaker.AudioOutputStream = stream
            level = clamp_level(pitch)
            if level:
                safe = (
                    str(text)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                ssml = (
                    '<speak version="1.0" '
                    'xmlns="http://www.w3.org/2001/10/synthesis">'
                    f'<prosody pitch="{level * 5:+d}%">{safe}</prosody>'
                    "</speak>"
                )
                # 同步朗读到文件（不带 SPF_ASYNC，否则文件还没写完就 Close）
                speaker.Speak(ssml, _SPF_IS_XML)
            else:
                speaker.Speak(str(text))
        finally:
            try:
                stream.Close()
            except Exception:
                pass

        progress(0.6, "正在编码 MP3…")

        with wave.open(tmp_path, "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())

        if not frames:
            raise RuntimeError("合成失败：本机语音没有产生音频数据")

        import lameenc

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        mp3 = encoder.encode(frames) + encoder.flush()

        if not mp3:
            raise RuntimeError("MP3 编码失败")
        progress(0.9, "正在写入文件…")
        return bytes(mp3)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------- 对外入口


def export_mp3(
    text: str,
    backend: str = "sapi",
    voice: str = "",
    rate: int = 0,
    pitch: int = 0,
    volume: float = 1.0,
    out_dir: str = "",
    filename: str = "",
    progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """把一段文字合成为 MP3 并保存，返回 ``{"ok", "path"|"error"}``。

    该函数会阻塞（合成 + 编码），请在线程里调用。
    """
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "请先输入要导出的文字"}

    report = progress or (lambda ratio, label: None)

    try:
        target = _resolve_target(out_dir, filename)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"无法创建导出目录：{e}"}

    try:
        if str(backend).lower() == "edge":
            audio = _export_edge(text, voice, rate, pitch, volume, report)
        else:
            audio = _export_sapi(text, voice, rate, pitch, volume, report)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}

    try:
        with open(target, "wb") as fh:
            fh.write(audio)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"写入文件失败：{e}"}

    report(1.0, "导出完成")
    return {"ok": True, "path": target, "size": len(audio)}
