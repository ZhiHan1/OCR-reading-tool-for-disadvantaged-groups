"""工作进程内的连续采集/VAD/串行 ASR。界面层适配，不修改 core 服务。"""
from collections import deque
import queue
import threading
import time

import numpy as np

from core.audio_capture import AudioCapture
from core.asr_service import ASRService, SUPPORT_MODEL_DIRS
from core.config_manager import reload_config
from qt_web.asr_models import configure_service
from qt_web.voice_modes import TurnDetector
from utils.paths import resolve_path


class LiveCapture(AudioCapture):
    def __init__(self, frames, notify_level, device_name=''):
        super().__init__(device_name=device_name)
        self.frames = frames
        self.notify_level = notify_level
        self.overflow = False
        self.last_level = 0.0
        # 静音自检用：记录本次录音中出现过的最大 RMS 与已采集采样数。
        # 光看某一帧为 0 没有意义（人说话之间本来就有停顿），必须看
        # 「一整段时间内有没有出现过任何声音」。
        self.peak_rms = 0.0
        self.samples = 0

    def _callback(self, indata, frames, time_info, status):
        if status.input_overflow:
            self.overflow = True
        try:
            self.frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.overflow = True
        rms = float(np.sqrt(np.mean(np.square(indata))))
        self.peak_rms = max(self.peak_rms, rms)
        self.samples += len(indata)
        now = time.monotonic()
        if now - self.last_level >= 0.033:
            self.last_level = now
            self.notify_level(min(1.0, rms * 12.0))

    def silent_for(self, seconds):
        """已采集满指定秒数，且整段时间的电平始终低于「绝对静音」阈值。

        阈值取得很低（-60 dBFS 量级），只用来抓「设备根本没在出声音」
        （休眠唤醒后端点失效、麦克风被物理静音、驱动重置），
        不会把「用户只是还没开口」误判成故障。
        """
        if self.samples < self.sample_rate * seconds:
            return False
        return self.peak_rms < 0.001


class AudioWindow:
    """绝对采样坐标缓存，只保留尚未送去识别的语音与少量前滚。"""
    def __init__(self):
        self.chunks = deque()
        self.end = 0

    def append(self, audio):
        self.chunks.append((self.end, audio))
        self.end += len(audio)

    def slice(self, start, end):
        parts = [a[max(0, start-pos):min(len(a), end-pos)]
                 for pos, a in self.chunks if pos < end and pos+len(a) > start]
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)

    def trim(self, before):
        while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) <= before:
            self.chunks.popleft()


