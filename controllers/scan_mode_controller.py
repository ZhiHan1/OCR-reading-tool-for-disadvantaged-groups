"""扫描模式控制器基类

功能3（实时朗读）、漫画模式、游戏模式共用同一套骨架：

- 区域选择 / 重新选择 / 清除
- 后台扫描线程的启动、停止、等待
- 状态栏、结果回调、状态变化通知
- 扫描间隔按配置热更新（每 10 轮重读一次配置）

三者的差异只在两处，由子类用钩子覆盖：

- ``_on_text(text)``      ：拿到一帧 OCR 文字后怎么处理（默认按历史记录只朗读新增部分）
- ``_prepare_start()``    ：每次启动线程前的额外准备（如清空历史）
- ``_on_region_selected()`` / ``_on_region_cleared()`` ：区域变化后的清理

注意：本基类不涉及 UI，回调都在后台线程中被调用，线程安全由主窗口负责。
"""
import threading
import logging
from typing import Callable, Optional, Tuple

from core.config_manager import get_config
from utils.screenshot import frame_changed, frame_signature

logger = logging.getLogger(__name__)


class ScanModeController:
    """扫描模式公共骨架（不可直接实例化）"""

    # 结果路由标识，与 ui/mixins/result_display.py 的 _RESULT_TARGETS 对应
    MODE = ""
    # 显示用名称，用于拼接 "[功能3] xxx" 这类提示
    LABEL = ""
    # 扫描间隔 / 历史条数所在的配置段
    CONFIG_SECTION = ""
    DEFAULT_SCAN_INTERVAL = 0.4
    # 「连续多少帧相同才确认」的默认值（子类可覆盖）
    DEFAULT_STABLE_FRAMES = 2

    # 状态提示文案（子类按需覆盖）
    SELECT_PROMPT = "请框选区域..."
    RESELECT_PROMPT = "请框选新区域..."
    RUNNING_STATUS = "实时识别运行中"
    STOPPED_STATUS = "已停止"
    CLEARED_STATUS = "区域已清除"

    def __init__(self, ocr_service, tts_service, screenshot_service):
        self.ocr = ocr_service
        self.tts = tts_service
        self.screenshot = screenshot_service

        self.region: Optional[Tuple[int, int, int, int]] = None
        self.is_running = False
        self.is_selecting = False
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        self.on_status_update: Optional[Callable[[str], None]] = None
        self.on_result: Optional[Callable[..., None]] = None
        self.on_state_changed: Optional[Callable[[bool], None]] = None

    # ===== 生命周期 =====

    def toggle(self):
        """启动/停止实时扫描"""
        if self.is_running:
            self.stop()
        else:
            self.start()

    def start(self):
        """启动实时扫描（没有区域时先让用户框选）"""
        if self.is_running or self.is_selecting:
            return

        if not self.region:
            self.is_selecting = True
            self._update_status(self.SELECT_PROMPT)
            try:
                region = self.screenshot.select_region()
                if region:
                    self.region = region
                    self._on_region_selected()
                    self._start_worker()
                else:
                    self._update_status("已取消")
            except Exception as e:
                self._report_error(e)
            finally:
                self.is_selecting = False
        else:
            self._start_worker()

    def _start_worker(self):
        """启动后台扫描线程"""
        # 确保 TTS 处于可用状态
        self.tts.restart()

        self._prepare_start()

        # 每次启动使用新的停止事件：旧线程可能还没退出，若共用同一个 Event，
        # clear() 会把它"救活"，结果同时跑两个扫描线程（双倍 OCR、重复朗读）。
        stop_event = threading.Event()
        self.stop_event = stop_event

        self.is_running = True
        self.worker_thread = threading.Thread(
            target=self._scan_loop, args=(stop_event,), daemon=True
        )
        self.worker_thread.start()

        self._show_result(
            "实时识别已启动", status=True, display=f"[{self.LABEL}] 实时识别已启动"
        )
        self._update_status(self.RUNNING_STATUS)
        self._notify_state_changed(True)

    def stop(self):
        """停止实时扫描（不阻塞 UI）"""
        if not self.is_running:
            return

        # 只发停止信号，不等线程退出，避免界面卡顿
        self.stop_event.set()
        self.is_running = False
        logger.debug("[%s] 已发送停止信号", self.LABEL)

        self._show_result("已停止", status=True, display=f"[{self.LABEL}] 已停止")
        self._update_status(self.STOPPED_STATUS)
        self._notify_state_changed(False)

    def _wait_for_thread_stop(self, timeout: float = 2.0) -> bool:
        """等待工作线程结束（仅供用户主动操作时调用）

        Returns:
            bool: True=线程已停止，False=超时
        """
        if not self.worker_thread or not self.worker_thread.is_alive():
            return True

        print(f"[{self.LABEL}] 等待扫描线程停止（最多{timeout}秒）...")
        self.worker_thread.join(timeout=timeout)

        if self.worker_thread.is_alive():
            print(f"[{self.LABEL}] 警告：线程未能及时停止")
            return False

        print(f"[{self.LABEL}] 扫描线程已停止")
        return True

    # ===== 扫描循环 =====

    def _scan_loop(self, stop_event: Optional[threading.Event] = None):
        """扫描循环（后台线程）

        Args:
            stop_event: 本次线程专属的停止事件（未传时退回 self.stop_event）
        """
        stop_event = stop_event or self.stop_event
        config = get_config()
        scan_interval = self._read_scan_interval(config)
        skip_unchanged = self._read_skip_unchanged(config)

        # 上一帧的画面指纹：画面没变就跳过 OCR（省掉绝大部分 CPU）
        last_signature = None
        loop_count = 0

        while not stop_event.is_set():
            try:
                # 每 10 轮重读一次配置，兼顾热更新和性能
                if loop_count % 10 == 0:
                    config = get_config()
                    scan_interval = self._read_scan_interval(config)
                    skip_unchanged = self._read_skip_unchanged(config)
                loop_count += 1

                image = self.screenshot.capture_region(self.region)
                if image:
                    # 变化检测：静态画面下不做推理（OCR 是这里唯一的重活）
                    if skip_unchanged:
                        signature = frame_signature(image)
                        if not frame_changed(signature, last_signature):
                            continue
                        last_signature = signature

                    text = self.ocr.recognize(image)
                    if text:
                        self._on_text(text)
            except Exception as e:
                logger.warning("[%s] 扫描失败: %s", self.LABEL, e)
            finally:
                # 放在 finally 里，保证截图失败/异常时也会休眠，不会空转占满 CPU
                stop_event.wait(scan_interval)

    def _read_skip_unchanged(self, config) -> bool:
        """是否启用「画面没变就跳过 OCR」（默认开启）"""
        return bool(config.get("ocr", "skip_unchanged", default=True))

    def _read_scan_interval(self, config) -> float:
        """从配置读取扫描间隔（非法值回退到默认值）

        间隔为 0 或负数会让循环变成忙等直接占满一个 CPU 核心，因此这里兜底。
        """
        value = config.get(
            "ocr", self.CONFIG_SECTION, "scan_interval", default=self.DEFAULT_SCAN_INTERVAL
        )
        try:
            interval = float(value)
        except (TypeError, ValueError):
            print(f"[{self.LABEL}] 扫描间隔配置无效（{value!r}），使用默认值")
            return self.DEFAULT_SCAN_INTERVAL

        if interval <= 0:
            print(f"[{self.LABEL}] 扫描间隔必须大于 0（{value!r}），使用默认值")
            return self.DEFAULT_SCAN_INTERVAL
        return interval

    def _read_stable_frames(self) -> int:
        """从配置读取「连续多少帧相同才确认」（非法值回退默认，限制 1~10）

        帧数越少响应越快（快字幕不易漏），越多越稳（慢字幕抗噪）。
        每帧约 scan_interval 秒。func3 与漫画模式共用本实现。
        """
        value = get_config().get(
            "ocr", self.CONFIG_SECTION, "stable_frames", default=self.DEFAULT_STABLE_FRAMES
        )
        try:
            frames = int(value)
        except (TypeError, ValueError):
            return self.DEFAULT_STABLE_FRAMES
        return max(1, min(10, frames))

    def _on_text(self, text: str):
        """处理一帧识别结果

        默认实现（漫画 / 游戏）：借助历史记录只朗读新增的文字部分。
        功能3 覆盖为基于去重服务的实现。
        """
        new_part = self.history.find_new_content(text)
        if not new_part:
            return

        self.history.add(text)
        print(f"[{self.LABEL}] {self.history.get_history_summary()}")

        # 线程安全由主窗口的结果回调保证
        # 返回 True 表示翻译已接管，此时不再朗读原文（避免原文 + 译文都读）
        if self._show_result(new_part):
            return
        self.tts.speak(new_part)

    # ===== 钩子（子类按需覆盖）=====

    def _prepare_start(self):
        """启动线程前的额外准备"""

    def _on_region_selected(self):
        """选定区域后（首次选择）"""

    def _on_region_cleared(self):
        """清除区域后"""

    # ===== 区域操作 =====

    def reselect(self):
        """重新选择区域"""
        if self.is_selecting:
            print(f"[{self.LABEL}] 正在选择区域，请等待...")
            return

        was_running = self.is_running

        if was_running:
            print(f"[{self.LABEL}] 停止当前扫描...")
            self.stop()
            self._wait_for_thread_stop(timeout=1.5)

        self.is_selecting = True
        try:
            self._update_status(self.RESELECT_PROMPT)

            region = self.screenshot.select_region()
            if region:
                self.region = region
                self._on_region_selected()
                self._show_result(
                    f"新区域: {region}", status=True,
                    display=f"[{self.LABEL}] 新区域: {region}",
                )

                # 之前在运行则用新区域自动重启
                if was_running:
                    print(f"[{self.LABEL}] 使用新区域重新启动...")
                    self._start_worker()
            else:
                self._update_status("已取消")
        except Exception as e:
            self._report_error(e)
            print(f"[{self.LABEL}] 重选区域失败: {e}")
        finally:
            self.is_selecting = False

    def clear_region(self):
        """清除选择的区域（运行中会先停止）"""
        if self.is_selecting:
            print(f"[{self.LABEL}] 正在选择区域，无法清除")
            return

        was_running = self.is_running

        # 先清空区域再通知 UI，这样一次通知就能得到正确的按钮状态
        self.region = None
        self._on_region_cleared()

        if was_running:
            print(f"[{self.LABEL}] 停止当前扫描...")
            self.stop()
            self._wait_for_thread_stop(timeout=1.5)
        else:
            # 未运行时 stop() 不会通知，这里补一次以刷新按钮
            self._notify_state_changed(False)

        self._show_result("已清除区域", status=True, display=f"[{self.LABEL}] 已清除区域")
        self._update_status(self.CLEARED_STATUS)

    def has_region(self) -> bool:
        """是否已选择区域"""
        return self.region is not None

    def reselect_region(self):
        """重新选择区域（reselect 的别名，供 UI 调用）"""
        self.reselect()

    # ===== 回调 =====

    def _update_status(self, status: str):
        """更新状态栏"""
        if self.on_status_update:
            self.on_status_update(status)

    def _notify_state_changed(self, is_running: bool):
        """通知 UI 运行状态变化"""
        if self.on_state_changed:
            self.on_state_changed(is_running)

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
