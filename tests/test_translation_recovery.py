"""守门测试：打包版「翻译功能失效」的两种成因。

2026-11-15 用户报告「打包后翻译功能失效」。现场（安装目录）：
    models/local/models_config.json → {"models": {}, "active_model": null}
    模型/翻译/HY-MT1.5-1.8B-Q6_K.gguf → 文件真实存在（1.4 GB）

GGUF 明明在，却完全没有记录。两条独立成因、都会让
`qt_web/api.py::translation_usable()` 返回 False（自动翻译静默失效）：

1. **scan() 新增模型从不设 active**。模型进了列表，但没人启用它。
2. **记录表为空时启动不做首次发现**。ModelDiscovery 要求文件稳定两轮
   才登记，只靠 3 秒一次定时器要等 6 秒以上。

测试不依赖真实权重，用占位文件。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.model_manager as mm_mod  # noqa: E402
from core.model_manager import ModelManager  # noqa: E402
from utils.model_discovery import ModelDiscovery as RealDiscovery  # noqa: E402

GGUF_MAGIC = b"GGUF"


class TranslationRecoveryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.models_dir = self.tmp / "models" / "local"
        self.models_dir.mkdir(parents=True)
        self.drop = self.tmp / "模型" / "翻译"
        self.drop.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _manager(self):
        """构造 ModelManager：模型目录与投放目录都指向临时目录。

        ⚠️ 两个补丁必须同时生效，否则会改写用户的真实 models_config.json
        （详见 tests/test_model_manager_invalid.py 里的同一处注释）。
        `base` 必须是投放目录的**父级**，与生产（项目根 / 安装目录）一致。
        """
        original = ModelManager.__init__
        drop = self.drop

        def factory(root, state_file, base):  # noqa: ANN001
            return RealDiscovery(str(drop), state_file, str(drop.parent))

        def init(instance, models_dir_arg="models/local"):  # noqa: ANN001
            original(instance, models_dir=str(self.models_dir))

        with mock.patch.object(mm_mod, "ModelDiscovery", factory):
            mm = ModelManager.__new__(ModelManager)
            init(mm)
        return mm

    def _write_empty_config(self):
        (self.models_dir / "models_config.json").write_text(
            json.dumps({"models": {}, "active_model": None, "active_model_path": None}),
            encoding="utf-8",
        )

    def _usable(self, mm) -> bool:
        """与 qt_web/api.py::translation_usable() 完全一致的判定。"""
        return any(m.get("is_active") for m in mm.get_all_models())

    # ---- 成因 1：scan() 新增模型要自动启用 -------------------------

    def test_scan_auto_enables_first_model(self):
        """记录表空 + 投放目录有 GGUF：scan() 必须自动启用第一个模型。"""
        self._write_empty_config()
        (self.drop / "HY-MT1.5-1.8B-Q6_K.gguf").write_bytes(GGUF_MAGIC + b"\0" * 64)

        mm = self._manager()
        mm.scan()
        mm.scan()

        self.assertTrue(mm.get_all_models(), "扫描后应登记到模型")
        self.assertIsNotNone(mm.active_model, "scan() 新增模型时必须自动启用，否则翻译不可用")
        self.assertTrue(self._usable(mm), "translation_usable() 的判定依据必须为真")
        self.assertEqual(
            Path(mm.get_active_model_path()).name, "HY-MT1.5-1.8B-Q6_K.gguf",
        )

    def test_scan_does_not_override_existing_active(self):
        """已有活动模型时，扫描不能把它换掉。"""
        alive = self.tmp / "chosen.gguf"
        alive.write_bytes(GGUF_MAGIC + b"\0" * 64)
        (self.models_dir / "models_config.json").write_text(json.dumps({
            "models": {"chosen": {"name": "chosen", "path": str(alive),
                                  "file_name": "chosen.gguf", "enabled": True}},
            "active_model": "chosen",
            "active_model_path": str(alive),
        }), encoding="utf-8")
        (self.drop / "another.gguf").write_bytes(GGUF_MAGIC + b"\0" * 64)

        mm = self._manager()
        mm.scan()
        mm.scan()

        self.assertEqual(mm.active_model, "chosen", "扫描不能抢走用户已选的活动模型")
        ids = [m["id"] for m in mm.get_all_models()]
        self.assertIn("another", ids, "新模型仍应被登记")

    # ---- 成因 2：记录表为空时启动即做首次发现 ----------------------

    def test_empty_config_triggers_immediate_discovery(self):
        """记录表为空时，构造完 ModelManager 就应能直接翻译，不用等定时器。"""
        self._write_empty_config()
        (self.drop / "HY-MT1.5-1.8B-Q6_K.gguf").write_bytes(GGUF_MAGIC + b"\0" * 64)

        mm = self._manager()  # 不调用 scan()

        self.assertTrue(
            self._usable(mm),
            "记录表为空时启动应立刻发现并启用已有 GGUF，否则前几秒翻译静默失效",
        )

    def test_nonempty_config_skips_immediate_discovery(self):
        """记录表非空时不触发首次发现（不额外扫盘）。"""
        alive = self.tmp / "alive.gguf"
        alive.write_bytes(GGUF_MAGIC + b"\0" * 64)
        (self.models_dir / "models_config.json").write_text(json.dumps({
            "models": {"alive": {"name": "alive", "path": str(alive),
                                 "file_name": "alive.gguf", "enabled": True}},
            "active_model": "alive",
            "active_model_path": str(alive),
        }), encoding="utf-8")

        mm = self._manager()
        self.assertEqual(list(mm.models), ["alive"])
        self.assertEqual(mm.active_model, "alive")

    def test_recovery_persisted(self):
        """首次发现的自动启用必须写回磁盘，下次启动才能直接可用。"""
        self._write_empty_config()
        (self.drop / "HY-MT1.5-1.8B-Q6_K.gguf").write_bytes(GGUF_MAGIC + b"\0" * 64)

        self._manager()
        saved = json.loads((self.models_dir / "models_config.json").read_text(encoding="utf-8"))
        self.assertIn("HY-MT1.5-1.8B-Q6_K", saved["models"])
        self.assertEqual(saved["active_model"], "HY-MT1.5-1.8B-Q6_K")


if __name__ == "__main__":
    unittest.main()
