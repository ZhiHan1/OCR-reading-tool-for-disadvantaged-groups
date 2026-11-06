"""从 Qt 资源导出 qwebchannel.js 到 qt_web/web/js/vendor/。

QWebChannel 的 JS 协议文件由 Qt 内置在 :/qtwebchannel/qwebchannel.js，
必须让页面能按相对路径加载（file:// 下跨 scheme 加载会被拦）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QFile, QIODevice

# 触发资源注册
import PySide6.QtWebChannel  # noqa: F401

OUT_DIR = os.path.join("qt_web", "web", "js", "vendor")
OUT = os.path.join(OUT_DIR, "qwebchannel.js")


def main():
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.exists():
        print("!! Qt 资源里找不到 qwebchannel.js")
        return 1
    if not f.open(QIODevice.ReadOnly):
        print("!! 打开资源失败")
        return 1
    data = bytes(f.readAll().data())
    f.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "wb") as fh:
        fh.write(data)
    print(f"已导出 {OUT}  {len(data)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
