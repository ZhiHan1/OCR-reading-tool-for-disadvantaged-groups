"""
TTS 语音合成 - 进程内 SAPI 版

与旧实现的区别（旧版用 PowerShell 子进程 + System.Speech）：
- 不再为朗读养一个 powershell.exe（实测约 117 MB 内存 / 27 个线程）
- 不再用 stdin 传 base64、stdout 回传完成标记，省掉进程间往返
- 打断不再靠 kill 进程，而是向同一个 SpVoice 发 purge 请求（毫秒级）
- 改语速/音量/音色只更新字段，朗读下一句时生效，不再重启进程

线程模型：
- SpVoice 是 COM 对象，只在 TTS 工作线程里创建和使用（该线程自己 CoInitialize）。
- 其它线程（UI、扫描线程）只改 Python 字段 + 推进「朗读代号」，
  由工作线程在等待循环里发现代号变化后执行 purge，避免跨线程调用 COM 造成阻塞。
"""
import collections
import locale
import re
import threading
import time

from core import edge_tts_backend
from core.audio_player import MciMp3Player, estimate_duration
from core.config_manager import get_config

# SAPI 的 Speak 标志位
SPF_ASYNC = 1              # 异步朗读，Speak 立刻返回
SPF_PURGEBEFORESPEAK = 2   # 清空待读队列（同时会中止正在朗读的那一句）
SPF_IS_XML = 8             # 按 SSML 解析（音调需要）

# ISpeechVoiceStatus.RunningState 的取值（SpeechRunState）
SRS_IS_SPEAKING = 2

# 等待朗读完成时的轮询间隔（秒）
_POLL_INTERVAL = 0.025
# 发出朗读请求后等待「真的开口」的最长时间（秒）。
# 极短的文本可能在两次轮询之间就读完了，属正常情况，按完成处理。
_START_GRACE = 0.4


def clamp_volume(volume) -> int:
    """把 0.0~1.0 的音量换算成 SAPI 需要的 0~100

    SAPI 的 Volume 只接受 0~100，超出范围会抛异常，
    表现为"朗读突然没声音了"，所以这里做范围限制。
    """
    try:
        value = float(volume)
    except (TypeError, ValueError):
        value = 1.0
    return int(max(0, min(100, round(value * 100))))


def clamp_level(value, default: int = 0) -> int:
    """把语速/音色限制在 -10 ~ 10（None 或非法值回退默认值）"""
    try:
        return int(max(-10, min(10, int(value))))
    except (TypeError, ValueError):
        return default


def split_sentences(text: str):
    """按常见中英文句末标点切分文本，保留标点和原有顺序。"""
    text = str(text).strip()
    if not text:
        return []
    parts = re.findall(r"[^。！？；：.!?;:]+[。！？；：.!?;:]*", text, re.S)
    return [part.strip() for part in parts if part.strip()]


def _culture_from_lcid(hex_lcid) -> str:
    """把 SAPI 返回的十六进制 LCID（如 "804"）转成 "zh-CN" 这样的语言标记"""
    try:
        lcid = int(str(hex_lcid), 16)
    except (TypeError, ValueError):
        return "unknown"

    name = locale.windows_locale.get(lcid)
    if not name:
        return "unknown"
    return name.replace("_", "-")


_available_voices_cache = None


def _enumerate_sapi_voices():
    """在当前线程枚举 SAPI 语音（调用方负责 CoInitialize/CoUninitialize）

    注意：所有 COM 对象都在本函数作用域内创建并在此返回时释放，
    保证释放发生在 CoUninitialize 之前，否则退出时会打印
    "Win32 exception occurred releasing IUnknown"。
    """
    import win32com.client

    voice = win32com.client.Dispatch("SAPI.SpVoice")
    tokens = voice.GetVoices()

    voices = []
    for i in range(tokens.Count):
        token = tokens.Item(i)
        # GetAttribute 缺失时返回空串，用描述兜底
        name = token.GetAttribute("Name") or token.GetDescription()
        voices.append({
            "name": name,
            "culture": _culture_from_lcid(token.GetAttribute("Language")),
            "gender": token.GetAttribute("Gender") or "Unknown",
            "age": token.GetAttribute("Age") or "Adult",
        })
    return voices


