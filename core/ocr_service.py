"""
OCR 识别服务 - 独立模块
职责：提供文字识别功能
"""
from typing import Optional
from PIL import Image
from .ocr_engine import OCREngine


class OCRService:
    """OCR识别服务"""

    def __init__(self, engine_name: str = "rapidocr"):
        """
        初始化OCR服务

        Args:
            engine_name: OCR引擎名称（rapidocr/paddleocr/easyocr）
        """
        # 注意：当前OCREngine只支持RapidOCR，engine_name参数保留以便将来扩展
        self.engine = OCREngine()
        self.engine_name = engine_name

    def warmup(self):
        """预热 OCR 模型（建议在后台线程调用，避免首次识别卡顿）"""
        self.engine.warmup()

    def reload_device(self, device_id: str = None):
        """切换 OCR 推理设备（CPU / 指定 GPU），引擎在下次识别时重新加载"""
        return self.engine.reload_device(device_id)

    @property
    def device_id(self) -> str:
        """当前 OCR 使用的设备标识"""
        return self.engine.device_id

    def recognize(self, image: Image.Image) -> Optional[str]:
        """
        识别图像中的文字

        Args:
            image: PIL图像对象

        Returns:
            识别出的文字，如果没有识别到则返回None
        """
        try:
            text = self.engine.recognize(image)
            return text if text else None
        except Exception as e:
            print(f"[OCR服务] 识别错误: {e}")
            return None
