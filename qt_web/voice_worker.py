"""Web 语音工作进程：命令读取独立于录音/VAD/ASR，停止无需等识别完成。"""
import json
import os
import sys
import threading
import time
from collections import deque

# 开发态用 `-m qt_web.voice_worker` 启动，不经过 voice_worker_entry；
# 这里补一次必须早于 numpy/funasr 的运行时限制。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_limits import apply_runtime_limits
apply_runtime_limits()


class Commands:
    """只读已到达的管道字节，避免后台阻塞读与本机 SciPy DLL 加载互锁。"""
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.buffer = b''
        self.messages = deque()
        self.closed = False
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            msvcrt.setmode(self.fd, os.O_BINARY)
            self.handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.fd))
            self.peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
            self.peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self.peek.restype = wintypes.BOOL

    def get(self):
        if self.messages:
            return self.messages.popleft()
        if self.closed:
            return None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            available = wintypes.DWORD()
            if not self.peek(self.handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error not in (109, 232):
                    raise OSError(error, '读取语音命令管道失败')
                self.closed = True
                return None
            size = min(available.value, 65536)
        else:
            import select
            size = 65536 if select.select([self.fd], [], [], 0)[0] else 0
        if size:
            data = os.read(self.fd, size)
            if not data:
                self.closed = True
                return None
            self.buffer += data
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    self.messages.append(json.loads(line.decode('utf-8')))
                except ValueError:
                    continue
        return self.messages.popleft() if self.messages else {}


def _idle_exit_seconds(reload=False, fallback=300):
    """闲置多少秒后退出进程（0 = 常驻）。

    reload=True 时重新读盘，供主进程改完设置后热生效；读失败则保留旧值。
    """
    try:
        from core.config_manager import get_config, reload_config

        cfg = reload_config() if reload else get_config()
        return max(0, int(cfg.get("asr", "idle_exit_seconds", default=300)))
    except Exception:
        return fallback


IDLE_REFRESH_SECONDS = 5.0


def main():
    wire = sys.stdout
    sys.stdout = sys.stderr
    from qt_web.voice_pipeline import VoicePipeline
    commands = Commands()
    lock = threading.Lock()

    def send(kind, **data):
        with lock:
            wire.write(json.dumps(dict(type=kind, **data), ensure_ascii=False) + '\n')
            wire.flush()

    pipeline = VoicePipeline(send)
    idle_exit = _idle_exit_seconds()
    idle_since = time.monotonic()
    next_idle_refresh = time.monotonic() + IDLE_REFRESH_SECONDS
    try:
        while True:
            msg = commands.get()
            if msg is None:
                break
            try:
                # 设置页可随时改闲置退出时间：每 5 秒读一次盘，
                # 改完无需重启语音进程也能生效。
                now = time.monotonic()
                if now >= next_idle_refresh:
                    next_idle_refresh = now + IDLE_REFRESH_SECONDS
                    updated = _idle_exit_seconds(reload=True, fallback=idle_exit)
                    if updated != idle_exit:
                        idle_exit = updated
                        print(
                            f'[语音进程] 闲置退出时间已更新为 '
                            f'{"常驻" if idle_exit == 0 else str(idle_exit) + " 秒"}',
                            flush=True,
                        )
                if msg:
                    idle_since = time.monotonic()
                    if msg.get('command') == 'start':
                        pipeline.start(msg)
                    elif msg.get('command') == 'load':
                        pipeline.prepare(msg)
                        pipeline.emit('idle', text='模型已加载')
                    elif msg.get('command') == 'stop' and msg['session'] == pipeline.session:
                        pipeline.stop()
                elif idle_exit and pipeline.is_idle():
                    # 闲置退出：模型权重占 1.7~2GB，进程内 unload 回收不了，
                    # 只有结束进程才能真正还给系统（下次识别重新加载约 11~14 秒）。
                    if time.monotonic() - idle_since >= idle_exit:
                        print(f'[语音进程] 闲置 {idle_exit} 秒，退出以释放内存', flush=True)
                        break
                pipeline.tick()
                time.sleep(0.01)
            except Exception as exc:
                pipeline.close()
                send('error', session=msg.get('session', pipeline.session), text=str(exc))
                break
    finally:
        pipeline.close()


if __name__ == '__main__':
    main()
