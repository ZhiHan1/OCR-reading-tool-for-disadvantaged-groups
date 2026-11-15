"""守门测试：打包体积控制（模型外置 + Qt / funasr 瘦身）。

背景：发布包曾达 4.45 GiB，其中模型只占约 0.9 GiB，大头是
CUDA 运行库 1.8 GiB 与整包 PySide6 448 MiB（含 3D/图表/PDF/虚拟键盘）。

这里锁住「不会随 bug 回归而膨胀」的几条规则，纯静态检查 spec，
不需要跑完整打包：
  ① 权重不进包（.onnx/.pt/.gguf 等）
  ② PySide6 只保留界面真正需要的 Qt 模块
  ③ funasr 的评测依赖（sklearn/numba/llvmlite）不进 worker
  ④ Chromium 本地化包只留中英文
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = ROOT / 'OCR_Reader_Web.spec'


def _load_spec_namespace():
    """把 spec 里的 _QT_KEEP 与 no_weights 取出来单独执行（不触发真实打包）。"""
    source = SPEC.read_text(encoding='utf-8')
    tree = ast.parse(source)
    namespace = {'Path': Path}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == '_QT_KEEP':
            exec(compile(ast.Module([node], []), '<spec>', 'exec'), namespace)
        elif isinstance(node, ast.FunctionDef) and node.name == 'no_weights':
            exec(compile(ast.Module([node], []), '<spec>', 'exec'), namespace)
    return namespace


class SpecWeightsTest(unittest.TestCase):
    """① 任何模型权重都不能进包。"""

    def setUp(self):
        self.no_weights = _load_spec_namespace()['no_weights']

    def test_model_weights_stripped(self):
        items = [[f'_internal/{name}', '', ''] for name in
                 ('model.onnx', 'model.pt', 'model.pth', 'x.gguf', 'x.safetensors', 'x.ckpt')]
        self.assertEqual(self.no_weights(items), [], '模型权重必须全部剔除')

    def test_debug_and_pdb_stripped(self):
        items = [['a.pdb', '', ''], ['b.lib', '', ''],
                 ['icudtl.debug.pak', '', ''],
                 ['v8_context_snapshot.debug.bin', '', '']]
        self.assertEqual(self.no_weights(items), [], '调试符号不能进发布包')

    def test_normal_files_kept(self):
        items = [['qt_web/web/index.html', '', ''], ['config.yaml', '', '']]
        self.assertEqual(len(self.no_weights(items)), 2, '普通文件不能被误删')


class QtSlimTest(unittest.TestCase):
    """② PySide6 只保留 QtWebEngine 依赖闭包。"""

    @classmethod
    def setUpClass(cls):
        ns = _load_spec_namespace()
        cls.keep = ns['_QT_KEEP']

    def _filter(self, *names):
        items = [[f'PySide6/{n}', '', ''] for n in names]
        # 每次重新取函数：挂在类上会被绑定成方法，多传一个 self
        return {Path(i[0]).name for i in _load_spec_namespace()['no_weights'](items)}

    def test_webengine_kept(self):
        """界面完全依赖 QWebEngine，缺一个都起不来。"""
        required = ['Qt6WebEngineCore.dll', 'Qt6WebEngineWidgets.dll',
                    'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
                    'Qt6Network.dll', 'Qt6WebChannel.dll']
        kept = self._filter(*required)
        self.assertEqual(kept, set(required), f'这些 DLL 必须保留: {required}')

    def test_opengl_software_fallback_kept(self):
        """opengl32sw 不在导入表里，是 qt6gui 的隐式回退，删了无显卡机器会黑屏。"""
        self.assertIn('opengl32sw.dll', self.keep,
                      'opengl32sw.dll 必须留在保留清单里')
        self.assertIn('opengl32sw.dll', self._filter('opengl32sw.dll'))

    def test_unused_qt_modules_removed(self):
        """3D / 图表 / PDF / 多媒体 / 虚拟键盘 / 测试模块都用不到。"""
        unused = ['Qt6Quick3D.dll', 'Qt6Charts.dll', 'Qt6Pdf.dll',
                  'Qt6Multimedia.dll', 'Qt6VirtualKeyboard.dll', 'Qt6Test.dll',
                  'Qt6DataVisualization.dll', 'Qt6Graphs.dll',
                  'Qt63DRender.dll', 'Qt6Sensors.dll']
        kept = self._filter(*unused)
        self.assertEqual(kept, set(), f'这些模块应被剔除，实际仍在: {kept}')

    def test_every_kept_dll_is_qt(self):
        """保留清单里不该混入非 Qt 的 DLL（防止手滑写错名字）。"""
        for name in self.keep:
            self.assertTrue(name.startswith(('qt6', 'pyside6', 'opengl32sw')),
                            f'保留清单里的 {name} 不像 Qt 模块')

    def test_translations_only_chinese_and_english(self):
        """Chromium 的 .pak 每个语言 0.3~1.6 MiB，216 个合计 53 MiB。"""
        names = ['zh-CN.pak', 'zh-TW.pak', 'en-US.pak', 'en-GB.pak',
                 'ja.pak', 'de.pak', 'ru.pak', 'ar.pak']
        kept = self._filter(*[f'translations/{n}' for n in names])
        self.assertEqual(kept, {'zh-CN.pak', 'zh-TW.pak', 'en-US.pak', 'en-GB.pak'},
                         '只应保留中英文本地化包')

    def test_qt_qm_translations_removed(self):
        """程序没有安装任何 QTranslator，Qt 自身的 .qm 永远不加载。"""
        kept = self._filter('translations/qtbase_zh_CN.qm', 'translations/qt_zh_CN.qm')
        self.assertEqual(kept, set())

    def test_devtools_resources_removed(self):
        """11 MiB 的开发者工具资源，发布版不开 Inspector。"""
        kept = self._filter('resources/qtwebengine_devtools_resources.pak')
        self.assertEqual(kept, set())

    def test_qml_plugins_removed(self):
        kept = self._filter('plugins/qmltooling/qmldbg_native.dll')
        self.assertEqual(kept, set())


class WorkerSlimTest(unittest.TestCase):
    """③ funasr 的评测/分离依赖不进 worker。"""

    def test_asr_only_excludes_declared(self):
        source = SPEC.read_text(encoding='utf-8')
        self.assertIn('_ASR_ONLY_EXCLUDES', source,
                      'worker 必须排除 sklearn/numba/llvmlite（约 127 MiB）')
        ns = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == '_ASR_ONLY_EXCLUDES':
                exec(compile(ast.Module([node], []), '<spec>', 'exec'), ns)
        excludes = set(ns.get('_ASR_ONLY_EXCLUDES', []))
        for name in ('sklearn', 'numba', 'llvmlite'):
            self.assertIn(name, excludes,
                          f'{name} 只被 funasr 的评测路径使用，应排除')

    def test_funasr_itself_not_excluded(self):
        """排除的是 funasr 的「可选依赖」，不能把 funasr 本身排掉。"""
        source = SPEC.read_text(encoding='utf-8')
        ns = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == '_ASR_ONLY_EXCLUDES':
                exec(compile(ast.Module([node], []), '<spec>', 'exec'), ns)
        excludes = set(ns.get('_ASR_ONLY_EXCLUDES', []))
        self.assertNotIn('funasr', excludes, 'funasr 是 ASR 的主体，绝不能排除')
        self.assertNotIn('torch', excludes,
                         'funasr 导入时强制拉起 torch，排掉会导致语音不可用')


class CudaRuntimeTest(unittest.TestCase):
    """④ CUDA 仍随包提供（GPU 是可选项，但有显卡就要能用）。"""

    def test_cufft_not_stripped(self):
        """cufft 被 onnxruntime_providers_cuda 与 torch_cpu 引用，不能删。"""
        no_weights = _load_spec_namespace()['no_weights']
        kept = no_weights([['gpu_runtime/cufft64_11.dll', '', '']])
        self.assertEqual(len(kept), 1, 'cufft 被 CUDA EP 引用，删了 GPU 推理会崩')

    def test_cuda_provider_not_stripped(self):
        no_weights = _load_spec_namespace()['no_weights']
        kept = no_weights([['onnxruntime/capi/onnxruntime_providers_cuda.dll', '', '']])
        self.assertEqual(len(kept), 1, 'CUDA EP 是 GPU 推理的唯一入口')

    def test_tensorrt_provider_stripped(self):
        no_weights = _load_spec_namespace()['no_weights']
        kept = no_weights([['onnxruntime/capi/onnxruntime_providers_tensorrt.dll', '', '']])
        self.assertEqual(kept, [], 'TensorRT EP 与本项目无关')


if __name__ == '__main__':
    unittest.main()
