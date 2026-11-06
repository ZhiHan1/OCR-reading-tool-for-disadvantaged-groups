"""
GGUF 翻译模型 - 使用 llama-cpp-python 加载 GGUF 格式的翻译模型
支持模型：HY-MT1.5-1.8B-Q6_K.gguf
"""
from core.translation_service import TranslationModel
import os
import sys
from typing import List
from core.language_utils import language_name
from utils.paths import resolve_path, app_root


class GGUFTranslationModel(TranslationModel):
    """GGUF 格式翻译模型（使用 llama-cpp-python）"""

    def __init__(self, model_path: str = None):
        """
        初始化 GGUF 翻译模型

        Args:
            model_path: GGUF 模型文件路径（可选）
                       如果为 None，则通过 ModelManager 获取活动模型路径
        """
        print("[GGUF翻译] ===== 初始化开始 =====")
        print(f"[GGUF翻译] 运行模式: {'打包模式' if getattr(sys, 'frozen', False) else '开发模式'}")
        if getattr(sys, 'frozen', False):
            print(f"[GGUF翻译] EXE路径: {sys.executable}")
            print(f"[GGUF翻译] EXE目录: {os.path.dirname(sys.executable)}")

        # 如果未指定路径，通过 ModelManager 获取
        if model_path is None:
            print("[GGUF翻译] 未指定模型路径，使用ModelManager获取活动模型...")
            model_path = self._get_model_path_from_manager()

            if model_path is None:
                error_msg = (
                    "未找到翻译模型配置!\n\n"
                    "请在 设置 → 翻译设置 中:\n"
                    "1. 点击'添加模型'按钮\n"
                    "2. 选择GGUF模型文件(.gguf)\n"
                    "3. 启用模型后即可使用翻译功能\n\n"
                    "提示: 如果您还没有模型文件,请先下载翻译模型"
                )
                print(f"[GGUF翻译] 错误: {error_msg}")
                raise FileNotFoundError(error_msg)

        print(f"[GGUF翻译] 模型路径: {model_path}")

        self.model_path = str(resolve_path(model_path))
        self.model = None
        self.loaded = False

        # 验证模型文件是否存在
        print("[GGUF翻译] 验证模型文件...")
        if not os.path.exists(self.model_path):
            error_msg = (
                f"模型文件不存在!\n\n"
                f"路径: {self.model_path}\n\n"
                f"可能的原因:\n"
                f"1. 模型文件已被移动或删除\n"
                f"2. 路径配置错误\n\n"
                f"解决方法:\n"
                f"请在 设置 → 翻译设置 中重新添加模型"
            )
            print(f"[GGUF翻译] 错误: {error_msg}")
            raise FileNotFoundError(error_msg)

        file_size_mb = os.path.getsize(self.model_path) / (1024 * 1024)
        print("[GGUF翻译] 模型文件验证成功")
        print(f"[GGUF翻译] 文件大小: {file_size_mb:.2f} MB")
        print("[GGUF翻译] ===== 初始化完成 =====")

    def _get_model_path_from_manager(self) -> str:
        """通过ModelManager获取活动模型路径（直接读取配置中的active_model_path）"""
        print("[GGUF翻译] " + "=" * 60)
        print("[GGUF翻译] _get_model_path_from_manager() 被调用")
        print("[GGUF翻译] " + "=" * 60)

        try:
            from core.model_manager import ModelManager

            print("[GGUF翻译] 初始化ModelManager...")
            model_manager = ModelManager()

            # 直接获取当前使用的模型路径
            config_path = model_manager.get_active_model_path()

            print(f"[GGUF翻译] get_active_model_path() 返回: {config_path}")

            if config_path is None:
                print("[GGUF翻译] ModelManager中没有活动模型路径")
                return None

            print(f"[GGUF翻译] 配置中的路径: {config_path}")

            # 策略1: 验证配置中的路径
            if os.path.exists(config_path):
                print("[GGUF翻译] [OK] 使用配置路径")
                return config_path

            # 策略2: 配置路径无效，尝试智能查找
            print("[GGUF翻译] 配置路径无效（可能是从其他环境复制的配置）")
            print("[GGUF翻译] 尝试智能查找模型文件...")

            # 获取活动模型信息
            active_model = model_manager.get_active_model()
            if not active_model:
                print("[GGUF翻译] [FAIL] 无法获取活动模型信息")
                return None

            file_name = active_model.get('file_name', '')
            if not file_name:
                print("[GGUF翻译] [FAIL] 配置中没有file_name")
                return None

            print(f"[GGUF翻译] 模型文件名: {file_name}")

            # 确定基础目录
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
                print(f"[GGUF翻译] 打包环境，基础目录: {base_dir}")
            else:
                base_dir = str(app_root())
                print(f"[GGUF翻译] 开发环境，基础目录: {base_dir}")

            # 尝试多个位置
            search_paths = [
                os.path.join(base_dir, file_name),                          # exe同级目录
                os.path.join(base_dir, "models", "local", file_name),       # models/local
                os.path.join(base_dir, "models", file_name),                # models
                config_path,                                                 # 配置中的原路径（再试一次）
            ]

            for idx, search_path in enumerate(search_paths, 1):
                print(f"[GGUF翻译]   位置{idx}: {search_path}")
                if os.path.exists(search_path):
                    print("[GGUF翻译] [OK] 找到模型文件！")
                    print("[GGUF翻译] 建议: 在UI中重新导入模型以更新配置路径")
                    return search_path

            # 都没找到
            print("[GGUF翻译] [FAIL] 在所有位置都未找到模型文件")
            print("[GGUF翻译] 请执行以下操作之一:")
            print("[GGUF翻译]   1. 在UI中重新导入模型文件")
            print(f"[GGUF翻译]   2. 将模型文件 '{file_name}' 复制到以下位置:")
            for path in search_paths[:3]:  # 只显示推荐位置
                print(f"[GGUF翻译]      - {path}")
            return None

        except Exception as e:
            print(f"[GGUF翻译] 使用ModelManager失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def load(self):
        """加载 GGUF 模型"""
        if self.loaded:
            print("[GGUF翻译] 模型已加载，跳过重复加载")
            return

        try:
            print("[GGUF翻译] ===== 开始加载模型 =====")
            print(f"[GGUF翻译] 模型文件: {self.model_path}")

            file_size_mb = os.path.getsize(self.model_path) / (1024 * 1024)
            print(f"[GGUF翻译] 文件大小: {file_size_mb:.2f} MB")

            # 检查llama_cpp是否可用
            print("[GGUF翻译] 检查llama_cpp库...")
            try:
                from llama_cpp import Llama
                print("[GGUF翻译] llama_cpp库导入成功 [OK]")
            except ImportError as e:
                error_msg = (
                    "未安装llama-cpp-python库!\n\n"
                    "这是翻译功能的必需依赖。\n\n"
                    "如果是打包版本，这不应该发生，请联系开发者。\n"
                    "如果是开发环境，请运行: pip install llama-cpp-python"
                )
                print(f"[GGUF翻译] 错误: {error_msg}")
                print(f"[GGUF翻译] 详细错误: {e}")
                raise ImportError(error_msg)

            # 再次检查模型文件（防止加载过程中文件被删除）
            if not os.path.exists(self.model_path):
                error_msg = f"模型文件不存在: {self.model_path}"
                print(f"[GGUF翻译] 错误: {error_msg}")
                raise FileNotFoundError(error_msg)

            print("[GGUF翻译] 正在加载模型...")
            print("[GGUF翻译] 提示: 大模型首次加载可能需要几秒到几分钟，请耐心等待")

            # 加载模型
            self.model = Llama(
                model_path=self.model_path,
                n_ctx=2048,  # 上下文长度
                n_threads=4,  # 使用4个线程
                n_gpu_layers=0,  # CPU模式，如果有GPU可以设置为-1
                verbose=False
            )

            self.loaded = True
            print("[GGUF翻译] ===== 模型加载成功! [OK] =====")

        except FileNotFoundError as e:
            print(f"[GGUF翻译] 文件未找到错误: {e}")
            self.loaded = False
            raise
        except ImportError as e:
            print(f"[GGUF翻译] 导入错误: {e}")
            self.loaded = False
            raise
        except Exception as e:
            print(f"[GGUF翻译] 加载模型失败: {e}")
            print(f"[GGUF翻译] 错误类型: {type(e).__name__}")
            import traceback
            print("[GGUF翻译] 堆栈跟踪:")
            traceback.print_exc()
            self.loaded = False
            raise

    def unload(self):
        """释放模型"""
        if self.model:
            del self.model
            self.model = None
        self.loaded = False
        print("[GGUF翻译] 模型已释放")

    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "zh") -> str:
        """
        翻译文本

        Args:
            text: 要翻译的文本
            source_lang: 源语言（zh=中文, en=英文, ja=日文等）
            target_lang: 目标语言

        Returns:
            翻译后的文本
        """
        if not self.loaded:
            self.load()

        if not text or not text.strip():
            return ""

        try:
            # HY-MT1.5 官方 prompt 模板（腾讯混元翻译模型）：
            #   将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：
            #   <空行>
            #   {source_text}
            # 目标语言用完整中文名（如「中文」「英语」「日语」）。源语言交给模型
            # 自动判断即可，无需在模板里显式标注，这样对「自动检测」最稳。
            target = language_name(target_lang)

            prompt = (
                f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n"
                f"{text}"
            )

            # 官方推荐采样参数：top_k=20, top_p=0.6, repetition_penalty=1.05,
            # temperature=0.7。llama-cpp-python 里对应的参数名是 repeat_penalty。
            response = self.model(
                prompt,
                max_tokens=self._max_tokens_for(prompt),
                temperature=0.7,
                top_p=0.6,
                top_k=20,
                repeat_penalty=1.05,
                stop=["</s>"],  # 停止标记（llama.cpp 会自动在 EOS 停止）
                echo=False  # 不回显输入
            )

            # 提取翻译结果
            if response and 'choices' in response and len(response['choices']) > 0:
                translated_text = response['choices'][0]['text'].strip()
                return translated_text
            else:
                return text

        except Exception as e:
            print(f"[GGUF翻译] 翻译失败: {e}")
            return f"翻译失败: {e}"

    def _max_tokens_for(self, prompt: str) -> int:
        """按真实 token 数计算本次生成上限

        旧实现是 `min(256, max(32, len(text) * 2))`——`min(256, …)` 是个硬上限，
        长文本必被截断。实测（2026-11-05）447 字原文实际需要 420 tokens，
        却只给了 256，`finish_reason` 直接是 `length`，译文后半段丢失。
        详见 KNOWLEDGE/translation.md §4.1（KB-TR-003）。

        现在改为：
        1. 用分词器得到 prompt 的真实 token 数（比按字数估算准得多）；
        2. 输出预留「原文 token 数 × 2 + 64」——翻译输出通常不比原文长太多，
           2 倍是宽松余量（实测 447 字：输出/原文 ≈ 0.94）；
        3. 不超过 `n_ctx` 减去 prompt 后剩余的空间，避免超出上下文报错。

        旧版本连 256 这种封顶都没有时，短文本反而被 `len(text) * 2` 卡得太紧
        （32 字中文约 30 token，只给 64）。这里统一用 token 数，不再受字数影响。
        """
        n_ctx = getattr(self.model, "n_ctx", None)
        # llama-cpp-python 里 n_ctx() 是方法（不是属性）；这里两种形态都兼容
        if callable(n_ctx):
            try:
                n_ctx = n_ctx()
            except Exception:
                n_ctx = None
        try:
            n_ctx = int(n_ctx) if n_ctx else 2048
        except (TypeError, ValueError):
            n_ctx = 2048
        try:
            prompt_tokens = len(self.model.tokenize(prompt.encode("utf-8")))
        except Exception:
            # tokenize 失败（极少见）时退回按字数粗估：中文约 1 字 1 token
            prompt_tokens = len(prompt)

        # 余量：按原文长度给 2 倍，最少 64，最多 1024
        wanted = max(64, min(1024, prompt_tokens * 2))
        # 上下文剩余：留 8 个 token 给结束符，避免刚好顶满
        remaining = n_ctx - prompt_tokens - 8
        return max(32, min(wanted, remaining))

    def translate_batch(self, texts: List[str], source_lang: str = "auto", target_lang: str = "zh") -> List[str]:
        """
        批量翻译（GGUF 本地模型逐条推理，避免一次拼多个句子超出上下文）

        Args:
            texts: 要翻译的文本列表
            source_lang: 源语言
            target_lang: 目标语言

        Returns:
            翻译后的文本列表
        """
        if not texts:
            return []
        # 基类声明为抽象方法，缺少实现会导致无法实例化整个模型
        return [self.translate(text, source_lang, target_lang) for text in texts]

    def name(self) -> str:
        """返回模型显示名称"""
        return "HY-MT 1.5 (本地)"

    def hint(self) -> str:
        """返回模型提示信息"""
        return "本地 GGUF 翻译模型，支持多语言互译，无需联网"

    def supports_batch(self) -> bool:
        """是否支持批量翻译"""
        return True