def get_available_voices(use_cache: bool = True):
    """获取系统可用的 TTS 语音列表

    进程内直接枚举 SAPI 语音令牌，耗时在毫秒级；
    首次结果会缓存到内存，后续调用直接返回。

    返回的每项形如：
        {"name": "Microsoft Huihui Desktop", "culture": "zh-CN",
         "gender": "Female", "age": "Adult"}
    """
    global _available_voices_cache
    if use_cache and _available_voices_cache is not None:
        return _available_voices_cache

    voices = []
    try:
        import pythoncom

        # 每次枚举可能在调用方自己的线程里，必须本线程初始化 COM
        pythoncom.CoInitialize()
        try:
            voices = _enumerate_sapi_voices()
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        print(f"[TTS] 枚举语音失败: {e}")

    if not voices:
        voices = [{
            "name": "Microsoft David Desktop",
            "culture": "en-US",
            "gender": "Male",
            "age": "Adult",
        }]

    _available_voices_cache = voices
    return voices


class _SapiVoice:
    """SpVoice 的轻量封装

    只能在创建它的线程（TTS 工作线程）里使用：
    win32com 的 COM 对象有线程套间亲和性，跨线程调用会被封送，
    在朗读进行中调用还可能直接阻塞，所以本类不做任何跨线程保护，
    由 TTSManager 保证调用都发生在工作线程。
    """

    def __init__(self):
        import win32com.client

        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        # 上次已写入 COM 的设置，避免每句都重复设置
        self._applied_settings = None
        self._applied_voice = None

    def apply_settings(self, rate, volume, pitch, voice_name):
        """把语速/音量/音色/语音写入 SpVoice（只有变化时才写）"""
        key = (rate, volume, pitch)
        if key != self._applied_settings:
            self._voice.Rate = clamp_level(rate)
            self._voice.Volume = clamp_volume(volume)
            self._applied_settings = key

        if voice_name and voice_name != self._applied_voice:
            try:
                # SAPI 令牌过滤语法；名字里带空格没问题
                tokens = self._voice.GetVoices(f"Name={voice_name}")
                if tokens.Count > 0:
                    self._voice.Voice = tokens.Item(0)
            except Exception as e:
                print(f"[TTS] 切换语音失败({voice_name}): {e}")
            # 无论成功与否都记账，避免每句都重试同一个不存在的名字
            self._applied_voice = voice_name

    def speak(self, text: str, pitch: int):
        """异步开始朗读（立刻返回，朗读在工作线程之外进行）"""
        pitch = clamp_level(pitch)
        if pitch:
            # System.Speech 不支持直接的 pitch 参数，用 SSML 的 prosody 实现。
            # 文本必须先转义，否则文本里的 < & > 会让 SSML 解析失败。
            safe = (
                str(text)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            ssml = (
                '<speak version="1.0" '
                'xmlns="http://www.w3.org/2001/10/synthesis">'
                f'<prosody pitch="{pitch * 5:+d}%">{safe}</prosody>'
                "</speak>"
            )
            self._voice.Speak(ssml, SPF_ASYNC | SPF_IS_XML)
        else:
            self._voice.Speak(str(text), SPF_ASYNC)

    def is_speaking(self) -> bool:
        try:
            return self._voice.Status.RunningState == SRS_IS_SPEAKING
        except Exception:
            return False

    def wait_until_quiet(self, timeout: float = 0.3) -> bool:
        """等到引擎不再处于「正在朗读」状态

        purge 是异步的（请求进 SAPI 自己的队列），打断后立刻朗读下一句时，
        RunningState 可能还是上一句残留的 2。若不等它落地，新的一句会被
        误判成「刚开始就结束」。正常空闲时这里是立即返回，没有额外开销。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_speaking():
                return True
            time.sleep(_POLL_INTERVAL)
        return False

    def purge(self):
        """清空队列并中止当前朗读（打断/停止/改设置时用）"""
        try:
            self._voice.Speak("", SPF_ASYNC | SPF_PURGEBEFORESPEAK)
        except Exception:
            pass

    def close(self):
        try:
            self.purge()
        except Exception:
            pass
        self._voice = None


class _SentenceQueue:
    """线程安全的有界 FIFO（带 peek，供在线语音预取下一句使用）。

    与 queue.Queue 的区别：提供 peek()，让播放线程能在朗读当前句时
    提前看到下一句并交给预取线程去合成，从而隐藏网络延迟。
    队列满时丢最旧（与之前 Queue 的「丢最旧」语义一致）。
    """

    def __init__(self, maxsize=50):
        self._maxsize = max(1, int(maxsize))
        self._deque = collections.deque()
        self._cond = threading.Condition()

    def put(self, item):
        with self._cond:
            if len(self._deque) >= self._maxsize:
                self._deque.popleft()
            self._deque.append(item)
            self._cond.notify()

    def get(self, timeout=None):
        """取队首；超时返回 None（区别于 queue.Queue 的抛异常）"""
        with self._cond:
            if timeout is None:
                while not self._deque:
                    self._cond.wait()
                return self._deque.popleft()
            end = time.time() + timeout
            while not self._deque:
                remaining = end - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._deque.popleft()

    def peek(self):
        """看队首但不取出；空则返回 None"""
        with self._cond:
            return self._deque[0] if self._deque else None

    def clear(self):
        with self._cond:
            self._deque.clear()


class TTSManager:
    """TTS 语音合成管理器

    对外接口与旧版一致（start/stop/pause/resume/stop_current/add/clear/
    set_rate/set_volume/set_pitch/set_voice），调用方无需改动。

    内部用「朗读代号」(seq) 处理打断：
    - 每次加入文本时带上当前代号；
    - 打断/停止会把代号 +1，正在等待的那一句发现代号变了就 purge 并放手；
    - 这样不会出现「新的一句被旧请求误判作废」的问题。
    """

    def __init__(self, rate=None, volume=None, voice_index=None, voice_name=None,
                 pitch=None, backend=None, edge_voice=None):
        config = get_config()

        self.rate = rate if rate is not None else config.get("tts", "rate", default=0)
        self.volume = volume if volume is not None else config.get("tts", "volume", default=1.0)
        self.voice_index = voice_index if voice_index is not None else config.get("tts", "voice_index", default=0)
        self.voice_name = voice_name if voice_name is not None else config.get("tts", "voice_name", default="")
        self.pitch = pitch if pitch is not None else config.get("tts", "pitch", default=0)

        # 语音来源：sapi（本机） / edge（微软在线）
        self.backend = self._normalize_backend(
            backend if backend is not None else config.get("tts", "backend", default="sapi")
        )
        self.edge_voice = (
            edge_voice if edge_voice is not None
            else config.get("tts", "edge_voice", default=edge_tts_backend.DEFAULT_EDGE_VOICE)
        )

        # 队列和去重
        self.queue = _SentenceQueue(maxsize=config.get("tts", "queue_max", default=50))
        self.dedup_window = config.get("tts", "dedup_window", default=2.0)
        self.timeout = config.get("tts", "timeout", default=30)
        self.last_queued_text = ""
        self.last_queued_time = 0.0

        # 在线语音音频缓存（LRU，键 = 语音+文本+语速/音量/音调）
        self._cache_max = config.get("tts", "cache_max", default=128)
        self._audio_cache = collections.OrderedDict()
        self._cache_lock = threading.Lock()

        # 在线语音预取状态（播放当前句时，后台合成下一句）
        self._prefetch = None  # {"key": ..., "event": threading.Event()}
        self._prefetch_lock = threading.Lock()

        # 状态控制
        self.enabled = True
        self._paused = threading.Event()
        self._shutdown = threading.Event()
        self._paused.set()
        # 是否正在朗读（仅作状态标记，打断不依赖它）
        self._speaking = threading.Event()

        # 朗读代号
        self._seq = 0
        self._seq_lock = threading.Lock()

        # 只在工作线程里创建/使用
        self._voice = None
        self._edge_client = None
        self._player = None
        self.worker_thread = None

    @staticmethod
    def _normalize_backend(backend) -> str:
        """把配置里的来源统一成 "sapi" / "edge" """
        return "edge" if str(backend or "").strip().lower() == "edge" else "sapi"

    def _use_edge(self) -> bool:
        """当前是否走在线语音（选了 edge 且 edge-tts 已安装）"""
        return self.backend == "edge" and edge_tts_backend.is_available()

    # ===== 生命周期 =====

    def start(self):
        """启动 TTS 工作线程"""
        if self.worker_thread and self.worker_thread.is_alive():
            return

        # 重置状态标志（允许停止后重新启动）
        self.enabled = True
        self._shutdown.clear()
        self._paused.set()

        self.worker_thread = threading.Thread(target=self._worker, daemon=True, name="TTSWorker")
        self.worker_thread.start()
        print("[TTS] 工作线程已启动")

    def stop(self):
        """停止 TTS（强制中止当前朗读并退出工作线程）"""
        self._shutdown.set()
        self._paused.set()

        # 作废当前朗读：等待中的 _speak 会立刻 purge 并返回
        self._new_seq()
        self.clear()

        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.5)

    def pause(self):
        """暂停 TTS"""
        self.enabled = False
        self._paused.clear()
        self.clear()

    def resume(self):
        """恢复 TTS"""
        self.enabled = True
        self._paused.set()
        if not (self.worker_thread and self.worker_thread.is_alive()):
            self.start()

    def stop_current(self):
        """停止当前朗读并清空队列

        不直接调 COM，只推进代号；工作线程在 15 ms 内发现并执行 purge，
        因此从 UI 线程调用也不会卡。
        """
        self._new_seq()
        self.clear()

    def _new_seq(self) -> int:
        """作废当前朗读：代号 +1，正在等待的那一句会立刻放手"""
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _current_seq(self) -> int:
        with self._seq_lock:
            return self._seq

    # ===== 对外朗读接口 =====

    def add(self, text, interrupt=False, dedup=True):
        """添加文本到朗读队列

        Args:
            text: 要朗读的文本
            interrupt: 是否打断当前朗读
            dedup: 是否启用「同一句 2 秒内不重复朗读」的兜底去重。
                   功能1/功能2 是用户手动触发，必须传 False——否则重复点同一句
                   会被静默丢弃，表现为"点了没反应"。
        """
        if not self.enabled or not text:
            return

        text = str(text).strip()
        if not text:
            return

        # 去重（仅后台扫描类调用需要）。放在打断之前：
        # 被判为重复的文本不应该去打断正在朗读的内容。
        if dedup:
            now = time.time()
            if text == self.last_queued_text and now - self.last_queued_time < self.dedup_window:
                return
            self.last_queued_text = text
            self.last_queued_time = now

        seq = self._current_seq()

        if interrupt:
            # 推进代号即可打断：正在等待的那一句会发现代号变化并 purge，
            # 同时它自己不会读完；新入队的这一句带着新代号，照常朗读。
            seq = self._new_seq()
            self.clear()

        # 入队（队列满时丢弃最旧的）。带上代号，_speak 用它判断自己是否已作废。
        # 长文本按句入队：第一句可立即开始，停止时也无需等待整段文本。
        for sentence in split_sentences(text):
            self.queue.put((seq, sentence))

    def clear(self):
        """清空队列"""
        self.queue.clear()
        self.last_queued_text = ""
        self.last_queued_time = 0.0

    # ===== 工作线程 =====

    def _worker(self):
        """工作线程：初始化 COM，从队列取文本并朗读"""
        # 本机 SAPI 需要 COM；在线语音不需要。缺 pywin32 时仍可尝试在线语音。
        pythoncom = None
        try:
            import pythoncom as _pythoncom
            pythoncom = _pythoncom
        except Exception as e:
            print(f"[TTS] 缺少 pywin32，本机语音不可用: {e}")

        if pythoncom is not None:
            pythoncom.CoInitialize()
        try:
            while not self._shutdown.is_set():
                self._paused.wait()
                if self._shutdown.is_set():
                    break
                item = self.queue.get(timeout=0.2)
                if item is None:
                    continue
                seq, text = item
                try:
                    self._speak(text, seq)
                except Exception as e:
                    print(f"[TTS错误] {e}")
        finally:
            self._close_voice()
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    def _ensure_voice(self):
        """获取（首次创建）SpVoice —— 必须由工作线程调用"""
        if self._voice is not None:
            return self._voice
        try:
            self._voice = _SapiVoice()
            print("[TTS] SAPI 语音引擎已就绪（进程内）")
        except Exception as e:
            print(f"[TTS] 初始化 SAPI 失败: {e}")
            self._voice = None
        return self._voice

    def _close_voice(self):
        if self._voice is not None:
            try:
                self._voice.close()
            except Exception:
                pass
            self._voice = None
        if self._player is not None:
            try:
                self._player.close()
            except Exception:
                pass
            self._player = None
        if self._edge_client is not None:
            try:
                self._edge_client.close()
            except Exception:
                pass
            self._edge_client = None

    def _speak(self, text, seq):
        """朗读一条文本（按当前来源分派到本机 SAPI 或在线语音）

        Args:
            text: 文本
            seq: 该文本入队时的朗读代号；代号变了说明已被新的打断/停止请求作废
        """
        if not text:
            return
        if seq != self._current_seq() or self._shutdown.is_set():
            return

        self._speaking.set()
        try:
            if self._use_edge():
                self._speak_edge(text, seq)
            else:
                self._speak_sapi(text, seq)
        except Exception as e:
            print(f"[TTS错误] {e}")
        finally:
            self._speaking.clear()

    def _speak_sapi(self, text, seq):
        """本机 SAPI 朗读"""
        voice = self._ensure_voice()
        if voice is None:
            return

        try:
            # 发出去之前再确认一次：这期间可能来了新的打断请求
            if seq != self._current_seq() or self._shutdown.is_set():
                return

            # 先确认上一句（可能刚被 purge）已经停下，再开口
            voice.wait_until_quiet()
            if seq != self._current_seq() or self._shutdown.is_set():
                return

            voice.apply_settings(self.rate, self.volume, self.pitch, self.voice_name)
            voice.speak(text, self.pitch)

            self._wait_until_done(voice, seq, text)

        except Exception as e:
            print(f"[TTS错误] {e}")
            try:
                voice.purge()
            except Exception:
                pass

    def _speak_edge(self, text, seq):
        """微软 Edge 在线语音朗读，失败时回退本机 SAPI"""
        audio = self._edge_audio(text, seq)

        if seq != self._current_seq() or self._shutdown.is_set():
            return

        if not audio:
            # 合成被中止（打断）时不回退；真的没拿到音频才回退
            if seq == self._current_seq() and not self._shutdown.is_set():
                self._speak_sapi(text, seq)
            return

        # 拿到音频后、开口前，就安排后台预取下一句，隐藏下一句的网络延迟
        self._prefetch_next()

        if not self._play_audio(audio):
            print("[TTS] 在线音频播放失败，回退本机语音")
            self._speak_sapi(text, seq)
            return

        self._wait_until_audio_done(seq, audio)

    def _edge_key(self, text):
        """音频缓存键：语音 + 文本 + 语速/音量/音调（任一变化即视为不同）"""
        return (self.edge_voice, text, self.rate, self.volume, self.pitch)

    def _cache_get(self, key):
        with self._cache_lock:
            audio = self._audio_cache.get(key)
            if audio is not None:
                self._audio_cache.move_to_end(key)
                return audio
            return None

    def _cache_put(self, key, audio):
        if not audio:
            return
        with self._cache_lock:
            self._audio_cache[key] = audio
            self._audio_cache.move_to_end(key)
            while len(self._audio_cache) > self._cache_max:
                self._audio_cache.popitem(last=False)

    def _edge_audio(self, text, seq):
        """获取某句的音频：先查缓存，再等预取，最后同步合成"""
        key = self._edge_key(text)
        audio = self._cache_get(key)
        if audio is not None:
            return audio

        # 若后台预取正好在合成这一句，等它完成，避免重复请求
        with self._prefetch_lock:
            pf = self._prefetch
            wait_ev = pf["event"] if (pf is not None and pf["key"] == key) else None
        if wait_ev is not None:
            wait_ev.wait(timeout=12)
            audio = self._cache_get(key)
            if audio is not None:
                return audio

        # 仍未命中则同步合成（首句或预取未覆盖的句）
        try:
            audio = self._synthesize_edge(text, seq)
        except Exception as e:
            print(f"[TTS] 在线语音合成失败，回退本机语音: {e}")
            return None
        self._cache_put(key, audio)
        return audio

    def _prefetch_next(self):
        """在后台线程合成队首的下一句并写入缓存（不阻塞播放）"""
        nxt = self.queue.peek()
        if not nxt:
            return
        _, next_text = nxt
        key = self._edge_key(next_text)
        if self._cache_get(key) is not None:
            return
        with self._prefetch_lock:
            if self._prefetch is not None:
                return  # 已有一个预取在进行
            event = threading.Event()
            self._prefetch = {"key": key, "event": event}

        # 快照当前设置，避免合成过程中用户改了语速/音色导致缓存键与音频不匹配
        params = self._edge_params()
        threading.Thread(
            target=self._prefetch_worker,
            args=(next_text, key, params, event),
            daemon=True,
            name="EdgePrefetch",
        ).start()

    def _prefetch_worker(self, text, key, params, event):
        """预取线程：用独立的 EdgeTTSClient 合成（事件循环有线程亲和性）"""
        client = None
        voice, rate, volume, pitch = params
        try:
            client = edge_tts_backend.EdgeTTSClient()
            audio = client.synthesize(text, voice, rate, volume, pitch)
            if audio:
                self._cache_put(key, audio)
        except Exception as e:
            print(f"[TTS] 预取下一句失败: {e}")
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            with self._prefetch_lock:
                if self._prefetch is not None and self._prefetch["event"] is event:
                    self._prefetch = None
            event.set()

    def _edge_params(self):
        """当前在线语音参数（已换算成 SSML 字符串）"""
        return (
            self.edge_voice,
            edge_tts_backend.rate_to_ssml(self.rate),
            edge_tts_backend.volume_to_ssml(self.volume),
            edge_tts_backend.pitch_to_ssml(self.pitch),
        )

    def _synthesize_edge(self, text, seq):
        """调用 edge-tts 合成 MP3 字节（工作线程内同步等待）"""
        client = self._ensure_edge_client()
        if client is None:
            raise RuntimeError("edge-tts 不可用")

        def should_stop():
            return seq != self._current_seq() or self._shutdown.is_set()

        voice, rate, volume, pitch = self._edge_params()
        return client.synthesize(
            text, voice, rate, volume, pitch, should_stop=should_stop,
        )

    def _ensure_edge_client(self):
        if self._edge_client is None:
            try:
                self._edge_client = edge_tts_backend.EdgeTTSClient()
            except Exception as e:
                print(f"[TTS] 初始化在线语音失败: {e}")
                self._edge_client = None
        return self._edge_client

    def _ensure_player(self):
        if self._player is None:
            try:
                self._player = MciMp3Player()
            except Exception as e:
                print(f"[TTS] 初始化音频播放器失败: {e}")
                self._player = None
        return self._player

    def _play_audio(self, audio) -> bool:
        player = self._ensure_player()
        if player is None:
            return False
        return player.play(audio)

    def _wait_until_audio_done(self, seq, audio):
        """等待在线音频播放结束，或等待自己被新的请求作废"""
        player = self._player
        if player is None:
            return

        budget = max(self.timeout, estimate_duration(len(audio)) + 5)

        def is_playing():
            return player.is_playing()

        def abort():
            player.stop()

        self._wait_active(seq, is_playing, abort, budget, "在线音频超时，中止播放")

    def _wait_until_done(self, voice, seq, text=""):
        """等到这一句读完，或等到自己被新的请求作废

        SAPI 的 RunningState：0=未开始/已清除，2=正在朗读，3=已结束。
        由于 Speak 是异步的，刚发出时状态可能还是 0，
        因此要先等它进入「正在朗读」，再等它离开该状态。
        """
        # 长文本要按字数放宽等待上限，否则会被 self.timeout 掐断
        budget = max(self.timeout, len(text) * 0.4 + 5)

        self._wait_active(
            seq, voice.is_speaking, voice.purge, budget, "超时，中止当前朗读"
        )

    def _wait_active(self, seq, is_active, abort, budget: float, timeout_msg: str):
        """轮询等待「先进入活动状态、再离开活动状态」

        本机 SAPI 与在线播放的等待逻辑完全一致（只有查询/中止的方式不同），
        差别全部由 `is_active` / `abort` 两个回调吸收。

        Args:
            seq: 入队时的朗读代号，变化即表示已被打断/停止
            is_active: 返回 True 表示「正在发声」
            abort: 中止本轮发声（SAPI 用 purge，播放器用 stop）
            budget: 最长等待秒数
            timeout_msg: 超时时要打印的提示
        """
        deadline = time.time() + budget
        grace = time.time() + _START_GRACE
        started = False

        while True:
            # 优先级最高：被打断 / 被停止 / 正在退出
            if seq != self._current_seq() or self._shutdown.is_set():
                abort()
                return

            if is_active():
                started = True
            elif started:
                return
            elif time.time() > grace:
                # 一直没进入活动状态（极短文本或引擎/播放器异常），按完成处理
                return

            if time.time() > deadline:
                print(f"[TTS] {timeout_msg}")
                abort()
                return

            time.sleep(_POLL_INTERVAL)

    # ===== 设置 =====

    def set_rate(self, rate):
        """动态设置语速（下一句生效，正在朗读的这句会被中止）"""
        self.rate = rate
        self._new_seq()

    def set_volume(self, volume):
        """动态设置音量（下一句生效）"""
        self.volume = volume
        self._new_seq()

    def set_pitch(self, pitch):
        """动态设置音色（下一句生效）"""
        self.pitch = pitch
        self._new_seq()

    def set_voice(self, voice_name):
        """动态切换语音（下一句生效）"""
        self.voice_name = voice_name
        self._new_seq()

    def set_backend(self, backend):
        """动态切换语音来源：sapi（本机）/ edge（在线，下一句生效）"""
        backend = self._normalize_backend(backend)
        if backend != self.backend:
            self.backend = backend
            self._new_seq()

    def set_edge_voice(self, edge_voice):
        """动态切换在线语音（下一句生效）"""
        self.edge_voice = edge_voice
        self._new_seq()
