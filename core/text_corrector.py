"""
文本修正模块 - 上下文感知版

修正 OCR 识别中的常见错误，但**只在上下文明确时**才修正，绝不盲目替换。

设计原则（参考 PaddleOCR / Tesseract 等成熟 OCR 后处理的通用思路）：
1. 不依赖「单字符 → 单字符」的暴力全局替换——那无法区分「OCR 识别错了」
   和「原文本来就是那个字符」，会为了修一个错误引入更多错误
   （例如把真实数字 66 / 6 / 9 全替换成引号）。
2. 标点恢复基于「成对匹配 + 紧邻中文字符」的上下文，而不是孤立替换。
3. 易混字符（一/|/l/I）只在「前后都是中文字、且落在固定短语里」时才替换，
   绝不碰英文/数字语境。
"""

import re


class TextCorrector:
    """OCR 文本修正器 - 上下文感知版"""

    def __init__(self):
        # 中文字符（含 CJK 扩展，覆盖常用汉字）
        self._chinese_char = r'[\u4e00-\u9fff\u3400-\u4dbf]'

        # ---- 中文标点恢复：英文半角标点 → 中文全角标点（仅中文上下文）----
        # 逗号 / 句号 / 问号 / 感叹号 / 冒号 / 分号
        self._punct_map = {
            ',': '，',
            '.': '。',
            '?': '？',
            '!': '！',
            ':': '：',
            ';': '；',
        }
        # 只在中文字符紧邻时替换（左邻或右邻是中文），避免改英文/数字里的标点
        self._punct_patterns = []
        for eng, chn in self._punct_map.items():
            e = re.escape(eng)
            # 中文 + 英文标点 -> 中文 + 中文标点
            self._punct_patterns.append((re.compile(f'({self._chinese_char}){e}'), f'\\1{chn}'))
            # 英文标点 + 中文 -> 中文标点 + 中文
            self._punct_patterns.append((re.compile(f'{e}({self._chinese_char})'), f'{chn}\\1'))

        # ---- 省略号恢复 ----
        # OCR 常把「…」识别成三个英文点、三个中点、或三个句号。
        # 只在「连续 3 个以上同类点」时合并成「…」，绝不把数字 0 当省略号。
        self._ellipsis_patterns = [
            (re.compile(r'\.{3,}'), '…'),      # ...
            (re.compile(r'·{3,}'), '…'),      # ···
            (re.compile(r'。{3,}'), '…'),     # 。。。
        ]

        # ---- 引号恢复 ----
        # 老版本 OCR 会把中文弯引号「“ ”」识别成 66 / 99，但用数字做全局替换
        # 会毁掉真实数字（66 元、第 6 章、9 楼）。这里改成「成对 + 紧邻中文」：
        # 仅当 66 / 99 成对出现且各自紧邻中文字符时，才恢复为一对弯引号。
        # 若不满足成对条件，宁可保留原样，也不乱改数字。
        self._quote_66 = re.compile(f'({self._chinese_char})66')
        self._quote_99 = re.compile(f'99({self._chinese_char})')

        # ---- 中文数字「一」恢复 ----
        # 竖线 |、小写 l、大写 I、数字 1 在中文语境里常是「一」的误识别。
        # 但 I/l/1 也可能是英文（iPhone、level、1 个）。因此只处理两类安全场景：
        #   A. 字符夹在两个中文字符之间（「第|次」→「第一次」）
        #   B. 落在固定高频短语里（「一 个/次/些/起/下/样/致/定」等）
        self._one_middle_patterns = []
        for ch in ('|', 'l', 'I', '1'):
            c = re.escape(ch)
            self._one_middle_patterns.append(
                (re.compile(f'({self._chinese_char}){c}({self._chinese_char})'), f'\\1一\\2')
            )

        # 固定短语：只替换 |/l/I（它们几乎不会作为数字「1」出现在「1个/1次」里），
        # 且要求前面是中文或行首，避免误伤英文。
        # 数字 1 只在「夹在两个中文字符之间」时才恢复（见 _one_middle_patterns）。
        tail = '(个|次|些|起|下|样|致|定|点|会儿|起)'
        self._one_phrase_patterns = [
            (re.compile(f'(第)[|lI]'), '\\1一'),
            (re.compile(f'(?<={self._chinese_char})[|lI]{tail}'), f'一\\1'),
            (re.compile(f'^[|lI]{tail}'), f'一\\1'),
        ]

        # ---- 空格清理 ----
        # 中文字符之间、中文与中文标点之间不应有空格
        self._space_patterns = [
            (re.compile(f'({self._chinese_char})\\s+({self._chinese_char})'), r'\1\2'),
            (re.compile(f'({self._chinese_char})\\s+([，。！？：；、])'), r'\1\2'),
            (re.compile(f'([，。！？：；、])\\s+({self._chinese_char})'), r'\1\2'),
            (re.compile(r'\s+'), ' '),
        ]

    # ===== 主入口 =====

    def correct(self, text: str) -> str:
        """修正 OCR 识别文本（无副作用，保留不能确定的字符）"""
        if not text or not text.strip():
            return text

        text = self._fix_chinese_number_one(text)
        text = self._fix_ellipsis(text)
        text = self._fix_quotes(text)
        text = self._fix_chinese_punctuation(text)
        text = self._clean_spaces(text)

        return text

    # ===== 各修正步骤 =====

    def _fix_chinese_number_one(self, text: str) -> str:
        """恢复中文数字「一」（仅中文上下文 + 固定短语，不碰英文/数字）"""
        # A. 夹在两个中文字符之间的 |/l/I/1
        for pattern, repl in self._one_middle_patterns:
            text = pattern.sub(repl, text)
        # B. 固定短语
        for pattern, repl in self._one_phrase_patterns:
            text = pattern.sub(repl, text)
        return text

    def _fix_ellipsis(self, text: str) -> str:
        """合并连续点号成省略号「…」"""
        for pattern, repl in self._ellipsis_patterns:
            text = pattern.sub(repl, text)
        return text

    def _fix_quotes(self, text: str) -> str:
        """成对恢复弯引号：仅当 66 与 99 成对出现且各自紧邻中文时。

        注意顺序与配对：先找左引号（中文后跟 66），再找右引号（99 后跟中文）。
        只替换「成对」的引号，落单的数字保留。
        """
        # 用一次性扫描：找到 66 和 99 的位置，配对成引号
        # 简化而安全的策略：仅当文本中同时存在「中文字+66」和「99+中文字」时，
        # 才把第一对 66/99 转成引号。避免把真实数字 66/99 改掉。
        if re.search(f'{self._chinese_char}66', text) and re.search(f'99{self._chinese_char}', text):
            # 只替换最靠左的一对
            text = self._quote_66.sub(r'\1“', text, count=1)
            text = self._quote_99.sub(r'”\1', text, count=1)
        return text

    def _fix_chinese_punctuation(self, text: str) -> str:
        """英文半角标点 → 中文全角标点（仅紧邻中文时）"""
        for pattern, repl in self._punct_patterns:
            text = pattern.sub(repl, text)
        return text

    def _clean_spaces(self, text: str) -> str:
        """清理中文字符之间 / 中文与中文标点之间的多余空格"""
        for pattern, repl in self._space_patterns:
            text = pattern.sub(repl, text)
        return text.strip()


# 全局单例
_corrector = TextCorrector()


def correct_text(text: str) -> str:
    """修正 OCR 识别文本的便捷函数"""
    return _corrector.correct(text)
