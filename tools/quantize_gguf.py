"""把 GGUF 转成指定量化档位（包装官方 llama-quantize.exe）。

为什么要有这个脚本
------------------
腾讯官方 HY-MT1.5-1.8B-GGUF 只发布了 Q4_K_M / Q6_K / Q8_0 三档，
没有 Q5_K_M。要拿到 Q5_K_M 只能自己量化。

**关键原则：从 Q8_0 量化，不要从 Q6_K 量化。**
k-quant 是有损压缩，拿已量化的权重再量化会叠加两层误差。
Q8_0 是最接近原始 fp16 的公开权重，作为量化源最干净。

踩过的坑
--------
不要用 `llama_cpp.llama_model_quantize()`（Python 包自带的 C API）。
llama-cpp-python 0.3.36 的预编译库在 Windows 上无论传给什么都会报：

    llama_model_quantize: failed to quantize: ios_base::failbit set: iostream stream error

连 `only_copy=True`（只复制张量、完全不量化）也照样失败，属于库本身写文件
环节的缺陷，与路径、磁盘、参数都无关。因此本脚本只调用官方
`llama-quantize.exe`。

获取 exe
--------
    winget install ggml.llamacpp
若 GitHub 直连不通，用代理下载 release 压缩包：

    curl -L -o llama.zip ^
      https://gh-proxy.com/https://github.com/ggml-org/llama.cpp/releases/download/b12098/llama-b12098-bin-win-cpu-x64.zip

用法
----
    # 预览
    py -3.14 tools/quantize_gguf.py --src a.gguf --ftype Q5_K_M --dry-run

    # 量化（--exe 可省略，脚本会在常见位置和 PATH 里找）
    py -3.14 tools/quantize_gguf.py --src a.gguf --ftype Q5_K_M --out b.gguf

    # 显式指定 exe
    py -3.14 tools/quantize_gguf.py --src a.gguf --exe D:/llamacpp/llama-quantize.exe
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FTYPES = ["Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L", "Q4_K_S", "Q4_K_M",
          "Q5_K_S", "Q5_K_M", "Q6_K", "Q8_0", "F16"]

EXE_CANDIDATES = [
    Path(os.environ.get("TEMP", "")) / "llamacpp" / "llama-quantize.exe",
    Path(r"C:\Program Files\llama.cpp\llama-quantize.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages",
]


def find_exe(explicit: str = "") -> Path | None:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise SystemExit(f"指定的 exe 不存在: {p}")

    hit = shutil.which("llama-quantize")
    if hit:
        return Path(hit)

    for c in EXE_CANDIDATES:
        if c.is_file():
            return c
        if c.is_dir():
            found = list(c.rglob("llama-quantize.exe"))
            if found:
                return found[0]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GGUF 量化（调用官方 llama-quantize.exe）")
    ap.add_argument("--src", required=True, help="源 GGUF（建议用 Q8_0）")
    ap.add_argument("--out", default="", help="输出 GGUF（默认 <src>_<ftype>.gguf）")
    ap.add_argument("--ftype", default="Q5_K_M", choices=FTYPES,
                    help="目标量化类型，默认 Q5_K_M")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--exe", default="", help="llama-quantize.exe 路径")
    ap.add_argument("--allow-requantize", action="store_true",
                    help="允许对已量化文件再量化（有损，慎用）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不执行")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    if not src.is_file():
        raise SystemExit(f"源文件不存在: {src}")

    dst = Path(args.out) if args.out else src.with_name(f"{src.stem}_{args.ftype}.gguf")
    if not dst.is_absolute():
        dst = (ROOT / dst).resolve()
    if dst == src:
        raise SystemExit("输出路径不能和源文件相同")

    # llama-quantize 不会创建父目录
    dst.parent.mkdir(parents=True, exist_ok=True)

    exe = find_exe(args.exe)
    src_mb = src.stat().st_size / 1048576

    print(f"源文件 : {src}")
    print(f"          {src_mb:.2f} MB")
    print(f"目标   : {dst}")
    print(f"档位   : {args.ftype}")
    print(f"线程   : {args.threads}")
    print(f"工具   : {exe if exe else '(未找到 llama-quantize.exe)'}")

    if args.dry_run:
        print("\n[dry-run] 未执行量化。")
        return 0

    if exe is None:
        raise SystemExit(
            "找不到 llama-quantize.exe。\n"
            "请执行 winget install ggml.llamacpp，或用 --exe 指定路径。\n"
            "注意：不要改用 llama_cpp.llama_model_quantize，该库在 Windows 上写文件有缺陷。"
        )

    free_mb = shutil.disk_usage(dst.anchor).free / 1048576
    need_mb = src_mb * 0.85
    if free_mb < need_mb:
        raise SystemExit(f"磁盘空间不足：需要约 {need_mb:.0f} MB，剩余 {free_mb:.0f} MB")

    cmd = [str(exe), str(src), str(dst), args.ftype, str(args.threads)]
    if args.allow_requantize:
        cmd.insert(1, "--allow-requantize")

    print(f"\n开始量化…（源 {src_mb:.0f} MB，预计输出约 {need_mb:.0f} MB）", flush=True)
    t0 = time.time()
    # 输出重定向到文件，避免 stderr 的进度信息污染终端
    log = dst.with_suffix(".quantize.log")
    with open(log, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    cost = time.time() - t0

    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise SystemExit("量化失败，返回码 %d\n日志末尾:\n%s\n完整日志: %s"
                         % (proc.returncode, "\n".join(tail), log))

    dst_mb = dst.stat().st_size / 1048576
    log.unlink(missing_ok=True)
    print(f"\n完成，耗时 {cost:.1f} s")
    print(f"输出大小: {dst_mb:.2f} MB")
    print(f"相对源文件: {100 * dst_mb / src_mb:.1f}%（省 {src_mb - dst_mb:.2f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
