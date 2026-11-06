"""
自动翻译管理器

职责：
- 管理本地 GGUF 翻译模型的加载（懒加载，只加载一次）
- 维护翻译请求队列和结果队列
- 在后台线程中依次执行翻译，避免阻塞 UI
- 判断各功能是否启用自动翻译

注意：本模块只负责翻译，不直接操作 UI。
结果通过 result_queue 交给 UI 层显示。
"""
import queue
import threading
import logging
from typing import List, Optional, Tuple
from core.language_utils import language_display_name, normalize_lang

logger = logging.getLogger(__name__)


# 手动翻译（翻译页）的伪模式名，用于把结果和自动翻译区分开
MANUAL_MODE = "__manual__"


# 模式 -> 翻译开关配置键
_MODE_CONFIG_KEY = {
    "func1": "func1_enabled",
    "func2": "func2_enabled",
    "func3": "func3_enabled",
    "manga": "manga_enabled",
    "game": "game_enabled",
}


class TranslationManager:
    """自动翻译管理器"""

    def __init__(self, config, tts_service, translation_service=None):
        """
        Args:
            config: 配置管理器
            tts_service: TTS 服务（首次翻译时需要停止原文朗读）
            translation_service: 多后端翻译服务；为 None 时内部新建
        """
        self.config = config
        self.tts = tts_service

        # 多后端翻译服务（本地 GGUF / Google / OPUS / 自定义在线服务商）
        # 这里统一作为「翻译模型」的入口，取代原来硬编码 llama_cpp 的加载逻辑。
        if translation_service is None:
            from core.translation_service import TranslationService
            translation_service = TranslationService(
                model_name=config.get("translation", "model", default="gguf"),
                source_lang=config.get("translation", "source_lang", default="auto"),
                target_lang=config.get("translation", "target_lang", default="zh"),
            )
        self.translation_service = translation_service

        # 兼容旧接口：is_model_loaded 等外部可能引用 self.model
        self.model = None

        # 翻译请求队列（待翻译文本）
        self.request_queue: queue.Queue = queue.Queue()

        # 实时扫描只处理最新请求；手动翻译仍按队列顺序保留。
        self._queue_lock = threading.Lock()
        self._auto_generation = 0
        self._last_auto_request = {}

        # 翻译结果队列（(result, mode)）
        self.result_queue: queue.Queue = queue.Queue()

        # 工作线程
        self._worker_running = False
        self._worker_thread: Optional[threading.Thread] = None

    # ===== 模型管理 =====

    @property
    def is_model_loaded(self) -> bool:
        """当前后端模型是否已加载"""
        return self.translation_service.current_model is not None

    def get_model_key(self) -> str:
        """当前翻译后端 key（gguf / google / opus / custom_openai / 服务商 id）"""
        return self.translation_service.model_name

    def switch_model(self, model_key: str):
        """切换翻译后端（只改配置，加载推迟到下次翻译）"""
        self.translation_service.switch_model(model_key)

    def ensure_model_loaded(self) -> bool:
        """确保当前后端模型已加载（交给 TranslationService 懒加载）"""
        return self.translation_service._ensure_model_loaded()

    def reset_model(self) -> bool:
        """丢弃已加载的模型，下次翻译时按最新后端重新加载

        用户启用/禁用/删除本地模型、或切换服务商后必须调用：
        懒加载过一次后会把模型实例一直留在内存里，不清理就会出现
        「换了模型仍用旧模型」，或者「已删除的模型文件仍被占用」。
        """
        self.translation_service.unload_model()
        return True

    def generate(self, text: str, source_lang: str = "auto", target_lang: str = "zh") -> Optional[str]:
        """
        调用当前后端翻译（同步调用，异常会向上抛出）

        Args:
            text: 原文（不再是预拼好的 prompt，语言参数单独传入）
            source_lang: 源语言代码
            target_lang: 目标语言代码

        Returns:
            翻译结果，失败时返回 None
        """
        result = self.translation_service.translate_with_lang(
            text, source_lang=source_lang, target_lang=target_lang
        )
        return result or None

    # ===== 自动翻译队列 =====

    def auto_translate_if_enabled(self, text: str, mode: str) -> bool:
        """
        自动翻译 OCR 结果（如果启用了翻译功能）

        Args:
            text: OCR识别的文本
            mode: 模式标识 ("func1", "func2", "func3", "manga", "game")

        Returns:
            bool: True=启用了翻译（调用者不要朗读原文），False=未启用翻译
        """
        config_key = _MODE_CONFIG_KEY.get(mode)
        if not config_key:
            print(f"[自动翻译] 未知模式: {mode}")
            return False

        if not self.config.get("translation", config_key, default=False):
            logger.debug("[自动翻译] %s 模式翻译未启用，将朗读原文", mode)
            return False

        if not text or not text.strip():
            return False

        logger.debug("[自动翻译] %s 模式已启用翻译，开始翻译", mode)

        source_lang = self.config.get("translation", "source_lang", default="auto")
        target_lang = self.config.get("translation", "target_lang", default="zh")

        # 同一模式、同一语言方向下，相同文本不重复提交翻译。
        request_key = (mode, source_lang, target_lang, text.strip())
        with self._queue_lock:
            is_duplicate = self._last_auto_request.get(mode) == request_key
            if not is_duplicate:
                self._last_auto_request[mode] = request_key

        # 重复请求仍返回 True，避免调用方把原文再次朗读。
        if is_duplicate:
            return True

        # 只在没有待处理请求时停止原文朗读。
        is_first_translation = self.request_queue.qsize() == 0
        if is_first_translation:
            try:
                self.tts.stop_current()
                logger.debug("[自动翻译] 首次翻译，已停止原文朗读")
            except Exception as e:
                print(f"[自动翻译] 停止朗读失败: {e}")
        else:
            logger.debug("[自动翻译] 翻译队列中还有 %s 个任务，不打断朗读", self.request_queue.qsize())

        logger.debug("[自动翻译] 源语言: %s, 目标语言: %s", source_lang, target_lang)

        self.enqueue_latest(text, source_lang, target_lang, mode)
        return True

    def enqueue(self, text: str, source_lang: str, target_lang: str, mode: str):
        """将翻译请求加入队列"""
        self.request_queue.put({
            "text": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "mode": mode,
        })
        print(f"[自动翻译] 翻译请求已加入队列，队列长度: {self.request_queue.qsize()}")

    def enqueue_latest(self, text: str, source_lang: str, target_lang: str, mode: str):
        """加入实时翻译请求，并丢弃尚未开始处理的旧实时请求。"""
        with self._queue_lock:
            self._auto_generation += 1
            generation = self._auto_generation

            # 手动翻译请求必须保留，只清理自动翻译请求。
            retained = []
            while True:
                try:
                    request = self.request_queue.get_nowait()
                except queue.Empty:
                    break
                if request.get("mode") == MANUAL_MODE:
                    retained.append(request)
                self.request_queue.task_done()

            for request in retained:
                self.request_queue.put_nowait(request)

            self.request_queue.put_nowait({
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "mode": mode,
                "generation": generation,
            })

        logger.debug("[自动翻译] 已更新为最新请求，队列长度: %s", self.request_queue.qsize())

    def enqueue_manual(self, text: str, source_lang: str, target_lang: str):
        """把「翻译页手动翻译」交给后台工作线程

        UI 线程不能直接跑模型推理（GGUF 一次推理可能几十秒），
        结果通过 result_queue 回到主线程轮询处理。
        """
        print("[翻译] 手动翻译请求已提交给后台线程")
        self.enqueue(text, source_lang, target_lang, MANUAL_MODE)

    def start_worker(self):
        """启动翻译工作线程"""
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        print("[翻译系统] 翻译工作线程已启动")

    def stop_worker(self):
        """停止翻译工作线程"""
        if not self._worker_running:
            return
        print("[翻译系统] 正在停止翻译工作线程...")
        self._worker_running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
        print("[翻译系统] 翻译工作线程已停止")

    def drain_results(self) -> List[Tuple[str, str]]:
        """取出当前所有已完成的翻译结果"""
        results = []
        while not self.result_queue.empty():
            try:
                results.append(self.result_queue.get_nowait())
            except queue.Empty:
                break
        return results

    def _worker(self):
        """翻译工作线程：一次只处理一个请求"""
        print("[翻译工作线程] 已启动")

        while self._worker_running:
            try:
                request = self.request_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[翻译工作线程] 取任务失败: {e}")
                continue

            try:
                if (
                    request.get("mode") != MANUAL_MODE
                    and request.get("generation") != self._auto_generation
                ):
                    continue
                self._translate_and_queue(
                    request["text"],
                    request["source_lang"],
                    request["target_lang"],
                    request["mode"],
                )
            except Exception as e:
                print(f"[翻译工作线程] 处理任务时出错: {e}")

        print("[翻译工作线程] 已停止")

    def build_prompt(self, text: str, source_lang: str, target_lang: str) -> str:
        """构建翻译提示词

        自动翻译（后台队列）和手动翻译（翻译页）共用这一份，避免两处格式漂移：
        改动一处就同时生效，不会出现「自动翻译好使、手动翻译乱码」这类问题。

        Args:
            text: 原文
            source_lang: 源语言（代码或中文名称，如 "en" / "英语"）
            target_lang: 目标语言（代码或中文名称，如 "zh" / "中文"）
        """
        source = normalize_lang(source_lang)
        target = normalize_lang(target_lang)

        if target == "en":
            return f"Translate this Chinese to English: {text}"
        if target == "zh":
            return f"将下面的英文翻译成中文：\n{text}"
        if source == "auto":
            return f"将下面的文本翻译成{language_display_name(target)}：\n{text}"
        return (
            f"将下面的{language_display_name(source)}"
            f"翻译成{language_display_name(target)}：\n{text}"
        )

    def _report_manual_failure(self, mode: str):
        """手动翻译失败时给 UI 一个明确信号

        自动翻译失败只是这一帧没结果，无需提示；手动翻译如果什么都不回，
        界面会永远停在"正在翻译..."（按钮也一直是禁用状态）。
        """
        if mode == MANUAL_MODE:
            self.result_queue.put(("", mode))

    def _translate_and_queue(self, text: str, source_lang: str, target_lang: str, mode: str):
        """执行翻译并把结果放入结果队列（在工作线程中调用）"""
        logger.debug("[翻译工作线程] 开始处理翻译请求，剩余队列: %s", self.request_queue.qsize())

        if not text or not text.strip():
            self._report_manual_failure(mode)
            return

        if not self.ensure_model_loaded():
            print("[翻译] 模型加载失败，跳过翻译")
            self._report_manual_failure(mode)
            return

        logger.debug("[翻译] 开始翻译")

        try:
            result = self.generate(text, source_lang, target_lang)
        except Exception as e:
            print(f"[翻译] 翻译失败: {e}")
            import traceback
            traceback.print_exc()
            self._report_manual_failure(mode)
            return

        if result:
            self.result_queue.put((result, mode))
        else:
            print("[翻译] 模型无响应")
            self._report_manual_failure(mode)
