"""打印本程序各进程的内存占用（任务管理器同口径：工作集 WS + 提交 Private）。

用法::

    py -3.14 tools/mem_report.py              # 打印一次
    py -3.14 tools/mem_report.py --watch 5    # 每 5 秒打印一次，Ctrl+C 结束
    py -3.14 tools/mem_report.py --json       # 机器可读

关注的三类进程：
    OCR_Reader_Web*       GUI（Qt + Chromium + OCR）
    OCR_VoiceWorker*      语音识别子进程（闲置退出前 ~2GB，退出后 0）
    *_VoiceWorker*        开发态 `py -m qt_web.voice_worker`
    QtWebEngineProcess*   页面渲染进程（Qt WebEngine 的独立子进程）

口径说明（KB-PERF-007）：

- **WS** = 工作集，任务管理器「内存」列。
- **提交** = PrivateUsage，任务管理器「提交大小」列，决定系统还能不能再分配。
- **不要用 psutil 的 `memory_info().private`**：在 psutil 7.x 上它等于 `vms`
  （虚拟地址空间），会随启动预读一路涨，看着像泄漏其实不是。本工具改用
  Win32 `GetProcessMemoryInfo` 取真实提交量；取不到时退化为 psutil rss。
- 语音 worker 也可能没有独立进程名（开发态就是 python.exe），所以按
  **父子关系**兜底：GUI 的子进程一并计入，避免漏掉渲染进程与语音子进程。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import time

MB = 1024 * 1024

# 进程名 -> 展示名（按包含关系匹配，忽略大小写）
ROLES = (
    ("voiceworker", "语音识别子进程"),
    ("ocr_reader", "GUI"),
    ("qtwebengineprocess", "页面渲染进程"),
    ("python", "Python 进程"),
)


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _win32_memory(pid: int):
    """返回 (WS, 提交, 峰值WS) 字节数；非 Windows 或失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if not ok:
                return None
            return (
                int(counters.WorkingSetSize),
                int(counters.PrivateUsage),
                int(counters.PeakWorkingSetSize),
            )
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _role(name: str) -> str:
    low = name.lower().replace(".exe", "")
    for key, label in ROLES:
        if key in low:
            return label
    return "其它"


def collect(include_other: bool = False) -> dict:
    try:
        import psutil
    except ImportError:
        raise SystemExit("需要 psutil：py -3.14 -m pip install psutil")

    self_pid = os.getpid()
    rows = []
    # 先建 pid -> ppid 表，用于按「GUI 的子进程」兜底识别渲染/语音进程
    all_procs = {}
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        all_procs[proc.info["pid"]] = proc

    def ancestors_of(pid: int, depth: int = 4):
        chain = []
        cur = pid
        for _ in range(depth):
            proc = all_procs.get(cur)
            if proc is None:
                break
            parent = proc.info.get("ppid")
            if not parent or parent == cur:
                break
            chain.append(parent)
            cur = parent
        return chain

    # 谁是「我们的」根进程：名字命中 python/ocr，且不是本工具自己
    def is_ours(name: str) -> bool:
        low = name.lower()
        return "python" in low or "ocr" in low

    roots = {
        pid
        for pid, proc in all_procs.items()
        if pid != self_pid and is_ours(proc.info.get("name") or "")
    }
    # GUI 子孙链上的进程也算（QtWebEngineProcess / 语音 worker）
    related = set(roots)
    for pid, proc in all_procs.items():
        if pid == self_pid:
            continue
        if any(a in roots for a in ancestors_of(pid)):
            related.add(pid)

    for pid in related:
        proc = all_procs.get(pid)
        if proc is None:
            continue
        try:
            name = proc.info.get("name") or ""
            role = _role(name)
            if role == "其它" and not include_other:
                continue
            info = proc.memory_info()
            win = _win32_memory(pid)
            if win is not None:
                ws, private, peak = (v / MB for v in win)
            else:  # 非 Windows 或权限不足时退化
                ws, private, peak = info.rss / MB, info.rss / MB, 0.0
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "role": role,
                    "ppid": proc.info.get("ppid"),
                    "rss_mb": round(ws, 1),
                    "private_mb": round(private, 1),
                    "peak_mb": round(peak, 1),
                    "cmd": " ".join(proc.cmdline()[1:3])[:60],
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue

    # 语音 worker 优先（内存大头）
    order = {"语音识别子进程": 0, "GUI": 1, "页面渲染进程": 2, "Python 进程": 3, "其它": 4}
    rows.sort(key=lambda r: (order.get(r["role"], 9), -r["private_mb"]))
    return {
        "at": time.strftime("%H:%M:%S"),
        "rows": rows,
        "total_rss_mb": round(sum(r["rss_mb"] for r in rows), 1),
        "total_private_mb": round(sum(r["private_mb"] for r in rows), 1),
    }


def render(report: dict) -> str:
    lines = [
        f"{'时间':<10}{'角色':<12}{'PID':>7}  {'WS MB':>8}{'提交 MB':>10}{'峰值WS':>9}  进程"
    ]
    for r in report["rows"]:
        lines.append(
            f"{report['at']:<12}{r['role']:<12}{r['pid']:>7}  {r['rss_mb']:>8.1f}"
            f"{r['private_mb']:>10.1f}{r.get('peak_mb', 0.0):>9.1f}  {r['name']}  {r['cmd']}".rstrip()
        )
    if not report["rows"]:
        lines.append("（没找到本程序的进程——GUI 未启动？）")
    lines.append(
        f"{'合计':<12}{'':<12}{'':>7}  {report['total_rss_mb']:>8.1f}"
        f"{report['total_private_mb']:>10.1f}"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本程序进程内存报告")
    ap.add_argument("--watch", type=float, default=0, help="每隔 N 秒打印一次（0=只打印一次）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--all-python", action="store_true", help="同时列出无关的 Python 进程")
    ap.add_argument("--exe", action="store_true", help="只报告打包版（OCR_Reader_Web / OCR_VoiceWorker）")
    ap.add_argument("--dev", action="store_true", help="只报告开发态（python -m qt_web.main / voice_worker）")
    args = ap.parse_args(argv)

    try:
        while True:
            report = collect(include_other=args.all_python)
            if args.exe or args.dev:
                if args.exe:
                    report["rows"] = [r for r in report["rows"] if "ocr_" in r["name"].lower()]
                else:
                    # 开发态：主进程 cmdline 含 qt_web，渲染/语音子进程靠名字与父子关系
                    report["rows"] = [
                        r for r in report["rows"]
                        if "qt_web" in r["cmd"].lower()
                        or r["role"] in ("页面渲染进程", "语音识别子进程")
                    ]
                report["total_rss_mb"] = round(sum(r["rss_mb"] for r in report["rows"]), 1)
                report["total_private_mb"] = round(sum(r["private_mb"] for r in report["rows"]), 1)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report), flush=True)
            if args.watch <= 0:
                return 0
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
