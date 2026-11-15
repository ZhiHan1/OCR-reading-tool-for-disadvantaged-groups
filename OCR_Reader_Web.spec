# -*- mode: python ; coding: utf-8 -*-
"""仅 Web GUI；双 EXE 共用依赖目录，不收集任何预训练权重。"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = os.path.abspath(SPECPATH)
excluded = ['PyQt5','PyQt6','PySide2','tensorflow','paddle','paddleocr','openvino',
            'main_modular_win11','qt_main','onnxruntime.training',
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
          'certifi','lameenc','pystray._win32','sounddevice','soundfile',
          # 翻译后端是**动态导入**的：core/translation_service.py 用
          # importlib.import_module("core.xxx_translation_model") 按名字加载，
          # 字符串不在静态依赖图里，PyInstaller 一个都收不到。
          # 漏了它们的后果（2026-11-15 实际发生）：日志刷
          #   [翻译服务] GGUF模型不可用: No module named 'core.gguf_translation_model'
          # 四个后端全部注册失败，翻译功能整体不可用，而界面看起来一切正常。
          # 必须显式列出；新增翻译后端时同步加到这里。
          'core.gguf_translation_model','core.opus_translation_model',
          'core.google_translation_model','core.custom_openai_translation_model',
          'core.translation_service']
worker_hidden = ['sounddevice','soundfile','kaldi_native_fbank','sentencepiece','tokenizers','scipy.linalg'] + collect_submodules('funasr')
# funasr 只在评测/语音分离里用到 sklearn、numba、llvmlite：
#   metrics/compute_eer.py       -> sklearn.metrics.roc_curve
#   models/campplus/cluster_backend.py -> sklearn
#   models/sense_voice/whisper_lib/timing.py -> numba
# 本项目只做 ASR 推理，这三条路径都不会走到（已实测：屏蔽后
# AutoModel 仍能加载 paraformer-large 并正常推理）。
# 排除后省下 sklearn 12 MiB + llvmlite 115 MiB + numba，约 130 MiB。
_ASR_ONLY_EXCLUDES = ['sklearn','numba','llvmlite','scipy.spatial','scipy.cluster']
common = dict(pathex=[root],hookspath=[os.path.join(root,'pyinstaller_hooks')])
gui = Analysis([os.path.join(root,'web_entry.py')],datas=data,hiddenimports=gui_hidden,
    excludes=excluded+['torch','funasr','transformers','scipy','sklearn','numba','llvmlite','qt_web.release_worker_check'],**common)
worker = Analysis([os.path.join(root,'voice_worker_entry.py')],datas=data[:2]+collect_data_files('funasr'),hiddenimports=worker_hidden,
    excludes=excluded+_ASR_ONLY_EXCLUDES+['PySide6','shiboken6','rapidocr','onnxruntime','llama_cpp','pystray','tkinter','qt_web.main','qt_web.release_gui_check'],**common)

# PyInstaller 的 PySide6 hook 会把整个 Qt 安装目录收进来：3D、图表、PDF、
# 虚拟键盘、Quick 各种控件样式……界面只用 QtWebEngineWidgets + Widgets。
# 这里按「从 QWebEngine 入口出发的 DLL 依赖闭包」保留必需项，其余剔除。
#
# 保留清单的推导方式：读每个 DLL 的 PE 导入表，从 qt6webenginecore /
# qt6webenginewidgets / qt6core / qt6gui / qt6widgets 出发做可达闭包，
# 不可达的 114 个 DLL 共 79 MiB（3D/Charts/Pdf/Multimedia 等）全部可去。
# opengl32sw.dll 是 qt6gui 的隐式软件 OpenGL 回退，不在导入表里，必须留着。
_QT_KEEP = {
    'qt6webenginecore.dll','qt6webenginewidgets.dll','qt6webenginequick.dll',
    'qt6core.dll','qt6gui.dll','qt6widgets.dll','qt6webchannel.dll','qt6network.dll',
    'qt6qml.dll','qt6qmlmodels.dll','qt6qmlmeta.dll','qt6qmlworkerscript.dll',
    'qt6quick.dll','qt6quickwidgets.dll','qt6opengl.dll','qt6openglwidgets.dll',
    'qt6positioning.dll','qt6printsupport.dll','qt6svg.dll','qt6concurrent.dll',
    'pyside6.abi3.dll','opengl32sw.dll',
}
def no_weights(toc):
    out = []
    for item in toc:
        path = item[0].replace(chr(92), '/')
        lower = path.lower()
        suffix = Path(path).suffix.lower()
        if suffix in {'.onnx','.pt','.pth','.gguf','.safetensors','.ckpt','.pdb','.lib'}:
            continue
        if path.endswith('.debug.pak') or path.endswith('v8_context_snapshot.debug.bin'):
            continue
        if 'onnxruntime_providers_tensorrt.dll' in lower or '/nvidia/' in lower:
            continue
        if path.startswith(('PySide6/qml/','rapidocr_onnxruntime/models/','rapidocr/models/')):
            continue
        # ---- Qt 瘦身（均为 GUI 侧，worker 侧本来就没有 PySide6）----
        if path.startswith('PySide6/'):
            name = path.rsplit('/', 1)[-1].lower()
            if name.startswith('qt6') and name.endswith('.dll') and name not in _QT_KEEP:
                continue
            # Chromium 本地化包：只留中英文（界面语言固定中文，其余语言永不加载）
            if name.endswith('.pak') and path.startswith('PySide6/translations/'):
                if not name.startswith(('zh', 'en-')):
                    continue
            # Qt 自身的 .qm 翻译：程序未安装任何 QTranslator，用不到
            if suffix == '.qm':
                continue
            # 开发者工具（F12）资源，发布版不提供 Inspector
            if name == 'qtwebengine_devtools_resources.pak':
                continue
            # QML 工具插件：界面不用 QML
            if path.startswith('PySide6/plugins/qmltooling/'):
                continue
        out.append(item)
    return out
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
