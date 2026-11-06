"""
系统托盘图标模块
职责：在任务栏显示托盘图标，提供右键菜单
"""
import pystray
from PIL import Image, ImageDraw
import threading
import os
import sys


def _resource_path(relative_path: str) -> str:
    """获取资源文件路径，兼容开发模式与 PyInstaller 打包模式。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


class SystemTray:
    """系统托盘管理器"""

    def __init__(self, app_name="OCR朗读工具", on_quit=None, on_show=None, icon_path="Logo.ico"):
        """
        初始化系统托盘

        Args:
            app_name: 应用名称
            on_quit: 退出回调函数
            on_show: 显示主窗口回调函数
            icon_path: 托盘图标文件（相对资源路径），加载失败时回退到内置绘制图标
        """
        self.app_name = app_name
        self.on_quit = on_quit
        self.on_show = on_show
        self.icon = None
        self.running = False
        self.icon_path = icon_path

    def _create_icon_image(self):
        """创建托盘图标：优先加载 Logo.ico，失败则回退到内置绘制图标"""
        # 1. 尝试加载 Logo.ico
        try:
            candidates = [
                _resource_path(self.icon_path),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", self.icon_path),
            ]
            for p in candidates:
                if p and os.path.exists(p):
                    img = Image.open(p)
                    # pystray 需要 RGBA 图像
                    if img.mode != "RGBA":
                        img = img.convert("RGBA")
                    print(f"[系统托盘] 已加载图标: {p}")
                    return img
        except Exception as e:
            print(f"[系统托盘] 加载 Logo.ico 失败，回退内置图标: {e}")

        # 2. 回退：画一个简单的圆形图标
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), (100, 102, 241))  # 靛蓝色背景
        dc = ImageDraw.Draw(image)

        # 画一个白色的圆
        margin = 8
        dc.ellipse([margin, margin, width - margin, height - margin], fill='white')

        # 画字母 "O"（代表OCR）
        dc.text((width // 2 - 8, height // 2 - 10), "O", fill=(100, 102, 241))

        return image

    def _create_menu(self):
        """创建右键菜单"""
        return pystray.Menu(
            pystray.MenuItem("显示主窗口", self._on_show_click, default=True),
            pystray.MenuItem("强制退出", self._on_quit_click)
        )

    def _on_show_click(self, icon, item):
        """显示主窗口"""
        if self.on_show:
            self.on_show()

    def _on_quit_click(self, icon, item):
        """强制退出（运行在托盘线程中）"""
        print("[系统托盘] 强制退出...")
        if self.on_quit:
            # 真正的退出在主线程执行（quit_app 里会 stop() 托盘）。
            # 这里不能自己 stop()：join 当前线程会抛 RuntimeError。
            self.on_quit()
        else:
            self.stop()

    def start(self):
        """启动系统托盘（在后台线程中运行）"""
        if self.running:
            return

        self.running = True

        def run_tray():
            try:
                icon_image = self._create_icon_image()
                self.icon = pystray.Icon(
                    self.app_name,
                    icon_image,
                    self.app_name,
                    self._create_menu()
                )
                print("[系统托盘] 已启动")
                self.icon.run()
            except Exception as e:
                print(f"[系统托盘] 启动失败: {e}")
                self.running = False

        self.tray_thread = threading.Thread(target=run_tray, daemon=True)
        self.tray_thread.start()

    def stop(self):
        """停止系统托盘"""
        if self.icon:
            try:
                self.icon.stop()
                self.running = False
                print("[系统托盘] 已停止")

                # 等待托盘线程结束（最多等待1秒）
                # 若 stop() 是在托盘线程自己里调用的，join 自己会抛 RuntimeError
                tray_thread = getattr(self, "tray_thread", None)
                if (tray_thread and tray_thread.is_alive()
                        and tray_thread is not threading.current_thread()):
                    tray_thread.join(timeout=1.0)
            except Exception as e:
                print(f"[系统托盘] 停止时出错: {e}")

    def notify(self, title, message):
        """显示系统通知"""
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                print(f"[系统托盘] 通知失败: {e}")
