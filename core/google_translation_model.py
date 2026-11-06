"""
Google 翻译 API 实现（使用 googletrans 库）
无需下载大型模型，直接调用在线翻译
"""
from typing import List
from core.translation_service import TranslationModel


class GoogleTranslationModel(TranslationModel):
    """Google 翻译模型（在线API）"""

    def __init__(self):
        self.translator = None
        self.loaded = False

    def load(self):
        """加载翻译器"""
        try:
            from googletrans import Translator
            self.translator = Translator()
            self.loaded = True
            print("[Google翻译] 翻译器已就绪")
        except ImportError:
            raise ImportError("缺少依赖: 请安装 googletrans\n"
                            "运行: pip install googletrans==4.0.0-rc1")
        except Exception as e:
            print(f"[Google翻译] 初始化失败: {e}")
            raise

    def unload(self):
        """释放翻译器"""
        self.translator = None
        self.loaded = False
        print("[Google翻译] 翻译器已释放")

    def translate(self, text: str, source_lang: str = "zh-cn", target_lang: str = "en") -> str:
        """
        单条翻译

        Args:
            text: 要翻译的文本
            source_lang: 源语言（zh-cn=中文简体）
            target_lang: 目标语言（en=英文）

        Returns:
            翻译后的文本
        """
        if not self.loaded or not self.translator:
            return f"[翻译器未加载] {text}"

        try:
            # 执行翻译
            result = self.translator.translate(text, src=source_lang, dest=target_lang)
            return result.text

        except Exception as e:
            print(f"[Google翻译] 翻译失败: {e}")
            return text

    def translate_batch(self, texts: List[str], source_lang: str = "zh-cn", target_lang: str = "en") -> List[str]:
        """
        批量翻译

        Args:
            texts: 要翻译的文本列表
            source_lang: 源语言
            target_lang: 目标语言

        Returns:
            翻译后的文本列表
        """
        if not self.loaded or not self.translator:
            return [f"[翻译器未加载] {text}" for text in texts]

        try:
            # 批量翻译
            results = self.translator.translate(texts, src=source_lang, dest=target_lang)
            return [r.text for r in results]

        except Exception as e:
            print(f"[Google翻译] 批量翻译失败: {e}")
            # 单条翻译作为备选
            return [self.translate(text, source_lang, target_lang) for text in texts]

    def supports_batch(self) -> bool:
        """支持批量翻译"""
        return True

    def hint(self) -> str:
        """返回模型提示"""
        return "Google 翻译在线API（需要网络连接）"

    def name(self) -> str:
        """返回模型名称"""
        return "Google 翻译"
