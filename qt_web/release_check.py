"""仅通过 --self-test 显式执行的发布版自检，输出 JSON 报告。"""
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

def save(report, path):
    Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

def imports(names):
    out={}
    for name in names:
        try:
            importlib.import_module(name);out[name]='ok'
        except Exception:
            out[name]=traceback.format_exc()
    return out

def worker_check(path):
    report={'imports':imports(['torch','numpy','scipy.linalg','funasr','sounddevice','soundfile',
                              'kaldi_native_fbank','sentencepiece','tokenizers','transformers'])}
    try:
        from funasr import AutoModel
        from funasr.register import tables
        report['architectures']={k:k in tables.model_classes for k in ['SenseVoiceSmall','BiCifParaformer','FsmnVADStreaming','CTTransformer']}
        import sounddevice as sd
        report['audio_devices']=len(sd.query_devices())
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

def gui_check(path):
    report={'imports':imports(['PySide6.QtWebEngineWidgets','PySide6.QtWebChannel','rapidocr',
        'onnxruntime','llama_cpp','win32com.client','pythoncom','pywintypes','edge_tts','aiohttp','certifi','lameenc','pystray._win32'])}
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from qt_web.main import WebMainWindow, WEB_DIR, resource_path
        from utils.paths import app_root,model_drop_root
        app=QApplication([]);app.setQuitOnLastWindowClosed(False)
        win=WebMainWindow();win.show()
        report['ui_file']=str(Path(WEB_DIR)/'index.html')
        report['icon']=Path(resource_path('Logo.ico')).is_file() and not win.windowIcon().isNull()
        report['folders']={k:model_drop_root(k).is_dir() for k in ['voice','translation']}
        report['config_created']=(app_root()/'config.yaml').is_file()
        from core.tts import get_available_voices
        report['sapi_voices']=len(get_available_voices())
        import lameenc
        encoder=lameenc.Encoder();encoder.set_bit_rate(64);encoder.set_in_sample_rate(16000);encoder.set_channels(1);encoder.set_quality(5)
        report['mp3_encoding']=len(encoder.encode(bytes(3200))+encoder.flush())>0
        win.api._scan_model_folders()
        win.api._scan_model_folders()
        if getattr(sys,'frozen',False):
            import tempfile
            with tempfile.TemporaryDirectory(prefix='scan-check-',dir=model_drop_root('voice')) as vd, tempfile.TemporaryDirectory(prefix='scan-check-',dir=model_drop_root('translation')) as td:
                voice_dir=Path(vd)
                (voice_dir/'config.yaml').write_text('model: SenseVoiceSmall',encoding='utf-8')
                (voice_dir/'model.pt').write_bytes(b'fixture-only-not-a-model')
                gguf=Path(td)/'scan-check.gguf';gguf.write_bytes(b'GGUFfixture-only')
                win.api._scan_model_folders();win.api._scan_model_folders()
                vr=win.voice.registry
                mm=win.api._model_manager()
                voice_found=[m for m in vr.list() if Path(m['path'])==voice_dir]
                translation_found=[m for m in mm.get_all_models() if Path(m['path'])==gguf]
                report['auto_discovery']={'voice':len(voice_found)==1,'translation':len(translation_found)==1}
                for m in voice_found:vr.remove(m['id']);vr.discovery.set_hidden(m['path'],False)
                for m in translation_found:mm.remove_model(m['id'],delete_file=False);mm.discovery.set_hidden(m['path'],False)
        if '--test-models' in sys.argv:
            spec=json.loads(Path(sys.argv[sys.argv.index('--test-models')+1]).read_text(encoding='utf-8'))
            from rapidocr import RapidOCR
            import numpy as np
            params={component+'.model_path':str(Path(spec['ocr'])/filename) for component,filename in [
                ('Det','PP-OCRv6_det_small.onnx'),('Cls','ch_ppocr_mobile_v2.0_cls_mobile.onnx'),('Rec','PP-OCRv6_rec_small.onnx')]}
            ocr=RapidOCR(params=params);ocr(np.full((80,320,3),255,dtype=np.uint8))
            from llama_cpp import Llama
            llm=Llama(model_path=spec['gguf'],n_ctx=128,n_threads=2,n_gpu_layers=0,verbose=False)
            llm('Hello',max_tokens=1);llm.close()
            report['external_model_tests']={'ocr':'loaded_and_inferred','gguf':'loaded_and_inferred'}
        def done(ok):
            report['ok']=bool(ok) and all(v=='ok' for v in report['imports'].values())
            save(report,path)
            win.api._model_scan_timer.stop();win.cursor_input.timer.stop();win.voice.close()
            win.pause_hotkeys(True);win.tray.stop()
            os._exit(0 if report['ok'] else 1)
        def receive(raw):
            try:
                report['pages']=json.loads(raw)
                report['voice_models']=win.voice.registry.list()
                report['translation_models']=win.api._models_state()['local']
                done(report['icon'] and all(report['folders'].values()) and all(report['pages'].values()) and report['sapi_voices']>0 and report['mp3_encoding'] and all(report.get('auto_discovery',{}).values()))
            except Exception:
                report['error']=traceback.format_exc();done(False)
        def loaded(ok):
            if not ok: report['error']='HTML failed to load';done(False);return
            js="(()=>{const A=window.APP;const out={};for(const name of ['home','translate','tts','voice_input','settings','about']){A.router.go(name,{animate:false});out[name]=!!document.querySelector('#content')&&document.querySelector('#content').textContent.trim().length>0;}A.router.go('voice_input',{animate:false});A.router.refresh();out.radios=document.querySelectorAll('[name=asr-input-mode]').length===3;return JSON.stringify(out);})()"
            QTimer.singleShot(2500,lambda:win.view.page().runJavaScript(js,0,receive))
        win.view.loadFinished.connect(loaded)
        QTimer.singleShot(60000,lambda:done(False))
        app.exec()
    except Exception:
        report['error']=traceback.format_exc();report['ok']=False;save(report,path);sys.exit(1)
