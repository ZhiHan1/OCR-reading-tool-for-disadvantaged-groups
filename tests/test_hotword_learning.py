"""个人词库自动学习（候选池）回归测试。

覆盖三件事，缺一不可：

1. `extract_edits` 词边界正确 —— 从「用户修改」里抠出来的必须是**整词**
   （阴极铜），不能是同音半截（音机 / 同），否则并进词表后既纠不准
   （会纠成「阴极铜铜」）又会误伤（单字「同」→「铜」乱改）。
2. `extract_edits` 不学无关改动 —— 插入、删除、改措辞都要返回空，
   这些属于用户润色，不是模型听错。
3. 候选池只进「候选」，不碰正式词表；拒绝后不复活；文件无 BOM、原子写。

纯函数 + 临时文件测试，秒级，不加载模型、不改用户 config.yaml。

用法（在项目根目录执行）：

    py -3.14 -m unittest tests.test_hotword_learning -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.hotword_learning as learning  # noqa: E402


# 测试用最小词表：模拟已有热词，用于锁定词边界
VOCAB = {"阴极铜", "电解铜", "无氧铜", "拉丝模", "连铸连轧", "铬锆铜", "退火炉"}


class ExtractEditsTest(unittest.TestCase):
    """从「识别原文 -> 用户确认文本」提取 (错写, 正写) 对。"""

    def test_single_word_typo_recovers_whole_word(self):
        """同音错字必须提取出完整词，而不是被 difflib 切开的半截。"""
        cases = [
            ("今天音机铜价格涨了", "今天阴极铜价格涨了", "音机铜", "阴极铜"),
            ("这批电解同已经到货", "这批电解铜已经到货", "电解同", "电解铜"),
            ("我们用无极铜做导体", "我们用无氧铜做导体", "无极铜", "无氧铜"),
            ("拉丝摸需要更换了", "拉丝模需要更换了", "拉丝摸", "拉丝模"),
            ("连珠连轧生产线", "连铸连轧生产线", "连珠连轧", "连铸连轧"),
            ("葛老铜的导电率怎么样", "铬锆铜的导电率怎么样", "葛老铜", "铬锆铜"),
        ]
        for raw, final, wrong, right in cases:
            with self.subTest(raw=raw):
                self.assertEqual(learning.extract_edits(raw, final, VOCAB), [(wrong, right)])

    def test_multiple_edits_in_one_sentence(self):
        raw = "音机铜和无极铜都涨价了"
        final = "阴极铜和无氧铜都涨价了"
        result = dict(learning.extract_edits(raw, final, VOCAB))  # 错写 -> 正写
        self.assertEqual(result, {"音机铜": "阴极铜", "无极铜": "无氧铜"})

    def test_punctuation_only_change_is_ignored(self):
        # 补标点不是听错，不该进词库
        self.assertEqual(
            learning.extract_edits("打开设置页面并保存配置", "打开设置页面，并保存配置", VOCAB),
            [],
        )

    def test_wording_change_is_ignored(self):
        # 追加语气词 = 长度变化，属于改措辞
        self.assertEqual(learning.extract_edits("明天开会", "明天开会吧", VOCAB), [])
        self.assertEqual(learning.extract_edits("把报告发给我", "把报告发给我吧", VOCAB), [])

    def test_deletion_is_ignored(self):
        # 「退了火炉」-> 「退火炉」是删字，长度不等，不能拿来当热词
        self.assertEqual(learning.extract_edits("昨天退了火炉温度不够", "昨天退火炉温度不够", VOCAB), [])

    def test_identical_text_returns_empty(self):
        self.assertEqual(learning.extract_edits("这批货到了吗", "这批货到了吗", VOCAB), [])
        self.assertEqual(learning.extract_edits("", "", VOCAB), [])
        self.assertEqual(learning.extract_edits("", "abc", VOCAB), [])

    def test_no_padding_leak(self):
        """回归：曾把整句或前一个词的尾字吸进热词（天音机铜 / 今天阴极铜价）。"""
        pairs = learning.extract_edits("今天音机铜价格涨了", "今天阴极铜价格涨了", VOCAB)
        self.assertEqual(len(pairs), 1)
        wrong, right = pairs[0]
        self.assertEqual((wrong, right), ("音机铜", "阴极铜"))
        for noise in ("今天", "价格", "涨了"):
            self.assertNotIn(noise, right)


class CandidateStoreTest(unittest.TestCase):
    """候选池：只增候选、可拒绝、不污染正式词表。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "candidates.json"
        self._orig_path = learning._candidates_path
        learning._candidates_path = lambda: self.path

    def tearDown(self):
        learning._candidates_path = self._orig_path
        self._tmp.cleanup()

    def test_add_and_weight_accumulates(self):
        self.assertEqual(learning.add_candidates([("音机铜", "阴极铜")]), 1)
        # 同一对再次学习：不算新增，权重 +1
        self.assertEqual(learning.add_candidates([("音机铜", "阴极铜")]), 0)
        items = learning.list_candidates()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["right"], "阴极铜")
        self.assertEqual(items[0]["weight"], 2)

    def test_file_is_utf8_without_bom_and_no_tmp_left(self):
        learning.add_candidates([("电解同", "电解铜")])
        raw = self.path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "候选池不能带 BOM")
        self.assertFalse(self.path.with_suffix(".tmp").exists(), "原子写不能残留 .tmp")
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["candidates"][0]["wrong"], "电解同")

    def test_dismiss_removes_and_blocks_revive(self):
        learning.add_candidates([("音机铜", "阴极铜"), ("电解同", "电解铜")])
        self.assertEqual(learning.dismiss_candidates(["电解铜"]), 1)
        self.assertEqual([c["right"] for c in learning.list_candidates()], ["阴极铜"])
        # 被拒绝过的候选不应因为再次学习而复活
        self.assertEqual(learning.add_candidates([("电解同", "电解铜")]), 0)
        self.assertEqual([c["right"] for c in learning.list_candidates()], ["阴极铜"])

    def test_clear_only_empties_candidates(self):
        learning.add_candidates([("音机铜", "阴极铜")])
        learning.dismiss_candidates(["无氧铜"])
        self.assertEqual(learning.clear_candidates(), 1)
        self.assertEqual(learning.list_candidates(), [])
        # dismissed 记录要保留，否则清空后用户拒绝过的词又会冒出来
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("无氧铜", data["dismissed"])

    def test_corrupt_file_moved_aside(self):
        self.path.write_bytes(b"{ not json")
        self.assertEqual(learning.list_candidates(), [])
        self.assertTrue(self.path.with_suffix(".corrupt").exists(), "损坏文件要留档而不是静默丢弃")

    def test_learning_never_touches_config(self):
        """核心约束：学习只进候选池，正式词表必须由用户确认后才变。"""
        from core.config_manager import get_config

        cfg = get_config()
        before = list(cfg.get("asr", "hotwords", default=None) or [])
        learning.add_candidates([("音机铜", "阴极铜")])
        after = list(cfg.get("asr", "hotwords", default=None) or [])
        self.assertEqual(before, after)


class ParseImportTest(unittest.TestCase):
    """手动导入词表的解析规则。"""

    def test_all_separators(self):
        text = "\n".join([
            "# 注释行",
            "阴极铜",
            "音机铜=阴极铜",
            "连珠连轧->连铸连轧",
            "葛老铜=>铬锆铜",
            "无极铜→无氧铜",
        ])
        self.assertEqual(
            learning.parse_import(text),
            [
                ("", "阴极铜"),
                ("音机铜", "阴极铜"),
                ("连珠连轧", "连铸连轧"),
                ("葛老铜", "铬锆铜"),
                ("无极铜", "无氧铜"),
            ],
        )

    def test_comments_and_blank_lines_skipped(self):
        self.assertEqual(learning.parse_import("\n\n# 说明\n> 引用\n// 注释\n"), [])


if __name__ == "__main__":
    unittest.main()
