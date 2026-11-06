"""
功能1控制器：框选朗读
职责：协调截图、OCR、TTS等服务完成框选朗读功能

注意：功能1 是「用户手动触发的一次性朗读」，不做任何去重。
去重（冷却时间 / 相似度）只属于功能3这类后台连续扫描，手动触发重复点
就应该立刻重新读，否则会表现为"点了没反应 / 延迟"。

公共骨架（回调注册、状态栏、结果路由、朗读开关）继承自
controllers/base_controller.py。
"""
from core.screenshot_service import ScreenshotService
from core.tts_service import TTSService
from core.ocr_service import OCRService

from controllers.base_controller import ReadOnceController


class Func1Controller(ReadOnceController):
    """功能1：框选朗读控制器"""

    MODE = "func1"
    LABEL = "功能1"

    def __init__(self,
                 ocr_service: OCRService,
                 tts_service: TTSService,
                 screenshot_service: ScreenshotService,
                 config=None):
        """初始化控制器（是否朗读原文由 _show_result() 的返回值决定）"""
        super().__init__(ocr_service, tts_service, screenshot_service, config)
        self.is_busy = False

    def execute(self, interrupt_mode: bool = False):
        """
        执行框选朗读

        Args:
            interrupt_mode: 是否打断当前朗读
        """
        if self.is_busy:
            return

        self.is_busy = True

        try:
            # 1. 更新状态
            self._update_status("请框选区域...")

            # 2. 选择区域并截图
            region, image = self.screenshot.capture_and_select()

            if not region or not image:
                self._update_status("已取消")
                return

            # 3. OCR识别
            self._update_status("识别中...")
            text = self.ocr.recognize(image)

            if not text:
                self._show_result("未识别到文字", status=True, display="[功能1] 未识别到文字")
                self._update_status("未识别到文字")
                return

            # 4. 显示结果并按需朗读原文
            self._speak_or_translate(text, interrupt_mode)

            self._update_status(f"识别: {text[:30]}")

        except Exception as e:
            self._report_error(e)

        finally:
            self.is_busy = False
