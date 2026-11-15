"""守门测试：权重外置 + 相对 EXE 解析 + 启动检测缺模型。

对应要求：
    1. 安装包里不许出现 .onnx / .pt / .gguf 等权重；
    2. 模型放在 EXE 同级的 模型/ 与 models/ 下，用相对路径解析，
       整个目录换盘符后仍然有效；
    3. 启动时自动检测目录里有没有模型，缺了要明确报出来。
"""
import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))


class ExternalModelLayoutTest(unittest.TestCase):
    """① 打包流程不复制权重；② 自检按外置目录判定。"""

    def test_build_web_no_longer_stages_weights(self):
        source = (ROOT / 'build_web.py').read_text(encoding='utf-8')
        self.assertNotIn('stage_voice_models.py', source,
                         '打包不应再把语音模型复制进安装包')
        self.assertNotIn('stage_ocr_runtime.py', source,
                         '打包不应再把 OCR 权重复制进安装包')
        self.assertIn('stage_gpu_runtime.py', source,
                      'CUDA 运行库属于程序本体，仍需随包')

    def test_build_web_rejects_model_weights(self):
        import build_web

        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            (release / 'OCR_Reader_Web.exe').write_bytes(b'MZ')
            (release / '_internal').mkdir()
            build_web.check_weights(release)  # 只有 EXE 时应当通过
            for suffix in ('.onnx', '.pt', '.gguf', '.safetensors'):
                weight = release / '_internal' / ('m' + suffix)
                weight.write_bytes(b'\x00')
                with self.assertRaises(RuntimeError, msg=suffix + ' 未被拦截'):
                    build_web.check_weights(release)
                weight.unlink()

    def test_build_web_allows_weights_in_external_folders(self):
        """外置目录本来就该有 .pt/.onnx，不能因此判定安装包夹带权重。"""
        import build_web

        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            (release / 'OCR_Reader_Web.exe').write_bytes(b'MZ')
            for relative in ('模型/语音/paraformer-large/model.pt',
                             'models/asr/fsmn-vad/model.pt',
                             'models/ocr/det.onnx'):
                target = release / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'\x00')
            build_web.check_weights(release)  # 不应抛异常

            # 但混进 _internal 就是错的
            stray = release / '_internal' / 'weights.onnx'
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.write_bytes(b'\x00')
            with self.assertRaises(RuntimeError):
                build_web.check_weights(release)

    def test_build_web_ignores_runtime_bins(self):
        """PySide6 的 v8 快照、chardet 的编码模型不是权重，不能误拦。"""
        import build_web

        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            (release / '_internal/PySide6/resources').mkdir(parents=True)
            (release / '_internal/chardet/models').mkdir(parents=True)
            (release / '_internal/PySide6/resources/v8_context_snapshot.bin').write_bytes(b'\x00')
            (release / '_internal/chardet/models/models.bin').write_bytes(b'\x00')
            build_web.check_weights(release)  # 不应抛异常

    def test_verify_release_forbids_weights_in_program_dir(self):
        source = (ROOT / 'tools/verify_web_release.py').read_text(encoding='utf-8')
        self.assertIn("'.onnx'", source, '自检必须同时禁止 .onnx')
        self.assertIn("'.pt'", source, '自检必须同时禁止 .pt')
        self.assertNotIn('voice_models_bundled', source,
                         '语义已改为外置，旧键名会误导')

    def test_paths_anchor_on_executable_when_frozen(self):
        import utils.paths as paths

        with tempfile.TemporaryDirectory() as temp:
            fake_exe = Path(temp) / 'OCR_Reader_Web.exe'
            fake_exe.write_bytes(b'MZ')
            # 关键：argv[0] 指向别处（打包后它往往不是 EXE），只有 sys.executable
            # 才能锚定发布目录。改回 argv[0] 时本测试必须失败。
            other = Path(temp) / 'somewhere-else'
            other.mkdir()
            original_argv0 = sys.argv[0]
            frozen = getattr(sys, 'frozen', None)
            executable = sys.executable
            try:
                sys.frozen = True
                sys.executable = str(fake_exe)
                sys.argv[0] = str(other / 'app.py')
                self.assertEqual(paths.app_root(), Path(temp).resolve())
                voice = paths.model_drop_root('voice')
                self.assertEqual(voice, Path(temp).resolve() / '模型' / '语音')
                # 相对路径必须相对 EXE 目录解析，不能落在当前工作目录
                stored = paths.store_path(voice / 'paraformer-large')
                self.assertEqual(stored, '模型/语音/paraformer-large')
                self.assertEqual(paths.resolve_path(stored), voice / 'paraformer-large')
            finally:
                sys.argv[0] = original_argv0
                sys.executable = executable
                if frozen is None:
                    del sys.frozen
                else:
                    sys.frozen = frozen


