"""验证语音输入页识别结果框的真实渲染状态（默认只读 + 编辑开关）。

在真实 QWebEngine 里切到语音页，分步读取/切换状态。

写法遵循知识库与既有脚本的结论，别再踩：
- KB-PERF-009（performance.md §10.8）：`router.go()` 非首次进入时**异步提交**，
  不能在同一段 JS 里紧接着断言 DOM；必须传 `{animate:false}` 并
  `router.refresh()`，且导航与断言分两次 `runJavaScript`。
- 全局只有 `window.APP`，`A` 是各模块内部的局部别名（core/namespace.js），
  所以 JS 里要先 `const A = window.APP`——smoke_gui.py 也是这么写的。
- 返回值统一 `JSON.stringify` 包一层；用 `processEvents` 同步等回调
  （与 tools/smoke_gui.py 一致）。
- **两次 click 必须分两次 runJavaScript**：同一同步块里连点两次，
  状态会在同一轮事件循环内来回切换，读到的 class 不反映中间态。

运行：py -3.14 tools/probe_voice_readonly.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

NAV_JS = (
    "(()=>{const A=window.APP;"
    "A.router.go('voice_input',{animate:false});A.router.refresh();"
    "return 'navigated';})()"
)

READ_JS = (
    "JSON.stringify((function(){"
    "var o=document.getElementById('asr-output');"
    "var e=document.getElementById('asr-edit-btn');"
    "if(!o) return {error:'no #asr-output'};"
    "return {readOnly:o.readOnly,hasAttr:o.hasAttribute('readonly'),"
    "cursor:getComputedStyle(o).cursor,cls:o.className,"
    "placeholder:o.getAttribute('placeholder')||'',"
    "label:e?e.textContent:null};"
    "})())"
)

CLICK_JS = (
    "JSON.stringify((function(){"
    "var o=document.getElementById('asr-output');"
    "var e=document.getElementById('asr-edit-btn');"
    "if(!e) return {error:'no #asr-edit-btn'};"
    "e.click();"
    "return {readOnly:o.readOnly,cls:o.className,label:e.textContent};"
    "})())"
)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from qt_web.main import WebMainWindow

    win = WebMainWindow()
    win.show()

    def call_js(js, timeout=8.0):
        box = {}
        win.view.page().runJavaScript(js, 0, lambda r: box.setdefault("v", r))
        deadline = time.time() + timeout
        while "v" not in box and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        return box.get("v")

    # 等 app.js 初始化（bridge.connect 是异步的）
    for _ in range(60):
        try:
            if json.loads(call_js(
                "JSON.stringify({r:!!(window.APP&&window.APP.router)})", 3
            ) or "{}").get("r"):
                break
        except Exception:
            pass
        app.processEvents()
        time.sleep(0.25)

    # 第 1 次：切页；第 2 次起：断言（见 KB-PERF-009）
    call_js(NAV_JS)
    time.sleep(1.0)
    app.processEvents()

    before = json.loads(call_js(READ_JS) or "{}")
    if "error" in before:
        print("PROBE FAILED:", before["error"])
        return 1

    after = json.loads(call_js(CLICK_JS) or "{}")
    time.sleep(0.2)
    app.processEvents()
    clicked = json.loads(call_js(READ_JS) or "{}")

    back = json.loads(call_js(CLICK_JS) or "{}")
    time.sleep(0.2)
    app.processEvents()
    restored = json.loads(call_js(READ_JS) or "{}")

    print(json.dumps(
        {"before": before, "afterClick": clicked, "afterSecondClick": restored},
        ensure_ascii=False, indent=2,
    ))

    checks = [
        ("初始 readOnly = true", before["readOnly"] is True, before["readOnly"]),
        ("初始有 readonly 属性", before["hasAttr"] is True, before["hasAttr"]),
        ("光标为 default", before["cursor"] == "default", before["cursor"]),
        ("占位文字不含「可直接修改」",
         "可直接修改" not in before["placeholder"], before["placeholder"]),
        ("初始按钮显示「编辑」", "编辑" in (before["label"] or ""), before["label"]),
        ("点一次后 readOnly = false", clicked["readOnly"] is False, clicked["readOnly"]),
        ("点一次后带上 editing 类", "editing" in clicked["cls"], clicked["cls"]),
        ("点一次后按钮显示「完成编辑」",
         "完成编辑" in (clicked["label"] or ""), clicked["label"]),
        ("点两次后回到只读", restored["readOnly"] is True, restored["readOnly"]),
        ("点两次后按钮回到「编辑」", "编辑" in (restored["label"] or ""), restored["label"]),
        ("切换确实发生变化", after.get("readOnly") is False or clicked["readOnly"] is False,
         after.get("readOnly")),
    ]

    print()
    ok = True
    for name, passed, detail in checks:
        mark = "PASS" if passed else "FAIL"
        extra = "" if passed else "   got=" + str(detail)
        print("  " + mark + "  " + name + extra)
        ok = ok and passed
    print()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
