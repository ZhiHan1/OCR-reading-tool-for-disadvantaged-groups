"""漫画模式控制器：智能去重，只朗读新增的文字部分

公共骨架见 controllers/scan_mode_controller.py。
漫画模式复用功能3的扫描间隔 / 历史条数配置（设置页也是按此保存的）。

与功能3 一样，漫画模式也做「稳定确认 + 切换兜底」：
手动下滑页面时，OCR 每帧都会捕获滚动中的中间态（半行字、半段话、错位），
若每帧立即朗读就会读半个字、读乱序、重复读。这里要求「连续 stable_frames
帧得到相同的新增文字」才朗读，滑动中间态自然被过滤掉。
"""
from controllers.scan_mode_controller import ScanModeController
from core.history_manager import HistoryManager


class MangaController(ScanModeController):
    """漫画模式控制器 - 智能去重朗读"""

    MODE = "manga"
    LABEL = "漫画模式"
    CONFIG_SECTION = "manga_mode"
    DEFAULT_SCAN_INTERVAL = 0.4
    SELECT_PROMPT = "请框选扫描区域..."
    RESELECT_PROMPT = "请重新框选区域..."
    RUNNING_STATUS = "漫画模式运行中"
    STOPPED_STATUS = "漫画模式已停止"

    # 确认帧数：新增文字连续多少帧一致才朗读（过滤滚动中间态）
    DEFAULT_STABLE_FRAMES = 2

    def __init__(self, ocr_service, tts_service, screenshot_service, max_history: int = 6):
        super().__init__(ocr_service, tts_service, screenshot_service)
        self.history = HistoryManager(max_history=max_history)

        # 稳定确认状态（针对「新增文字」做连续帧确认）
        self._pending_new = ""          # 候选新增文字
        self._pending_count = 0         # 候选已连续出现的帧数
        self._pending_full = ""         # 候选对应的完整文本（朗读后写入历史）

    def _prepare_start(self):
        """每次启动都从零开始累计，避免沿用上一次的内容"""
        self.history.clear()
        self._reset_pending()

    def _on_region_selected(self):
        self.history.clear()
        self._reset_pending()

    def _on_region_cleared(self):
        self.history.clear()
        self._reset_pending()

    def _reset_pending(self):
        self._pending_new = ""
        self._pending_count = 0
        self._pending_full = ""

    def _on_text(self, text: str):
        """处理一帧识别结果：只朗读「稳定下来的新增文字」

        流程：
        1. 用历史记录算出本帧相对已读内容的新增部分；
        2. 新增部分连续 stable_frames 帧一致才朗读（过滤滚动中间态）；
        3. 朗读后写入历史，供后续帧继续做增量判断。
        """
        text = (text or "").strip()
        if not text:
            # 画面暂时无字：上一帧未确认的新增文字视为切换，直接补读
            if self._pending_new:
                self._speak_new(self._pending_new, self._pending_full)
            self._reset_pending()
            return

        # 1. 计算本帧新增文字（相对历史记录）
        new_part = self._extract_new_part(text)

        if not new_part:
            # 无新增（页面静止）：上一帧未确认的候选若已滚走，补读
            if self._pending_new:
                self._speak_new(self._pending_new, self._pending_full)
            self._reset_pending()
            return

        # 2. 新增文字连续确认
        if new_part == self._pending_new:
            self._pending_count += 1
        else:
            # 新增内容变了：说明还在滚动（中间态），替换候选
            self._pending_new = new_part
            self._pending_full = text
            self._pending_count = 1

        # 3. 达到确认帧数才朗读
        stable_frames = self._read_stable_frames()
        if self._pending_count >= stable_frames:
            self._speak_new(self._pending_new, self._pending_full)
            self._reset_pending()

    def _extract_new_part(self, text: str) -> str:
        """计算本帧相对历史记录的新增文字（空字符串表示无新增）"""
        new_part = self.history.find_new_content(text)
        return (new_part or "").strip()

    def _speak_new(self, new_part: str, full_text: str):
        """朗读并记录新增文字"""
        new_part = (new_part or "").strip()
        if not new_part:
            return

        # 写入历史（记录完整文本，供后续帧做增量参照）
        self.history.add(full_text or new_part)

        # 显示并朗读新增部分；返回 True 表示翻译已接管，不再朗读原文
        if self._show_result(new_part):
            return
        self.tts.speak(new_part)
