"""语音输入页「识别结果框」的行为约定。

背景（2026-11-15）：用户反馈识别结果框不应该能编辑，只能复制。
但该框可编辑是为了配合「保存修改」——它对比「模型原文」与「用户改后的文本」
提取差异，写进个人词库候选。直接改成只读会丢掉这个学习功能，
因此改为**默认只读 + 「编辑」按钮临时解锁**。

这里守住的约定：

1. 结果框初始必须是 `readonly`（带 `readonly` 属性），不能一上来就可编辑。
2. 必须有「编辑」按钮，且点击能在只读/可编辑之间切换。
3. 初始化调用必须是静默的（`setEditing(false, true)`），
   否则会覆盖页面下方的「就绪 · 松开自动识别」提示。
4. 编辑中收到新的识别结果时，不能更新 `lastRaw`（diff 基准），
   否则用户已做的纠错会被冲掉、学不到。
5. 「保存修改」在只读且内容未改时，要提示去点「编辑」。
6. `index.html` 里 24 处 `?v=` 必须一致，否则改了 JS/CSS 仍用缓存。

运行：py -3.14 -m unittest tests.test_voice_output_readonly -v
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE_JS = ROOT / "qt_web" / "web" / "js" / "pages" / "voice.js"
INDEX_HTML = ROOT / "qt_web" / "web" / "index.html"
COMPONENTS_CSS = ROOT / "qt_web" / "web" / "css" / "components.css"


class TestVoiceOutputReadonly(unittest.TestCase):
    def setUp(self):
        self.src = VOICE_JS.read_text(encoding="utf-8")

    def test_textarea_starts_readonly(self):
        """结果框的 HTML 里必须带 readonly 属性。"""
        match = re.search(r'<textarea[^>]*id="asr-output"[^>]*>', self.src)
        self.assertIsNotNone(match, "找不到 #asr-output 的 textarea 定义")
        self.assertIn(
            "readonly",
            match.group(0),
            "识别结果框必须默认 readonly（默认只读，避免误触改字）",
        )

    def test_placeholder_does_not_promise_editing(self):
        """占位文字不能再说「可直接修改」，会与实际只读行为矛盾。"""
        match = re.search(r'<textarea[^>]*id="asr-output"[^>]*>', self.src)
        self.assertNotIn(
            "可直接修改",
            match.group(0),
            "结果框已改为默认只读，占位文字不应再写「可直接修改」",
        )

    def test_edit_button_exists(self):
        self.assertIn(
            'id: "asr-edit-btn"',
            self.src,
            "必须有「编辑」按钮，否则只读后就无法纠错与学习词库",
        )

    def test_edit_toggles_readonly(self):
        """点击编辑按钮要真正切换 readonly 状态。"""
        self.assertIn(
            "output.readOnly = !on",
            self.src,
            "setEditing 必须实际设置 textarea.readOnly",
        )
        self.assertIn(
            'editBtn.dataset.editing !== "1"',
            self.src,
            "编辑按钮需按当前状态切换（解锁/锁定）",
        )

    def test_initial_call_is_quiet(self):
        """初始化必须是静默调用，否则会覆盖「就绪」提示。"""
        self.assertIn(
            "setEditing(false, true)",
            self.src,
            "初始应调用 setEditing(false, true)，避免覆盖页面就绪提示",
        )
        self.assertNotIn(
            "setEditing(false);",
            self.src,
            "存在非静默的 setEditing(false)，会清掉就绪提示",
        )

    def test_edit_does_not_clobber_last_raw(self):
        """编辑中收到新结果不能覆盖 lastRaw，否则纠错基准丢失。"""
        handler = re.search(
            r'A\.bridge\.on\("asr-result".*?\n      \}\);', self.src, re.S
        )
        self.assertIsNotNone(handler, "找不到 asr-result 处理函数")
        body = handler.group(0)
        self.assertIn("editing", body, "asr-result 处理里应判断当前是否在编辑")
        self.assertRegex(
            body,
            r"if\s*\(!editing\)\s*lastRaw\s*=",
            "编辑中不应更新 lastRaw（应写成 if (!editing) lastRaw = ...）",
        )

    def test_save_hint_guides_to_edit(self):
        """只读且未修改时点保存，应引导去点「编辑」。"""
        self.assertIn(
            '先点「✏️ 编辑」',
            self.src,
            "只读状态下点「保存修改」应提示先点编辑",
        )

    def test_copy_works_in_readonly(self):
        """只读不能影响复制：复制走 Python 剪贴板，不依赖可编辑。"""
        self.assertIn('A.bridge.call("copy_text"', self.src)


class TestReadonlyStyle(unittest.TestCase):
    def test_readonly_and_editing_styles_exist(self):
        css = COMPONENTS_CSS.read_text(encoding="utf-8")
        self.assertIn("textarea.ta[readonly]", css, "缺少只读态样式")
        self.assertIn(".editing", css, "缺少编辑态样式（解锁时应有明显反馈）")


class TestAssetVersionBumped(unittest.TestCase):
    """改了 JS/CSS 必须 bump 版本号，否则 QWebEngine 继续用缓存。"""

    def test_all_versions_identical(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        versions = re.findall(r"\?v=([0-9a-z]+)", html)
        self.assertEqual(len(versions), 24, f"应有 24 处 ?v=，实际 {len(versions)}")
        self.assertEqual(
            len(set(versions)), 1, f"版本号不一致：{sorted(set(versions))}"
        )

    def test_version_not_stale(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        version = set(re.findall(r"\?v=([0-9a-z]+)", html)).pop()
        self.assertGreaterEqual(
            version,
            "20261115e",
            "改了任何 JS/CSS 必须 bump 版本号（当前期望 >= 20261115e）",
        )


if __name__ == "__main__":
    unittest.main()
