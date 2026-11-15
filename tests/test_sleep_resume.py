"""守门测试：休眠唤醒后语音失灵的三层防护。

故障场景（用户实测）：
    电脑休眠 → 唤醒 → 按住说话 → 录音流打开「成功」 → 但采到纯静音
    → VAD 检测不到语音 → 界面显示「未检测到说话，已暂停」
    → 重试无效，只有重启软件才恢复。

根因：PortAudio 只在导入时 Pa_Initialize() 一次，设备表固化；
      Windows 休眠会复位音频驱动，唤醒后端点重新枚举，表就过期了。

防护：
    ① PowerMonitor 监听 WM_POWERBROADCAST，唤醒时重启语音工作进程；
    ② VoicePipeline 静音自检，设备一直没声音时给出可操作的提示；
    ③ 唤醒期间若正在录音，不打断用户，等本轮结束再重连。
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PowerMonitorTest(unittest.TestCase):
    """① 电源事件监听。"""

    def test_resume_message_constants(self):
        from qt_web.power_monitor import RESUME_MESSAGES, WM_POWERBROADCAST

        self.assertEqual(WM_POWERBROADCAST, 0x0218)
        # PBT_APMRESUMEAUTOMATIC / PBT_APMRESUMESUSPEND / PBT_APMRESUMECRITICAL
        self.assertEqual(RESUME_MESSAGES, {0x0012, 0x0004, 0x0013})

    def test_throttle_collapses_duplicate_resumes(self):
        """Windows 一次唤醒常连发 2~3 条事件，不能因此重启多次。"""
        import ast

        source = (ROOT / 'qt_web/power_monitor.py').read_text(encoding='utf-8')
        self.assertIn('_THROTTLE_SECONDS', source)
        # 只看可执行代码，不看文档字符串：
        # 必须用 monotonic——休眠期间 time.time() 会跳变，可能减出负数。
        calls = set()
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'time'):
                calls.add(node.func.attr)
        self.assertIn('monotonic', calls)
        self.assertNotIn('time', calls, '计时不能用 time.time()，休眠会让它跳变')

    def test_ignores_unrelated_windows_messages(self):
        from qt_web.power_monitor import PowerMonitor

        monitor = PowerMonitor()
        fired = []
        monitor.resumed.connect(lambda: fired.append(1))
        monitor.suspended.connect(lambda: fired.append(2))
        # 非电源消息（指针为空，解析失败）应当放行给 Qt
        handled, _ = monitor.handle_native_event(0, 0)
        self.assertFalse(handled)
        self.assertEqual(fired, [])


class SilenceCheckTest(unittest.TestCase):
    """② 静音自检：区分「设备没声音」和「用户没开口」。"""

    def _capture(self):
        import queue as _q
        from qt_web.voice_pipeline import LiveCapture

        return LiveCapture(_q.Queue(), lambda level: None, device_name='')

    def test_all_zero_audio_detected_as_silent(self):
        """全 0 音频（休眠后端点失效）必须判定为静音。"""
        import numpy as np

        capture = self._capture()
        for _ in range(10):
            capture._callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, _Status())
        self.assertTrue(capture.silent_for(1.0),
                        '全 0 音频应判定为设备无声音')

    def test_normal_speech_not_flagged(self):
        """正常说话电平不能被误判成故障。"""
        import numpy as np

        capture = self._capture()
        speech = (np.sin(np.arange(1600) * 0.1) * 0.3).astype(np.float32).reshape(-1, 1)
        capture._callback(speech, 1600, None, _Status())
        for _ in range(10):
            capture._callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, _Status())
        self.assertFalse(capture.silent_for(1.0),
                         '出现过正常语音就不该判定为设备故障')

    def test_short_recording_not_flagged(self):
        """刚开口的头几百毫秒不能被判定为静音。"""
        import numpy as np

        capture = self._capture()
        capture._callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, _Status())
        self.assertFalse(capture.silent_for(5.0),
                         '采集时长不足检查窗口时不应误报')

    def test_faint_noise_above_threshold_not_flagged(self):
        """真实设备的底噪（-60 dBFS 以上）不算设备故障。"""
        import numpy as np

        capture = self._capture()
        noise = (np.random.default_rng(0).normal(0, 0.002, (1600, 1))).astype(np.float32)
        for _ in range(10):
            capture._callback(noise, 1600, None, _Status())
        self.assertFalse(capture.silent_for(1.0),
                         '有底噪说明设备在工作，不应报「没有声音」')


class _Status:
    input_overflow = False


class ResumeHandlingTest(unittest.TestCase):
    """③ 唤醒时的行为：空闲则重连，录音中不打断。"""

    def test_resume_restarts_worker_when_idle(self):
        source = (ROOT / 'qt_web/voice_session.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'handle_system_resume':
                method = ast.get_source_segment(source, node) or ''
        self.assertIsNotNone(method, 'VoiceSession 必须有 handle_system_resume')
        self.assertIn('_discard', method, '唤醒后应重启工作进程以重建 PortAudio 设备表')
        self.assertIn("self.phase != 'idle'", method,
                      '录音进行中不能直接重启，会打断用户说话')

    def test_main_window_listens_for_power_events(self):
        source = (ROOT / 'qt_web/main.py').read_text(encoding='utf-8')
        self.assertIn('PowerMonitor', source)
        self.assertIn('nativeEvent', source)
        self.assertIn('handle_system_resume', source)

    def test_deferred_reconnect_after_busy(self):
        """唤醒时正在录音，不能丢话，也不能把重连忘掉。

        必须等本轮结束补做，否则用户下次说话仍落在失效的设备表上。
        """
        import ast

        source = (ROOT / 'qt_web/voice_session.py').read_text(encoding='utf-8')
        self.assertIn('_resume_pending', source)

        tree = ast.parse(source)
        method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_reconnect_if_pending':
                method = ast.get_source_segment(source, node) or ''
        self.assertIsNotNone(method, '必须有 _reconnect_if_pending 补做重连')
        # 补做路径不能再走 _state('idle')，否则递归
        self.assertNotIn("_state('idle'", method,
                         '补做重连不能调用 _state，会无限递归')

    def test_pipeline_raises_actionable_error_on_silence(self):
        """静音时必须报出可操作的原因，而不是含糊的「未检测到说话」。"""
        source = (ROOT / 'qt_web/voice_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('silent_for', source)
        self.assertIn('麦克风没有声音', source)
        # 提示要给出「休眠唤醒」这个最常见原因
        self.assertIn('休眠', source)


if __name__ == '__main__':
    unittest.main()