class ModelLayoutDiscoveryTest(unittest.TestCase):
    """③ 只要模型放到约定目录，扫描就能自动发现，无需手工登记。"""

    def test_scan_finds_model_in_drop_folder(self):
        """放到 模型/语音 下的模型，要么被默认条目命中，要么被扫描补登记。"""
        import yaml
        from qt_web.asr_models import ModelRegistry

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for name in ('paraformer-large', 'sensevoice-small'):
                model = base / '模型/语音' / name
                model.mkdir(parents=True)
                (model / 'config.yaml').write_text(
                    yaml.safe_dump({'model': 'BiCifParaformer'}), encoding='utf-8')
                # 真实权重是几百 MB，扫描按「是否像完整权重」判定，
                # 这里用超过 2 MiB 占位文件模拟，避免把空壳误当模型。
                with (model / 'model.pt').open('wb') as stream:
                    stream.seek(3 * 1024 * 1024 - 1)
                    stream.write(b'\x00')

            registry = ModelRegistry(base=base)
            registry.scan()
            registry.scan()  # 稳定两轮才登记，与生产逻辑一致
            records = registry.records()
            paths = {item['path'] for item in records}
            self.assertIn('模型/语音/paraformer-large', paths,
                          '约定的 paraformer-large 目录应被登记')
            self.assertIn('模型/语音/sensevoice-small', paths,
                          '用户自己放进去的模型应被自动扫描发现')
            for item in records:
                self.assertFalse(Path(item['path']).is_absolute(),
                                 '目录簿必须写相对路径，换盘符才不失效')
                self.assertTrue((base / item['path']).is_dir(),
                                '相对路径必须相对 EXE 目录解析')


class SyncExternalModelsTest(unittest.TestCase):
    """同步脚本的返回值契约：调用方要用 copied_files / copied_bytes / registry。"""

    def test_sync_returns_documented_keys(self):
        import sync_external_models

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'release'
            target.mkdir()
            result = sync_external_models.sync(target)
            for key in ('copied_files', 'copied_bytes', 'registry', 'models'):
                self.assertIn(key, result, f'sync() 返回值缺少 {key}')
            self.assertIsInstance(result['registry'], list)
            # 目录簿必须是相对路径，否则换盘符就失效
            for item in result['registry']:
                self.assertFalse(Path(item['path']).is_absolute(), item)

    def test_sync_skips_when_target_is_project_root(self):
        import sync_external_models

        result = sync_external_models.sync(None)
        self.assertEqual(result['copied_files'], 0)
        self.assertIn('registry', result)


class StartupModelCheckTest(unittest.TestCase):
    """③ 启动检测：缺模型时给出明确的绝对路径提示，并推送 models-missing。"""

    def _fake_api(self, models):
        import types

        import qt_web.api as api

        api._qtimer_calls.clear()
        fake = types.SimpleNamespace()
        fake._push = lambda payload: api._pushed.append(payload)
        fake.do_asr_models = lambda p: {'models': models}
        return api, fake

    def test_check_reports_missing_paths(self):
        api, fake = self._fake_api([])
        payload = api.WebApi._check_model_health(fake)
        self.assertTrue(payload['missing'], '模型不在时应报缺失')
        from utils.paths import app_root
        root = str(app_root())
        self.assertTrue(all(p.startswith(root) for p in payload['missing']),
                        '提示路径必须是本机绝对路径，用户才能直接打开')
        self.assertEqual(payload['type'], 'models-missing')
        self.assertTrue(api._qtimer_calls, '检测结果必须排队推送给前端')

    def test_check_silent_when_models_present(self):
        """本机模型齐全时不得误报。"""
        api, fake = self._fake_api([{'id': 'paraformer_large', 'available': True}])
        payload = api.WebApi._check_model_health(fake)
        self.assertEqual(payload['missing'], [])

    def test_startup_order_scans_then_checks_models(self):
        """真实 __init__：先排目录扫描，再排模型健康检查。"""
        import qt_web.api as api

        recorded = []
        api._qtimer_calls.clear()

        class Timer:
            """伪 QTimer：记录 interval/timeout 连接，不真正启动定时器。"""

            def __init__(self, *_a, **_k):
                self._interval = 0
                self._callbacks = []

            def setInterval(self, value):
                self._interval = value

            def interval(self):
                return self._interval

            def start(self):
                pass

            class _Signal:
                def __init__(self):
                    self._callbacks = []

                def connect(self, callback):
                    self._callbacks.append(callback)

            timeout = _Signal()

        original_later = api._later
        original_timer = api.QTimer
        original_devices = api.WebApi._refresh_input_devices

        def capture(delay_ms, callback):
            recorded.append((delay_ms, getattr(callback, '__name__', '')))

        api.QTimer = Timer
        api._later = capture
        api.WebApi._refresh_input_devices = lambda self: []

        class Window:
            app = None
            config = None
            translation_manager = None
            bridge = None
            router = None
            voice = None

        try:
            api.WebApi.__init__(api.WebApi.__new__(api.WebApi), Window())
        except AttributeError:
            # __init__ 内部还可能碰到真实窗口才有的成员；排队登记发生在这些访问
            # 之前，这里只关心「启动时排了哪些回调」，因此忽略后续异常。
            pass
        finally:
            api._later = original_later
            api.QTimer = original_timer
            api.WebApi._refresh_input_devices = original_devices

        names = [name for _, name in recorded]
        self.assertIn('_scan_model_folders', names, '启动应先扫目录')
        self.assertIn('_check_model_health', names, '启动必须检测模型是否齐全')
        check_delay = dict((name, delay) for delay, name in recorded)['_check_model_health']
        self.assertGreaterEqual(check_delay, 1000, '健康检查应延后，不拖慢启动')

    def test_frontend_handles_models_missing(self):
        app_js = (ROOT / 'qt_web/web/js/app.js').read_text(encoding='utf-8')
        self.assertIn("A.bridge.on('models-missing'", app_js,
                      '前端必须订阅 models-missing')
        self.assertIn('缺少模型文件', app_js, '提示文案必须面向用户')

    def test_frontend_alert_text_survives(self):
        """alert() 必须能展示多行路径（用户要照着放文件）。"""
        app_js = (ROOT / 'qt_web/web/js/app.js').read_text(encoding='utf-8')
        self.assertIn('pre-wrap', app_js, '多行提示需要保留换行')


