"""语音模型路径目录簿：只写 JSON 元数据，绝不复制/移动/删除模型。"""
import json
import os
from pathlib import Path
import uuid

import yaml
from utils.paths import app_root, resolve_path, store_path, model_drop_root
from utils.model_discovery import ModelDiscovery, path_key

ROOT = app_root()
REGISTRY = ROOT / 'models' / 'asr' / 'registry.json'
KINDS = {'SenseVoiceSmall': 'sensevoice', 'BiCifParaformer': 'paraformer_large', 'Paraformer': 'paraformer_large'}
DEFAULTS = [
    dict(id='paraformer_large', name='Paraformer-Large', kind='paraformer_large', path='模型/语音/paraformer-large'),
]


def inspect_model(path, base=None):
    directory = resolve_path(path, base)
    if not directory.exists():
        raise ValueError(f'模型路径不存在，请重新导入：{directory}')
    if not directory.is_dir():
        raise ValueError('请选择完整模型文件夹，而不是单个权重文件')
    config = directory / 'config.yaml'
    if not config.is_file() or not (directory / 'model.pt').is_file():
        raise ValueError('缺少 config.yaml 或 model.pt，请选择完整的 FunASR 模型目录')
    data = yaml.safe_load(config.read_text(encoding='utf-8'))
    architecture = data.get('model') if isinstance(data, dict) else None
    if architecture not in KINDS:
        raise ValueError('当前支持 SenseVoice-Small、Paraformer 系列 FunASR 本地模型，不支持 GGUF/ONNX 单文件')
    return directory, KINDS[architecture]


class ModelRegistry:
    def __init__(self, file=None, defaults=None, base=None):
        self.base = Path(base or app_root()).resolve()
        self.file = Path(file) if file is not None else self.base / 'models/asr/registry.json'
        self.defaults = DEFAULTS if defaults is None else defaults
        self.discovery = ModelDiscovery(model_drop_root('voice',self.base),self.file.with_name('scan_hidden.json'),self.base)

    def scan(self):
        entries=self.records()
        known={path_key(m['path'],self.base) for m in entries}
        added=[]
        for path in self.discovery.candidates('voice'):
            if path_key(path,self.base) in known:continue
            try:directory,kind=inspect_model(path,self.base)
            except (ValueError,OSError,yaml.YAMLError):continue
            item=dict(id='scan_'+self.discovery.unique_id(directory),name=directory.name,kind=kind,path=str(directory))
            entries.append(item);added.append(item['id']);known.add(path_key(directory,self.base))
        if added:self._save(entries)
        return added

    def _save(self, items):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix('.tmp')
        portable = [dict(item, path=store_path(item['path'], self.base)) for item in items]
        temp.write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.file)

    def records(self):
        if not self.file.exists():
            entries = [dict(x) for x in self.defaults if resolve_path(x['path'], self.base).is_dir()]
            self._save(entries)
            return entries
        data = json.loads(self.file.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError('模型目录簿格式不正确')
        return data

    def list(self):
        return [dict(item, path=str(resolve_path(item['path'], self.base)),
                     available=(resolve_path(item['path'], self.base) / 'model.pt').is_file()
                     and (resolve_path(item['path'], self.base) / 'config.yaml').is_file()) for item in self.records()]

    def get(self, model_id):
        item = next((m for m in self.records() if m['id'] == model_id), None)
        if item is None:
            raise ValueError('请先导入并选择本地语音模型')
        directory, _ = inspect_model(item['path'], self.base)
        return dict(item, path=str(directory))

    def add(self, path, name=''):
        directory, kind = inspect_model(path, self.base)
        self.discovery.set_hidden(directory,False)
        entries = self.records()
        for item in entries:
            if os.path.normcase(str(resolve_path(item['path'], self.base))) == os.path.normcase(str(directory)):
                return dict(item, path=str(directory))
        item = dict(id='local_' + uuid.uuid4().hex[:12], name=name.strip() or directory.name,
                    kind=kind, path=str(directory))
        entries.append(item)
        self._save(entries)
        return item

    def remove(self, model_id):
        entries = self.records()
        for item in entries:
            if item['id']==model_id:self.discovery.set_hidden(item['path'],True)
        self._save([m for m in entries if m['id'] != model_id])


def configure_service(service, descriptor):
    """将已验证的路径接入原 ASRService，禁止缺失路径回退在线下载。"""
    from core import asr_service as module
    directory, kind = inspect_model(descriptor['path'])
    key = descriptor['id']
    module.LOCAL_MODELS[key] = str(directory)
    module.LOCAL_MODEL_DIRS[key] = str(directory)
    module.LOCAL_MODEL_LABELS[key] = descriptor['name']
    module.LOCAL_MODEL_LOAD_ARGS[key] = {'vad_model': 'fsmn-vad'} if kind == 'sensevoice' else {'punc_model': 'ct-punc'}
    if service._model_name != key:
        service.switch_model(key)
    # switch_model 会清空 _model_kind，所以放在它之后设置。
    service._model_kind = kind
    # 内存快照；工作进程不写 config.yaml。
    service.config.set('asr', 'backend', value='local')
    service.config.set('asr', 'local_model', value=key)
