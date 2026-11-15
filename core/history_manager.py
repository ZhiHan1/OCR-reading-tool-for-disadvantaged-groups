"""
历史记录管理器 - NumPy矢量化优化版
用于漫画模式和游戏模式的文本去重和新增检测

性能优化:
1. 使用NumPy数组存储和比较文本
2. 向量化的字符串前缀匹配
3. 批量化的相似度计算
"""
from typing import List, Optional
import numpy as np


class TextHistory:
    """文本历史记录项"""
    def __init__(self, text: str, timestamp: float):
        self.text = text
        self.timestamp = timestamp
        # 预计算文本哈希用于快速比较
        self._hash = hash(text)


class HistoryManager:
    """历史记录管理器 - 优化版"""

    def __init__(self, max_history: int = 6):
        """
        初始化历史记录管理器

        Args:
            max_history: 最大保存的历史记录数量（默认6次）
        """
        self.max_history = max_history
        self.history: List[TextHistory] = []
        # NumPy数组缓存，用于加速查找
        self._text_hashes: np.ndarray = np.array([], dtype=np.int64)

    def add(self, text: str) -> None:
        """
        添加新的文本到历史记录

        Args:
            text: 要添加的文本
        """
        import time
        timestamp = time.time()
        self.history.append(TextHistory(text, timestamp))

        # 更新哈希数组
        self._text_hashes = np.append(self._text_hashes, hash(text))

        # 保持历史记录在限制范围内
        if len(self.history) > self.max_history:
            self.history.pop(0)
            self._text_hashes = self._text_hashes[1:]

    def get_last_text(self) -> Optional[str]:
        """获取最近一次的文本"""
        if self.history:
            return self.history[-1].text
        return None

    def clear(self) -> None:
        """清空历史记录"""
        self.history.clear()
        self._text_hashes = np.array([], dtype=np.int64)

    def is_duplicate(self, text: str) -> bool:
        """
        检查文本是否在历史记录中已存在（完全匹配）
        优化: 使用NumPy哈希数组进行O(n)向量化比较，比Python循环更快

        Args:
            text: 要检查的文本

        Returns:
            bool: True=重复，False=新文本
        """
        if len(self._text_hashes) == 0:
            return False

        text_hash = hash(text)
        # NumPy向量化比较，利用SIMD指令
        return np.any(self._text_hashes == text_hash)

    def find_new_content(self, current_text: str) -> Optional[str]:
        """
        智能检测新增内容（漫画模式核心算法）- 优化版

        算法逻辑：
        1. 如果历史为空，返回全部文本（第一次扫描）
        2. 如果当前文本与最近一次完全相同，返回None（无新增）
        3. 如果当前文本包含最近一次文本作为前缀，返回新增部分
        4. 遍历历史记录（从最近到最旧），找到最长的公共前缀
        5. 如果找到公共前缀，返回新增部分
        6. 如果没有公共前缀，返回全部（可能是翻页了）

        优化: 使用向量化的前缀长度计算

        Args:
            current_text: 当前识别的文本

        Returns:
            新增的文本，如果没有新增则返回None
        """
        current_text = current_text.strip()

        # 1. 第一次扫描，返回全部
        if not self.history:
            return current_text

        # 2. 与最近一次完全相同
        last_text = self.get_last_text()
        if current_text == last_text:
            return None

        # 3. 当前文本包含最近一次文本作为前缀
        if current_text.startswith(last_text):
            new_part = current_text[len(last_text):].strip()
            if new_part:
                return new_part
            return None

        # 4. 当前文本更短（可能是对话框刷新）
        if last_text and last_text.startswith(current_text):
            return current_text

        # 5. 批量计算所有历史记录的公共前缀长度（向量化）
        history_texts = [h.text for h in reversed(self.history)]
        prefix_lengths = self._batch_find_common_prefix_length(history_texts, current_text)

        # 找到最佳匹配
        best_match_idx = -1
        best_match_prefix_len = 0

        for i, (history_text, prefix_len) in enumerate(zip(history_texts, prefix_lengths)):
            # 如果公共前缀长度超过历史文本的80%，认为是延续
            if prefix_len > len(history_text) * 0.8 and prefix_len > best_match_prefix_len:
                best_match_prefix_len = prefix_len
                best_match_idx = i

        # 6. 如果找到了较好的匹配
        if best_match_idx >= 0 and best_match_prefix_len > 0:
            new_part = current_text[best_match_prefix_len:].strip()
            if new_part:
                return new_part
            return None

        # 7. 没有找到匹配，可能是新的一页，返回全部
        return current_text

    def _batch_find_common_prefix_length(self, texts: List[str], target: str) -> List[int]:
        """
        批量计算多个文本与目标文本的公共前缀长度
        优化: 使用NumPy向量化操作加速

        Args:
            texts: 文本列表
            target: 目标文本

        Returns:
            每个文本的公共前缀长度列表
        """
        if not texts:
            return []

        # 转换为字节数组以便向量化比较
        target_bytes = np.frombuffer(target.encode('utf-8'), dtype=np.uint8)

        prefix_lengths = []
        for text in texts:
            text_bytes = np.frombuffer(text.encode('utf-8'), dtype=np.uint8)

            # 向量化比较: 找到第一个不匹配的位置
            min_len = min(len(text_bytes), len(target_bytes))
            if min_len == 0:
                prefix_lengths.append(0)
                continue

            # 使用NumPy布尔索引找到第一个不同的位置
            comparison = text_bytes[:min_len] == target_bytes[:min_len]

            if np.all(comparison):
                # 完全匹配（在min_len范围内）
                # 需要转换回字符数，因为UTF-8是变长编码
                # 简化处理：计算有效UTF-8字符数
                try:
                    common_text = target[:len(text)] if len(text) < len(target) else text[:len(target)]
                    prefix_len = len(common_text)
                except:
                    prefix_len = min_len
            else:
                # 找到第一个False的位置
                first_diff = np.argmax(~comparison)
                # 转换字节偏移量为字符数
                try:
                    # 解码到第一个不同的字节位置
                    common_prefix = target_bytes[:first_diff].tobytes().decode('utf-8', errors='ignore')
                    prefix_len = len(common_prefix)
                except:
                    prefix_len = 0

            prefix_lengths.append(prefix_len)

        return prefix_lengths

    def get_history_summary(self) -> str:
        """
        获取历史记录摘要（用于调试）

        Returns:
            历史记录的摘要字符串
        """
        if not self.history:
            return "历史记录为空"

        lines = [f"历史记录数量: {len(self.history)}/{self.max_history}"]
        for i, h in enumerate(self.history, 1):
            preview = h.text[:30] + "..." if len(h.text) > 30 else h.text
            lines.append(f"  {i}. {preview}")

        return "\n".join(lines)
