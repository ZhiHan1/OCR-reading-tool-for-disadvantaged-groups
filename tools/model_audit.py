"""检查模型目录：哪些必需、哪些多余、哪些是重复副本。只报告，不删除。

用法：
    py -3.14 tools/model_audit.py             # 报告项目根目录
    py -3.14 tools/model_audit.py <目录>      # 报告发布目录

判定规则：
    必需   voice：模型/语音/<含 config.yaml+model.pt>、models/asr/fsmn-vad、models/asr/ct-punc
           ocr  ：models/ocr 下三个 onnx（缺失时会回退 rapidocr 包内副本）
          翻译  ：模型/翻译/*.gguf（用户自备，不算缺失）
    多余   .cache / __pycache__ / *.bak / *.tmp / 空目录 / 扫描隐藏簿
    重复   models/ocr 与 rapidocr 包内同名同大小的 onnx
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
JUNK_DIRS = {'.cache', '__pycache__'}
JUNK_SUFFIXES = {'.bak', '.tmp', '.metadata'}
# 运行时状态文件：由程序自己生成，不算模型权重，也不必分发
STATE_FILES = {'models/asr/registry.json', 'models/asr/scan_hidden.json',
               'models/asr/hotword_candidates.json',
               'models/local/models_config.json',
               'models/local/scan_hidden.json'}


def _size(path):
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    return path.stat().st_size if path.exists() else 0


def audit(base):
    base = Path(base).resolve()
    report = {'root': str(base), 'required': [], 'missing': [],
              'junk': [], 'empty_dirs': [], 'duplicates': [], 'state_files': []}

    # --- 必需：语音识别模型 ---
    for model in sorted((base / '模型/语音').glob('*/')) if (base / '模型/语音').is_dir() else []:
        if (model / 'config.yaml').is_file() and (model / 'model.pt').is_file():
            report['required'].append({'path': model.relative_to(base).as_posix(),
                                       'size': _size(model)})
    if not report['required']:
        report['missing'].append('模型/语音/<识别模型>')
    for relative in ('models/asr/fsmn-vad', 'models/asr/ct-punc'):
        directory = base / relative
        if (directory / 'config.yaml').is_file() and (directory / 'model.pt').is_file():
            report['required'].append({'path': relative, 'size': _size(directory)})
        else:
            report['missing'].append(relative)

    # --- 必需：OCR ---
    for name in REQUIRED_OCR:
        path = base / 'models/ocr' / name
        if path.is_file():
            report['required'].append({'path': 'models/ocr/' + name, 'size': path.stat().st_size})
        else:
            report['missing'].append('models/ocr/' + name)

    # --- 翻译模型（用户自备） ---
    for gguf in sorted((base / '模型/翻译').glob('*.gguf')):
        report['required'].append({'path': gguf.relative_to(base).as_posix(),
                                   'size': gguf.stat().st_size})

    # --- 多余文件 ---
    for path in base.rglob('*'):
        relative = path.relative_to(base)
        if relative.parts[:1] not in {('模型',), ('models',)}:
            continue
        if any(part in JUNK_DIRS for part in relative.parts):
            report['junk'].append({'path': relative.as_posix(), 'size': _size(path)})
        elif path.is_file() and path.suffix.lower() in JUNK_SUFFIXES:
            report['junk'].append({'path': relative.as_posix(), 'size': _size(path)})
        elif path.is_dir() and not any(path.iterdir()):
            report['empty_dirs'].append(relative.as_posix())
        elif path.is_file() and relative.as_posix() in STATE_FILES:
            report['state_files'].append(relative.as_posix())

    # --- 重复副本：models/ocr vs rapidocr 包 ---
    try:
        import rapidocr
        bundled = Path(rapidocr.__file__).resolve().parent / 'models'
        for path in (base / 'models/ocr').glob('*.onnx'):
            twin = bundled / path.name
            if twin.is_file() and twin.stat().st_size == path.stat().st_size:
                report['duplicates'].append({
                    'path': 'models/ocr/' + path.name,
                    'same_as': str(twin),
                    'note': '缺失时回退包内副本，外置这份可删（删前确认包内存在）',
                })
    except ImportError:
        pass

    report['totals'] = {
        'required_bytes': sum(item['size'] for item in report['required']),
        'junk_bytes': sum(item['size'] for item in report['junk']),
        'duplicate_bytes': sum((base / item['path']).stat().st_size
                               for item in report['duplicates']),
    }
    return report


def main(argv):
    base = Path(argv[1]).resolve() if len(argv) > 1 else ROOT
    report = audit(base)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['missing']:
        print('\n缺少必需模型：\n  ' + '\n  '.join(report['missing']))
    if report['junk']:
        print('\n可清理：\n  ' + '\n  '.join(item['path'] for item in report['junk']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
