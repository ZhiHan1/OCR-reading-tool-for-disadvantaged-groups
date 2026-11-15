"""麦克风录音模块 - 用于语音输入的音频采集

职责：
- 封装 sounddevice 的录音，提供「开始录音 / 停止录音并返回音频数据」两个简单接口。
- 返回 16kHz 单声道 float32 的 NumPy 数组（FunASR 输入格式）。
- 线程安全：录音在独立线程中执行，不阻塞调用方。

设计说明：
- sounddevice.InputStream 的回调在 PortAudio 的后台线程里触发，把数据拼进
  NumPy 数组/列表，停止时一次性返回。
- 采样率固定 16kHz（ASR 模型标准输入），声道数 1，位深 float32。
"""
import threading
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失时降级
    sd = None
    _SD_AVAILABLE = False

# 目标采样率 / 声道（ASR 标准输入）
SAMPLE_RATE = 16000
CHANNELS = 1
# NumPy dtype（float32，FunASR 默认输入）
DTYPE = "float32"

# 单次录音的最长时长（秒），防止用户一直按住不松导致内存无限增长
MAX_DURATION = 120.0


def is_available() -> bool:
    """sounddevice 是否可用（未安装时返回 False，由调用方提示安装）"""
    return _SD_AVAILABLE and sd is not None


# 同一支麦克风在 Windows 上会被 MME / DirectSound / WASAPI / WDM-KS 重复枚举，
# 设备名里还带空格和括号。名字相同且输入规格相同的视为同一支，只留一个。
_HOSTAPI_RANK = {'MME': 0, 'Windows DirectSound': 1, 'Windows WASAPI': 2, 'Windows WDM-KS': 3}


def input_devices():
    """列出 Windows 判定为**在线**的麦克风。

    判据与 Windows「设置 → 系统 → 声音」完全同源：
    读 Core Audio 的 `IMMDevice::GetState()`（见 `core.audio_endpoints`），
    只保留 `DEVICE_STATE_ACTIVE` 的采集设备，
    排除「已禁用 / 不存在 / 未插入」——那些正是用户看到的
    「列出来的麦克风大多没在线」。

    **不做音频探测**：不打开设备试录，只查状态（毫秒级，无副作用）。
    虚拟声卡若在 Windows 里是「在线」，这里也会列出来——那是系统的
    真实判定，不做额外猜测。

    降级：Windows 状态查询失败（非 Windows / COM 异常）时不过滤，
    退回 sounddevice 的原始列表，绝不清空。

    返回 [{'name', 'channels', 'samplerate', 'hostapi', 'index'}]，
    保持 sounddevice 的枚举顺序。
    同名设备按 _HOSTAPI_RANK 归并：优先 MME 的原始名（与 Windows 设置一致）。
    """
    if not is_available():
        return []
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001 - 音频后端异常时降级为空列表
        print(f'[录音] 设备枚举失败: {exc}')
        return []

    # ---- 第一层：Windows 原生状态 ----
    try:
        from core.audio_endpoints import active_capture_names

        online = active_capture_names()
    except Exception:  # noqa: BLE001
        online = None

    groups = []
    for index, device in enumerate(devices):
        channels = int(device.get('max_input_channels') or 0)
        if channels <= 0:
            continue
        name = str(device.get('name') or '').strip() or '未命名输入设备'
        try:
            hostapi = str(hostapis[device['hostapi']]['name'])
        except (IndexError, KeyError, TypeError):
            hostapi = ''
        samplerate = int(device.get('default_samplerate') or 0)
        # 被截断的名字（MME）是完整名字的前缀，据此把同一支麦克风合并到一起。
        group = next((g for g in groups if name.startswith(g['full']) or g['full'].startswith(name)), None)
        if group is None:
            group = {'full': name, 'entries': []}
            groups.append(group)
        else:
            group['full'] = max(group['full'], name, key=len)
        group['entries'].append({'index': index, 'name': name, 'channels': channels,
                                 'samplerate': samplerate, 'hostapi': hostapi})

    result = []
    for group in groups:
        entries = group['entries']
        preferred = next((e for e in entries if e['hostapi'] == 'MME'),
                         next((e for e in entries if e['hostapi'] == 'Windows WASAPI'), entries[0]))

        # Windows 说不在线（已禁用 / 不存在 / 未插入）的直接丢。
        # `online is None` 表示查不到（非 Windows / COM 异常），此时不过滤。
        if online is not None and not _windows_says_online(group['full'], entries, online):
            continue

        result.append({'name': group['full'], 'channels': preferred['channels'],
                       'samplerate': preferred['samplerate'], 'hostapi': preferred['hostapi'],
                       'index': preferred['index']})
    return result


