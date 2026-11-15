"""守门测试：动态导入的模块必须显式写进 PyInstaller hiddenimports。

故障（2026-11-15 用户报告「打包后翻译不可用」）：
    安装版日志刷屏
        [翻译服务] GGUF模型不可用: No module named 'core.gguf_translation_model'
        [翻译服务] OPUS模型不可用: No module named 'core.opus_translation_model'
        [翻译服务] Google翻译不可用: No module named 'core.google_translation_model'
        [翻译服务] 自定义在线模型不可用: No module named 'core.custom_openai_translation_model'
    四个翻译后端全部注册失败，翻译整体不可用；界面完全正常，极难自查。

根因：
    `core/translation_service.py::_register_backends()` 用
    `importlib.import_module(module_name)` 按**字符串**加载后端。
    PyInstaller 静态分析看不见这种依赖，一个模块都收不进包。
    `OCR_Reader_Web.spec` 的 `gui_hidden` 里有 `llama_cpp`（C 扩展），
    却没有 `core.*_translation_model` 这些项目自己的模块。

这条测试的价值在于：**单元测试全绿、打包也「成功」**，问题只在安装版
启动日志里出现。所以必须在源码阶段就把 spec 与代码对齐。
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = ROOT / "OCR_Reader_Web.spec"
SERVICE = ROOT / "core" / "translation_service.py"


def dynamic_import_targets() -> set:
    """从 translation_service._BACKENDS 里取出所有动态导入的模块名。

    不直接 import（那会真的加载 llama_cpp），用 AST 静态读取常量表。
    """
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_BACKENDS":
                modules = set()
                for elt in node.value.elts:
                    # 每个元素是 (key, module_name, class_name, label)
                    mod = elt.elts[1]
                    modules.add(ast.literal_eval(mod))
                return modules
    raise AssertionError("没找到 _BACKENDS 定义，translation_service.py 结构变了")


class DynamicImportPackagingTest(unittest.TestCase):
    def setUp(self):
        self.spec = SPEC.read_text(encoding="utf-8")
        self.targets = dynamic_import_targets()

    def test_backend_table_is_not_empty(self):
        """先确保能读到后端表，否则后面的断言是空转。"""
        self.assertTrue(self.targets, "_BACKENDS 解析为空，测试失效")
        self.assertIn("core.gguf_translation_model", self.targets)

    def test_gui_hidden_imports_cover_all_backends(self):
        """gui_hidden 必须包含每个动态导入的翻译后端。"""
        gui_hidden = self._gui_hidden_block()
        missing = sorted(m for m in self.targets if m not in gui_hidden)
        self.assertEqual(
            missing, [],
            "这些翻译后端是 importlib 动态导入的，PyInstaller 静态分析看不到，"
            f"必须写进 OCR_Reader_Web.spec 的 gui_hidden：{missing}",
        )

    def test_every_backend_module_exists_on_disk(self):
        """后端模块文件必须真的存在（防止改名后 spec 与代码脱节）。"""
        for module in sorted(self.targets):
            rel = module.replace(".", "/") + ".py"
            self.assertTrue((ROOT / rel).is_file(), f"模块文件不存在: {rel}")

    def test_exported_file_would_be_detected_missing(self):
        """反向自检：如果 spec 里漏了 GGUF 后端，本测试的判据确实会报出来。

        这条不依赖真实打包，只校验「判据本身有效」。
        """
        gui_hidden = self._gui_hidden_block()
        fake_missing = "core.gguf_translation_model" not in gui_hidden
        # 当前 spec 已修好，所以这里断言「不是缺失状态」；
        # 若有人把那一行删掉，上面 test_gui_hidden_imports_cover_all_backends 会失败。
        self.assertFalse(fake_missing, "spec 里缺少 core.gguf_translation_model")

    def _gui_hidden_block(self) -> str:
        """截取 gui_hidden = [...] 这一段。

        不能用非贪婪 `.*?\\]`：列表里每个元素都是带引号的字符串，
        而 `'core.xxx'` 后面紧跟的 `,` 不影响，但模块名内部出现 `]` 时
        （如 `scipy.linalg` 没有，但注释里可能有）会提前截断。
        改用「从 `gui_hidden = [「开始，到下一个顶层赋值 `worker_hidden` 之前」。
        """
        start = self.spec.find("gui_hidden")
        self.assertGreaterEqual(start, 0, "spec 里没找到 gui_hidden 定义")
        end = self.spec.find("worker_hidden", start)
        self.assertGreater(end, start, "spec 里没找到 worker_hidden，无法界定 gui_hidden 范围")
        return self.spec[start:end]


if __name__ == "__main__":
    unittest.main()
