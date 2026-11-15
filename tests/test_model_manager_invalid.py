"""守门测试：本地模型列表不能出现「文件已不存在」的模型。

故障现象（2026-11-15，用户报告）：
    设置 → 翻译 → 本地模型下拉菜单里列出了打不开的模型，
    启动日志有「模型路径失效，请重新导入」但条目照样显示。

根因：
    `ModelManager._load_config()` 在文件不存在时仍然执行
    `valid_models[model_id] = model_info`，只打了一条警告。
    失效记录于是进入 `self.models`，经 `get_all_models()` 喂给
    `qt_web/api.py::_models_state()`，最终渲染到下拉框。

本测试不依赖真实模型权重，全部用临时目录里的占位文件。
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

from core.model_manager import ModelManager  # noqa: E402

GGUF_MAGIC = b"GGUF"


def write_config(directory: Path, models: dict, active: str = None) -> Path:
    """在 models_config.json 里写入给定模型表（路径原样保存）。"""
    path = directory / "models_config.json"
    data = {
        "models": models,
        "active_model": active,
        "active_model_path": models.get(active, {}).get("path") if active else None,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_config(directory: Path) -> dict:
    return json.loads((directory / "models_config.json").read_text(encoding="utf-8"))


class InvalidModelRecordTest(unittest.TestCase):
    """失效记录必须被丢弃，不能只打警告。

    构造 ModelManager 时要同时打两个补丁：
    - `ModelManager.__init__` 不暴露 `models_dir` 参数，只能包一层传临时目录；
    - `core.model_manager` 命名空间里的 `ModelDiscovery` 换成指向临时投放目录的版本。

    ⚠️ **两个补丁必须在 `ModelManager()` 构造期间同时生效**。曾经只补丁第二个、
       `__init__` 仍走真实目录，测试直接改写了用户的 `models/local/models_config.json`
       （真实配置里被塞进一条指向临时目录的 ghost 记录）。改这个文件务必先跑一遍。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.models_dir = self.tmp / "models" / "local"
        self.models_dir.mkdir(parents=True)
        self.drop = self.tmp / "drop"
        self.drop.mkdir()

        self.alive = self.tmp / "alive.gguf"
        self.alive.write_bytes(GGUF_MAGIC + b"\0" * 64)

        self.dead_path = self.tmp / "gone" / "ghost.gguf"  # 故意不创建

    def tearDown(self):
        self._tmp.cleanup()

    def _manager(self) -> ModelManager:
        """构造 ModelManager，把模型目录与投放目录都指到临时目录。"""
        return _patched_init(self.models_dir, self.drop)

    # ---- 核心守卫 -------------------------------------------------

    def test_missing_file_record_is_dropped(self):
        """文件不存在的记录不能出现在列表里。"""
        write_config(self.models_dir, {
            "alive": {"name": "alive", "path": str(self.alive), "file_name": "alive.gguf"},
            "ghost": {"name": "ghost", "path": str(self.dead_path), "file_name": "ghost.gguf"},
        })
        mm = self._manager()
        ids = [m["id"] for m in mm.get_all_models()]
        self.assertIn("alive", ids, "有效模型不能被一并剔除")
        self.assertNotIn("ghost", ids, "文件不存在的模型不能出现在列表里")
        self.assertNotIn("ghost", mm.models, "失效记录不能留在 self.models")

    def test_active_model_pointing_to_missing_file_is_cleared(self):
        """active_model 指向失效文件时必须清空，否则 translation_usable() 会误判可翻。"""
        write_config(
            self.models_dir,
            {"ghost": {"name": "ghost", "path": str(self.dead_path), "file_name": "ghost.gguf"}},
            active="ghost",
        )
        mm = self._manager()
        self.assertIsNone(mm.active_model, "失效的活动模型必须重置为 None")
        self.assertIsNone(mm.active_model_path)
        self.assertIsNone(mm.get_active_model())
        self.assertEqual(mm.get_all_models(), [])

    def test_valid_active_model_untouched(self):
        """有效模型当活动模型时，不能被误伤。"""
        write_config(
            self.models_dir,
            {
                "alive": {"name": "alive", "path": str(self.alive), "file_name": "alive.gguf"},
                "ghost": {"name": "ghost", "path": str(self.dead_path), "file_name": "ghost.gguf"},
            },
            active="alive",
        )
        mm = self._manager()
        self.assertEqual(mm.active_model, "alive")
        self.assertEqual(Path(mm.active_model_path).resolve(), self.alive.resolve())
        active = [m for m in mm.get_all_models() if m["is_active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "alive")

    def test_cleanup_is_persisted_to_disk(self):
        """剔除结果必须写回配置文件，否则下次启动又会读回来。"""
        write_config(self.models_dir, {
            "alive": {"name": "alive", "path": str(self.alive), "file_name": "alive.gguf"},
            "ghost": {"name": "ghost", "path": str(self.dead_path), "file_name": "ghost.gguf"},
        })
        self._manager()
        saved = read_config(self.models_dir)
        self.assertIn("alive", saved["models"])
        self.assertNotIn("ghost", saved["models"], "失效记录必须从磁盘配置里清掉")

    # ---- 防止「删了又加回来」 ------------------------------------

    def test_scan_does_not_resurrect_removed_model(self):
        """失效记录清掉后，scan() 不能把同一个文件重新加回来（它已不存在）。"""
        write_config(self.models_dir, {
            "ghost": {"name": "ghost", "path": str(self.dead_path), "file_name": "ghost.gguf"},
        })
        mm = self._manager()
        # 扫描两轮：ModelDiscovery 要求稳定两轮才登记
        mm.scan()
        mm.scan()
        ids = [m["id"] for m in mm.get_all_models()]
        self.assertNotIn("ghost", ids, "scan() 不能把失效文件重新登记")

    def test_scan_readds_model_that_came_back(self):
        """用户确认：记录可以删，文件回来了应由「启动时的 scan()」重新登记。

        场景要按真实路径摆：记录里的路径**在投放目录内**、文件被删掉，
        然后原样放回去。这样 setUp 里那个「投放目录父级」的模拟目录不会干扰。
        """
        inside = self.drop / "ghost.gguf"
        write_config(self.models_dir, {
            "ghost": {"name": "ghost", "path": str(inside), "file_name": "ghost.gguf"},
        })
        mm = self._manager()
        self.assertEqual(mm.get_all_models(), [], "文件缺失时记录应被剔除")

        # 文件真的出现在投放目录里
        inside.write_bytes(GGUF_MAGIC + b"\0" * 64)
        mm.scan()
        mm.scan()  # ModelDiscovery 要求稳定两轮才登记

        ids = [m["id"] for m in mm.get_all_models()]
        self.assertIn("ghost", ids, "文件回来后应被 scan() 重新登记")

    # ---- 运行期兜底 ----------------------------------------------

    def test_runtime_deletion_hidden_from_list(self):
        """运行期间文件被删（scan 尚未跑到）时，列表也不能出现该模型。"""
        write_config(self.models_dir, {
            "alive": {"name": "alive", "path": str(self.alive), "file_name": "alive.gguf"},
        })
        mm = self._manager()
        self.assertEqual(len(mm.get_all_models()), 1)

        self.alive.unlink()
        self.assertEqual(
            mm.get_all_models(), [],
            "文件被删后 get_all_models() 必须兜底过滤，否则下拉出现幽灵条目",
        )


def _patched_init(models_dir: Path, drop_root: Path):
    """让 ModelManager 使用临时目录与临时投放目录（不改生产构造函数签名）。"""
    original = ModelManager.__init__

    def init(self, models_dir_arg="models/local"):  # noqa: ANN001
        # ModelManager.__init__ 里 `ModelDiscovery(...)` 走的是模块级名字，
        # 所以直接替换导入到 core.model_manager 命名空间里的那个类最稳。
        import core.model_manager as mm_mod
        from utils.model_discovery import ModelDiscovery as RealDiscovery

        def factory(root, state_file, base):  # noqa: ANN001
            # base 必须是**投放目录的父级**，与生产一致（生产是项目根、投放是
            # `模型/翻译`）。若把 base 设成投放目录自身，`store_path()` 会退化成
            # 裸文件名 `ghost.gguf`，而 `path_key()` 用绝对路径，两者永不相等，
            # `seen` 缓存永远命中不了，scan() 也就永远不会登记 —— 那是测试脚手架
            # 的假失败，不是业务 bug。
            return RealDiscovery(str(drop_root), state_file, str(drop_root.parent))

        with mock.patch.object(mm_mod, "ModelDiscovery", factory):
            original(self, models_dir=str(models_dir))

    instance = ModelManager.__new__(ModelManager)
    init(instance)
    return instance


if __name__ == "__main__":
    unittest.main()
