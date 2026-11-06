# -*- mode: python ; coding: utf-8 -*-
"""仅 Web GUI；双 EXE 共用依赖目录，不收集任何预训练权重。"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = os.path.abspath(SPECPATH)
excluded = ['PyQt5','PyQt6','PySide2','tensorflow','paddle','paddleocr','openvino',
            'main_modular_win11','qt_main','customtkinter','onnxruntime.training',
            'IPython','notebook','jupyterlab','pytest']
data = [(os.path.join(root,'Logo.ico'),'.'),(os.path.join(root,'defaults.yaml'),'.'),
        (os.path.join(root,'qt_web/web'),'qt_web/web')]
for package in ['rapidocr','certifi']:
    data += collect_data_files(package)
for package in ['funasr','transformers','tokenizers','sentencepiece','soundfile','sounddevice',
                'kaldi-native-fbank','torch','scipy','numpy','hydra-core','omegaconf','modelscope']:
    try: data += copy_metadata(package)
    except Exception: pass
gui_hidden = ['PySide6.QtWebEngineWidgets','PySide6.QtWebChannel','rapidocr.main',
          'onnxruntime','llama_cpp','pythoncom','pywintypes','win32com.client',
          'edge_tts','edge_tts.communicate','edge_tts.voices','edge_tts.drm','aiohttp',
          'certifi','lameenc','pystray._win32','sounddevice','soundfile']
worker_hidden = ['sounddevice','soundfile','kaldi_native_fbank','sentencepiece','tokenizers','scipy.linalg'] + collect_submodules('funasr')
common = dict(pathex=[root],hookspath=[os.path.join(root,'pyinstaller_hooks')])
gui = Analysis([os.path.join(root,'web_entry.py')],datas=data,hiddenimports=gui_hidden,
    excludes=excluded+['torch','funasr','transformers','scipy','sklearn','numba','llvmlite','qt_web.release_worker_check'],**common)
worker = Analysis([os.path.join(root,'voice_worker_entry.py')],datas=data[:2]+collect_data_files('funasr'),hiddenimports=worker_hidden,
    excludes=excluded+['PySide6','shiboken6','rapidocr','onnxruntime','llama_cpp','pystray','tkinter','qt_web.main','qt_web.release_gui_check'],**common)

def no_weights(toc):
    return [item for item in toc if Path(item[0]).suffix.lower() not in {'.onnx','.pt','.pth','.gguf','.safetensors','.ckpt'}
            and Path(item[0]).suffix.lower() not in {'.pdb','.lib'}
            and not item[0].endswith('.debug.pak')
            and 'onnxruntime_providers_tensorrt.dll' not in item[0].lower()
            and '/nvidia/' not in item[0].replace(chr(92),'/').lower()
            and not item[0].replace(chr(92),'/').startswith('PySide6/qml/')
            and not item[0].replace(chr(92),'/').startswith('rapidocr_onnxruntime/models/')
            and not item[0].replace(chr(92),'/').startswith('rapidocr/models/')]
gui.datas = no_weights(gui.datas)
worker.datas = no_weights(worker.datas)
gui.binaries = no_weights(gui.binaries)
worker.binaries = no_weights(worker.binaries)
gui_exe = EXE(PYZ(gui.pure),gui.scripts,[],exclude_binaries=True,name='OCR_Reader_Web',
              console=False,upx=False,icon=os.path.join(root,'Logo.ico'))
worker_exe = EXE(PYZ(worker.pure),worker.scripts,[],exclude_binaries=True,name='OCR_VoiceWorker',
                 console=True,upx=False,icon=os.path.join(root,'Logo.ico'))
coll = COLLECT(gui_exe,worker_exe,gui.binaries,gui.datas,worker.binaries,worker.datas,
               strip=False,upx=False,name='OCR_Reader_Web')
