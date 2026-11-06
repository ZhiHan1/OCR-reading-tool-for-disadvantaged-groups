"""游戏模式控制器：文字冒险游戏实时识别

职责：管理后台扫描线程，识别后只朗读/显示新增的文字。

文字冒险游戏的文字常以「打字机」形式逐字输出（你走 → 你走进 → 你走进了一
间屋子）。若每帧立即朗读新增片段，一句话会被拆成一个字一个字地读，非常破碎。
因此这里做「稳定确认」：只有当识别到的完整文本连续 stable_frames 帧不变
（意味着打字机输出已结束）时才朗读，且只朗读相对历史的「新增整句」。

公共骨架见 controllers/scan_mode_controller.py。
"""
from controllers.scan_mode_controller import ScanModeController
from core.history_manager import HistoryManager
from core.ocr_service import OCRService
from core.screenshot_service import ScreenshotService
from core.tts_service import TTSService


class GameModeController(ScanModeController):
    """游戏模式：文字冒险游戏实时识别控制器"""

    MODE = "game"
    LABEL = "游戏模式"
    CONFIG_SECTION = "game_mode"
    DEFAULT_SCAN_INTERVAL = 0.3
    SELECT_PROMPT = "请框选游戏文字区域..."
    RESELECT_PROMPT = "请框选新区域..."
    RUNNING_STATUS = "游戏模式运行中"

    # 确认帧数：完整文本连续多少帧不变才朗读（等待打字机输出结束）
    DEFAULT_STABLE_FRAMES = 2

    def __init__(self,
                 ocr_service: OCRService,
                 tts_service: TTSService,
                 screenshot_service: ScreenshotService,
                 max_history: int = 6):
        """初始化控制器"""
        super().__init__(ocr_service, tts_service, screenshot_service)
        self.history = HistoryManager(max_history=max_history)

        # 稳定确认状态
        self._candidate = ""          # 当前候选完整文本
        self._candidate_count = 0     # 候选已连续出现的帧数

    def _prepare_start(self):
        self.history.clear()
        self._candidate = ""
        self._candidate_count = 0

    def _on_region_selected(self):
        """换区域意味着换了一段剧情，历史记录必须清空"""
        self.history.clear()
        self._candidate = ""
        self._candidate_count = 0

    def _on_region_cleared(self):
        self.history.clear()
        self._candidate = ""
        self._candidate_count = 0

    def _on_text(self, text: str):
        """处理一帧识别结果：等完整文本稳定后再朗读新增整句

        打字机逐字输出时文本每帧都在变，只有连续 stable_frames 帧完全相同
        （即输出已停止）才朗读。朗读的是相对历史的「新增整句」。
        """
        text = (text or "").strip()

        # ---- 画面里没文字 ----
        if not text:
            # 上一句候选还没读就消失了（对话切换）：补读
            if self._candidate:
                self._speak_new(self._candidate)
            self._candidate = ""
            self._candidate_count = 0
            return

        # ---- 文本与候选相同（打字机已停/稳定）----
        if text == self._candidate:
            self._candidate_count += 1
            stable_frames = self._read_stable_frames()
            if self._candidate_count >= stable_frames:
                # 稳定：朗读相对历史的新增部分
                self._speak_new(text)
                self._candidate = ""
                self._candidate_count = 0
            return

        # ---- 文本变了（打字机在输出，或切到下一句）----
        # 如果候选存在且已稳定到一半，说明打字机还在输出，不朗读；
        # 只有「上一句候选已稳定但还没读」的情况，这里不触发（由上面两个分支处理）。
        self._candidate = text
        self._candidate_count = 1

    def _speak_new(self, full_text: str):
        """朗读相对历史记录的新增整句"""
        new_part = self.history.find_new_content(full_text)
        if not new_part or not new_part.strip():
            return

        # 记录完整文本到历史，供后续增量判断
        self.history.add(full_text)

        # 显示并朗读新增部分；返回 True 表示翻译已接管，不再朗读原文
        if self._show_result(new_part):
            return
        self.tts.speak(new_part)