class VoicePipeline:
    # FSMN-VAD 的起点通常比真实发声晚 100~300ms，直接按 VAD 起点切片会吞掉
    # 第一个字（例如「你好」的「你」）。这里统一向前多留 300ms 作为前滚。
    PREROLL_MS = 300

    # 静音自检窗口：连续这么多秒采集到的电平都低于「绝对静音」阈值，
    # 判定为设备没在出声音（而不是用户没开口）。取 5 秒是因为人正常呼吸/
    # 环境底噪都会产生 > -60 dBFS 的电平；真静音只可能是设备侧问题。
    SILENCE_CHECK_SECONDS = 5.0

    def __init__(self, send):
        self.send = send
        self.asr = ASRService()
        self.vad = None
        self.capture = None
        self.active = False
        self.ending = False
        self.session = 0
        self.jobs = queue.Queue(maxsize=32)
        self.completed = queue.Queue()
        self.pending = 0
        self.output = []
        self.last_heartbeat = 0.0
        self.frames = queue.Queue(maxsize=500)
        self.thread = threading.Thread(target=self._recognize, daemon=True, name='ASRSegments')
        self.thread.start()

    def emit(self, kind, **data):
        self.send(kind, session=self.session, **data)

    def is_idle(self):
        """没有录音、没有待识别任务、也没有等待收尾。"""
        return not self.active and not self.pending and not getattr(self, 'ending', False)

    def prepare(self, msg):
        self.session = msg['session']
        self.asr.config = reload_config()
        configure_service(self.asr, msg['model'])
        self.emit('preparing', text='正在准备本地识别模型…')
        if self.asr._load_local_model() is None:
            raise RuntimeError(self.asr.load_error() or '模型加载失败')
        self.emit('model', text='已加载：' + self.asr.model_label())

    def start(self, msg):
        if self.active or self.pending:
            raise RuntimeError('上一语音任务尚未结束')
        self.prepare(msg)
        options = msg['options']
        self.mode = options['mode']
        self.silence_ms = options['segment_silence_ms']
        self.detector = TurnDetector(self.mode, options['turn_silence_ms'], options['initial_silence_ms'])
        if self.vad is None:
            from funasr import AutoModel
            path = resolve_path(self.asr.config.get('asr', 'vad_path', default=SUPPORT_MODEL_DIRS['fsmn-vad']))
            if not (path / 'model.pt').is_file() or not (path / 'config.yaml').is_file():
                raise RuntimeError('缺少本地 FSMN-VAD，无法进行停顿分段')
            self.vad = AutoModel(model=str(path), device='cpu', ncpu=2, disable_update=True,
                                 trust_remote_code=False, disable_pbar=True, disable_log=True)
        self.cache = {}
        self.window = AudioWindow()
        self.pending_start = None
        self.held = None
        self.processed_samples = 0
        self.frames = queue.Queue(maxsize=500)
        self.output = []
        self.pending = 0
        self.ending = False
        self.stop_reason = ''
        self._last_text = None
        # 麦克风来自配置；空字符串表示跟随系统默认输入设备。
        self.device_name = str(self.asr.config.get('asr', 'device_name', default='') or '')
        self.capture = LiveCapture(self.frames, lambda value: self.emit('level', value=value),
                                   device_name=self.device_name)
        if not self.capture.start():
            if self.device_name:
                raise RuntimeError(
                    f'麦克风启动失败：找不到或无法打开「{self.device_name}」，'
                    '请检查设备是否已连接，或换一个输入设备')
            raise RuntimeError('麦克风启动失败，请检查输入设备')
        self.active = True
        self.emit('recording')

    def _recognize(self):
        while True:
            sid, audio = self.jobs.get()
            try:
                # 选定模型已在 prepare 中加载。直接调用以暴露推理错误，避免静默吞错。
                from core.asr_service import (
                    _clean_text, active_ms, filter_hallucination, local_generate_kwargs,
                )
                from core.hotwords import apply_hotwords
                kind = self.asr._model_kind or self.asr._model_name or 'paraformer_large'
                kwargs = local_generate_kwargs(kind)
                result = self.asr._model.generate(input=audio, batch_size_s=60, disable_pbar=True, **kwargs)
                text = _clean_text(result[0].get('text', '')) if result else ''
                # 热词必须在 _clean_text 之后：generate 返回的是带空格的分字形式，
                # 拼音匹配器按连续串匹配，先纠错会全部漏掉（见 core/hotwords.py）。
                text = apply_hotwords(text)
                text = filter_hallucination(text, len(audio) / 16, active_ms(audio))
                self.completed.put((sid, text, None))
            except Exception as exc:
                self.completed.put((sid, '', str(exc)))
            finally:
                self.jobs.task_done()

    def _submit(self, start, end):
        audio = self.window.slice(max(0, start), min(end, self.window.end))
        if len(audio) < 3200:
            return
        try:
            self.jobs.put_nowait((self.session, audio))
        except queue.Full:
            raise RuntimeError('识别速度跟不上录音，已停止；请换用更快模型')
        self.pending += 1

    def feed(self, audio, final=False):
        self.window.append(audio)
        self.processed_samples += len(audio)
        results = self.vad.generate(input=audio, cache=self.cache, fs=16000, chunk_size=200,
                                    is_streaming_input=True, is_final=final, batch_size=1,
                                    dynamic_silence=False, max_end_silence_time=self.silence_ms,
                                    disable_pbar=True)
        for result in results:
            events = result.get('value', [])
            self.detector.events(events)
            for start_ms, end_ms in events:
                if start_ms >= 0:
                    if self.pending_start is not None:
                        raise RuntimeError('VAD 返回了重复的起点，请重新录音')
                    # 向前多留 PREROLL_MS，避免 FSMN-VAD 起点偏晚吞掉首字。
                    preroll = self.PREROLL_MS * 16
                    self.pending_start = max(0, int(start_ms * 16) - preroll)
                if end_ms >= 0 and self.pending_start is not None:
                    self._submit(self.pending_start, int(end_ms * 16))
                    self.pending_start = None
        # 很长且无停顿的语音分块识别，但绝不据此自动结束整轮发言。
        if self.pending_start is not None and self.processed_samples - self.pending_start >= 480000:
            self._submit(self.pending_start, self.processed_samples)
            self.pending_start = self.processed_samples
        retain = self.pending_start if self.pending_start is not None else self.processed_samples - 32000
        self.window.trim(max(0, retain))

    def tick(self):
        if self.active:
            if self.capture.overflow:
                raise RuntimeError('音频缓冲溢出，已停止以避免丢字；请减少系统负载')
            # 静音自检：录音流开了却一直没任何电平，说明设备在「假工作」。
            # 最常见原因是电脑休眠唤醒后 PortAudio 设备表过期——唤醒事件
            # 会重启工作进程，但若事件丢失仍需这里兜底，给出可操作的提示，
            # 而不是 15 秒后含糊地说「未检测到说话」。
            if self.capture.silent_for(self.SILENCE_CHECK_SECONDS):
                raise RuntimeError(
                    '麦克风没有声音：录音已打开但一直采集到静音。'
                    '常见原因是电脑休眠唤醒后音频设备失效，'
                    '请重新插拔麦克风，或在语音页点「刷新」后重新开始；'
                    '若仍无效请重启本软件')
            # 保留最后一个非空块，在 stop 时用 is_final=True 只提交一次。
            for _ in range(8):
                try:
                    audio = self.frames.get_nowait()
                except queue.Empty:
                    break
                if self.held is not None:
                    self.feed(self.held)
                self.held = audio
                if self.detector.should_stop(self.processed_samples / 16):
                    self.stop_reason = '发言结束，已暂停' if self.detector.heard_speech else '未检测到说话，已暂停'
                    self.stop()
                    break
            now = time.monotonic()
            if now - self.last_heartbeat > 1:
                self.last_heartbeat = now
                self.emit('heartbeat', pending=self.pending, speech=self.detector.in_speech)
        while True:
            try:
                sid, text, error = self.completed.get_nowait()
            except queue.Empty:
                break
            if sid != self.session:
                continue
            self.pending -= 1
            if error:
                raise RuntimeError('语音识别失败：' + error)
            if text:
                # 连续两段完全相同的长句，多半是模型在静音/噪声上重复「幻觉」；
                # 只对较长文本判重，避免把正常的口头重复（「谢谢」）当成幻觉丢掉。
                if len(text) >= 8 and text == getattr(self, '_last_text', None):
                    continue
                self._last_text = text
                if self.mode == 'realtime':
                    self.emit('result', text=text)
                else:
                    self.output.append(text)
        if self.ending and self.pending == 0:
            if self.mode != 'realtime' and self.output:
                self.emit('result', text='\n'.join(self.output))
            self.output = []
            self.ending = False
            self.emit('idle', text=self.stop_reason or '已停止 · 点击可再次开始')

    def stop(self):
        if not self.active:
            return
        self.active = False
        self.capture.stop()
        # 麦克风停止后冲刷队列；识别线程继续完成末尾任务，不再采集新语音。
        while True:
            try:
                audio = self.frames.get_nowait()
            except queue.Empty:
                break
            if self.held is not None:
                self.feed(self.held)
            self.held = audio
        if self.held is not None:
            self.feed(self.held, final=True)
            self.held = None
        if self.pending_start is not None:
            # 用户明确停止时提交当前未闭合语音；不是自动判断其语义已结束。
            self._submit(self.pending_start, self.processed_samples)
            self.pending_start = None
        self.ending = True
        self.emit('recognizing')

    def close(self):
        self.active = False
        if self.capture:
            self.capture.stop()
