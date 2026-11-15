"""
翻译控制器 - 管理翻译功能的业务逻辑
职责：切换翻译后端、查询模型信息

注意：本类**不负责执行翻译**。

历史上的 ``translate(text)`` 是同步阻塞调用，结果只通过 ``on_result(result)``
回调送出、没有返回值；而现在翻译统一走 ``controllers.translation_manager`` 的
后台队列（``enqueue_manual``），结果经由 ``result_queue`` 回到主线程再推前端。
那条回调还有个真实 Bug：它调用 ``on_result(result)`` 只传一个字符串，而
``qt_ui/bridge.emit_result`` 期望 ``on_result(mode, text, status, display)``
四参数，接错了会把译文当成 mode、文本变成空——只是无人调用才没暴露。
因此 ``translate()`` 与 ``on_result`` / ``on_status_update`` 一并删除，
需要翻译请用 ``TranslationManager``。
"""
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
        self.translation_service.switch_model(model_key)
