"""
应用控制器 - 统一管理所有服务和功能控制器
职责：初始化服务、协调控制器、提供统一接口给UI层

懒构造
------
控制器只有在真正被用到时才创建（`@property`）。原因是启动时把 9 个控制器
全部造出来要多花约 0.2 秒，而大部分页面根本不会被点开——尤其 Web 版
语音识别跑在独立进程里，`voice_input` / `asr_service` 在 GUI 进程里基本用不上。
对外接口保持不变：`app.func3` 第一次读取时自动构造，之后是同一个实例。
"""
from typing import Optional
import threading

from core.config_manager import get_config
from controllers.func3_controller import Func3Controller
from controllers.manga_controller import MangaController
from controllers.game_mode_controller import GameModeController
from controllers.translation_controller import TranslationController


class AppController:
    """应用主控制器"""

    # ==================== 懒构造 ====================

    # 所有可懒构造的成员：(属性名, "_make_" 方法所在) —— 属性由 _install_accessors() 生成
    LAZY_MEMBERS = (
        "ocr_service", "tts_service", "screenshot_service", "translation_service",
        "func1", "func2", "func3", "manga_mode", "game_mode",
        "translation", "voice_input", "asr_service", "func3_dedup",
    )

    def __init__(self):
        """初始化应用控制器（只建配置与懒构造骨架）"""
        self.config = get_config()
        self._lazy = {}
        self._build_lock = threading.RLock()

        # 预热 OCR 模型（可配置）：startup = 启动即后台加载，lazy = 首次识别时加载。
        # 默认 lazy：OCR 权重常驻约 65MB 提交内存，而首次识别多花的 0.3~1 秒
        # 远小于「一直占着」的代价。
        self._warmup_ocr()

    def _lazy_get(self, name: str):
        obj = self._lazy.get(name)
        if obj is None:
            # 加锁：后台线程（帧处理、语音回调）可能和 UI 线程同时首次访问
            with self._build_lock:
                obj = self._lazy.get(name)
                if obj is None:
                    obj = getattr(self, "_make_" + name)()
                    self._lazy[name] = obj
                    setattr(self, "_" + name, obj)  # 便于调试时看到
        return obj

    # ---------- 服务层构造 ----------

    def _make_ocr_service(self):
        from core.ocr_service import OCRService

        return OCRService(self.config.get("ocr", "engine", default="rapidocr"))

    def _make_tts_service(self):
        from core.tts_service import TTSService

        return TTSService(
            rate=self.config.get("tts", "rate", default=0),
            volume=self.config.get("tts", "volume", default=1.0),
            voice_index=self.config.get("tts", "voice_index", default=0),
        )

    def _make_screenshot_service(self):
        from core.screenshot_service import ScreenshotService

        return ScreenshotService()

    def _make_translation_service(self):
        from core.translation_service import TranslationService

        return TranslationService(
            model_name=self.config.get("translation", "model", default="gguf"),
            source_lang=self.config.get("translation", "source_lang", default="auto"),
            target_lang=self.config.get("translation", "target_lang", default="zh"),
        )

    def _make_asr_service(self):
        from core.asr_service import ASRService

        return ASRService(self.config)

    def _make_func3_dedup(self):
        from core.deduplication_service import DeduplicationService

        return DeduplicationService(**self._dedup_settings()["func3_dedup"])

    # ---------- 控制器构造 ----------

    def _make_func1(self):
        from controllers.func1_controller import Func1Controller

        return Func1Controller(
            self.ocr_service, self.tts_service, self.screenshot_service, self.config
        )

    def _make_func2(self):
        from controllers.func2_controller import Func2Controller

        return Func2Controller(
            self.ocr_service, self.tts_service, self.screenshot_service, self.config
        )

    def _make_func3(self):
        return Func3Controller(
            self.ocr_service, self.tts_service, self.screenshot_service, self.func3_dedup
        )

    def _make_manga_mode(self):
        return MangaController(
            self.ocr_service,
            self.tts_service,
            self.screenshot_service,
            max_history=self.config.get("ocr", "manga_mode", "history_size", default=6),
        )

    def _make_game_mode(self):
        return GameModeController(
            self.ocr_service,
            self.tts_service,
            self.screenshot_service,
            max_history=self.config.get("ocr", "game_mode", "history_size", default=5),
        )

    def _make_translation(self):
        return TranslationController(self.translation_service)

    def _make_voice_input(self):
        # 仅旧 tkinter 版用；放到这里延迟导入，避免 GUI 进程启动时就
        # 拉起 sounddevice（实测 176ms）与麦克风后端。
        from controllers.voice_input_controller import VoiceInputController

        return VoiceInputController(self.asr_service)

    # ==================== 预热 ====================

    def _warmup_ocr(self):
        """按配置预热 OCR（失败不影响正常使用）"""
        mode = str(self.config.get("ocr", "warmup", default="lazy")).lower()
        if mode != "startup":
            return

        import threading

        def run():
            try:
                self.ocr_service.warmup()
            except Exception as e:
                print(f"[应用控制器] OCR 预热失败: {e}")

        threading.Thread(target=run, daemon=True, name="OCRWarmup").start()

    def _dedup_settings(self):
        """从配置读取去重参数（初始化与热重载共用同一份定义）

        说明：只有功能3（后台连续扫描）需要去重。
        功能1（框选朗读）/ 功能2（固定区域朗读）是用户手动触发的一次性朗读，
        不做去重——手动重复点就应该立刻重读，走去重会表现为"点了没反应"。
        """
        c = self.config
        return {
            "func3_dedup": {
                "repeat_cooldown": c.get("ocr", "func3", "repeat_cooldown", default=3.0),
                "similarity_threshold": c.get("ocr", "func3", "similarity_threshold", default=0.85),
                "min_length_for_similarity": c.get("ocr", "func3", "min_length_for_similarity", default=3),
            },
        }

    # ==================== 公共操作 ====================

    # 后台常驻扫描的控制器（三者互斥，同时只允许一个在跑）
    SCAN_MODE_ATTRS = ("func3", "manga_mode", "game_mode")

    def _built(self, name: str):
        """已构造的控制器；没构造过说明它不可能在运行。"""
        return self._lazy.get(name)

    def stop_scan_modes(self, except_mode: Optional[str] = None):
        """
        停止所有正在运行的扫描模式

        Args:
            except_mode: 要保留的模式属性名（如 "manga_mode"），None 表示全停

        说明：三个扫描模式共用截图/OCR/TTS，如果同时运行会争抢屏幕区域、
        成倍占用 CPU，并且朗读互相打断，因此启动任一模式前必须先停掉其它模式。
        """
        for attr in self.SCAN_MODE_ATTRS:
            if attr == except_mode:
                continue
            controller = self._built(attr)
            if controller is not None and getattr(controller, "is_running", False):
                controller.stop()

    def stop_all(self):
        """停止所有操作"""
        # 停止所有扫描模式（功能3 / 漫画 / 游戏）
        self.stop_scan_modes()

        # 停止当前朗读
        tts = self._built("tts_service")
        if tts is not None:
            tts.stop_current()

        # 取消进行中的语音输入录音
        voice = self._built("voice_input")
        if voice is not None:
            try:
                if getattr(voice, "is_recording", False):
                    voice.cancel()
            except Exception as e:
                print(f"[应用控制器] 取消语音录音失败: {e}")

        # 重置去重状态（仅功能3有去重）
        dedup = self._built("func3_dedup")
        if dedup is not None:
            dedup.reset()

    def reload_config(self):
        """重新加载配置"""
        from core.config_manager import reload_config
        self.config = reload_config()

        # 更新TTS设置（含语音来源与音色，保证设置页保存后立即生效）
        tts = self._built("tts_service")
        if tts is not None:
            tts.update_settings(
                rate=self.config.get("tts", "rate", default=0),
                volume=self.config.get("tts", "volume", default=1.0),
                pitch=self.config.get("tts", "pitch", default=0),
                voice_name=self.config.get("tts", "voice_name", default=""),
                backend=self.config.get("tts", "backend", default="sapi"),
                edge_voice=self.config.get(
                    "tts", "edge_voice", default="zh-CN-XiaoxiaoNeural"
                ),
            )

        # 更新去重设置（与初始化共用同一份配置定义）
        dedup = self._built("func3_dedup")
        if dedup is not None:
            dedup.update_settings(**self._dedup_settings()["func3_dedup"])

        # 更新翻译语言设置
        translation = self._built("translation_service")
        if translation is not None:
            translation.update_settings(
                source_lang=self.config.get("translation", "source_lang", default="auto"),
                target_lang=self.config.get("translation", "target_lang", default="zh"),
            )

        # 更新漫画 / 游戏模式的历史记录条数
        for attr, section in (("manga_mode", "manga_mode"), ("game_mode", "game_mode")):
            controller = self._built(attr)
            history = getattr(controller, "history", None)
            if history is not None:
                history.max_history = self.config.get(
                    "ocr", section, "history_size", default=5
                )

        print("[应用控制器] 配置已重新加载")

    def shutdown(self):
        """关闭应用并清理所有资源"""
        print("[应用控制器] 开始关闭...")

        # 1. 停止所有功能
        self.stop_all()

        # 2. 等待三个扫描线程真正退出（最多各 2 秒）
        for attr in self.SCAN_MODE_ATTRS:
            controller = self._built(attr)
            if controller is not None:
                controller._wait_for_thread_stop(timeout=2.0)

        # 3. 停止TTS服务
        tts = self._built("tts_service")
        if tts is not None:
            tts.stop()

        # 4. 停止翻译服务
        translation = self._built("translation_service")
        if translation is not None:
            translation.stop()

        print("[应用控制器] 已关闭")


def _make_accessor(name: str):
    """生成一个只读属性，等价于原来的

        @property
        def ocr_service(self):
            return self._lazy_get("ocr_service")

    十三个成员的写法完全一样，逐个手写只会让「新增成员」漏改一处。
    """
    def getter(self):
        return self._lazy_get(name)
    getter.__name__ = name
    getter.__doc__ = f"懒构造的 `{name}`（首次访问时才创建）"
    return property(getter)


# 把 LAZY_MEMBERS 里的每个名字挂成 AppController 的只读属性
for _member in AppController.LAZY_MEMBERS:
    setattr(AppController, _member, _make_accessor(_member))
del _member
