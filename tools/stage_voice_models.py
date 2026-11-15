"""把随包发布的 FunASR 语音模型复制到 Web 发布目录。

只复制**实际存在**的语音模型：识别模型（SenseVoice / Paraformer）按目录自适应，
VAD 与标点属于运行时必需件，缺任何一个都直接失败。
翻译 GGUF 始终由用户放在 <发布目录>/模型/翻译 中，不进入安装包。
"""
import json
import shutil
import sys
from pathlib import Path

from model_dirs import KINDS, MODEL_FILES, RUNTIME_MODELS, VOICE_MODELS, detect_kind

ROOT = Path(__file__).resolve().parents[1]


def copy_model(source: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        copy_function=shutil.copy2,
        ignore=shutil.ignore_patterns('.cache', '__pycache__'),
    )


def stage(output):
    out = Path(output)
    manifest = {'models': [], 'registry': [], 'total_bytes': 0}

    def collect(target_rel):
        files = [p for p in (out / target_rel).rglob('*') if p.is_file()]
        if any(p.suffix.lower() in {'.gguf', '.safetensors', '.ckpt'} for p in files):
            raise RuntimeError(f'语音模型目录里混入了翻译/LLM 权重：{target_rel}')
        size = sum(p.stat().st_size for p in files)
        manifest['models'].append({'path': target_rel, 'files': len(files), 'size': size})
        manifest['total_bytes'] += size
        return files

    registry = []
    for source_rel, model_id, name in VOICE_MODELS:
        source = ROOT / source_rel
        if not source.is_dir():
            print(f'STAGE_SKIP_MISSING {source_rel}')
            continue
        missing = [f for f in MODEL_FILES if not (source / f).is_file()]
        if missing:
            raise FileNotFoundError(f'语音模型不完整：{source_rel} 缺少 {", ".join(missing)}')
        kind = detect_kind(source)
        if kind is None:
            raise ValueError(f'无法识别语音模型架构（config.yaml 的 model 字段）：{source_rel}')
        copy_model(source, out / source_rel)
        files = collect(source_rel)
        if not any(p.name == 'model.pt' for p in files):
            raise FileNotFoundError(f'复制后缺少 model.pt：{source_rel}')
        registry.append({'id': model_id, 'name': name, 'kind': kind, 'path': source_rel})
    if not registry:
        raise FileNotFoundError('发布目录至少需要一个可用的语音识别模型（模型/语音/ 下为空）')

    for source_rel, target_rel in RUNTIME_MODELS:
        source = ROOT / source_rel
        if not source.is_dir():
            raise FileNotFoundError(f'缺少语音运行时模型目录：{source_rel}')
        missing = [f for f in MODEL_FILES if not (source / f).is_file()]
        if missing:
            raise FileNotFoundError(f'语音运行时模型不完整：{source_rel} 缺少 {", ".join(missing)}')
        copy_model(source, out / target_rel)
        collect(target_rel)

    # 发布版自带的识别模型，首次启动即可在界面中看到；路径保持相对，
    # 复制整个发布目录到别的机器后仍然有效。
    registry_file = out / 'models/asr/registry.json'
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest['registry'] = [item['id'] for item in registry]

    # 翻译模型仍然从外置文件夹读取，首次启动时由 bootstrap 创建。
    (out / '模型/翻译').mkdir(parents=True, exist_ok=True)

    (out / 'voice-models-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return manifest


if __name__ == '__main__':
    result = stage(sys.argv[1])
    print('VOICE_MODELS_READY', len(result['models']), result['total_bytes'],
          'registry=' + ','.join(result['registry']))
