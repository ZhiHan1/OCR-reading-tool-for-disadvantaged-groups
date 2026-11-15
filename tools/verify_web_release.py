"""对实际发布 EXE 自检，不依赖源码运行结果作为成功依据。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import queue
import threading
import time

OCR_FILES = {
    'PP-OCRv6_det_small.onnx',
    'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
    'PP-OCRv6_rec_small.onnx',
}
MODEL_FILES = ('config.yaml', 'model.pt')
RUNTIME_VOICE = ('models/asr/fsmn-vad', 'models/asr/ct-punc')
# 识别模型按发布目录里实际存在的算，不再硬性要求 SenseVoice。
VOICE_CANDIDATES = (
    ('模型/语音/SenseVoiceSmall', 'SenseVoice-Small', 'sensevoice'),
    ('模型/语音/paraformer-large', 'Paraformer-Large', 'paraformer_large'),
)


def main():
    out = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parents[1]
    report = {'release': str(out), 'checks': {}}
    checks = report['checks']
    voice_models = [
        (relative, name, kind)
        for relative, name, kind in VOICE_CANDIDATES
        if (out / relative).is_dir()
    ]

    # 权重一律外置：**程序本体**里不许出现 .onnx / .pt / .gguf 等；
    # 模型只放在 EXE 同级的 模型/ 与 models/ 下（相对 sys.executable 解析），
    # 所以这两个目录要排除在「本体」之外。
    forbidden = {'.gguf', '.safetensors', '.ckpt', '.onnx', '.pt', '.pth', '.bin'}
    external_prefixes = ('模型', 'models')
    # Chromium V8 快照与 chardet 的编码模型也是 .bin，但它们是运行库不是权重。
    non_model = (('_internal', 'PySide6'), ('_internal', 'chardet'))

    def in_external(relative):
        parts = relative.parts
        if parts[:1] in [(p,) for p in external_prefixes]:
            return True
        return any(parts[:len(prefix)] == prefix for prefix in non_model)

    weights = [
        p.relative_to(out).as_posix()
        for p in out.rglob('*')
        if p.is_file()
        and p.suffix.lower() in forbidden
        and not in_external(p.relative_to(out))
    ]
    checks['translation_weights_external'] = not weights
    report['stray_weights'] = weights[:20]
    report['stray_weights'] = weights[:20]
    checks['translation_folder_external'] = (out / '模型/翻译').is_dir()
    checks['ocr_runtime_assets'] = OCR_FILES <= {
        p.name for p in (out / 'models/ocr').glob('*.onnx')
    }
    checks['voice_models_external'] = bool(voice_models) and all(
        (out / relative / name).is_file()
        for relative, _, _ in voice_models
        for name in MODEL_FILES
    ) and all(
        (out / relative / name).is_file()
        for relative in RUNTIME_VOICE
        for name in MODEL_FILES
    )
    report['voice_recognition_models'] = [item[0] for item in voice_models]
    registry_file = out / 'models/asr/registry.json'
    try:
        registry = json.loads(registry_file.read_text(encoding='utf-8'))
        checks['voice_registry'] = (
            isinstance(registry, list)
            and len(registry) >= 1
            and all((out / item['path']).is_dir() for item in registry)
        )
    except (OSError, ValueError, KeyError, TypeError):
        checks['voice_registry'] = False

    # CUDA 运行库按清单随包发布，保证 OCR 可以真正走 GPU。
    gpu_manifest = out / 'gpu-runtime-manifest.json'
    runtime = out / '_internal/gpu_runtime'
    try:
        entries = json.loads(gpu_manifest.read_text(encoding='utf-8'))
        checks['gpu_runtime_assets'] = bool(entries) and all(
            (runtime / item['name']).is_file()
            and (runtime / item['name']).stat().st_size == item['size']
            for item in entries
        )
    except (OSError, ValueError, KeyError, TypeError):
        checks['gpu_runtime_assets'] = False
    checks['cuda_provider'] = bool(
        list((out / '_internal').rglob('onnxruntime_providers_cuda.dll'))
    ) and not bool(
        list((out / '_internal').rglob('onnxruntime_providers_tensorrt.dll'))
    )

    checks['project_icon'] = hashlib.sha256(
        (root / 'Logo.ico').read_bytes()
    ).hexdigest() == hashlib.sha256((out / '_internal/Logo.ico').read_bytes()).hexdigest()
    checks['ui_assets'] = all(
        (out / '_internal' / p.relative_to(root)).is_file()
        and (out / '_internal' / p.relative_to(root)).read_bytes() == p.read_bytes()
        for p in (root / 'qt_web/web').rglob('*')
        if p.is_file()
    )
    checks['webengine_process'] = bool(list((out / '_internal').rglob('QtWebEngineProcess.exe')))
    checks['https_certificate'] = (out / '_internal/certifi/cacert.pem').is_file()
    checks['mp3_encoder'] = bool(list((out / '_internal').rglob('lameenc*.pyd')))
    checks['debug_resources_removed'] = not bool(
        list((out / '_internal').rglob('*.debug.pak'))
    )
    checks['legacy_main_excluded'] = (
        not (out / 'OCR_Reader_Qt.exe').exists()
        and not (out / 'OCR_Reader.exe').exists()
    )

    from PyInstaller.archive.readers import CArchiveReader

    archives = {}
    for executable in ('OCR_Reader_Web.exe', 'OCR_VoiceWorker.exe'):
        archive = CArchiveReader(str(out / executable))
        pyz_name = next(name for name, entry in archive.toc.items() if name.endswith('.pyz'))
        archives[executable] = set(archive.open_embedded_archive(pyz_name).toc)
    gui_modules = archives['OCR_Reader_Web.exe']
    worker_modules = archives['OCR_VoiceWorker.exe']
    checks['gui_no_asr_framework'] = not any(
        n == 'torch' or n.startswith(('torch.', 'funasr.')) for n in gui_modules
    )
    checks['worker_no_gui_framework'] = not any(
        n == 'PySide6' or n.startswith('PySide6.') for n in worker_modules
    )
    checks['no_legacy_main_modules'] = not any(
        n in gui_modules | worker_modules for n in ['qt_main', 'main_modular_win11']
    )

    # 语音模型/OCR/VAD/标点全部从 EXE 同级的外置目录读取；只有翻译 GGUF 来自源码树。
    external = {
        'voice': [
            {'id': 'test_' + kind, 'name': name, 'kind': kind,
             'path': str(out / relative)}
            for relative, name, kind in voice_models
        ],
        'vad': str(out / 'models/asr/fsmn-vad'),
        'punc': str(out / 'models/asr/ct-punc'),
        'ocr': str(out / 'models/ocr'),
        'gguf': str(root / '模型/翻译/HY-MT1.5-1.8B-Q6_K.gguf'),
        'expect_gpu': True,
    }
    test_spec = out.parent / 'external-model-test.json'
    test_spec.write_text(json.dumps(external, ensure_ascii=False), encoding='utf-8')
    for exe, name, timeout in [
        ('OCR_Reader_Web.exe', 'gui', 240),
        ('OCR_VoiceWorker.exe', 'worker', 360),
    ]:
        target = out / (name + '-self-test.json')
        target.unlink(missing_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONIOENCODING='utf-8')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env['PATH'] = str(out / '_internal/gpu_runtime') + os.pathsep + env.get('PATH', '')
        try:
            with (out / (name + '-self-test.log')).open('wb') as log:
                p = subprocess.run(
                    [str(out / exe), '--self-test', str(target)],
                    cwd=tempfile.gettempdir(),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
            data = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {'error': 'no report'}
            report[name] = data
            checks[name] = p.returncode == 0 and data.get('ok') is True
        except Exception as exc:
            checks[name] = False
            report[name] = {'error': str(exc)}
        if name == 'worker' and checks.get('worker'):
            # worker 走真实启动路径，顺带确认麦克风能被枚举到：
            # GUI 进程不加载 sounddevice，真正打开设备的永远是语音子进程。
            devices = (report.get('worker') or {}).get('input_devices')
            checks['microphone_listing'] = isinstance(devices, list) and len(devices) > 0
            report['input_devices'] = devices
        if checks.get(name) and external['voice'] and all(Path(m['path']).is_dir() for m in external['voice']):
            target = out / (name + '-inference-test.json')
            with (out / (name + '-inference-test.log')).open('wb') as log:
                try:
                    run = subprocess.run(
                        [str(out / exe), '--self-test', str(target),
                         '--test-models', str(test_spec)],
                        cwd=tempfile.gettempdir(),
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=timeout,
                    )
                    data = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
                    required = {'ocr', 'gguf'} if name == 'gui' else (
                        {item['kind'] for item in external['voice']} | {'vad'}
                    )
                    checks[name + '_inference'] = (
                        run.returncode == 0
                        and data.get('ok') is True
                        and required <= set(data.get('external_model_tests', {}))
                    )
                    report[name + '_inference'] = data
                    if name == 'gui':
                        checks['gpu_inference'] = data.get('gpu_inference') == 'CUDAExecutionProvider passed'
                except Exception as exc:
                    checks[name + '_inference'] = False
                    report[name + '_inference'] = {'error': str(exc)}
    if checks.get('worker') and external['voice'] and Path(external['voice'][0]['path']).is_dir():
        import yaml

        config_file = out / 'config.yaml'
        config_original = config_file.read_bytes()
        config = yaml.safe_load(config_original)
        config.setdefault('asr', {}).update(
            vad_path=external['vad'], punc_path=external['punc']
        )
        config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        messages = queue.Queue()
        with (out / 'worker-ipc-test.log').open('wb') as log:
            proc = subprocess.Popen(
                [str(out / 'OCR_VoiceWorker.exe')],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                cwd=out,
                env=env,
            )

            def reader():
                for line in proc.stdout:
                    try:
                        messages.put(json.loads(line.decode('utf-8')))
                    except (UnicodeError, ValueError):
                        pass

            threading.Thread(target=reader, daemon=True).start()
            try:
                command = {'command': 'load', 'session': 812, 'model': external['voice'][0]}
                proc.stdin.write((json.dumps(command) + chr(10)).encode('utf-8'))
                proc.stdin.flush()
                deadline = time.monotonic() + 150
                observed = []
                while time.monotonic() < deadline:
                    item = messages.get(timeout=max(.1, deadline - time.monotonic()))
                    observed.append(item)
                    if item.get('type') in ('idle', 'error'):
                        break
                checks['worker_ipc'] = (
                    any(m.get('type') == 'model' for m in observed)
                    and observed[-1].get('type') == 'idle'
                )
                report['worker_ipc'] = observed
                if checks['worker_ipc']:
                    command = {
                        'command': 'start',
                        'session': 813,
                        'model': external['voice'][0],
                        'options': {
                            'mode': 'manual',
                            'segment_silence_ms': 800,
                            'turn_silence_ms': 3000,
                            'initial_silence_ms': 15000,
                        },
                    }
                    proc.stdin.write((json.dumps(command) + chr(10)).encode('utf-8'))
                    proc.stdin.flush()
                    recording = False
                    stopped = False
                    levels = 0
                    recorded_at = None
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        item = messages.get(timeout=max(.1, deadline - time.monotonic()))
                        if item.get('session') != 813:
                            continue
                        kind = item.get('type')
                        if kind == 'error':
                            raise RuntimeError(item.get('text'))
                        if kind == 'recording':
                            recording = True
                            recorded_at = time.monotonic()
                        if kind == 'level':
                            levels += 1
                        if recording and not stopped and time.monotonic() - recorded_at >= .5:
                            proc.stdin.write(
                                (json.dumps({'command': 'stop', 'session': 813}) + chr(10)).encode('utf-8')
                            )
                            proc.stdin.flush()
                            stopped = True
                        if kind == 'idle':
                            break
                    checks['worker_record_stop'] = recording and stopped and levels > 0 and kind == 'idle'
                    report['worker_record_stop'] = {
                        'recording': recording,
                        'stopped': stopped,
                        'level_updates': levels,
                        'last_event': kind,
                    }
            except Exception as exc:
                checks['worker_ipc'] = False
                report['worker_ipc'] = {'error': repr(exc), 'messages': observed}
            finally:
                proc.kill()
                proc.wait(timeout=10)
                config_file.write_bytes(config_original)

    all_files = [f for f in out.rglob('*') if f.is_file()]
    total = sum(f.stat().st_size for f in all_files)
    report['size_bytes'] = {
        'total': total,
        'gui_exe': (out / 'OCR_Reader_Web.exe').stat().st_size,
        'worker_exe': (out / 'OCR_VoiceWorker.exe').stat().st_size,
    }
    report['ok'] = all(checks.values())
    (out / 'release-test.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
