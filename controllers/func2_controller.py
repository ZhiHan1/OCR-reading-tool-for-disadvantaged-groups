"""
功能2控制器：固定区域朗读
职责：管理固定区域，提供按需朗读功能

注意：功能2 是「用户按按钮手动触发的一次性朗读」，不做任何去重。
去重（冷却时间 / 相似度）只属于功能3这类后台连续扫描，手动触发重复点
就应该立刻重新读，否则会表现为"点了没反应 / 延迟"。

公共骨架（回调注册、状态栏、结果路由、朗读开关）继承自
controllers/base_controller.py。
"""
from typing import Optional, Tuple

from core.ocr_service import OCRService
from core.screenshot_service import ScreenshotService
from core.tts_service import TTSService

from controllers.base_controller import ReadOnceController


class Func2Controller(ReadOnceController):
    """功能2：固定区域朗读控制器"""

    MODE = "func2"
    LABEL = "功能2"

    def __init__(self,
                 ocr_service: OCRService,
                 tts_service: TTSService,
                 screenshot_service: ScreenshotService,
                 config=None):
        """初始化控制器（是否朗读原文由 _show_result() 的返回值决定）"""
        super().__init__(ocr_service, tts_service, screenshot_service, config)

        self.region: Optional[Tuple[int, int, int, int]] = None
        self.is_selecting = False
        # 一次识别（截图 + OCR）进行中；防止连点导致多份 OCR 同时跑
        self.is_reading = False

        self.on_region_changed = None

    def select_region(self):
        """选择固定区域"""
        if self.is_selecting:
            return

        self.is_selecting = True

        try:
            self._update_status("请框选区域...")

            # 选择区域
            region = self.screenshot.select_region()

            if region:
                self.region = region
                self._show_result(f"区域: {region}", status=True, display=f"[功能2] 区域: {region}")
                self._update_status("区域已选")
                if self.on_region_changed:
                    self.on_region_changed(True)
            else:
                self._update_status("已取消")

        except Exception as e:
            self._report_error(e)

        finally:
            self.is_selecting = False

    def read_region(self, interrupt_mode: bool = False):
        """
        朗读固定区域的内容

        Args:
            interrupt_mode: 是否打断当前朗读
        """
        if not self.region:
            self._show_result("请先选择区域", status=True, display="[功能2] 请先选择区域")
            return

        if self.is_reading:
            print("[功能2] 上一次识别还在进行中，已忽略本次点击")
            return

        self.is_reading = True
        try:
            # 1. 截图
            image = self.screenshot.capture_region(self.region)
            if not image:
                self._show_result("截图失败", status=True, display="[功能2] 截图失败")
                return

            # 2. OCR识别
            text = self.ocr.recognize(image)

            if not text:
                self._show_result("未识别到文字", status=True, display="[功能2] 未识别到文字")
                return

            # 3. 显示结果并按需朗读原文
            self._speak_or_translate(text, interrupt_mode)

            self._update_status(f"识别: {text[:30]}")

        except Exception as e:
            self._report_error(e)

        finally:
            self.is_reading = False

    def clear_region(self):
        """清除固定区域"""
        self.region = None
        self._show_result("已清除区域", status=True, display="[功能2] 已清除区域")
        self._update_status("区域已清除")
        if self.on_region_changed:
            self.on_region_changed(False)

    def has_region(self) -> bool:
        """是否已选择区域"""
        return self.region is not None
