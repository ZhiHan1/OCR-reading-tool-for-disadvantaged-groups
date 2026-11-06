"""把参考模板 ui/ui_template.html 的 <style> 块按语义拆成多个 CSS 模块。

只读取参考文件，绝不修改它；输出到 qt_web/web/css/。
按行号区间切分，保证与参考样式逐字节一致（含注释）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = os.path.join("ui", "ui_template.html")
OUT = os.path.join("qt_web", "web", "css")

# (输出文件, 起始行, 结束行, 说明) —— 1-based，闭区间
SECTIONS = [
    ("tokens.css", 8, 255, "设计变量：深色 :root、亮色主题、亮色光球细节、毛玻璃开/关"),
    ("layout.css", 256, 349, "骨架、侧边栏、主区域、分区标题"),
    ("components.css", 350, 517, "玻璃卡片、功能卡片、按钮、表单、文本域"),
    ("panels.css", 518, 617, "结果显示区、状态栏、标签页、设置子分区、语音输入、TTS 进度、工具类"),
    ("light-overrides.css", 618, 710, "亮色主题下控件细节修正（下拉/滚动条/标签栏/语义按钮等）"),
    ("pages.css", 711, 767, "翻译模型导航行、弹窗、响应式媒体查询"),
]

HEADER = "/* 自动拆分自 ui/ui_template.html 第 {a}-{b} 行 —— {desc}\n   参考文件保持原样，本文件由 tools/split_reference_css.py 生成。 */\n"


def main():
    if not os.path.exists(SRC):
        print("参考文件不存在:", SRC)
        return 1
    lines = open(SRC, encoding="utf-8").read().splitlines()

    # 校验 <style> ... </style>
    style_start = next(i for i, l in enumerate(lines) if l.strip().lower() == "<style>")
    style_end = next(i for i, l in enumerate(lines) if l.strip().lower() == "</style>")
    print(f"<style> 行 {style_start + 1} .. </style> 行 {style_end + 1}")

    os.makedirs(OUT, exist_ok=True)
    for name, a, b, desc in SECTIONS:
        if not (style_start + 1 <= a <= b <= style_end):
            print(f"!! 区间越界: {name} {a}-{b}")
            return 1
        body = "\n".join(lines[a - 1:b])
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADER.format(a=a, b=b, desc=desc))
            f.write(body.rstrip() + "\n")
        print(f"  {name:22s} {b - a + 1:4d} 行  {os.path.getsize(path):6d} 字节")

    # 完整性校验：各段拼接后应与原 style 内容等价（忽略首尾空白）
    joined = []
    for name, a, b, _d in SECTIONS:
        joined.extend(lines[a - 1:b])
    original = lines[style_start + 1:style_end]
    ok = [l.strip() for l in joined if l.strip()] == [l.strip() for l in original if l.strip()]
    print("拼接完整性校验:", "OK" if ok else "不匹配")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
