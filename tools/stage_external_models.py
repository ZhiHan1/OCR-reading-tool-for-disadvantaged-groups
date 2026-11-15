"""把外置模型目录的内容列出成清单，供安装包与发布自检对照。

权重**不进安装包**：`tools/sync_external_models.py` 只把文件摆在 EXE 同级
`模型/` 与 `models/` 下，本脚本负责把「目录里到底有什么」写成
`external-models-manifest.json`（全部相对路径），用于：
    1. 打包后核对模型目录是否齐全；
    2. 发布自检判断缺哪些文件，给出可读的提示。

用法：
    py -3.14 tools/stage_external_models.py <目标目录>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_OCR = (
    'PP-OCRv6_det_small.onnx',
    'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
    'PP-OCRv6_rec_small.onnx',
)
VOICE_DIRS = ('模型/语音/SenseVoiceSmall', '模型/语音/paraformer-large')
RUNTIME_DIRS = ('models/asr/fsmn-vad', 'models/asr/ct-punc')
MODEL_FILES = ('config.yaml', 'model.pt')
SKIP_DIRS = {'.cache', '__pycache__'}
SKIP_SUFFIXES = {'.bak', '.tmp'}


def _walk(directory):
    for item in sorted(directory.rglob('*')):
        if not item.is_file():
            continue
        relative = item.relative_to(directory)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if item.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield relative, item


def _describe(base, relative):
    directory = base / relative
    if not directory.is_dir():
        return {'path': relative, 'present': False, 'files': 0, 'size': 0}
    files = list(_walk(directory))
    return {
        'path': relative,
        'present': True,
        'files': len(files),
        'size': sum(item.stat().st_size for _, item in files),
        'complete': all((directory / name).is_file() for name in MODEL_FILES),
    }


def stage(output):
    """扫描目标目录里的外置模型，写清单；缺什么就记进 missing，不抛异常。"""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    ocr = _describe(out, 'models/ocr')
    ocr['required'] = {name: (out / 'models/ocr' / name).is_file() for name in REQUIRED_OCR}
    voice = [_describe(out, relative) for relative in VOICE_DIRS]
    runtime = [_describe(out, relative) for relative in RUNTIME_DIRS]

    registry_file = out / 'models/asr/registry.json'
    try:
        registry = json.loads(registry_file.read_text(encoding='utf-8'))
        if not isinstance(registry, list):
            registry = []
    except (OSError, ValueError):
        registry = []

    missing = []
    if not any(item['present'] and item['complete'] for item in voice):
        missing.append('模型/语音/<识别模型>（需要 config.yaml + model.pt）')
    missing += [item['path'] for item in runtime if not (item['present'] and item['complete'])]
    missing += ['models/ocr/' + name for name, ok in ocr['required'].items() if not ok]

    manifest = {
        'ocr': ocr,
        'voice': voice,
        'runtime': runtime,
        'registry': [
            {'id': item.get('id'), 'path': item.get('path')}
            for item in registry if isinstance(item, dict)
        ],
        'missing': missing,
        'total_bytes': ocr['size'] + sum(i['size'] for i in voice + runtime),
    }
    (out / 'external-models-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    result = stage(sys.argv[1] if len(sys.argv) > 1 else ROOT)
    print('EXTERNAL_MODELS_READY', result['total_bytes'],
          'missing=' + (','.join(result['missing']) or 'none'))
    sys.exit(1 if result['missing'] else 0)
