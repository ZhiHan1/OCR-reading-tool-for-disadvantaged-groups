"""
翻译控制器 - 管理翻译功能的业务逻辑
职责：协调翻译服务，处理翻译请求，管理翻译状态
"""
from typing import Optional, Callable
from core.translation_service import TranslationService


class TranslationController:
    """翻译功能控制器"""

    def __init__(self, translation_service: TranslationService):
        """
        初始化翻译控制器

        Args:
            translation_service: 翻译服务实例
        """
        self.translation_service = translation_service

        # 回调函数
        self.on_result: Optional[Callable] = None
        self.on_status_update: Optional[Callable] = None

    def translate(self, text: str):
        """
        翻译文本

        Args:
            text: 要翻译的文本
        """
        if not text or not text.strip():
            if self.on_status_update:
                self.on_status_update("请输入要翻译的文本")
            return

        try:
            # 更新状态
            if self.on_status_update:
                self.on_status_update("正在翻译...")

            # 执行翻译
            result = self.translation_service.translate(text)

            # 返回结果
            if self.on_result:
                self.on_result(result)

            # 更新状态
            if self.on_status_update:
                self.on_status_update("翻译完成")

        except Exception as e:
            error_msg = f"翻译失败: {e}"
            print(f"[翻译控制器] {error_msg}")
            if self.on_status_update:
                self.on_status_update(error_msg)

    def get_available_models(self):
        """获取可用翻译模型列表"""
        return self.translation_service.get_available_models()

    def get_current_model(self):
        """获取当前模型信息"""
        return self.translation_service.get_current_model_info()

    def switch_model(self, model_key: str):
        """
        切换翻译模型（只改配置，模型在下次翻译时懒加载）

        Args:
            model_key: 模型标识
        """
        try:
            if self.on_status_update:
                self.on_status_update(f"正在切换到模型: {model_key}")

            self.translation_service.switch_model(model_key)

            if self.on_status_update:
                self.on_status_update(f"已切换到模型: {model_key}")

        except Exception as e:
            error_msg = f"切换模型失败: {e}"
            print(f"[翻译控制器] {error_msg}")
            if self.on_status_update:
                self.on_status_update(error_msg)
