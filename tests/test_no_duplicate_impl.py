"""重复实现守门测试。

守的是「同一份逻辑被写了多遍」这个历史问题。2026-11-15 的全仓库重复审查
（809 个函数/方法）发现控制器层有几处逐字复制，改一处很容易漏掉另一处。
这里用 AST 静态检查把结论固化下来，防止以后又被写回去。

覆盖项：

1. ``ScanModeController`` 必须继承 ``BaseController``，且**不再自己实现**
   ``_update_status`` / ``_show_result`` / ``_report_error``——这三个方法
   曾与 ``base_controller.py`` 逐字相同（AST 完全一致）。
2. 那两个方法的返回约定不能漂移：``_show_result`` 必须把回调的返回值
   转成 bool 并返回，后端依赖它决定是否朗读原文。
3. ``release_gui_check`` 与 ``release_worker_check`` 共用的 ``save`` / ``imports``
   必须仍然存在且行为一致（两者都是打包自检入口，改坏只有打包时才暴露）。

运行：py -3.14 -m unittest tests.test_no_duplicate_impl -v
"""
import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _class_node(path, name):
    """取指定文件里的类定义节点，找不到返回 None。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _method_names(class_node):
    return {
        n.name
        for n in class_node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class TestScanModeInheritsBase(unittest.TestCase):
    """扫描模式基类不能重新实现一次性朗读基类的回调。"""

    def setUp(self):
        self.base = _class_node(ROOT / "controllers" / "base_controller.py", "BaseController")
        self.scan = _class_node(
            ROOT / "controllers" / "scan_mode_controller.py", "ScanModeController"
        )
        self.assertIsNotNone(self.base, "找不到 BaseController")
        self.assertIsNotNone(self.scan, "找不到 ScanModeController")

    def test_scan_mode_inherits_base_controller(self):
        bases = {ast.unparse(b) for b in self.scan.bases}
        self.assertIn(
            "BaseController",
            bases,
            "ScanModeController 必须继承 BaseController，否则三个回调会被重新实现一遍",
        )

    def test_shared_callbacks_not_redefined(self):
        """这 3 个方法只能来自基类，子类里出现即为重复实现。"""
        base_methods = _method_names(self.base)
        scan_methods = _method_names(self.scan)
        for name in ("_update_status", "_show_result", "_report_error"):
            self.assertIn(name, base_methods, f"BaseController 缺少 {name}")
            self.assertNotIn(
                name,
                scan_methods,
                f"ScanModeController 又实现了 {name}，应直接继承 BaseController",
            )

    def test_scan_mode_keeps_own_callback(self):
        """`_notify_state_changed` 是扫描模式独有的，不能被顺手删掉。"""
        self.assertIn("_notify_state_changed", _method_names(self.scan))


class TestSharedCallbackBehaviour(unittest.TestCase):
    """三个共享回调的行为契约（合并前后必须一致）。"""

    def _make(self, mode="func1", label="功能1"):
        from controllers.base_controller import BaseController

        class _C(BaseController):
            MODE = mode
            LABEL = label

        return _C(ocr_service=None, tts_service=None, screenshot_service=None)

    def test_show_result_returns_bool_of_callback(self):
        c = self._make()
        c.on_result = lambda mode, text, status, display: "truthy"
        self.assertIs(c._show_result("x"), True)

    def test_show_result_without_callback_is_false(self):
        self.assertIs(self._make()._show_result("x"), False)

    def test_show_result_passes_mode_and_display(self):
        c = self._make(mode="game", label="游戏")
        seen = {}

        def cb(mode, text, status, display):
            seen.update(mode=mode, text=text, status=status, display=display)
            return False

        c.on_result = cb
        c._show_result("内容", status=True, display="[游戏] 内容")
        self.assertEqual(seen["mode"], "game")
        self.assertEqual(seen["text"], "内容")
        self.assertIs(seen["status"], True)
        self.assertEqual(seen["display"], "[游戏] 内容")

    def test_report_error_shows_and_sets_status(self):
        c = self._make(label="功能1")
        seen = []
        c.on_result = lambda mode, text, status, display: seen.append((text, status, display))
        c.on_status_update = lambda s: seen.append(("status", s))

        c._report_error(RuntimeError("炸了"))

        self.assertEqual(seen[0][0], "炸了")
        self.assertIs(seen[0][1], True)
        self.assertEqual(seen[0][2], "[功能1错误] 炸了")
        self.assertEqual(seen[1], ("status", "错误"))

    def test_scan_mode_gets_same_behaviour(self):
        """ScanModeController 实例上这三个方法同样可用（来自继承）。"""
        from controllers.scan_mode_controller import ScanModeController

        class _S(ScanModeController):
            MODE = "func3"
            LABEL = "功能3"

        s = _S(ocr_service=None, tts_service=None, screenshot_service=None)
        seen = {}
        s.on_result = lambda mode, text, status, display: seen.update(
            mode=mode, text=text, display=display
        )
        self.assertIs(s._show_result("内容", display="[功能3] 内容"), False)
        self.assertEqual(seen["mode"], "func3")
        self.assertEqual(seen["display"], "[功能3] 内容")

        s.on_status_update = lambda v: seen.update(status=v)
        s._report_error(ValueError("坏"))
        self.assertEqual(seen["display"], "[功能3错误] 坏")
        self.assertEqual(seen["status"], "错误")


class TestReleaseCheckHelpers(unittest.TestCase):
    """两个自检入口的 save()/imports() 必须都存在且行为一致。"""

    def _load(self, module_name):
        import importlib

        return importlib.import_module(module_name)

    def _save_and_read(self, module):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            module.save({"中文": "值", "n": 1}, str(path))
            raw = path.read_text(encoding="utf-8")
            self.assertIn("中文", raw, "save 必须不转义中文")
            self.assertEqual(json.loads(raw), {"中文": "值", "n": 1})
            return raw

    def test_both_helpers_exist(self):
        for name in ("qt_web.release_gui_check", "qt_web.release_worker_check"):
            module = self._load(name)
            self.assertTrue(hasattr(module, "save"), f"{name} 缺少 save()")
            self.assertTrue(hasattr(module, "imports"), f"{name} 缺少 imports()")

    def test_save_behaviour_identical(self):
        gui = self._load("qt_web.release_gui_check")
        worker = self._load("qt_web.release_worker_check")
        self.assertEqual(self._save_and_read(gui), self._save_and_read(worker))

    def test_imports_reports_ok_and_failure(self):
        for name in ("qt_web.release_gui_check", "qt_web.release_worker_check"):
            module = self._load(name)
            out = module.imports(["json", "definitely_not_a_real_module_xyz"])
            self.assertEqual(out["json"], "ok")
            self.assertIn("Error", out["definitely_not_a_real_module_xyz"])


class TestTranslationControllerNoDeadCallbacks(unittest.TestCase):
    """TranslationController 不再提供会推空消息的翻译回调。

    历史 Bug：它调用 ``on_result(result)``（1 个参数），而
    ``bridge.emit_result`` 期望 ``on_result(mode, text, status, display)``。
    接错时译文被当成 mode、文本为空，用户看到一条空白结果。
    翻译现在统一走 TranslationManager 队列。
    """

    def _cls(self):
        from controllers.translation_controller import TranslationController

        return TranslationController

    def test_no_translate_method(self):
        self.assertFalse(
            hasattr(self._cls(), "translate"),
            "TranslationController.translate() 已删除，请改用 TranslationManager",
        )

    def test_no_result_callbacks(self):
        inst = self._cls()(translation_service=object())
        for attr in ("on_result", "on_status_update"):
            self.assertFalse(
                hasattr(inst, attr),
                f"TranslationController 不应再有 {attr}——接错会推空白消息",
            )

    def test_switch_model_delegates(self):
        seen = []

        class _Svc:
            def switch_model(self, key):
                seen.append(key)

        self._cls()(_Svc()).switch_model("gguf")
        self.assertEqual(seen, ["gguf"])

    def test_main_does_not_wire_translation_callbacks(self):
        src = (ROOT / "qt_web" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "app.translation.on_result",
            src,
            "main.py 不应再把 TranslationController.on_result 接到 bridge.emit_result",
        )
        self.assertNotIn("app.translation.on_status_update", src)

    def test_main_api_switches_still_use_controller(self):
        """switch_model 仍要经控制器走（4 处调用），别被误删。"""
        src = (ROOT / "qt_web" / "api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            src.count("app.translation.switch_model"),
            4,
            "api.py 里的 app.translation.switch_model 调用少了，检查是否误删",
        )


class TestSharedHelpersAreSingleSource(unittest.TestCase):
    """公共工具必须只有一份实现，不能再各自复制。"""

    def test_release_checks_import_shared_helpers(self):
        for name in ("qt_web/release_gui_check.py", "qt_web/release_worker_check.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "from qt_web.release_check_common import",
                src,
                f"{name} 应导入公共实现，而不是自己再写一遍 save/imports",
            )
            self.assertNotIn(
                "def imports(names)", src, f"{name} 不应再本地定义 imports()"
            )
            self.assertNotIn("def save(report, path)", src, f"{name} 不应再本地定义 save()")

    def test_release_check_common_is_stdlib_only(self):
        """两个自检跑在独立 exe 里，公共模块不能引入重型依赖。"""
        src = (ROOT / "qt_web/release_check_common.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        heavy = {"torch", "PySide6", "onnxruntime", "numpy", "funasr", "cv2", "sounddevice"}
        self.assertFalse(
            top_level & heavy, f"release_check_common 引入了重型依赖: {top_level & heavy}"
        )

    def test_tools_share_model_dirs(self):
        for name in ("tools/stage_voice_models.py", "tools/sync_external_models.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "from model_dirs import",
                src,
                f"{name} 应导入 tools/model_dirs.py 的共用定义",
            )
            self.assertNotIn(
                "def detect_kind(", src, f"{name} 不应再本地定义 detect_kind()"
            )

    def test_duplicated_constants_removed(self):
        """KINDS 表只能有一处定义，否则增删模型时会漏改。"""
        for name in ("tools/stage_voice_models.py", "tools/sync_external_models.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "'BiCifParaformer': 'paraformer_large'",
                src,
                f"{name} 里还有一份 KINDS 表副本",
            )


class TestDetectKind(unittest.TestCase):
    """共用的 detect_kind 行为（对应原先两份不同实现的差异）。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))

    def _write(self, tmp, content):
        path = Path(tmp) / "config.yaml"
        path.write_text(content, encoding="utf-8")
        return Path(tmp)

    def test_known_architectures(self):
        from model_dirs import detect_kind

        with tempfile.TemporaryDirectory() as tmp:
            for arch, kind in (
                ("SenseVoiceSmall", "sensevoice"),
                ("BiCifParaformer", "paraformer_large"),
                ("Paraformer", "paraformer_large"),
            ):
                d = self._write(tmp, f"model: {arch}\n")
                self.assertEqual(detect_kind(d), kind, f"{arch} 应映射为 {kind}")

    def test_unknown_and_missing(self):
        from model_dirs import detect_kind

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_kind(self._write(tmp, "model: SomethingElse\n")))
            self.assertIsNone(detect_kind(Path(tmp) / "not_exist"), "目录不存在应返回 None")

    def test_malformed_yaml_returns_none(self):
        """曾有一份实现在这里抛异常（只捕 ValueError），合并后必须都返回 None。"""
        from model_dirs import detect_kind

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_kind(self._write(tmp, "model: [unclosed\n")))
            self.assertIsNone(detect_kind(self._write(tmp, "- 这不是 dict\n")))


if __name__ == "__main__":
    unittest.main()