class OCRModelPathTest(unittest.TestCase):
    """OCR 权重同样外置：优先 EXE 同级 models/ocr，缺失时回退包内自带的副本。"""

    FILES = ('PP-OCRv6_det_small.onnx',
             'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
             'PP-OCRv6_rec_small.onnx')

    def _params(self, base):
        import core.ocr_engine as engine

        class Config:
            def get(self, section, key, default=None):
                return '' if key.endswith('_model_path') else default

        fake = engine.OCREngine.__new__(engine.OCREngine)
        fake.config = Config()
        fake.det_limit_type = 'max'
        fake.det_limit_side_len = 1280
        fake.use_angle_cls = False
        fake.intra_op_num_threads = 4
        fake.device_id = 'cpu'

        original = engine.resolve_path
        engine.resolve_path = lambda value, _b=None: (
            base / value) if not Path(value).is_absolute() else Path(value)
        try:
            return fake._build_params()
        finally:
            engine.resolve_path = original

    def test_prefers_external_models_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'models/ocr').mkdir(parents=True)
            for name in self.FILES:
                (base / 'models/ocr' / name).write_bytes(b'\x00')
            params = self._params(base)
            for name in self.FILES:
                chosen = Path(params['Det.model_path' if name.startswith('PP')
                              else ('Rec.model_path' if 'rec' in name else 'Cls.model_path')])
                self.assertEqual(chosen.parent, base / 'models/ocr',
                                 '应当优先用外置目录里的模型')

    def test_falls_back_to_external_models_folder(self):
        """自定义路径失效时回退到 EXE 同级 models/ocr，而不是 site-packages 的副本。

        发布版不带任何权重（.spec 的 no_weights 会剥掉 rapidocr/models），
        若这里回退到包内副本，开发机测试通过、发布版才崩。
        """
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'models/ocr').mkdir(parents=True)
            for name in self.FILES:
                (base / 'models/ocr' / name).write_bytes(b'\x00')
            params = self._params(base)
            for key in ('Det', 'Cls', 'Rec'):
                chosen = Path(params[key + '.model_path'])
                self.assertEqual(chosen.parent, base / 'models/ocr')

    def test_missing_models_raise_clear_error(self):
        """外置目录为空时必须明确报错，不能静默用别的副本。"""
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError) as ctx:
                self._params(Path(temp))
            self.assertIn('models/ocr', str(ctx.exception))


