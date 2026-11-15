"""Web 版一键打包：PyInstaller 双 EXE → GPU 运行库 → 发布版自检。

**权重不进安装包**。产物里只有程序本体（双 EXE + `_internal/` + 说明 + 图标）；
模型放在 EXE 同级的 `模型/`（语音、翻译）与 `models/`（OCR / VAD / 标点 /
目录簿）下，由 `utils.paths.app_root()` 以 `sys.executable` 为锚按相对路径查找，
整目录换盘符、换机器都能用。

用法：
    py -3.14 build_web.py               完整打包 + 自检
    py -3.14 build_web.py --skip-tests  只打包，不跑发布版自检
    py -3.14 build_web.py --sync-models 打包后顺带把本机权重同步到产物目录

产物：dist/Web-<yyyyMMdd-HHmmss>/OCR_Reader_Web/
最新产物路径写入 build/latest-web-release.txt。
任何一步失败都保留现场（build.log / 发布目录），不做清理。
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

PREFLIGHT = (
    'import PyInstaller, PySide6.QtWebEngineWidgets, rapidocr, onnxruntime, '
    'llama_cpp, edge_tts, lameenc, sounddevice, soundfile, '
    'kaldi_native_fbank, win32com.client; print("DEPENDENCIES_OK")'
)
EXES = ('OCR_Reader_Web.exe', 'OCR_VoiceWorker.exe')
WEIGHT_SUFFIXES = {'.gguf', '.safetensors', '.ckpt', '.onnx', '.pt', '.pth'}
# 不属于权重的二进制：Chromium 的 V8 快照、chardet 的编码模型、DLL 之外的 .bin。
# 一律按路径排除，避免「装了 PySide6 就永远打包失败」。
NON_MODEL_BINS = ('_internal/PySide6/', '_internal/chardet/')
MIN_FREE_BYTES = 6 * 1024 ** 3  # 6 GiB：依赖 + 双 EXE 的保守下限（权重不再进包）


def say(message=''):
    print(message, flush=True)


def step(title):
    say()
    say('== ' + title + ' ==')


def child_env():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    return env


def run(cmd, log_path=None, tail=60):
    """运行子进程；给了 log_path 就把 stdout/stderr 一起写进去。"""
    if log_path is None:
        return subprocess.run(cmd, cwd=ROOT, env=child_env()).returncode
    with Path(log_path).open('wb') as stream:
        code = subprocess.run(
            cmd, cwd=ROOT, env=child_env(), stdout=stream, stderr=subprocess.STDOUT
        ).returncode
    if code != 0:
        say('---- 失败日志尾部（' + str(log_path) + '）----')
        text = Path(log_path).read_text(encoding='utf-8', errors='replace')
        say('\n'.join(text.splitlines()[-tail:]))
    return code


def check_disk(target, need=MIN_FREE_BYTES):
    probe = Path(target)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < need:
        raise RuntimeError(
            '磁盘空间不足：{:.1f} GiB 可用，至少需要 {:.1f} GiB（{}）'
            .format(free / 1024 ** 3, need / 1024 ** 3, probe)
        )
    return free


def py(*args):
    return [PYTHON, *args]


def check_weights(release, external=()):
    """检查安装包本体里没有权重。

    外置模型目录（`模型/`、`models/`）本来就该有 `.pt/.onnx`，因此按路径前缀排除，
    而不是靠后缀一刀切——否则外置方案永远无法通过检查。
    """
    release = Path(release)
    skip = {('模型',), ('models',)} | {tuple(Path(p).parts) for p in external}
    skip |= {tuple(Path(p).parts) for p in NON_MODEL_BINS}

    def is_external(relative):
        parts = tuple(relative.parts)
        return any(parts[:len(prefix)] == prefix for prefix in skip if prefix)

    found = [
        path.relative_to(release).as_posix()
        for path in release.rglob('*')
        if path.is_file()
        and path.suffix.lower() in WEIGHT_SUFFIXES
        and not is_external(path.relative_to(release))
    ]
    if found:
        raise RuntimeError('安装包本体里出现了权重文件（应放 EXE 同级的 模型/ 与 models/）：'
                           + ', '.join(found[:10]))


def main():
    parser = argparse.ArgumentParser(description='打包 Web 版 OCR 朗读工具')
    parser.add_argument('--skip-tests', action='store_true', help='跳过发布版自检')
    parser.add_argument('--tag', default='', help='自定义产物名后缀')
    parser.add_argument('--sync-models', action='store_true',
                        help='打包后把本机权重同步到产物目录（默认不同步，纯出程序）')
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    name = 'Web-' + stamp + (('-' + args.tag) if args.tag else '')
    release_root = ROOT / 'dist' / name
    work = ROOT / 'build' / 'web'
    build_log = release_root / 'build.log'
    latest = ROOT / 'build' / 'latest-web-release.txt'
    out = release_root / 'OCR_Reader_Web'

    say('项目目录: ' + str(ROOT))
    say('Python  : ' + PYTHON)
    say('输出    : ' + str(release_root))

    step('1/6 环境预检')
    free = check_disk(ROOT)
    say('磁盘可用: {:.1f} GiB'.format(free / 1024 ** 3))
    if run(py('-c', PREFLIGHT)) != 0:
        raise RuntimeError('依赖预检失败：见上方输出')
    say('DEPENDENCIES_OK')

    step('2/6 PyInstaller 双 EXE')
    release_root.mkdir(parents=True, exist_ok=True)
    code = run(
        py('-m', 'PyInstaller', '--noconfirm', '--clean',
           '--workpath', str(work), '--distpath', str(release_root),
           'OCR_Reader_Web.spec'),
        log_path=build_log,
    )
    if code != 0:
        raise RuntimeError('PyInstaller 失败，日志：' + str(build_log))
    for exe in EXES:
        target = out / exe
        if not target.is_file():
            raise RuntimeError('缺少可执行文件：' + str(target))
    say('EXE 就绪: ' + ', '.join(
        '{} ({:.1f} MiB)'.format(exe, (out / exe).stat().st_size / 1024 ** 2)
        for exe in EXES
    ))

    step('3/6 随包说明与图标')
    shutil.copy2(ROOT / 'Logo.ico', out / 'Logo.ico')
    shutil.copy2(ROOT / 'WEB_RELEASE.md', out / 'README.md')
    check_weights(out)
    say('README.md / Logo.ico 已复制')

    step('4/6 运行时资源（仅程序本体）')
    if run(py('tools/stage_gpu_runtime.py', str(out))) != 0:
        raise RuntimeError('CUDA 运行库 staging 失败：tools/stage_gpu_runtime.py')
    # 权重一律外置，安装包里不许出现 .onnx / .pt / .gguf：
    # 缺模型不是打包错误，运行时由 <EXE 目录>/模型 与 models/ 提供。
    (out / '模型/翻译').mkdir(parents=True, exist_ok=True)
    check_weights(out)
    if args.sync_models:
        if run(py('tools/sync_external_models.py', str(out))) != 0:
            raise RuntimeError('外置模型同步失败：tools/sync_external_models.py')
    code = run(py('tools/stage_external_models.py', str(out)))
    if code != 0:
        say('提示：外置模型不完整，安装包可正常生成，但运行前需要补齐模型目录。')
        say('      可用 py -3.14 tools\\sync_external_models.py "' + str(out) + '" 同步本机权重。')
    say('staging 完成')

    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(str(out), encoding='utf-8')

    manifest_file = out / 'external-models-manifest.json'
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        say('外置模型: ' + (
            '完整' if not manifest.get('missing')
            else '缺少 ' + ', '.join(manifest['missing'])
        ))

    if args.skip_tests:
        step('5/6 自检（已跳过）')
    else:
        step('5/6 发布版自检')
        if run(py('tools/verify_web_release.py', str(out))) != 0:
            raise RuntimeError(
                '发布版自检失败，产物与日志保留：' + str(out)
            )
        say('release-test.json ok=true')

    step('6/6 汇总')
    files = [path for path in out.rglob('*') if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    say('程序文件数: {}'.format(len(files)))
    say('程序体积: {:.2f} GiB（不含模型）'.format(total / 1024 ** 3))
    say('')
    say('WEB_BUILD_OK: ' + str(out))
    say('最新产物记录: ' + str(latest))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        say()
        say('WEB_BUILD_FAILED')
        traceback.print_exc()
        sys.exit(1)
