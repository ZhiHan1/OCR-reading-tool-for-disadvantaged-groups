"""控制器与翻译后端重构后的守门测试。

守的是 2026-11-05 那次「后端压缩与优化」改动里容易被后续修改破坏的约定：

1. ``func1`` / ``func2`` 继承 ``BaseController`` 后，三个行为必须保持和重构前一致：
   - ``_show_result`` 返回 True（翻译接管）时**不朗读**原文；
   - 朗读开关关闭时**不朗读**；
   - 朗读时一律 ``dedup=False``（手动触发，重复点也要重读）。
2. ``scan_mode_controller._read_stable_frames`` 的取值约束（1~10，非法值回退默认），
   func3 / 漫画 / 游戏三个子类共用同一份实现，不能再各自覆盖。
3. ``AppController`` 的 13 个懒构造属性必须仍然是**只读 property**。
4. ``GGUFTranslationModel._max_tokens_for`` 不能再有 256 的硬上限（KB-TR-003）。

运行：py -3.14 -m unittest tests.test_backend_refactor -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _FakeTTS:
    def __init__(self):
        self.calls = []

    def speak(self, text, interrupt=False, dedup=True):
        self.calls.append({"text": text, "interrupt": interrupt, "dedup": dedup})


class _FakeOCR:
    def __init__(self, text="测试文字"):
        self.text = text

    def recognize(self, image):
        return self.text


class _FakeScreenshot:
    def capture_and_select(self):
        return (0, 0, 10, 10), object()

    def capture_region(self, region):
        return object()

    def select_region(self):
        return (0, 0, 10, 10)


class _Config:
    """最小配置桩：只实现 get(*keys, default=)"""

    def __init__(self, values=None):
        self.values = values or {}

    def get(self, *keys, default=None):
        return self.values.get(".".join(keys), default)


class ReadOnceControllerTest(unittest.TestCase):
    """func1 / func2 的朗读决策（重构前两份代码逐字相同，现在共用 _speak_or_translate）"""

    def _make(self, which, config=None, result_handled=False):
        from controllers.func1_controller import Func1Controller
        from controllers.func2_controller import Func2Controller

        tts = _FakeTTS()
        cls = Func1Controller if which == "func1" else Func2Controller
        ctrl = cls(_FakeOCR(), tts, _FakeScreenshot(), config if config is not None else _Config())
        ctrl.handled = result_handled
        ctrl.seen = []

        def on_result(mode, text, status, display):
            ctrl.seen.append({"mode": mode, "text": text, "status": status, "display": display})
            return ctrl.handled

        ctrl.on_result = on_result
        return ctrl, tts

    def test_mode_and_label_are_set(self):
        for which in ("func1", "func2"):
            ctrl, _ = self._make(which)
            self.assertEqual(ctrl.MODE, which)
            self.assertTrue(ctrl.LABEL)

    def test_translation_handled_skips_speaking(self):
        for which in ("func1", "func2"):
            ctrl, tts = self._make(which, result_handled=True)
            ctrl._speak_or_translate("你好")
            self.assertEqual(tts.calls, [], f"{which}: 翻译接管时不应朗读原文")
            self.assertEqual(len(ctrl.seen), 1)

    def test_read_switch_off_skips_speaking(self):
        for which in ("func1", "func2"):
            cfg = _Config({f"recognition.{which}_read": False})
            ctrl, tts = self._make(which, config=cfg)
            ctrl._speak_or_translate("你好")
            self.assertEqual(tts.calls, [], f"{which}: 朗读开关关闭时不应朗读")

    def test_speaks_without_dedup(self):
        for which in ("func1", "func2"):
            ctrl, tts = self._make(which)
            ctrl._speak_or_translate("你好", interrupt_mode=True)
            self.assertEqual(len(tts.calls), 1, f"{which}: 应朗读")
            call = tts.calls[0]
            self.assertEqual(call["text"], "你好")
            self.assertTrue(call["interrupt"])
            self.assertFalse(call["dedup"], f"{which}: 手动触发必须 dedup=False")

    def test_display_prefix_matches_label(self):
        for which in ("func1", "func2"):
            ctrl, _ = self._make(which)
            ctrl._speak_or_translate("你好")
            self.assertEqual(ctrl.seen[0]["display"], f"[{ctrl.LABEL}] 你好")

    def test_func1_execute_end_to_end(self):
        ctrl, tts = self._make("func1")
        ctrl.execute()
        self.assertEqual(len(tts.calls), 1)
        self.assertFalse(ctrl.is_busy)

    def test_func2_read_region_requires_region(self):
        ctrl, tts = self._make("func2")
        ctrl.read_region()
        self.assertEqual(tts.calls, [])
        ctrl.region = (0, 0, 10, 10)
        ctrl.read_region()
        self.assertEqual(len(tts.calls), 1)


class ScanModeStableFramesTest(unittest.TestCase):
    """_read_stable_frames 已上提到 ScanModeController，三个子类不应再各自覆盖"""

    def _make(self, cls_name):
        from controllers import func3_controller, game_mode_controller, manga_controller

        cls = {
            "func3": func3_controller.Func3Controller,
            "manga": manga_controller.MangaController,
            "game": game_mode_controller.GameModeController,
        }[cls_name]
        obj = cls.__new__(cls)
        return obj

    def test_subclasses_do_not_override(self):
        from controllers.scan_mode_controller import ScanModeController

        for name in ("func3", "manga", "game"):
            obj = self._make(name)
            self.assertIs(
                type(obj)._read_stable_frames,
                ScanModeController._read_stable_frames,
                f"{name} 不应再覆盖 _read_stable_frames",
            )

    def test_clamp_and_fallback(self):
        from controllers.scan_mode_controller import ScanModeController

        class Probe(ScanModeController):
            CONFIG_SECTION = "func3"
            DEFAULT_STABLE_FRAMES = 2

        probe = Probe.__new__(Probe)

        # scan_mode_controller 用的是 `from core.config_manager import get_config`，
        # 名字已绑定到它自己的模块命名空间，必须 patch 那里才生效。
        import controllers.scan_mode_controller as smc

        original = smc.get_config
        try:
            for raw, expected, why in (
                (0, 1, "0 视为『出现即读』→ 夹到 1"),
                (1, 1, "下限 1"),
                (3, 3, "正常值"),
                (10, 10, "上限 10"),
                (99, 10, "超上限夹到 10"),
                ("bad", 2, "非数字回退默认"),
                (None, 2, "None 回退默认"),
                (2.7, 2, "浮点截断为 2"),
                (True, 1, "bool 按 int 1 处理"),
            ):
                smc.get_config = lambda raw=raw: _Config({"ocr.func3.stable_frames": raw})
                self.assertEqual(
                    probe._read_stable_frames(), expected, f"输入 {raw!r}（{why}）"
                )
        finally:
            smc.get_config = original


class AppControllerAccessorTest(unittest.TestCase):
    """13 个懒构造属性必须仍是只读 property，且都有对应的 _make_ 方法"""

    def test_all_members_are_readonly_properties(self):
        from controllers.app_controller import AppController

        self.assertEqual(len(AppController.LAZY_MEMBERS), 13)
        for name in AppController.LAZY_MEMBERS:
            self.assertIsInstance(
                getattr(AppController, name, None), property, f"{name} 不是 property"
            )
            self.assertTrue(
                hasattr(AppController, "_make_" + name), f"{name} 缺少 _make_ 方法"
            )

    def test_property_is_readonly(self):
        from controllers.app_controller import AppController

        inst = AppController.__new__(AppController)
        inst._lazy = {}
        inst._build_lock = __import__("threading").RLock()
        with self.assertRaises(AttributeError):
            inst.func1 = object()


class MaxTokensTest(unittest.TestCase):
    """KB-TR-003：max_tokens 不能有 256 的硬上限

    注意：llama-cpp-python 里 `Llama.n_ctx` 是**方法**（`n_ctx()`），不是属性。
    第一版测试把它写成了属性，真机跑才发现 `int(<method>)` 抛 TypeError，
    所以下面同时覆盖「方法」与「属性」两种形态。
    """

    def _model(self, prompt_tokens, n_ctx=2048):
        from core.gguf_translation_model import GGUFTranslationModel

        fake = type("FakeLlama", (), {})()
        fake.tokenize = lambda data, add_bos=True, special=False: list(range(prompt_tokens))
        fake.n_ctx = lambda: n_ctx  # 真实形态：方法
        m = GGUFTranslationModel.__new__(GGUFTranslationModel)
        m.model = fake
        return m

    def test_long_text_is_not_capped_at_256(self):
        # 447 字实测需要 420 tokens；旧实现只给 256，必然截断
        m = self._model(278)
        self.assertGreater(m._max_tokens_for("x"), 256)

    def test_never_exceeds_context_window(self):
        m = self._model(1000)
        budget = m._max_tokens_for("x")
        self.assertLessEqual(1000 + budget, 2048)
        self.assertGreaterEqual(budget, 32)

    def test_short_text_still_gets_room(self):
        m = self._model(10)
        self.assertGreaterEqual(m._max_tokens_for("x"), 64)

    def test_n_ctx_property_form_also_works(self):
        """兼容把 n_ctx 实现成属性的其它版本 / 假对象"""
        from core.gguf_translation_model import GGUFTranslationModel

        class PropN:
            n_ctx = 1024

            def tokenize(self, *a, **k):
                return list(range(900))

        m = GGUFTranslationModel.__new__(GGUFTranslationModel)
        m.model = PropN()
        self.assertLessEqual(900 + m._max_tokens_for("x"), 1024)

    def test_tokenize_failure_falls_back(self):
        from core.gguf_translation_model import GGUFTranslationModel

        class Boom:
            def n_ctx(self):
                return 2048

            def tokenize(self, *a, **k):
                raise RuntimeError("boom")

        m = GGUFTranslationModel.__new__(GGUFTranslationModel)
        m.model = Boom()
        self.assertGreaterEqual(m._max_tokens_for("短" * 100), 32)


class ConfigDefaultsCacheTest(unittest.TestCase):
    """load_defaults 现在走 lru_cache，必须仍返回可以自由修改的独立副本"""

    def test_returns_independent_copies(self):
        from core.config_manager import load_defaults

        a = load_defaults()
        b = load_defaults()
        self.assertEqual(a, b)
        a.setdefault("tts", {})["rate"] = 999
        self.assertNotEqual(a, b, "改副本不应影响下一次的结果")
        self.assertNotIn("_probe", b)


if __name__ == "__main__":
    unittest.main()
