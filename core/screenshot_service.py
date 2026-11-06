"""
截图服务 - 独立模块
职责：提供屏幕截图功能
"""
from typing import Tuple, Optional
from PIL import Image
from utils.screenshot import grab_region


class ScreenshotService:
    """截图服务

    选区实现通过 ``selector`` 注入（Web/Qt 版用 QtSelectorAdapter）。
    未注入时直接返回 None 并提示，不再回退到旧的 tkinter 选区器
    （``legacy/`` 已删除，见 KNOWLEDGE/performance.md 的代码精简记录）。
    """

    def __init__(self):
        """初始化截图服务（selector 由调用方注入）"""
        self.selector = None

    def _get_selector(self):
        if self.selector is None:
            print("[截图服务] 未注入选区实现，请通过 selector 参数传入 QtSelectorAdapter")
            return None
        return self.selector

    def select_region(self) -> Optional[Tuple[int, int, int, int]]:
        """
        让用户框选屏幕区域

        Returns:
            区域坐标 (x, y, width, height)，如果取消则返回None
        """
        selector = self._get_selector()
        if selector is None:
            print("[截图服务] Web/Qt 版请通过 QtSelectorAdapter 注入选区实现")
            return None
        try:
            region = selector.select()
            return region
        except Exception as e:
            print(f"[截图服务] 选择区域错误: {e}")
            return None

    def capture_region(self, region: Tuple[int, int, int, int]) -> Optional[Image.Image]:
        """
        截取指定区域的图像

        Args:
            region: 区域坐标 (x, y, width, height)

        Returns:
            PIL图像对象，失败则返回None
        """
        try:
            image = grab_region(region)
            return image
        except Exception as e:
            print(f"[截图服务] 截图错误: {e}")
            return None

    def capture_and_select(self) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[Image.Image]]:
        """
        选择区域并立即截图

        Returns:
            (区域坐标, 图像对象)
        """
        region = self.select_region()
        if region:
            image = self.capture_region(region)
            return region, image
        return None, None
