"""功能3控制器：实时扫描朗读

职责：管理后台扫描线程，持续识别、去重后显示并朗读（原文显示与翻译按 func3 开关）。
公共骨架见 controllers/scan_mode_controller.py。
"""
from controllers.scan_mode_controller import ScanModeController
from core.config_manager import get_config
from core.deduplication_service import DeduplicationService
from core.ocr_service import OCRService
from core.screenshot_service import ScreenshotService
from core.tts_service import TTSService


class Func3Controller(ScanModeController):
    """功能3：实时扫描朗读控制器"""

    MODE = "func3"
    LABEL = "功能3"
    CONFIG_SECTION = "func3"
    DEFAULT_SCAN_INTERVAL = 0.4
    RUNNING_STATUS = "实时识别运行中"

    # 确认帧数：候选文本连续多少帧相同即确认朗读（过滤 OCR 单帧噪声）
    # 默认 2 帧 ≈ 0.8 秒，能兼顾快字幕响应与慢字幕稳定。
    DEFAULT_STABLE_FRAMES = 2
    # 短句阈值：字数 <= 此值视为「短句」，出现 1 帧即朗读（短字幕停留时间短，等不起多帧确认）
    DEFAULT_SHORT_TEXT_MAX_LEN = 2

    def __init__(self,
                 ocr_service: OCRService,
                 tts_service: TTSService,
                 screenshot_service: ScreenshotService,
                 dedup_service: DeduplicationService):
        """初始化控制器"""
        super().__init__(ocr_service, tts_service, screenshot_service)
        self.dedup = dedup_service

        # 时间自适应确认状态：
        # _candidate      当前正在确认的候选文本
        # _candidate_count 候选已连续出现的帧数
        # _candidate_read  候选是否已经朗读过（防止同一句反复确认）
        self._candidate = ""
        self._candidate_count = 0
        self._candidate_read = False

    def _read_short_text_max_len(self):
        """从配置读取短句阈值（非法值回退默认）

        字数 <= 此值的短句出现 1 帧即朗读；越长越保守。
        """
        value = get_config().get(
            "ocr", self.CONFIG_SECTION, "short_text_max_len", default=self.DEFAULT_SHORT_TEXT_MAX_LEN
        )
        try:
            max_len = int(value)
        except (TypeError, ValueError):
            return self.DEFAULT_SHORT_TEXT_MAX_LEN
        # 0 表示禁用「短句即读」（所有句子都走多帧确认），上限 10 字
        return max(0, min(10, max_len))

    def _prepare_start(self):
        """每次启动都清空确认状态，避免沿用上一次的候选"""
        self._candidate = ""
        self._candidate_count = 0
        self._candidate_read = False

    def _on_region_selected(self):
        self._prepare_start()

    def _on_region_cleared(self):
        self._prepare_start()

    def _on_text(self, text: str):
        """时间自适应确认：连续帧确认 + 字幕切换兜底补读

        视频字幕有快有慢，固定窗口投票无法兼顾：
        - 快字幕（只停留 1~2 帧）攒不满窗口就被丢弃 → 漏读；
        - 慢字幕停留很久 → 去重冷却不够会重复读。

        这里改用「连续 stable_frames 帧相同即确认」+「切换瞬间补读」：
        1. 新文本立即成为候选，不要求先攒满窗口；
        2. 候选连续出现 stable_frames 帧即朗读（快字幕也能尽快响应）；
        3. 一旦检测到字幕切换（文本变 B 或消失），若上一句候选还没朗读，
           立刻补读它——保证快字幕不被丢；
        4. 慢字幕朗读一次后靠去重冷却 + 相似度去重防重复。
        """
        text = (text or "").strip()
        stable_frames = self._read_stable_frames()
        short_text_max_len = self._read_short_text_max_len()

        # ---- 情况1：画面里没文字 ----
        if not text:
            # 上一句候选还没读就消失了（快字幕）：补读
            if self._candidate and not self._candidate_read:
                self._speak(self._candidate)
            self._prepare_start()
            return

        # ---- 情况2：文本与候选相同（字幕仍在停留）----
        if text == self._candidate:
            self._candidate_count += 1
            # 短句（<=short_text_max_len 字）出现即读，不等多帧；长句需连续 stable_frames 帧确认
            required = 1 if len(text) <= short_text_max_len else stable_frames
            if self._candidate_count >= required and not self._candidate_read:
                self._candidate_read = True
                self._speak(text)
            return

        # ---- 情况3：字幕切换（text 变成新的内容）----
        # 上一句候选还没读就切走了（快字幕）：补读
        if self._candidate and not self._candidate_read:
            self._speak(self._candidate)

        # 新文本成为候选
        self._candidate = text
        self._candidate_count = 1
        self._candidate_read = False

        # 新候选若是短句，立即朗读（不等下一帧）
        if len(text) <= short_text_max_len:
            self._candidate_read = True
            self._speak(text)

    def _speak(self, text: str):
        """朗读文本：先经过去重，再显示并朗读"""
        if self.dedup.should_skip(text):
            return
        # 功能3 与功能1/2 共用结果框：显示原文，并按 func3 的翻译开关决定是否翻译。
        # 返回 True 表示翻译已接管，不再朗读原文。
        if self._show_result(text, display=f"[{self.LABEL}] {text}"):
            return
        self.tts.speak(text)
