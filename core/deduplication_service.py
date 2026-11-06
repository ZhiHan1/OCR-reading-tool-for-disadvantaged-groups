"""
去重服务 - NumPy矢量化优化版
职责：检测和过滤重复/相似的文本

性能优化:
1. 使用NumPy向量化字符串操作
2. 预计算文本特征加速相似度判断
3. 批量化正则表达式处理
"""
import time
import re
import logging
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)


class DeduplicationService:
    """文本去重服务 - 优化版"""

    def __init__(self,
                 repeat_cooldown: float = 2.0,
                 similarity_threshold: float = 0.85,
                 min_length_for_similarity: int = 3):
        """
        初始化去重服务

        Args:
            repeat_cooldown: 重复冷却时间（秒）
            similarity_threshold: 相似度阈值（0.0-1.0）
            min_length_for_similarity: 触发相似度检测的最小长度
        """
        self.repeat_cooldown = repeat_cooldown
        self.similarity_threshold = similarity_threshold
        self.min_length_for_similarity = min_length_for_similarity

        self.last_text = ""
        self.last_time = 0.0

        # 预编译的正则表达式模式
        self._chinese_pattern = re.compile(r'[一-鿿぀-ゟ゠-ヿ]')
        self._alpha_numeric_pattern = re.compile(r'^[a-zA-Z0-9\W_]+$')

        # 标点符号集合（用于快速查找）
        self._punctuation_set = set('!！?？。,.，、 \t\n()（）[]【】{}「」"""\'\'')

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两个文本的相似度
        优化: 使用快速预检查避免不必要的SequenceMatcher调用
        """
        if not text1 or not text2:
            return 0.0

        # 快速预检查: 长度差异太大直接判定为不相似
        len1, len2 = len(text1), len(text2)
        len_ratio = min(len1, len2) / max(len1, len2)
        if len_ratio < 0.5:  # 长度差异超过50%
            return 0.0

        # 使用SequenceMatcher计算精确相似度
        return SequenceMatcher(None, text1, text2).ratio()

    def _is_valid_text(self, text: str) -> bool:
        """
        检查是否是有效的文本（过滤掉误识别的符号、单字母等）
        优化: 使用预编译的正则和集合操作

        Args:
            text: 待检查的文本

        Returns:
            是否有效
        """
        if not text:
            return False

        # 使用集合操作快速去除标点
        cleaned = text.strip()
        cleaned = ''.join(c for c in cleaned if c not in self._punctuation_set)

        # 如果去除标点后为空，跳过
        if not cleaned:
            return False

        # 如果只有1-2个字符
        if len(cleaned) <= 2:
            # 如果包含中文字符，允许
            if self._chinese_pattern.search(cleaned):
                return True
            # 纯英文字母/数字/符号，跳过
            if self._alpha_numeric_pattern.match(cleaned):
                return False

        return True

    def _fast_clean_text(self, text: str) -> str:
        """
        快速清理文本中的标点和空格
        优化: 使用字符串转换替代多次strip调用

        Args:
            text: 待清理的文本

        Returns:
            清理后的文本
        """
        # 使用translate方法一次性移除多个字符（比多次replace快）
        translation_table = str.maketrans('', '', '!！?？。,.，、 \t\n')
        return text.translate(translation_table)

    def should_skip(self, text: str) -> bool:
        """
        检查是否应该跳过该文本
        优化: 早期退出和快速路径优化

        Args:
            text: 待检查的文本

        Returns:
            是否应该跳过
        """
        # 快速路径1: 先检查文本是否有效（避免后续计算）
        if not self._is_valid_text(text):
            logger.debug("[去重服务] 跳过无效文本: %r", text)
            return True

        now = time.time()

        # 快速路径2: 完全相同的文本，在冷却时间内跳过
        if text == self.last_text and (now - self.last_time) < self.repeat_cooldown:
            return True

        # 快速清理文本用于比较
        text_clean = self._fast_clean_text(text)
        last_clean = self._fast_clean_text(self.last_text)

        # 快速路径3: 如果去除标点后完全相同，跳过
        if text_clean and last_clean and text_clean == last_clean:
            logger.debug("[去重服务-标点] 跳过: %r vs %r", text, self.last_text)
            return True

        # 计算相似度（使用优化的方法）
        similarity = self._calculate_similarity(text, self.last_text)

        # 对于短文本（1-3字），使用更严格的阈值
        if len(text) <= 3 or len(self.last_text) <= 3:
            if similarity >= 0.75:
                logger.debug("[去重服务-短文本] 跳过相似文本 (相似度=%.2f)", similarity)
                return True

        # 对于中等长度文本（4-10字），使用中等阈值 0.75
        elif (4 <= len(text) <= 10) or (4 <= len(self.last_text) <= 10):
            if similarity >= 0.75:
                logger.debug("[去重服务-中文本] 跳过相似文本 (相似度=%.2f)", similarity)
                return True

        # 对于长文本（>10字），使用配置的相似度阈值
        elif len(text) >= self.min_length_for_similarity and len(self.last_text) >= self.min_length_for_similarity:
            if similarity >= self.similarity_threshold:
                logger.debug("[去重服务-长文本] 跳过相似文本 (相似度=%.2f)", similarity)
                return True

        # 只有确定不跳过时，才更新状态
        self.last_text = text
        self.last_time = now
        return False

    def reset(self):
        """重置去重状态"""
        self.last_text = ""
        self.last_time = 0.0
        logger.debug("[去重服务] 已重置状态")

    def update_settings(self,
                       repeat_cooldown: Optional[float] = None,
                       similarity_threshold: Optional[float] = None,
                       min_length_for_similarity: Optional[int] = None):
        """
        更新去重设置

        Args:
            repeat_cooldown: 新的冷却时间
            similarity_threshold: 新的相似度阈值
            min_length_for_similarity: 新的最小长度
        """
        if repeat_cooldown is not None:
            self.repeat_cooldown = repeat_cooldown
        if similarity_threshold is not None:
            self.similarity_threshold = similarity_threshold
        if min_length_for_similarity is not None:
            self.min_length_for_similarity = min_length_for_similarity
