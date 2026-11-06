"""
翻译服务 - 统一翻译接口，支持多种翻译模型
职责：提供统一的翻译接口，管理不同的翻译模型
"""
from typing import Optional, List, Callable
from abc import ABC, abstractmethod
import threading


class TranslationModel(ABC):
    """翻译模型基类 - 所有翻译模型必须实现此接口"""

    @abstractmethod
    def load(self):
        """加载模型"""
        pass

    @abstractmethod
    def unload(self):
        """释放模型"""
        pass

    @abstractmethod
    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "zh") -> str:
        """
        单条翻译

        Args:
            text: 要翻译的文本
            source_lang: 源语言，auto表示自动检测
            target_lang: 目标语言

        Returns:
            翻译后的文本
        """
        pass

    @abstractmethod
    def translate_batch(self, texts: List[str], source_lang: str = "auto", target_lang: str = "zh") -> List[str]:
        """
        批量翻译

        Args:
            texts: 要翻译的文本列表
            source_lang: 源语言
            target_lang: 目标语言

        Returns:
            翻译后的文本列表
        """
        return [self.translate(text, source_lang, target_lang) for text in texts]

    @abstractmethod
    def supports_batch(self) -> bool:
        """返回是否支持批量翻译"""
        pass

    @abstractmethod
    def hint(self) -> str:
        """返回模型提示文字"""
        pass

    @abstractmethod
    def name(self) -> str:
        """返回模型名称"""
        pass