def _windows_says_online(group_name: str, entries, online: dict) -> bool:
    """组内任一别名被 Windows 判为在线，就算这支麦克风在线。

    Windows 的友好名和 PortAudio 的名字常有出入，例如：
        Windows: 麦克风 (HECATE G2无线版)
        MME 截断: 麦克(?  (31 字符上限)
        WASAPI:   麦克风 (HECATE G2无线版)
    所以按「前缀互相包含」宽松匹配。
    """
    names = {group_name} | {e['name'] for e in entries}
    for candidate in names:
        if not candidate:
            continue
        for online_name in online:
            if not online_name:
                continue
            if candidate == online_name:
                return True
            if candidate.startswith(online_name) or online_name.startswith(candidate):
                return True
    return False


def find_input_device(name):
    """按设备名匹配 sounddevice 的输入端口索引；找不到或为空返回 None（走系统默认）。"""
    if not name or not is_available():
        return None
    name = str(name).strip()
    if not name:
        return None
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:  # noqa: BLE001
        return None
    best = None
    best_exact = False
    for index, device in enumerate(devices):
        if int(device.get('max_input_channels') or 0) <= 0:
            continue
        candidate = str(device.get('name') or '').strip()
        # 精确匹配优先；否则接受 MME 截断名与完整名的前缀关系。
        exact = candidate == name
        if not exact and not (candidate.startswith(name) or name.startswith(candidate)):
            continue
        try:
            hostapi = str(hostapis[device['hostapi']]['name'])
        except Exception:  # noqa: BLE001
            hostapi = ''
        score = (exact, -_HOSTAPI_RANK.get(hostapi, 99))
        if best is None:
            best, best_exact = index, score
            continue
        if score > best_exact:
            best, best_exact = index, score
    return best


class AudioCapture:
    """麦克风录音器（非阻塞）"""

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS,
                 device_name: str = ''):
        self.sample_rate = sample_rate
        self.channels = channels
        # 设备名而非索引：索引会随插拔变化，名字跨进程稳定。
        self.device_name = device_name
        self._stream = None
        self._chunks: list = []
        self._lock = threading.Lock()
        self._recording = False
        self.on_level = None

    def start(self) -> bool:
        """开始录音

        Returns:
            bool: 是否成功启动
        """
        if not is_available():
            return False

        # 全局热键可能在 keyboard 的监听线程中调用 start()。Windows 音频后端
        # 依赖线程级 COM；没有这一步时，WASAPI 可能静默回退到 WDM-KS，导致
        # 某些麦克风在 GUI 中打不开，而命令行测试却正常。
        try:
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
        except Exception:
            pass
        with self._lock:
            if self._recording:
                return False
            self._chunks = []
            self._recording = True

        try:
            device = find_input_device(self.device_name)
            if self.device_name and device is None:
                # 选了具体设备却已拔掉：明确报错，不要偷偷回退到默认麦克风。
                print(f'[录音] 找不到所选麦克风: {self.device_name}')
                with self._lock:
                    self._recording = False
                return False
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DTYPE,
                device=device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[录音] 启动失败: {e}")
            with self._lock:
                self._recording = False
            self._stream = None
            return False
        return True

    def _callback(self, indata, frames, time_info, status):  # noqa: unused args
        """PortAudio 回调：把音频块追加到缓存"""
        with self._lock:
            self._chunks.append(indata.copy())
        callback = self.on_level
        if callback is not None:
            try:
                level = float(np.sqrt(np.mean(np.square(indata))))
                callback(min(1.0, level * 12.0))
            except Exception:
                pass

    def stop(self) -> Optional[np.ndarray]:
        """停止录音并返回已采集的音频（float32，shape=(n,)，单声道）

        Returns:
            np.ndarray 或 None（未在录音、无数据时返回 None）
        """
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            chunks = self._chunks
            self._chunks = []

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f"[录音] 停止流失败: {e}")
            finally:
                self._stream = None

        if not chunks:
            return None
        audio = np.concatenate(chunks, axis=0)
        # 多声道时取第一声道；这里是单声道，reshape 保证一维
        if audio.ndim > 1:
            audio = audio[:, 0]
        return audio

    def duration(self, audio: np.ndarray) -> float:
        """返回音频时长（秒）"""
        if audio is None or len(audio) == 0:
            return 0.0
        return len(audio) / self.sample_rate
