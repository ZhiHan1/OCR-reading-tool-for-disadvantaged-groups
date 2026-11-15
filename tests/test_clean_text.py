"""模型输出清理（`_clean_text`）回归测试。

覆盖一件事：**Paraformer 的逐 token 空格必须按语境合并，不能一刀切。**

背景（实测踩坑）：
    Paraformer 输出 `T P 1 紫铜`，但 `_clean_text` 的三条正则全部以
    `_CJK`（中文）为锚点，纯字母数字串一条都不匹配，空格原样留下。
    结果热词表里 27 条牌号（TP1 / QSn6.5-0.1 / BFe10-1-1 …）
    **永远匹配不上**——用户说「用 T P 1 紫铜」永远不会被纠成「TP1」。

修法要点（每一条都有反向用例守着）：
1. 含字母的分段才合并。`5 0 元` 不动——它可能是「五十元」也可能是「5、0 元」，
   粘成 `50` 反而制造新错误。
2. 全部由常见英文单词组成的分段判为句子，保留空格。
   `Hello world` 不能变成 `Helloworld`。
3. 牌号里的 `.` 与 `-` 两侧空格也要吸收：`Q Sn 6.5 - 0.1` → `QSn6.5-0.1`。

纯函数测试，秒级，不加载模型。

用法（在项目根目录执行）：

    py -3.14 -m unittest tests.test_clean_text -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.asr_service import _clean_text  # noqa: E402


class CodeTextJoinTest(unittest.TestCase):
    """牌号逐字符分开时要合回原形。"""

    CASES = [
        ("T P 1 紫铜", "TP1 紫铜"),
        ("T U 2 铜管", "TU2 铜管"),
        ("T P 2 / T P 3 牌号", "TP2 / TP3 牌号"),
        ("Q Sn 6.5 - 0.1 锡青铜", "QSn6.5-0.1 锡青铜"),
        ("H Pb 59 - 1 黄铜", "HPb59-1 黄铜"),
        ("H Pb 62 - 3 铅黄铜", "HPb62-3 铅黄铜"),
        ("B Fe 10 - 1 - 1 白铜", "BFe10-1-1 白铜"),
        ("T Ag 0.1 银铜", "TAg0.1 银铜"),
        ("T Be 2 铍铜", "TBe2 铍铜"),
        ("T Cr 1 铬锆铜", "TCr1 铬锆铜"),
        ("Q Be 2 铍青铜", "QBe2 铍青铜"),
        ("Q Sn 4 - 3 锡青铜", "QSn4-3 锡青铜"),
        ("Q Al 10 - 4 - 4 铝青铜", "QAl10-4-4 铝青铜"),
        ("Q Si 3 - 1 硅青铜", "QSi3-1 硅青铜"),
        ("H Al 77 - 2 铝黄铜", "HAl77-2 铝黄铜"),
        ("H Sn 70 - 1 锡黄铜", "HSn70-1 锡黄铜"),
        ("B Zn 15 - 20 白铜", "BZn15-20 白铜"),
    ]

    def test_spaced_codes_joined(self):
        for src, want in self.CASES:
            with self.subTest(src=src):
                self.assertEqual(_clean_text(src), want)


class ChineseJoinTest(unittest.TestCase):
    """中文 token 合并（原有行为不能退化）。"""

    def test_chinese_tokens_joined(self):
        self.assertEqual(_clean_text("今 天 天 气 不 错"), "今天天气不错")
        self.assertEqual(_clean_text("阴 极 铜 价 格 上 涨"), "阴极铜价格上涨")

    def test_short_text_kept_apart(self):
        # 数字与中文之间不加空格也不粘连，保持原样
        self.assertEqual(_clean_text("第 3 页"), "第 3 页")


class EnglishPreservedTest(unittest.TestCase):
    """英文句子必须保留单词间空格。"""

    def test_english_sentences_untouched(self):
        for text in ("Hello world", "I love Python code", "Open AI 公司", "the end"):
            with self.subTest(text=text):
                self.assertEqual(_clean_text(text), text)

    def test_english_mixed_with_chinese(self):
        self.assertEqual(_clean_text("Hello world 这个 项目"), "Hello world 这个项目")


class PureNumberGuardTest(unittest.TestCase):
    """纯数字不合并：粘成 50 可能把「五十」改错。"""

    def test_pure_digits_not_joined(self):
        self.assertEqual(_clean_text("5 0 元"), "5 0 元")
        self.assertEqual(_clean_text("1 2 3"), "1 2 3")


class EmptyAndTagTest(unittest.TestCase):
    """空输入与 SenseVoice 标签（原有行为）。"""

    def test_empty(self):
        self.assertEqual(_clean_text(""), "")
        self.assertEqual(_clean_text(None), "")

    def test_tags_removed(self):
        self.assertEqual(_clean_text("<|zh|><|NEUTRAL|>你好"), "你好")


if __name__ == "__main__":
    unittest.main()