class TranslationService:
    """翻译服务 - 管理翻译模型和提供统一接口"""

    # 可用模型注册表（键 = 配置里的 model 值）
    AVAILABLE_MODELS = {}

    # 后端注册表：键 → (模块名, 类名, 显示名用于日志)。
    # 用「延迟导入」是为了缺依赖时不影响启动：任何一个 import 失败只打印一行，
    # 其余后端照常注册。
    _BACKENDS = (
        ("gguf", "core.gguf_translation_model", "GGUFTranslationModel", "GGUF模型"),
        ("opus", "core.opus_translation_model", "OPUSTranslationModel", "OPUS模型"),
        ("google", "core.google_translation_model", "GoogleTranslationModel", "Google翻译"),
        ("custom_openai", "core.custom_openai_translation_model",
         "CustomOpenAITranslationModel", "自定义在线模型"),
    )

    @classmethod
    def _register_backends(cls):
        """把 _BACKENDS 里所有能成功导入的模型注册进 AVAILABLE_MODELS（幂等）"""
        import importlib

        for key, module_name, class_name, label in cls._BACKENDS:
            if key in cls.AVAILABLE_MODELS:
                continue
            try:
                module = importlib.import_module(module_name)
                cls.AVAILABLE_MODELS[key] = getattr(module, class_name)
                if key == "gguf":
                    print("[翻译服务] GGUF模型已注册")
            except ImportError as e:
                print(f"[翻译服务] {label}不可用: {e}")

    def __init__(self, model_name: str = "gguf", source_lang: str = "auto", target_lang: str = "zh"):
        """
        初始化翻译服务

        模型注册在这里完成，但模型本身延迟到首次翻译时才加载，
        避免程序启动时就加载大模型导致界面卡顿。

        Args:
            model_name: 模型名称
            source_lang: 源语言
            target_lang: 目标语言
        """
        # 尝试注册可用模型（延迟导入，缺少依赖时不影响启动）
        self._register_backends()

        self.model_name = model_name
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.current_model: Optional[TranslationModel] = None
        self.callback: Optional[Callable] = None
        # 用可重入锁：translate_with_lang() 持锁运行，内部 _ensure_model_loaded() 也要持锁
        self._lock = threading.RLock()

        # 模型后台保持管理（避免重复加载）
        self._idle_timer = None
        # 闲置多久卸载模型（秒）。GGUF 权重约 1.4GB，默认只保留一分钟；
        # 设为 0 表示不自动卸载，模型常驻。
        try:
            from core.config_manager import get_config

            self._idle_timeout = max(
                0, int(get_config().get("translation", "idle_unload_seconds", default=60))
            )
        except Exception:
            self._idle_timeout = 60

        # 非阻塞释放标记：切换/卸载模型撞上正在推理时置位，
        # 由推理线程结束前真正释放（避免 UI 线程等锁卡住几十秒）
        self._release_pending = False

    def _ensure_model_loaded(self) -> bool:
        """确保模型已加载（调用方需持有 self._lock）"""
        if self.current_model is not None:
            return True

        if self.model_name not in self.AVAILABLE_MODELS:
            # 配置的模型不可用，尝试任一可用模型
            if not self.AVAILABLE_MODELS:
                return False
            self.model_name = next(iter(self.AVAILABLE_MODELS))

        self._load_model(self.model_name)
        return self.current_model is not None

    def _load_model(self, model_name: str):
        """加载指定模型"""
        if model_name not in self.AVAILABLE_MODELS:
            print(f"[翻译服务] 模型 {model_name} 不存在")
            # 尝试使用第一个可用模型
            if self.AVAILABLE_MODELS:
                model_name = list(self.AVAILABLE_MODELS.keys())[0]
                print(f"[翻译服务] 使用默认模型: {model_name}")
            else:
                print("[翻译服务] 错误: 没有可用的翻译模型")
                return

        try:
            model_class = self.AVAILABLE_MODELS[model_name]
            model = model_class()
            model.load()
            self.current_model = model
            self.model_name = model_name
            print(f"[翻译服务] 已加载模型: {model.name()}")
        except Exception as e:
            self._report_load_failure(model_name, model_class, e)
            self.current_model = None

    def _report_load_failure(self, model_name: str, model_class, error: Exception):
        """输出模型加载失败日志，并针对「抽象方法未实现」给出可操作的提示

        历史上出现过 GGUFTranslationModel 漏实现基类抽象方法 translate_batch，
        导致实例化直接抛 TypeError。异常被 except 吞掉后，日志里只有一行
        "Can't instantiate abstract class ..."，很难看出是翻译功能整体失效，
        用户侧表现为「翻译没反应」。这里把这类错误单独识别并明确指出来，
        避免以后再花时间排查。
        """
        import traceback

        print(f"[翻译服务] 加载模型失败: {error}")
        print(f"[翻译服务] 模型类: {model_class.__module__}.{model_class.__name__}")

        abstract_methods = getattr(model_class, "__abstractmethods__", None)
        if abstract_methods:
            missing = sorted(abstract_methods)
            print("[翻译服务] 原因: 该模型类未实现基类 TranslationModel 的抽象方法")
            print(f"[翻译服务] 缺少的方法: {', '.join(missing)}")
            print(
                "[翻译服务] 修复: 在 "
                f"{model_class.__module__} 中补上这些方法后即可恢复翻译功能"
            )
        else:
            print("[翻译服务] 堆栈跟踪:")
            traceback.print_exc()

        # 全部模型都不可用时，用户会看到「翻译没反应」，这里明确说明
        if not self.AVAILABLE_MODELS:
            print("[翻译服务] 当前没有任何可用翻译模型，翻译功能将不可用")

    def switch_model(self, model_name: str):
        """切换翻译模型（只改配置，加载推迟到下次翻译）

        不在 UI 线程里同步加载模型：GGUF 模型首次加载可能耗时几十秒到几分钟，
        原来在下拉框回调里直接 load() 会把整个界面卡住。加载交给 translate()
        里的 _ensure_model_loaded() 懒加载（发生在后台翻译线程）。

        Args:
            model_name: 新模型名称
        """
        self.model_name = model_name

        # 释放旧模型（非阻塞：推理中只打标记，由推理线程结束后释放）
        self._release_model_nonblocking()

        print(f"[翻译服务] 已切换模型配置: {model_name}（下次翻译时加载）")

    def _release_model_nonblocking(self) -> bool:
        """非阻塞地释放当前模型

        推理线程持有 `_lock` 时（GGUF 一次推理可能几十秒），UI 线程不能干等，
        只置 `_release_pending`，由推理线程在 `_translate_internal` 的 finally
        里真正释放。

        Returns:
            bool: True=已立即释放完成，False=已标记为推理结束后释放
        """
        if self._lock.acquire(blocking=False):
            try:
                self._release_model_locked()
            finally:
                self._lock.release()
            return True

        self._release_pending = True
        print("[翻译服务] 模型正在使用中，已标记为推理结束后释放")
        return False

    def _release_model_locked(self):
        """释放当前模型（调用方需持有 self._lock）"""
        if self.current_model is None:
            return
        model, self.current_model = self.current_model, None
        try:
            model.unload()
        except Exception as e:
            print(f"[翻译服务] 释放旧模型失败: {e}")

    def unload_model(self):
        """释放当前模型，但不立即加载新模型

        用于模型配置（启用/禁用/删除/切换服务商）发生变化后：真正的加载交给
        下次翻译时的 _ensure_model_loaded() 懒加载，避免在 UI 线程上同步加载
        大模型把界面卡死。
        """
        self._release_model_nonblocking()

        # 取消未触发的空闲卸载定时器，避免它稍后再操作一次
        if self._idle_timer:
            try:
                self._idle_timer.cancel()
            except Exception:
                pass
            self._idle_timer = None

        print("[翻译服务] 已释放模型，将在下次翻译时按最新配置重新加载")

    def translate(self, text: str, callback: Optional[Callable] = None) -> str:
        """
        翻译文本

        Args:
            text: 要翻译的文本
            callback: 翻译完成后的回调函数

        Returns:
            翻译后的文本
        """
        result = self._translate_internal(text, self.source_lang, self.target_lang)
        if callback:
            callback(result)
        return result

    def _translate_internal(self, text: str, source_lang: str, target_lang: str) -> str:
        """统一执行单条翻译，避免默认语言和显式语言两套逻辑漂移。"""
        if not text or not text.strip():
            return ""

        try:
            with self._lock:
                if not self._ensure_model_loaded():
                    return text
                self._reset_idle_timer()
                try:
                    return self.current_model.translate(
                        text, source_lang=source_lang, target_lang=target_lang
                    )
                finally:
                    if self._release_pending:
                        self._release_pending = False
                        self._release_model_locked()
        except Exception as e:
            print(f"[翻译服务] 翻译失败: {e}")
            return text

    def _reset_idle_timer(self):
        """重置空闲计时器"""
        if self._idle_timeout <= 0:
            return
        # 取消旧的定时器
        if self._idle_timer:
            self._idle_timer.cancel()

        # 设置新的定时器（默认 60 秒无活动后卸载模型）
        self._idle_timer = threading.Timer(self._idle_timeout, self._unload_idle_model)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _unload_idle_model(self):
        """卸载空闲模型"""
        with self._lock:
            if self.current_model:
                try:
                    print(f"[翻译服务] {self._idle_timeout} 秒无活动，卸载模型释放内存")
                    self.current_model.unload()
                    self.current_model = None
                except Exception as e:
                    print(f"[翻译服务] 卸载模型失败: {e}")

    def translate_batch(self, texts: List[str], callback: Optional[Callable] = None) -> List[str]:
        """
        批量翻译

        Args:
            texts: 要翻译的文本列表
            callback: 翻译完成后的回调函数

        Returns:
            翻译后的文本列表
        """
        if not texts:
            return []

        try:
            with self._lock:
                if not self._ensure_model_loaded():
                    return texts

                results = self.current_model.translate_batch(
                    texts,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang
                )

            # 调用回调
            if callback:
                callback(results)

            return results

        except Exception as e:
            print(f"[翻译服务] 批量翻译失败: {e}")
            return texts

    def translate_with_lang(self, text: str, source_lang: str = "auto", target_lang: str = "zh") -> str:
        """带语言参数的翻译（供 TranslationManager 等调用方使用）

        与 translate() 的区别：source/target 由调用方逐次传入，而非实例默认值。
        """
        return self._translate_internal(text, source_lang, target_lang)

    def update_settings(self, source_lang: Optional[str] = None, target_lang: Optional[str] = None):
        """
        更新翻译设置

        Args:
            source_lang: 源语言
            target_lang: 目标语言
        """
        if source_lang is not None:
            self.source_lang = source_lang
        if target_lang is not None:
            self.target_lang = target_lang

        print(f"[翻译服务] 已更新设置: {self.source_lang} -> {self.target_lang}")

    def get_available_models(self) -> List[dict]:
        """
        获取可用模型列表

        Returns:
            模型信息列表 [{"name": "模型名", "hint": "提示文字"}, ...]
        """
        models = []
        for model_key, model_class in self.AVAILABLE_MODELS.items():
            try:
                model = model_class()
                models.append({
                    "key": model_key,
                    "name": model.name(),
                    "hint": model.hint(),
                    "supports_batch": model.supports_batch()
                })
            except Exception as e:
                print(f"[翻译服务] 获取模型 {model_key} 信息失败: {e}")

        return models

    def get_current_model_info(self) -> dict:
        """获取当前模型信息"""
        if self.current_model:
            return {
                "key": self.model_name,
                "name": self.current_model.name(),
                "hint": self.current_model.hint(),
                "supports_batch": self.current_model.supports_batch()
            }
        return {}

    def stop(self):
        """停止翻译服务并释放资源"""
        with self._lock:
            if self.current_model:
                try:
                    self.current_model.unload()
                except Exception as e:
                    print(f"[翻译服务] 停止服务时出错: {e}")
                finally:
                    self.current_model = None

        print("[翻译服务] 已停止")
