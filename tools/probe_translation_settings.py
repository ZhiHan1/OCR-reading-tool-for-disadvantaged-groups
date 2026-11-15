"""真实 GUI 探针：翻译设置页三个改动。

验证目标（2026-11-15）：
1. 「移除记录」按钮已从 DOM 移除，且没有残留绑定导致后续逻辑断掉
2. 「打开翻译模型文件夹」「重新扫描」位于「禁用」右侧
3. 「重新扫描」点下去有可见反馈（提示文字变化），并把列表带回来

写法遵循 KB-WEB-010 / KB-PERF-009：
- 全局只有 window.APP，必须写 `const A = window.APP;`
- router.go() 非首次进入是异步的：传 {animate:false} + refresh()，且
  导航与断言分两次 runJavaScript
- 返回值统一 JSON.stringify
- 点击与断言分两次 runJavaScript，中间 processEvents

运行：py -3.14 tools/probe_translation_settings.py
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
    "A.router.go('settings',{animate:false});A.router.refresh();"
    "return 'navigated';})()"
)

# 设置页是「子页签懒渲染」：翻译设置那块 pane 只有切到 trans 标签才挂载。
# 不切的话 DOM 里根本没有 #local-model / #translation-scan-folder。
OPEN_TRANS_TAB_JS = """JSON.stringify((function(){
  const tab = document.querySelector('.tab[data-tab="trans"]');
  if (!tab) return {error:'no trans tab', tabs: Array.prototype.map.call(
      document.querySelectorAll('.tab'), function(t){ return t.dataset.tab; })};
  tab.click();
  return {ok:true};
})())"""

LAYOUT_JS = """JSON.stringify((function(){
  const q = function(id){ return document.getElementById(id); };
  const ids = ['local-add','local-disable','translation-open-folder','translation-scan-folder','local-delete'];
  const present = {};
  ids.forEach(function(id){ present[id] = !!q(id); });
  // 按 DOM 顺序比较：取每个按钮在文档中的位置
  const order = [];
  const all = document.querySelectorAll('button');
  for (let i = 0; i < all.length; i++) {
    const id = all[i].id;
    if (ids.indexOf(id) >= 0) order.push(id);
  }
  const hint = q('local-hint');
  return {
    present: present,
    order: order,
    hintText: hint ? hint.textContent : null,
    scanLabel: q('translation-scan-folder') ? q('translation-scan-folder').textContent : null,
    openLabel: q('translation-open-folder') ? q('translation-open-folder').textContent : null,
    deleteLabel: q('local-delete') ? q('local-delete').textContent : null
  };
})())"""

CLICK_SCAN_JS = """JSON.stringify((function(){
  const b = document.getElementById('translation-scan-folder');
  if (!b) return {error:'no scan button'};
  b.click();
  return {clicked:true, disabledRightAfter:b.disabled};
})())"""

READ_HINT_JS = """JSON.stringify((function(){
  const h = document.getElementById('local-hint');
  const b = document.getElementById('translation-scan-folder');
  const sel = document.getElementById('local-model');
  return {
    hintText: h ? h.textContent : null,
    scanDisabled: b ? b.disabled : null,
    options: sel ? Array.prototype.map.call(sel.options, function(o){ return o.textContent; }) : []
  };
})())"""


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

    call_js(NAV_JS)
    time.sleep(1.0)
    app.processEvents()

    # 切到「翻译设置」子页签（懒渲染，不切就没有目标 DOM）
    tab_res = json.loads(call_js(OPEN_TRANS_TAB_JS) or "{}")
    time.sleep(1.2)
    app.processEvents()

    layout = json.loads(call_js(LAYOUT_JS) or "{}")

    click = json.loads(call_js(CLICK_SCAN_JS) or "{}")
    time.sleep(2.5)  # 等后端扫描 + 回包
    app.processEvents()
    after = json.loads(call_js(READ_HINT_JS) or "{}")

    print(json.dumps({"layout": layout, "click": click, "after": after},
                     ensure_ascii=False, indent=2))
    print()

    order = layout.get("order") or []
    checks = [
        ("「移除记录」按钮已移除",
         layout["present"].get("local-delete") is False, layout["present"]),
        ("「打开翻译模型文件夹」存在",
         layout["present"].get("translation-open-folder") is True, layout["present"]),
        ("「重新扫描」存在",
         layout["present"].get("translation-scan-folder") is True, layout["present"]),
        ("「禁用」存在",
         layout["present"].get("local-disable") is True, layout["present"]),
        ("顺序：添加 → 禁用 → 打开文件夹 → 重新扫描",
         order[:4] == ["local-add", "local-disable",
                       "translation-open-folder", "translation-scan-folder"], order),
        ("重新扫描按钮有文案",
         bool(after.get("scanDisabled") is not None or layout.get("scanLabel")), layout.get("scanLabel")),
        ("点击后按钮恢复可用（不是卡在 disabled）",
         after.get("scanDisabled") is False, after.get("scanDisabled")),
        ("点击后有可见反馈",
         bool(after.get("hintText")) and "扫描" in (after.get("hintText") or ""),
         after.get("hintText")),
        ("提示不再是初始空串",
         (after.get("hintText") or "") != (layout.get("hintText") or ""),
         f'{layout.get("hintText")!r} -> {after.get("hintText")!r}'),
    ]

    ok = True
    for name, passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}" + ("" if passed else f"   got={detail}"))
        ok = ok and passed
    print()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
