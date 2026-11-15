"""把权重同步到「外置模型目录」，不进入安装包。

打包只产出 EXE：权重一律放在 EXE 同级的 `模型/`（语音）与 `models/`
（OCR / VAD / 标点 / 目录簿）。运行时由 `utils.paths.app_root()` 以
`sys.executable` 为锚解析相对路径，因此整个目录可以整体拷到任意盘符。

用法：
    py -3.14 tools/sync_external_models.py <目标目录>     # 例如 dist/Web-xxx/OCR_Reader_Web
    py -3.14 tools/sync_external_models.py                # 默认同步回项目根目录（开发态）

同步规则：
    - 只同步**实际存在**的源目录，缺的打印 STAGE_SKIP_MISSING 而不是失败——
      外置模型允许用户事后自己放。
    - 已存在且大小一致的文件跳过；不删除目标目录里的其它内容（用户自己放的模型保留）。
    - 目标与源相同（开发态）时直接跳过，避免自己拷自己。
    - 同步完成后重写 `models/asr/registry.json`，路径一律写相对路径。
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (源相对路径, 目标相对路径)。前两个是识别模型，后两个是运行时必需件。
VOICE_DIRS = (
    ('模型/语音/SenseVoiceSmall', '模型/语音/SenseVoiceSmall'),
    ('模型/语音/paraformer-large', '模型/语音/paraformer-large'),
)
RUNTIME_DIRS = (
    ('models/asr/fsmn-vad', 'models/asr/fsmn-vad'),
    ('models/asr/ct-punc', 'models/asr/ct-punc'),
)
OCR_DIRS = (
    ('models/ocr', 'models/ocr'),
)

MODEL_FILES = ('config.yaml', 'model.pt')
# FunASR config.yaml 的 model 字段 → 目录簿里的 kind
KINDS = {
    'SenseVoiceSmall': 'sensevoice',
    'BiCifParaformer': 'paraformer_large',
    'Paraformer': 'paraformer_large',
}
IGNORE = shutil.ignore_patterns('.cache', '__pycache__', '*.bak', '*.tmp')


def detect_kind(directory):
    try:
        import yaml
        data = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return KINDS.get(data.get('model')) if isinstance(data, dict) else None


def copy_tree(source, target):
    """只补缺失/大小不一致的文件；返回 (新增文件数, 字节数)。"""
    added = size = 0
    for item in source.rglob('*'):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        if any(part in {'.cache', '__pycache__'} for part in relative.parts):
            continue
        destination = target / relative
        if destination.is_file() and destination.stat().st_size == item.stat().st_size:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        added += 1
        size += item.stat().st_size
    return added, size


def sync(target=None):
    destination_root = Path(target).resolve() if target else ROOT
    if destination_root == ROOT:
        print('SYNC_TARGET_IS_PROJECT_ROOT (无需同步)')
        return {'copied_files': 0, 'copied_bytes': 0, 'models': [], 'registry': []}

    registry = []
    copied = size = 0
    for source_rel, target_rel in VOICE_DIRS:
        source = ROOT / source_rel
        if not source.is_dir():
            print(f'SYNC_SKIP_MISSING {source_rel}')
            continue
        missing = [f for f in MODEL_FILES if not (source / f).is_file()]
        if missing:
            print(f'SYNC_SKIP_INCOMPLETE {source_rel} 缺 {", ".join(missing)}')
            continue
        kind = detect_kind(source)
        if kind is None:
            print(f'SYNC_SKIP_UNKNOWN_ARCH {source_rel}')
            continue
        files, octets = copy_tree(source, destination_root / target_rel)
        copied += files
        size += octets
        registry.append(dict(
            id=target_rel.rsplit('/', 1)[-1].replace('-', '_'),
            name=source.name, kind=kind, path=target_rel,
        ))

    for source_rel, target_rel in RUNTIME_DIRS + OCR_DIRS:
        source = ROOT / source_rel
        if not source.is_dir():
            print(f'SYNC_SKIP_MISSING {source_rel}')
            continue
        files, octets = copy_tree(source, destination_root / target_rel)
        copied += files
        size += octets

    # 目录簿只写相对路径，整目录搬走后仍然有效。
    if registry:
        registry_file = destination_root / 'models/asr/registry.json'
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination_root / '模型/翻译').mkdir(parents=True, exist_ok=True)

    manifest = {'models': registry, 'registry': registry,
                'copied_files': copied, 'copied_bytes': size}
    (destination_root / 'external-models-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    result = sync(sys.argv[1] if len(sys.argv) > 1 else None)
    print('EXTERNAL_MODELS_SYNCED', result['copied_files'], result['copied_bytes'],
          'registry=' + ','.join(item['id'] for item in result['registry']))
