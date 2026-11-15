"""仅通过 --self-test 显式执行的发布版自检，输出 JSON 报告。"""
import json
from pathlib import Path
import sys
import traceback

from qt_web.release_check_common import imports, save


def worker_check(path):
    report={'imports':imports(['torch','numpy','scipy.linalg','funasr','sounddevice','soundfile',
                              'kaldi_native_fbank','sentencepiece','tokenizers','transformers'])}
    try:
        from funasr import AutoModel
        from funasr.register import tables
        report['architectures']={k:k in tables.model_classes for k in ['SenseVoiceSmall','BiCifParaformer','FsmnVADStreaming','CTTransformer']}
        import sounddevice as sd
        report['audio_devices']=len(sd.query_devices())
        from core.audio_capture import input_devices
        report['input_devices']=input_devices()
        if '--test-models' in sys.argv:
            import numpy as np
            from core.asr_service import ASRService
            from qt_web.asr_models import configure_service
            spec=json.loads(Path(sys.argv[sys.argv.index('--test-models')+1]).read_text(encoding='utf-8'))
            service=ASRService()
            service.config.set('asr','vad_path',value=spec['vad'])
            service.config.set('asr','punc_path',value=spec['punc'])
            outcomes={}
            for item in spec['voice']:
                configure_service(service,item)
                model=service._load_local_model()
                if model is None: raise RuntimeError(service.load_error())
                model.generate(input=np.zeros(16000,dtype=np.float32),disable_pbar=True)
                outcomes[item['kind']]='loaded_and_inferred'
            vad=AutoModel(model=spec['vad'],device='cpu',disable_update=True)
            vad.generate(input=np.zeros(16000,dtype=np.float32),disable_pbar=True)
            outcomes['vad']='loaded_and_inferred'
            report['external_model_tests']=outcomes
    except Exception:report['error']=traceback.format_exc()
    report['ok']=all(v=='ok' for v in report['imports'].values()) and all(report.get('architectures',{}).values()) and 'error' not in report
    save(report,path)
    sys.exit(0 if report['ok'] else 1)
