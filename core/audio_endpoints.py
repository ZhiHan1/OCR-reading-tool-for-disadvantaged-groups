"""Windows 音频设备状态查询（Core Audio / MMDevice API）。

为什么需要这个模块：

`sounddevice`（PortAudio）只能告诉你「端口能不能打开」。虚拟声卡
（VB-Audio Cable、Virtual Desktop Audio、Pico Streaming）即使没有
任何音频源，端口照样能打开并返回**纯静音**，于是它们会出现在麦克风
列表里，用户选了才发现录不到声音。

Windows「设置 → 系统 → 声音」用的是 Core Audio 的 `IMMDevice::GetState()`，
能准确区分四种状态：

    ACTIVE      在线    驱动活跃，设备可用
    DISABLED    已禁用  用户在设备管理器里禁用了
    NOTPRESENT  不存在  设备已拔出 / 驱动卸载但注册项还在
    UNPLUGGED   未插入  接口在但没插设备

本模块用 ctypes 直接调 COM 拿这个状态，不依赖第三方库。
失败时（非 Windows、COM 不可用）所有函数返回 None，调用方降级为
「不过滤」，绝不因为查询失败而把设备列表清空。

实现注意（踩过的坑）：
- vtable 索引必须精确。`IMMDevice` 是
  0=QueryInterface 1=AddRef 2=Release 3=Activate 4=OpenPropertyStore
  5=GetId 6=GetState，用 5 会读到 `GetId` 的指针位置，状态永远是 0。
- `EnumAudioEndpoints(eCapture, ...)` 第一个参数 0=eRender、1=eCapture。
  传 0 会拿到 40 个扬声器，而不是麦克风。
- `OpenPropertyStore(DWORD)` 参数类型必须写 `DWORD`，写成
  `POINTER(c_void_p)` 会报「参数错误」。
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import HRESULT, POINTER, Structure, byref, c_uint, c_ulong, c_ushort, c_void_p
from ctypes.wintypes import DWORD
from typing import Dict, List, Optional

_IS_WINDOWS = sys.platform == 'win32'

# Windows 设备状态位（mmdeviceapi.h）
DEVICE_STATE_ACTIVE = 0x1
DEVICE_STATE_DISABLED = 0x2
DEVICE_STATE_NOTPRESENT = 0x4
DEVICE_STATE_UNPLUGGED = 0x8

STATE_NAMES = {
    DEVICE_STATE_ACTIVE: 'active',
    DEVICE_STATE_DISABLED: 'disabled',
    DEVICE_STATE_NOTPRESENT: 'notpresent',
    DEVICE_STATE_UNPLUGGED: 'unplugged',
}
STATE_LABELS = {
    DEVICE_STATE_ACTIVE: '在线',
    DEVICE_STATE_DISABLED: '已禁用',
    DEVICE_STATE_NOTPRESENT: '不存在',
    DEVICE_STATE_UNPLUGGED: '未插入',
}


class _GUID(Structure):
    _fields_ = [('Data1', c_ulong), ('Data2', c_ushort), ('Data3', c_ushort),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = uuid.UUID(text)
    return _GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))


CLSID_MMDeviceEnumerator = _guid('BCDE0395-E52F-467C-8E3D-C4579291692E')
IID_IMMDeviceEnumerator = _guid('A95664D2-9614-4F35-A746-DE8DB63617E6')
PKEY_FriendlyName = _guid('A45C254E-DF1C-4EFD-8020-67D146A850E0')


class _PROPERTYKEY(Structure):
    _fields_ = [('fmtid', _GUID), ('pid', DWORD)]


def _vtable_call(obj: c_void_p, index: int, restype, *argtypes):
    """按 vtable 索引取 COM 接口方法。"""
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(table[index])


_COM_READY = None


def _com_init() -> bool:
    """初始化 COM（每线程一次）。失败返回 False，调用方降级。"""
    global _COM_READY
    if not _IS_WINDOWS:
        return False
    if _COM_READY is None:
        try:
            ole32 = ctypes.windll.ole32
            hr = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            # S_OK(0) 与 S_FALSE(1) 都算成功；RPC_E_CHANGED_MODE 说明已初始化过
            _COM_READY = hr in (0, 1) or (hr & 0xFFFFFFFF) == 0x80010106
        except Exception:  # noqa: BLE001
            _COM_READY = False
    return bool(_COM_READY)


def _read_friendly_name(p_dev: c_void_p) -> str:
    p_store = c_void_p()
    # IMMDevice::OpenPropertyStore(DWORD dwStgmAccess, IPropertyStore**)
    if _vtable_call(p_dev, 4, HRESULT, DWORD, POINTER(c_void_p))(p_dev, DWORD(0), byref(p_store)) != 0:
        return ''
    pk = _PROPERTYKEY(PKEY_FriendlyName, 14)
    buf = (ctypes.c_ubyte * 32)()
    # IPropertyStore::GetValue(const PROPERTYKEY*, PROPVARIANT*)
    hr = _vtable_call(p_store, 5, HRESULT, POINTER(_PROPERTYKEY), c_void_p)(
        p_store, byref(pk), ctypes.cast(buf, c_void_p))
    if hr != 0:
        return ''
    if ctypes.c_ushort.from_buffer(buf, 0).value != 31:  # VT_LPWSTR
        return ''
    ptr = c_void_p.from_buffer(buf, 8).value
    return ctypes.wstring_at(ptr) if ptr else ''


def capture_devices() -> Optional[List[dict]]:
    """列出所有**采集**设备及其 Windows 状态。

    返回 [{'name': ..., 'state': int, 'state_name': str, 'state_zh': str}]，
    包含已禁用 / 不存在的设备（供诊断）。

    查询失败（非 Windows / COM 不可用）返回 None——调用方必须把 None
    当作「不知道」，降级为不过滤，而不是当作「没有设备」。
    """
    if not _com_init():
        return None
    try:
        ole32 = ctypes.windll.ole32
        p_enum = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1,
                                    byref(IID_IMMDeviceEnumerator), byref(p_enum))
        if hr != 0:
            return None

        p_coll = c_void_p()
        # IMMDeviceEnumerator::EnumAudioEndpoints(EDataFlow, DWORD dwStateMask, IMMDeviceCollection**)
        # EDataFlow: 0=eRender 1=eCapture 2=eAll
        hr = _vtable_call(p_enum, 3, HRESULT, c_uint, DWORD, POINTER(c_void_p))(
            p_enum, 1, DWORD(0xF), byref(p_coll))
        if hr != 0:
            return None

        count = c_uint()
        _vtable_call(p_coll, 3, HRESULT, POINTER(c_uint))(p_coll, byref(count))

        items: List[dict] = []
        for i in range(count.value):
            p_dev = c_void_p()
            if _vtable_call(p_coll, 4, HRESULT, c_uint, POINTER(c_void_p))(p_coll, i, byref(p_dev)) != 0:
                continue
            state = DWORD()
            # IMMDevice::GetState —— 索引 6，不是 5（5 是 GetId）
            if _vtable_call(p_dev, 6, HRESULT, POINTER(DWORD))(p_dev, byref(state)) != 0:
                continue
            items.append({
                'name': _read_friendly_name(p_dev),
                'state': int(state.value),
                'state_name': STATE_NAMES.get(state.value, f'0x{state.value:X}'),
                'state_zh': STATE_LABELS.get(state.value, '未知'),
            })
        return items
    except Exception as exc:  # noqa: BLE001 - COM 异常一律降级
        print(f'[录音] Core Audio 状态查询失败: {exc}')
        return None


def active_capture_names() -> Optional[Dict[str, str]]:
    """返回 {设备友好名: 'active'}，只含 Windows 判定为「在线」的采集设备。

    同名设备只保留一条。查询失败返回 None。
    """
    items = capture_devices()
    if items is None:
        return None
    names: Dict[str, str] = {}
    for item in items:
        if item['state'] != DEVICE_STATE_ACTIVE:
            continue
        name = (item.get('name') or '').strip()
        if name:
            names[name] = 'active'
    return names


if __name__ == '__main__':  # 手动诊断用
    data = capture_devices()
    if data is None:
        print('状态查询不可用')
    else:
        for d in sorted(data, key=lambda x: x['state']):
            print(f"[{d['state_zh']:5s}] {d['name']}")
