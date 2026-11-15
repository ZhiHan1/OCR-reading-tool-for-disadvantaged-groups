"""热词（L1 文本层）防误伤回归测试。

覆盖一件事：**热词只能纠「确实听错」的专有名词，不能改坏正常文本。**

背景：热词表从 2 条扩到 300+ 条后，拼音整串相似度 0.80 几乎能被任何句子撞上，
实测 14 句日常话里 13 句被误改（「已经」→「阴极铜」、「个功」→「铬锆铜」）。
根因与修法见 `core/hotwords.py` 的 `_is_plausible_typo` 与 `FUZZY_MIN_CHARS`：

- 模糊匹配只用于 3 字及以上的词（两字同音词无法在读音层区分）；
- 中文替换必须等长，且逐字拼音最多 1 个字对不上。

纯函数测试，秒级，不需要加载模型。

用法（在项目根目录执行）：

    py -3.14 -m unittest tests.test_hotwords -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.hotwords as hotwords  # noqa: E402


class PlausibleTypoTest(unittest.TestCase):
    """逐字对齐判据：真错写放行，错位窗口拒绝。"""

    TRUTHY = [
        ("电解同", "电解铜"),
        ("无养铜杆", "无氧铜杆"),
        ("阳极坭", "阳极泥"),
        ("镀锡铜先", "镀锡铜线"),
        ("铜精匡", "铜精矿"),
        ("漆包先", "漆包线"),
        ("库纳万象", "昆仑万象"),
    ]

    FALSY = [
        ("已经", "阴极铜"),      # 窗口比目标词短
        ("个功", "铬锆铜"),
        ("银价", "阴极板"),
        ("京出", "浸出率"),
        ("经", "晶粒"),
        ("段文字", "端子"),
    ]

    def test_real_typos_pass(self):
        for original, replacement in self.TRUTHY:
            with self.subTest(original=original, replacement=replacement):
                self.assertTrue(hotwords._is_plausible_typo(original, replacement))

    def test_misaligned_windows_rejected(self):
        for original, replacement in self.FALSY:
            with self.subTest(original=original, replacement=replacement):
                self.assertFalse(hotwords._is_plausible_typo(original, replacement))


class SplitForFuzzyTest(unittest.TestCase):
    """两字词、牌号都不进模糊池，显式项单独保留。"""

    def test_short_words_excluded_from_fuzzy(self):
        explicit, fuzzy = hotwords._split_for_fuzzy(["铜线", "阳极泥", "方通=方铜"])
        self.assertEqual(fuzzy, ["阳极泥"])
        self.assertEqual(explicit, ["方通->方铜"])

    def test_explicit_separator_kept(self):
        _, fuzzy = hotwords._split_for_fuzzy(["方通->方铜"])
        self.assertEqual(fuzzy, [])

    def test_code_like_excluded_from_fuzzy(self):
        """牌号没有拼音，进模糊池会退化成字面比对而互相篡改。"""
        codes = ["TP1", "TU2", "BFe10-1-1", "BFe30-1-1", "QSn6.5-0.1", "HPb59-1"]
        _, fuzzy = hotwords._split_for_fuzzy(codes)
        self.assertEqual(fuzzy, [], "纯字母数字牌号不应进模糊池")

    def test_code_and_chinese_mixed(self):
        """词表混装时，中文词照常进模糊池，牌号被排除。"""
        explicit, fuzzy = hotwords._split_for_fuzzy(["阴极铜", "TP1", "无氧铜杆", "BFe10-1-1"])
        self.assertEqual(fuzzy, ["阴极铜", "无氧铜杆"])
        self.assertEqual(explicit, [])


class CodeLikeGuardTest(unittest.TestCase):
    """牌号防篡改：实测 BFe10-1-1 与 BFe30-1-1 曾互相误改。"""

    @classmethod
    def setUpClass(cls):
        from core.config_manager import get_config

        cls.cfg = get_config()
        cls._orig = cls.cfg.get("asr", "hotwords", default=None)
        cls.codes = ["BFe10-1-1", "BFe30-1-1", "TP1", "TU2", "QSn6.5-0.1",
                     "QSn6.5-0.4", "HPb59-1", "HPb62-3", "QAl9-4", "QAl10-4-4"]
        cls.cfg.set("asr", "hotwords", value=list(cls.codes))
        hotwords.reload_hotwords()

    @classmethod
    def tearDownClass(cls):
        cls.cfg.set("asr", "hotwords", value=cls._orig)
        hotwords.reload_hotwords()

    def test_codes_never_rewritten(self):
        """每个牌号原样输入，必须一字不改（尤其是 10 与 30 这类相邻编号）。"""
        for code in self.codes:
            with self.subTest(code=code):
                text = f"这批{code}材料"
                self.assertEqual(hotwords.apply_hotwords(text), text)

    def test_similar_codes_do_not_swap(self):
        """BFe10-1-1 / BFe30-1-1 是最容易互改的一对。"""
        for code in ("BFe10-1-1", "BFe30-1-1"):
            with self.subTest(code=code):
                self.assertEqual(hotwords.apply_hotwords(code), code)


class NormalizeEntryTest(unittest.TestCase):
    """注释与 Markdown 说明不是词。"""

    def test_comments_dropped(self):
        for text in ["# 注释", "> 引用说明", "// 注释"]:
            with self.subTest(text=text):
                self.assertEqual(hotwords._normalize_entry(text), "")

    def test_equals_becomes_arrow(self):
        # funasr 只认 => -> →，不认等号
        self.assertEqual(hotwords._normalize_entry("欧拉玛=Ollama"), "欧拉玛->Ollama")


class GuardMatchesTest(unittest.TestCase):
    """端到端：正常句子一字不改，真错写仍然纠正。"""

    SAFE = [
        "今天天气不错我们出去走走吧",
        "请帮我打开设置页面",
        "这个文件已经保存好了",
        "明天的会议改到下午三点",
        "我需要你写一段Python代码",
        "混凝土浇筑完成以后要养护七天",
        "黄同学今天请假了",
        "我们下周去北京出差",
        "第一季度营收同比增长百分之十七点五",
        "这个铜线已经拉丝完成了",
        "混凝土和钢筋的用量要重新核算",
        "请把阳极泥送去提纯",
        "无氧铜杆的规格是多少",
        "铜精矿的品位要求",
        "镀锡铜线的抗拉强度",
    ]

    @classmethod
    def setUpClass(cls):
        path = ROOT / "KNOWLEDGE" / "hotwords_metal.txt"
        words = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                word = hotwords._normalize_entry(line)
                if word and word not in words:
                    words.append(word)
        cls.words = [w for w in words if len(w) >= hotwords.FUZZY_MIN_CHARS]
        if not cls.words:
            raise unittest.SkipTest("没有可用的领域词表")

        from core.config_manager import get_config

        cls.cfg = get_config()
        cls._orig = cls.cfg.get("asr", "hotwords", default=None)
        cls.cfg.set("asr", "hotwords", value=cls.words)
        hotwords.reload_hotwords()

    @classmethod
    def tearDownClass(cls):
        cls.cfg.set("asr", "hotwords", value=cls._orig)
        hotwords.reload_hotwords()

    def test_normal_sentences_untouched(self):
        for text in self.SAFE:
            with self.subTest(text=text):
                self.assertEqual(hotwords.apply_hotwords(text), text)

    def test_real_typos_corrected(self):
        cases = [
            ("今天讨论无养铜杆的工艺", "无氧铜杆"),
            ("请把阳极坭送去提纯", "阳极泥"),
            ("铜精匡的品位需要确认", "铜精矿"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertIn(expected, hotwords.apply_hotwords(text))


if __name__ == "__main__":
    unittest.main()
