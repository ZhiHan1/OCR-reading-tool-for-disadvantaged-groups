"""麦克风列表过滤回归测试。

覆盖一件事：**只列 Windows 判定为「在线」的采集设备。**

背景：
    用户反馈「识别出来的麦克风很多都是没有在线的」。
    实测本机 sounddevice 枚举出 15 个输入端口，其中：
        - 3 个 Windows 状态为「不存在」（设备已拔出，注册项还在）
        - 1 个为「已禁用」
        - 其余同名设备在 MME/DirectSound/WASAPI/WDM-KS 下重复出现
    用户在下拉框里选中一个不存在的设备，录音直接失败。

修法：
    `core/audio_endpoints.py` 用 ctypes 调 Core Audio 的
    `IMMDevice::GetState()`（与「设置 → 系统 → 声音」同源），
    `input_devices()` 据此过滤，**不做音频探测**。

这些测试用假数据验证过滤逻辑本身，不依赖真实声卡，可以在任何机器上跑。

用法（在项目根目录执行）：

    py -3.14 -m unittest tests.test_audio_devices -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.audio_capture as capture  # noqa: E402
import core.audio_endpoints as endpoints  # noqa: E402


def _dev(name, hostapi_index):
    return {'name': name, 'max_input_channels': 1, 'default_samplerate': 44100,
            'hostapi': hostapi_index}


HOSTAPIS = [{'name': 'MME'}, {'name': 'Windows WASAPI'}, {'name': 'Windows WDM-KS'}]


class DeviceFilterTest(unittest.TestCase):
    """input_devices 按 Windows 状态过滤。"""

    def _run(self, devices, online, online_ok=True):
        with mock.patch.object(capture, 'is_available', return_value=True), \
             mock.patch.object(capture.sd, 'query_hostapis', return_value=HOSTAPIS), \
             mock.patch.object(capture.sd, 'query_devices', return_value=devices), \
             mock.patch('core.audio_endpoints.active_capture_names',
                        return_value=(online if online_ok else None)):
            return capture.input_devices()

    def test_offline_devices_excluded(self):
        """Windows 说不在线的设备必须从列表里消失。"""
        devices = [
            _dev('麦克风 (HECATE G2无线版)', 0),
            _dev('麦克风 (High Definition Audio Device)', 0),
            _dev('麦克风 (Virtual Desktop Audio)', 0),
        ]
        online = {'麦克风 (HECATE G2无线版)': 'active',
                  '麦克风 (Virtual Desktop Audio)': 'active'}
        result = self._run(devices, online)
        names = [d['name'] for d in result]
        self.assertIn('麦克风 (HECATE G2无线版)', names)
        self.assertIn('麦克风 (Virtual Desktop Audio)', names)
        self.assertNotIn('麦克风 (High Definition Audio Device)', names,
                         '不存在的设备不该出现在下拉框')

    def test_all_offline_gives_empty(self):
        devices = [_dev('麦克风 (Gone)', 0)]
        self.assertEqual(self._run(devices, {}), [])

    def test_duplicate_hostapis_merged(self):
        """同一支麦克风在多个 hostapi 下只出现一次。"""
        devices = [
            _dev('麦克风 (HECATE G2无线版)', 0),   # MME
            _dev('麦克风 (HECATE G2无线版)', 1),   # WASAPI
            _dev('麦克风 (HECATE G2无线版)', 2),   # WDM-KS
        ]
        result = self._run(devices, {'麦克风 (HECATE G2无线版)': 'active'})
        self.assertEqual(len(result), 1)

    def test_prefers_mme(self):
        """归并后优先保留 MME 端口（名字与 Windows 设置一致）。"""
        devices = [
            _dev('麦克风 (HECATE G2无线版)', 1),   # WASAPI 在前
            _dev('麦克风 (HECATE G2无线版)', 0),   # MME
        ]
        result = self._run(devices, {'麦克风 (HECATE G2无线版)': 'active'})
        self.assertEqual(result[0]['hostapi'], 'MME')

    def test_output_only_devices_excluded(self):
        """没有输入通道的设备不该出现。"""
        device = {'name': '扬声器 (Speakers)', 'max_input_channels': 0,
                  'default_samplerate': 44100, 'hostapi': 0}
        self.assertEqual(self._run([device], {'扬声器 (Speakers)': 'active'}), [])

    def test_degrades_when_status_unavailable(self):
        """查不到 Windows 状态时不过滤，退回原始列表（绝不能清空）。"""
        devices = [_dev('麦克风 (A)', 0), _dev('麦克风 (B)', 0)]
        result = self._run(devices, {}, online_ok=False)
        self.assertEqual(len(result), 2, '状态查询失败必须降级，不能把设备全丢掉')


class NameMatchTest(unittest.TestCase):
    """Windows 友好名与 PortAudio 设备名常有出入，按前缀宽松匹配。"""

    def test_exact_match(self):
        self.assertTrue(capture._windows_says_online(
            '麦克风 (HECATE G2无线版)', [], {'麦克风 (HECATE G2无线版)': 'active'}))

    def test_truncated_mme_name_matches(self):
        """MME 名字被截断到 31 字符，仍要能对上完整名。"""
        full = '麦克风 (HECATE G2无线版)'
        truncated = full[:12]
        self.assertTrue(capture._windows_says_online(
            truncated, [], {full: 'active'}))

    def test_alias_entry_matches(self):
        """组内任一别名命中即可。"""
        entries = [{'name': '麦克风 (HECATE G2无线版)', 'hostapi': 'Windows WASAPI'}]
        self.assertTrue(capture._windows_says_online(
            '麦克', entries, {'麦克风 (HECATE G2无线版)': 'active'}))

    def test_no_match(self):
        self.assertFalse(capture._windows_says_online(
            '麦克风 (Gone)', [], {'麦克风 (HECATE G2无线版)': 'active'}))


class StateLabelsTest(unittest.TestCase):
    """状态位与中文标签要和 mmdeviceapi.h 一致。"""

    def test_state_constants(self):
        self.assertEqual(endpoints.DEVICE_STATE_ACTIVE, 0x1)
        self.assertEqual(endpoints.DEVICE_STATE_DISABLED, 0x2)
        self.assertEqual(endpoints.DEVICE_STATE_NOTPRESENT, 0x4)
        self.assertEqual(endpoints.DEVICE_STATE_UNPLUGGED, 0x8)

    def test_labels(self):
        self.assertEqual(endpoints.STATE_LABELS[0x1], '在线')
        self.assertEqual(endpoints.STATE_LABELS[0x2], '已禁用')
        self.assertEqual(endpoints.STATE_LABELS[0x4], '不存在')
        self.assertEqual(endpoints.STATE_LABELS[0x8], '未插入')


class RealStatusQueryTest(unittest.TestCase):
    """真实查询 Windows 状态（需要 Windows + 声卡，其它平台跳过）。"""

    @unittest.skipUnless(sys.platform == 'win32', '仅 Windows')
    def test_capture_devices_readable(self):
        data = endpoints.capture_devices()
        if data is None:
            self.skipTest('COM 查询不可用')
        # 每个条目都要有名字和合法状态位
        for item in data:
            self.assertIn(item['state'], {0x1, 0x2, 0x4, 0x8})
            self.assertIsInstance(item['state_zh'], str)

    @unittest.skipUnless(sys.platform == 'win32', '仅 Windows')
    def test_active_subset_of_all(self):
        all_devices = endpoints.capture_devices()
        if all_devices is None:
            self.skipTest('COM 查询不可用')
        active = endpoints.active_capture_names()
        self.assertIsNotNone(active)
        active_from_all = {d['name'] for d in all_devices if d['state'] == 0x1}
        self.assertEqual(set(active), active_from_all)


if __name__ == '__main__':
    unittest.main()
