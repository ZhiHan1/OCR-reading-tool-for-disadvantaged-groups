"""语音模型目录的共用定义。

`stage_voice_models.py`（早期打包路径）与 `sync_external_models.py`（现在的
外置同步路径）都需要同一套东西：识别模型目录表、运行时必需件、FunASR
`config.yaml` 的 `model` 字段到 kind 的映射，以及「读一个模型目录判断它是什么」。

这些曾各写一份，两边的 `detect_kind` 甚至异常捕获范围都不同（一个捕
`ValueError`、一个捕 `yaml.YAMLError`）。这里统一，避免表项增删时漏改一边。

只提供**纯数据与纯函数**，不做任何复制/写盘动作——两个用法差异太大，
留在各自的脚本里。
"""
from pathlib import Path

import yaml

# 语音识别模型：(源目录相对路径, 模型 id, 显示名)。目录不存在时视为可选。
VOICE_MODELS = (
    ('模型/语音/SenseVoiceSmall', 'sensevoice', 'SenseVoice-Small'),
    ('模型/语音/paraformer-large', 'paraformer_large', 'Paraformer-Large'),
)

# 运行时必需件：(源相对路径, 目标相对路径)。缺一不可。
RUNTIME_MODELS = (
    ('models/asr/fsmn-vad', 'models/asr/fsmn-vad'),
    ('models/asr/ct-punc', 'models/asr/ct-punc'),
)

# 一个模型目录要能用的最小文件集
MODEL_FILES = ('config.yaml', 'model.pt')

# FunASR config.yaml 里的 `model` 字段 → 统一 kind
KINDS = {
    'SenseVoiceSmall': 'sensevoice',
    'BiCifParaformer': 'paraformer_large',
    'Paraformer': 'paraformer_large',
}


def detect_kind(directory):
    """读 `<directory>/config.yaml` 判断模型种类，无法判断时返回 None。

    Args:
        directory: 模型目录（含 config.yaml）

    Returns:
        str | None: `sensevoice` / `paraformer_large`，或 None
    """
    try:
        data = yaml.safe_load((Path(directory) / 'config.yaml').read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return KINDS.get(data.get('model'))
