"""
OPUS-MT 翻译模型实现
使用 HuggingFace transformers 加载本地 OPUS-MT 模型
"""
from typing import List
from core.translation_service import TranslationModel
import os
from utils.paths import resolve_path


class OPUSTranslationModel(TranslationModel):
    """OPUS-MT 翻译模型"""

    def __init__(self, model_path: str = "models/OPUS-MT/opus-mt-zh-en"):
        """
        初始化 OPUS-MT 模型

        Args:
            model_path: 模型路径（支持相对路径，相对于exe目录）
        """
        self.model_path = str(resolve_path(model_path))

        self.model = None
        self.tokenizer = None
        self.loaded = False
        print(f"[OPUS-MT] 模型路径已解析: {self.model_path}")

    def load(self):
        """加载模型"""
        try:
            from transformers import MarianMTModel, MarianTokenizer

            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"模型路径不存在: {self.model_path}")

            print(f"[OPUS-MT] 正在加载模型: {self.model_path}")

            # 加载分词器
            self.tokenizer = MarianTokenizer.from_pretrained(
                self.model_path,
                local_files_only=True
            )

            # 加载模型
            self.model = MarianMTModel.from_pretrained(
                self.model_path,
                local_files_only=True
            )

            self.loaded = True
            print("[OPUS-MT] 模型加载成功")

        except ImportError:
            raise ImportError("缺少依赖: 请安装 transformers 和 torch\n"
                            "运行: pip install transformers torch sentencepiece")
        except Exception as e:
            print(f"[OPUS-MT] 加载模型失败: {e}")
            raise

    def unload(self):
        """释放模型"""
        if self.model:
            del self.model
            self.model = None
        if self.tokenizer:
            del self.tokenizer
            self.tokenizer = None
        self.loaded = False
        print("[OPUS-MT] 模型已释放")

    def translate(self, text: str, source_lang: str = "zh", target_lang: str = "en") -> str:
        """
        单条翻译

        Args:
            text: 要翻译的文本
            source_lang: 源语言（本模型固定为zh）
            target_lang: 目标语言（本模型固定为en）

        Returns:
            翻译后的文本
        """
        if not self.loaded or not self.model or not self.tokenizer:
            return f"[模型未加载] {text}"

        try:
            # 编码输入
            inputs = self.tokenizer(text, return_tensors="pt", padding=True)

            # 生成翻译
            translated = self.model.generate(**inputs)

            # 解码输出
            result = self.tokenizer.decode(translated[0], skip_special_tokens=True)

            return result

        except Exception as e:
            print(f"[OPUS-MT] 翻译失败: {e}")
            return text

    def translate_batch(self, texts: List[str], source_lang: str = "zh", target_lang: str = "en") -> List[str]:
        """
        批量翻译

        Args:
            texts: 要翻译的文本列表
            source_lang: 源语言
            target_lang: 目标语言

        Returns:
            翻译后的文本列表
        """
        if not self.loaded or not self.model or not self.tokenizer:
            return [f"[模型未加载] {text}" for text in texts]

        try:
            # 批量编码
            inputs = self.tokenizer(texts, return_tensors="pt", padding=True, truncation=True)

            # 批量生成翻译
            translated = self.model.generate(**inputs)

            # 批量解码
            results = [
                self.tokenizer.decode(t, skip_special_tokens=True)
                for t in translated
            ]

            return results

        except Exception as e:
            print(f"[OPUS-MT] 批量翻译失败: {e}")
            return texts

    def supports_batch(self) -> bool:
        """支持批量翻译"""
        return True

    def hint(self) -> str:
        """返回模型提示"""
        return "OPUS-MT 中文→英文翻译模型（离线本地模型）"

    def name(self) -> str:
        """返回模型名称"""
        return "OPUS-MT (中文→英文)"
