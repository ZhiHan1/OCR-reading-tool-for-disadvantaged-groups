"""GUI 层语音会话：按钮优先接管，可真正结束卡住的音频/识别进程。"""
import json
import os
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from core.config_manager import get_config
from qt_web.asr_models import ModelRegistry
from qt_web.voice_modes import settings
from utils.paths import app_root


class VoiceSession(QObject):
    changed = Signal(dict)
    level = Signal(float)
    result = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 'idle'
        self.owner = None
        self.session = 0
        self.process = None
        self.model_status = '模型尚未加载'
        self.config = get_config()
        self.registry = ModelRegistry()
        self.mode = 'manual'
        self.pending = 0
        self.text = '就绪 · 松开自动识别'
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._timeout)

    def snapshot(self):
        return dict(type='asr-state', phase=self.phase, source=self.owner, session=self.session,
                    recording=self.phase == 'recording', busy=self.phase != 'idle',
                    text=self.text, model_status=self.model_status, mode=self.mode, pending=self.pending)

    def _state(self, phase, text):
        self.phase, self.text = phase, text
        if phase == 'idle':
            self.owner = None
            self._watchdog.stop()
            self.level.emit(0.0)
        self.changed.emit(self.snapshot())

    def _spawn(self):
        p = QProcess(self)
        p.setWorkingDirectory(str(app_root()))
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        if not getattr(sys, 'frozen', False):
            env.insert('PYTHONPATH', str(app_root()) + os.pathsep + env.value('PYTHONPATH'))
        p.setProcessEnvironment(env)
        self.process = p
        p._voice_buffer = b''
        p.readyReadStandardOutput.connect(lambda: self._read(p))
        p.readyReadStandardError.connect(lambda: self._logs(p))
        p.finished.connect(lambda *_: self._finished(p))
        p.errorOccurred.connect(lambda error: self._error(p, error))
        if getattr(sys, 'frozen', False):
            p.start(str(app_root() / 'OCR_VoiceWorker.exe'), [])
        else:
            p.start(sys.executable, ['-u', '-m', 'qt_web.voice_worker'])
        return p

    def _send(self, command, **extra):
        p = self.process or self._spawn()
        p.write((json.dumps(dict(command=command, session=self.session, **extra)) + '\n').encode('utf-8'))

    def _discard(self):
        self._watchdog.stop()
        p = self.process
        self.session += 1
        if p is not None:
            # 不启动第二个采集进程，直到旧进程确认结束。
            self.process = None
            if p.state() != QProcess.NotRunning:
                p.kill()
                if not p.waitForFinished(1500) and p.state() != QProcess.NotRunning:
                    self.process = p
                    self._state('error', '旧录音未能结束，请再次按住说话重试')
                    return False
            p.deleteLater()
        self.model_status = '模型尚未加载（下次识别时加载）'
        self.level.emit(0.0)
        return True

    def start(self, source):
        if source not in ('button', 'float'):
            return dict(ok=False, error='语音输入快捷键已移除')
        try:
            model = self.registry.get(self.config.get('asr', 'local_model', default='paraformer_large'))
        except (ValueError, OSError) as exc:
            return dict(ok=False, error=str(exc))
        if self.phase != 'idle':
            if source != 'button':
                return dict(ok=False, busy=True, **{'state': self.snapshot()})
            if not self._discard():
                return dict(ok=False, error=self.text)
        self.session += 1
        self.owner = source
        options = settings(self.config)
        if source == 'button':
            options['mode'] = 'manual'
        self.mode = options['mode']
        self.pending = 0
        self._state('starting', '正在准备麦克风和模型…')
        self._watchdog.start(120000)
        self._send('start', model=model, options=options)
        return dict(ok=True, session=self.session)

    def stop(self, source, session=None):
        if (source != self.owner or self.phase not in ('starting', 'recording')
                or (session is not None and session != self.session)):
            return dict(ok=False, busy=self.phase != 'idle')
        self._state('stopping', '正在结束录音…')
        self.level.emit(0.0)
        self._watchdog.start(120000)
        self._send('stop')
        return dict(ok=True)

    def load_model(self):
        if self.phase != 'idle':
            return dict(ok=False, error='语音任务进行中，可按住说话接管')
        try:
            model = self.registry.get(self.config.get('asr', 'local_model', default='paraformer_large'))
        except (ValueError, OSError) as exc:
            return dict(ok=False, error=str(exc))
        self.session += 1
        self.model_status = '正在加载…'
        self._state('loading', '正在加载模型…')
        self._watchdog.start(120000)
        self._send('load', model=model)
        return dict(ok=True, status=self.model_status)

    def _read(self, p):
        raw = bytes(p.readAllStandardOutput())
        if p is not self.process:
            return
        p._voice_buffer += raw
        while b'\n' in p._voice_buffer:
            line, p._voice_buffer = p._voice_buffer.split(b'\n', 1)
            try:
                msg = json.loads(line.decode('utf-8'))
            except (ValueError, UnicodeError):
                continue
            if msg.get('session') != self.session:
                continue
            kind = msg.get('type')
            if kind == 'level':
                if self.phase == 'recording':
                    self.level.emit(float(msg['value']))
            elif kind == 'preparing':
                if self.phase != 'stopping':
                    self.text = msg.get('text', '正在准备…')
                    self.changed.emit(self.snapshot())
            elif kind == 'heartbeat':
                if self.phase == 'recording':
                    self.pending = int(msg.get('pending', 0))
                    self._watchdog.start(15000)
                    self.changed.emit(self.snapshot())
            elif kind == 'recording' and self.phase == 'starting':
                text = '正在录音… 松开结束' if self.owner == 'button' else {
                    'manual': '持续录音中 · 再点停止并输出文字',
                    'realtime': '实时分段识别中 · 点击停止',
                    'auto': '正在听您说话 · 长停顿后自动结束',
                }[self.mode]
                self._state('recording', text)
                self._watchdog.start(15000)
            elif kind == 'recognizing':
                self._state('recognizing', '识别中…（按住说话可重新开始）')
                self._watchdog.start(120000)
            elif kind == 'result':
                self.result.emit(msg['text'])
            elif kind == 'model':
                self.model_status = msg['text']
                self.changed.emit(self.snapshot())
            elif kind == 'idle':
                self._state('idle', msg.get('text', '就绪'))
            elif kind == 'error':
                text = msg.get('text', '语音任务失败')
                if self._discard():
                    self._state('idle', text)
                return

    def _logs(self, p):
        text = bytes(p.readAllStandardError()).decode('utf-8', errors='replace')
        if text.strip():
            print(text.rstrip(), flush=True)

    def _finished(self, p):
        if p is self.process:
            self.process = None
            self.model_status = '模型尚未加载'
            # 闲置退出（asr.idle_exit_seconds）是正常行为：进程结束时已经在
            # idle 阶段就不该提示「异常」，否则用户隔一会儿回到界面会看到
            # 一句莫名其妙的「语音进程已结束」。
            busy = self.phase != 'idle'
            self._state('idle', '语音进程意外结束，按住说话可重试' if busy
                        else '就绪 · 松开自动识别')
            p.deleteLater()

    def _error(self, p, error):
        if p is self.process and error == QProcess.FailedToStart:
            self._finished(p)

    def _timeout(self):
        if self._discard():
            self._state('idle', '语音任务超时已清理，按住说话可重试')

    def unload(self):
        if self.phase != 'idle':
            return False
        if self._discard():
            self._state('idle', '模型已卸载，等待加载')
            return True
        return False

    def close(self):
        self._discard()
