"""控制器公共骨架

`func1`（框选朗读）与 `func2`（固定区域朗读）是「用户手动触发的一次性朗读」，
两者除了「怎么拿到截图 / 截图区域」之外，其余流程完全一致：

- 回调注册：`on_status_update` / `on_result`（+ func2 的 `on_region_changed`）
- 状态栏、结果路由（决定是否交给翻译链路）
- 「翻译接管就跳过原文朗读」的决策
- 朗读开关：`recognition.<section>_read`

历史上这几段在 func1 / func2 里各写了一份逐字相同的实现，改其中一处很容易漏掉
另一处（`dedup=False` 的注释就重复了四遍）。本基类把它们收拢到一处。

扫描类（func3 / 漫画 / 游戏）的骨架在 `scan_mode_controller.py`，两者互不相干：
扫描类有后台线程与去重，这里没有。
"""
from typing import Callable, Optional

from core.config_manager import get_config


class BaseController:
    """一次性朗读类控制器的公共骨架"""

    # 结果路由标识（与 qt_ui/bridge.py 的 `_RESULT_TARGETS` 对应）
    MODE = ""
    # 显示用名称，用于拼接 "[功能1] ..." 这类提示
    LABEL = ""

    def __init__(self, ocr_service, tts_service, screenshot_service, config=None):
        self.ocr = ocr_service
        self.tts = tts_service
        self.screenshot = screenshot_service
        self.config = config

        self.on_status_update: Optional[Callable[[str], None]] = None
        self.on_result: Optional[Callable[..., bool]] = None

    # ===== 回调 =====

    def _update_status(self, status: str):
        """更新状态栏"""
        if self.on_status_update:
            self.on_status_update(status)

    def _show_result(self, text: str, status: bool = False, display: str = None) -> bool:
        """显示结果（status=True 表示状态消息，不参与翻译）

        Returns:
            bool: True 表示已交给翻译链路，调用方不要再朗读原文
        """
        if self.on_result:
            return bool(self.on_result(self.MODE, text, status, display))
        return False

    def _report_error(self, error):
        """报告异常：显示错误信息并更新状态栏"""
        self._show_result(str(error), status=True, display=f"[{self.LABEL}错误] {error}")
        self._update_status("错误")

    # ===== 朗读决策 =====

    def _should_read(self) -> bool:
        """本功能的「朗读原文」开关是否开启（默认开）

        配置键为 `recognition.<MODE>_read`（`func1_read` / `func2_read`），
        与 `qt_ui/bridge.py` 读取的是同一个键。
        """
        config = self.config if self.config is not None else get_config()
        return bool(config.get("recognition", f"{self.MODE}_read", default=True))

    def _speak_or_translate(self, text: str, interrupt_mode: bool = False):
        """显示 OCR 结果；翻译未接管且朗读开关打开时朗读原文

        这是 func1 / func2 共用的核心决策，两处顺序必须一致：
        1. 先 `_show_result`，返回 True 表示翻译已接管 → 不读原文；
        2. 否则看朗读开关；
        3. 朗读时一律 `dedup=False`——手动触发，同一句重复点也要立刻重读。
        """
        if self._show_result(text, display=f"[{self.LABEL}] {text}"):
            print(f"[{self.LABEL}控制器] 翻译已启用，跳过原文朗读")
        elif not self._should_read():
            print(f"[{self.LABEL}控制器] 朗读开关已关闭，跳过原文朗读")
        else:
            print(f"[{self.LABEL}控制器] 翻译未启用，朗读原文")
            self.tts.speak(text, interrupt=interrupt_mode, dedup=False)


class ReadOnceController(BaseController):
    """手动触发、每次只读一次（func1 / func2 的共同点，目前无需额外成员）"""