class AsrModelInterfaceTest(unittest.TestCase):
    """语音模型只靠自动扫描，不再有手工导入/移除接口。

    `do_asr_import_model` / `do_asr_remove_model` 前端从未调用（语音页只有
    模型下拉框），而 `ModelRegistry.scan()` 已经把「把模型放进 模型/语音/ 就
    能用」这件事做完了。如果有人重新加回接口，这里会拦住。
    """

    def test_manual_import_remove_handlers_are_gone(self):
        source = (ROOT / 'qt_web/api.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        handlers = {node.name for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)}
        self.assertNotIn('do_asr_import_model', handlers,
                         '语音模型不做手工导入，靠 ModelRegistry.scan() 自动识别')
        self.assertNotIn('do_asr_remove_model', handlers)
        # 通用选目录/选文件仍在用（TTS 导出），删语音接口时不能连带删掉。
        self.assertIn('do_pick_folder', handlers)

    def test_registry_still_registers_scan_results(self):
        """删掉的只是 remove()，扫描登记用的 add() 必须保留。"""
        source = (ROOT / 'qt_web/asr_models.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'ModelRegistry':
                methods = {child.name for child in node.body
                           if isinstance(child, ast.FunctionDef)}
        self.assertIn('add', methods)
        self.assertIn('scan', methods)
        self.assertNotIn('remove', methods,
                         'remove() 只服务于已删除的「移除模型」接口')

    def test_no_code_calls_removed_registry_methods(self):
        """删掉的方法不能还留有调用方。

        真实踩过的坑：`ModelRegistry.remove()` 删掉后，`qt_web/release_gui_check.py`
        仍在调用它，导致**发布版自检直接失败**（AttributeError），而单元测试
        全绿——因为没人检查这个文件。
        """
        # 用 ast 找真正的 `<x>.remove(...)` 调用，避免被注释/字符串干扰
        suspects = []
        for path in ROOT.rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(('build/', 'dist/', '.cache/')) or '__pycache__' in rel:
                continue
            if rel == 'tests/test_external_models.py':
                continue  # 本测试自身会提到 remove
            text = path.read_text(encoding='utf-8', errors='replace')
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != 'remove':
                    continue
                # 只关心「持有 ModelRegistry 的对象」上的 remove：
                # 变量名形如 vr / registry / self.voice.registry
                base = func.value
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, 'id', '')
                if name in ('registry', 'vr'):
                    suspects.append(f'{rel}:{node.lineno}: {name}.remove(...)')
        self.assertEqual(suspects, [],
                         'ModelRegistry 已无 remove()，这些调用会让打包自检失败：'
                         f'{suspects}')

    def test_release_gui_check_cleans_registry_entries(self):
        """自检脚本用完临时模型目录后必须从登记簿摘掉，且不能调 remove()。"""
        source = (ROOT / 'qt_web/release_gui_check.py').read_text(encoding='utf-8')
        self.assertIn('auto_discovery', source)
        self.assertNotIn('vr.remove(', source,
                         'ModelRegistry 已无 remove()，调用会让发布版自检失败')
        self.assertIn('vr._save(', source,
                      '应通过过滤 records() 后落盘来清理临时条目')

    def test_bridge_no_longer_waits_for_import_dialog(self):
        """bridge 的超时豁免名单不该再挂着已删除的动作名。"""
        source = (ROOT / 'qt_web/web/js/core/bridge.js').read_text(encoding='utf-8')
        self.assertNotIn('asr_import_model', source)
        self.assertIn("action === 'pick_folder'", source)

    def test_no_frontend_unreachable_handlers(self):
        """后端每个 do_* 都要有前端调用方，否则就是死接口。

        2026-11-15 清理掉的是 3 个：`do_hotwords_clear_candidates`、
        `do_set_ocr_device`、`do_save_config`。这条测试防止它们被重新加回来。
        """
        source = (ROOT / 'qt_web/api.py').read_text(encoding='utf-8')
        handlers = {node.name for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.FunctionDef) and node.name.startswith('do_')}

        web = ''.join(p.read_text(encoding='utf-8', errors='replace')
                      for p in (ROOT / 'qt_web/web').rglob('*')
                      if p.suffix in {'.js', '.html'})
        called = set(re.findall(r"\.call\(\s*['\"]([a-z0-9_]+)['\"]", web))

        for name in ('do_hotwords_clear_candidates', 'do_set_ocr_device', 'do_save_config'):
            self.assertNotIn(name, handlers, f'{name} 前端从未调用，是死接口')

        # 有前端调用方的必须都还有 handler，避免前端点了报「未知动作」。
        unimplemented = sorted(a for a in called if 'do_' + a not in handlers)
        self.assertEqual(unimplemented, [], '前端在调不存在的动作')

    def test_hotkey_module_removed(self):
        """热键由 qt_web/main.py 用 keyboard 库实现，旧 utils/hotkey.py 是残留。"""
        self.assertFalse((ROOT / 'utils/hotkey.py').exists(),
                         'utils/hotkey.py 全仓库无人导入，不应存在')
        source = (ROOT / 'qt_web/main.py').read_text(encoding='utf-8')
        self.assertIn('keyboard', source, '热键应继续由 qt_web/main.py 实现')


if __name__ == '__main__':
    unittest.main()
