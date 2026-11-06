"""GUI 冒烟自检（开发态）：真实 WebMainWindow + 全部页面 + 关键后端动作。

用法：py -3.14 -m tools.smoke_gui [--show]
输出 JSON 报告到 stdout（日志走 stderr）。
"""
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from utils.env_limits import apply_runtime_limits  # noqa: E402

apply_runtime_limits()

report = {"steps": {}}


def step(name, fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        report["steps"][name] = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "value": value}
    except Exception:
        report["steps"][name] = {"ok": False, "error": traceback.format_exc()}
    return report["steps"][name]


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    from qt_web.main import WebMainWindow
    from core.ocr_engine import OCREngine

    t0 = time.perf_counter()
    win = WebMainWindow()
    report["window_init_ms"] = round((time.perf_counter() - t0) * 1000)
    if "--show" in sys.argv:
        win.show()

    def do_pages():
        # router.go() 带退场动画（setTimeout 200ms+），同步查询拿不到新页面。
        # 因此这里对最后一页改用 router.refresh()（同步重绘）再统计，避免假阴性。
        js = ("(()=>{const A=window.APP;const out={};"
              "A.router.go('home',{animate:false});"
              "for(const n of ['home','translate','tts','voice_input','settings','about']){"
              "A.router.go(n,{animate:false});"
              "const c=document.querySelector('#content');out[n]=!!c&&c.textContent.trim().length>0;}"
              "A.router.go('voice_input',{animate:false});A.router.refresh();"
              "out.radios=document.querySelectorAll('[name=asr-input-mode]').length===3;"
              "out.navBridge=!!(A.nav&&A.nav.page);"
              "return JSON.stringify(out);})()")
        out = {}
        win.view.page().runJavaScript(js, 0, lambda r: out.setdefault("js", r))
        deadline = time.time() + 10
        while "js" not in out and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        return json.loads(out["js"])

    def do_state():
        state = win.api.do_state({})
        assert state.get("ok"), state
        s = state["state"]
        return {
            "regions": s.get("regions"),
            "running": s.get("running"),
            "models": len(s.get("asr", {}).get("models") or []),
            "scan_interval": win.api._model_scan_timer.interval(),
        }

    def do_nav_hot():
        win.nav.page("settings")
        hot = win.api._model_scan_timer.interval()
        win.nav.page("home")
        cold = win.api._model_scan_timer.interval()
        return {"settings_interval": hot, "home_interval": cold}

    def do_ocr():
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 56)
        img = Image.new("RGB", (900, 140), "white")
        ImageDraw.Draw(img).text((20, 30), "OCR SMOKE 2026", font=font, fill="black")
        return OCREngine().recognize(img)

    def do_tts():
        win.app.tts_service.speak("冒烟测试", interrupt=True)
        time.sleep(0.3)
        win.app.tts_service.stop_current()
        return "spoken"

    def do_translate():
        out = win.app.translation_service.translate_with_lang("Hello world", "en", "zh")
        return (out or "")[:60]

    def do_shot():
        svc = win.app.screenshot_service
        return [
            type(svc.selector).__name__ if svc.selector is not None else None,
            callable(getattr(svc.selector, "select", None)),
        ]

    def do_voice_dev():
        # 开发态语音：python -m qt_web.voice_worker
        s = win.voice
        return {"model_status": s.model_status, "phase": getattr(s, "phase", None)}

    def missing_gui_models():
        return sorted(k for k in ("func1", "func2", "func3", "manga_mode", "game_mode") if k in win.app._lazy)

    def done():
        step("pages", do_pages)
        step("state", do_state)
        step("nav_interval", do_nav_hot)
        step("screenshot_selector", do_shot)
        step("ocr", do_ocr)
        step("tts", do_tts)
        step("translate", do_translate)
        step("gui_controllers_touched", missing_gui_models)
        report["ok"] = all(v.get("ok") for v in report["steps"].values())
        try:
            win.api._model_scan_timer.stop()
            win.cursor_input.timer.stop()
            win.voice.close()
            win.pause_hotkeys(True)
            win.tray.stop()
        except Exception:
            report["cleanup_error"] = traceback.format_exc()
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stdout, flush=True)
        os._exit(0 if report["ok"] else 1)

    def loaded(ok):
        report["load_finished"] = bool(ok)
        report["stuck_stage"] = "loadFinished(callback)"
        if not ok:
            report["ok"] = False
            report["error"] = "HTML failed to load"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            os._exit(1)

        def start_js():
            # 等 qwebchannel 建好（bridge.connect 是异步的）
            def probe(tries=0):
                ready = {}
                win.view.page().runJavaScript(
                    "JSON.stringify({ready:!!(window.APP&&window.APP.nav),"
                    "app:typeof window.APP,"
                    "rd:document.querySelectorAll('[name=asr-input-mode]').length})",
                    0,
                    lambda r: ready.setdefault("v", r),
                )
                t = time.time() + 5
                while "v" not in ready and time.time() < t:
                    app.processEvents()
                    time.sleep(0.02)
                if "v" not in ready:
                    report["js_ready"] = {"no_response": True}
                    done()
                    return
                try:
                    got = json.loads(ready.get("v") or "{}")
                except Exception:
                    got = {}
                report["js_ready"] = got
                if not got.get("ready") and tries < 8 and got.get("app") == "object":
                    QTimer.singleShot(400, lambda: probe(tries + 1))
                    return
                done()

            probe()

        QTimer.singleShot(1500, start_js)

    win.view.loadFinished.connect(loaded)
    QTimer.singleShot(90000, lambda: (report.update(ok=False, error="timeout"), print(json.dumps(report, ensure_ascii=False, indent=2)), os._exit(1)))
    app.exec()


if __name__ == "__main__":
    main()
