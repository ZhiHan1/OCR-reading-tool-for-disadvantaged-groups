/* ============================================================
   键码映射
   把浏览器 KeyboardEvent 转成后端 keyboard 库认识的
   热键字符串：单键 "f7"、组合 "ctrl+f1"。
   后端全局热键走 OS 级钩子，与本模块无关；这里只服务于「按键设置」
   页面里的录制功能。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;

  /* KeyboardEvent.key -> keyboard 库键名 */
  const KEY_MAP = {
    " ": "space",
    Escape: "esc",
    Enter: "enter",
    Tab: "tab",
    Backspace: "backspace",
    Delete: "delete",
    Insert: "insert",
    Home: "home",
    End: "end",
    PageUp: "page up",
    PageDown: "page down",
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    CapsLock: "caps lock",
    "`": "`",
    "-": "-",
    "=": "=",
    "[": "[",
    "]": "]",
    "\\": "\\",
    ";": ";",
    "'": "'",
    ",": ",",
    ".": ".",
    "/": "/",
  };

  /* 纯修饰键：按下时先不结束录制，等真正的键 */
  const MODIFIERS = ["Control", "Shift", "Alt", "Meta", "CapsLock"];

  function normalizeKey(ev) {
    /* QWebEngine 对 F1-F12 的 key/code 在不同 Qt/Chromium 版本里略有差异；
       优先使用 key，异常时从 code 还原，避免语音输入页无法录制功能键。 */
    const raw = (/^F\d{1,2}$/i.test(ev.code || "") &&
      (!ev.key || ev.key === "Unidentified" || ev.key === "Unknown"))
      ? ev.code
      : (ev.key || ev.code || "");
    const k = /^Key[A-Z]$/.test(raw) ? raw.slice(3) : raw;

    if (k === "Control") return "ctrl";
    if (k === "Shift") return "shift";
    if (k === "Alt") return "alt";
    if (k === "Meta") return "windows";

    if (/^F\d{1,2}$/i.test(k)) return k.toLowerCase();
    if (/^F\d{1,2}$/i.test(ev.code || "")) return String(ev.code).toLowerCase();
    if (KEY_MAP[k]) return KEY_MAP[k];

    /* 字母 / 数字 / 符号：统一小写 */
    if (k.length === 1) return k.toLowerCase();

    return k.toLowerCase();
  }

  function modifierPrefix(ev) {
    const parts = [];
    if (ev.ctrlKey) parts.push("ctrl");
    if (ev.altKey) parts.push("alt");
    if (ev.shiftKey) parts.push("shift");
    if (ev.metaKey) parts.push("windows");
    return parts;
  }

  const keys = {
    isModifierOnly: function (ev) {
      return MODIFIERS.indexOf(ev.key) >= 0;
    },

    /* 键盘事件 -> "ctrl+f1" 这类字符串；纯修饰键返回 null */
    fromKeyEvent: function (ev) {
      if (keys.isModifierOnly(ev)) return null;
      const base = normalizeKey(ev);
      if (!base) return null;
      const mods = modifierPrefix(ev);
      /* 组合键里的修饰键本身不再重复 */
      return mods.concat([base]).join("+");
    },

    /* 把后端配置值转成键帽显示文本：ctrl+f1 -> Ctrl+F1 */
    display: function (value) {      if (!value) return "未绑定快捷键";
      const v = String(value);
      return v
        .split("+")
        .map(function (p) {
          if (p === "ctrl") return "Ctrl";
          if (p === "alt") return "Alt";
          if (p === "shift") return "Shift";
          if (p === "windows") return "Win";
          if (p === "esc") return "Esc";
          if (p === "space") return "Space";
          if (p.length <= 3 && /^f\d+$/i.test(p)) return p.toUpperCase();
          if (p.length === 1) return p.toUpperCase();
          return p.charAt(0).toUpperCase() + p.slice(1);
        })
        .join("+");
    },
  };

  /* ------------------------------------------------------------
     键帽录制（共享）
     点击键帽 → 捕获下一个键盘键 → 写回后端配置。
     「按键设置」页和「语音输入」页都用它；没有 data-hotkey 的键帽
     只做视觉反馈（见 components.bindCommon）。
     ------------------------------------------------------------ */
  keys.bindCapture = function (root) {
    A.qsa("kbd.cap[data-hotkey]", root || document).forEach(function (cap) {
      if (cap.dataset.captureBound === "1") return;
      cap.dataset.captureBound = "1";

      const which = cap.dataset.hotkey;

      cap.addEventListener("click", function (e) {
        e.stopPropagation();
        if (cap.dataset.capturing === "1") return;

         cap.dataset.capturing = "1";
          const oldText = cap.textContent;
         cap.textContent = "按键…";
         /* kbd 默认不可获得键盘焦点；QWebEngine 在点击后会把按键留给
            Chromium 内部控件，导致 window 捕获不到。主动把焦点放回键帽。 */
         cap.setAttribute("tabindex", "0");
         try { cap.focus(); } catch (err) { /* ignore */ }

        let finished = false;

        function cleanup() {
          finished = true;
          cap.dataset.capturing = "0";
           window.removeEventListener("keydown", onKey, true);
           document.removeEventListener("keydown", onKey, true);
          A.bridge.call("hotkey_capture_end", {});
        }

         function restore() {
           cleanup();
           cap.textContent = oldText;
         }

         function clearBinding() {
           if (finished) return;
           cleanup();
           A.bridge.call("set_hotkey", { key: which, value: "" }).then(function (res) {
             if (res && res.ok) {
               cap.textContent = keys.display("");
               A.state.hotkeys = res.hotkeys || A.state.hotkeys;
               A.bridge.emit("toast", { text: "已清空快捷键", ok: true });
             } else {
               cap.textContent = oldText;
             }
           }).catch(function () { cap.textContent = oldText; });
         }

        function commit(value) {
          if (finished) return;
          cleanup();
          A.bridge
            .call("set_hotkey", { key: which, value: value })
            .then(function (res) {
              if (res && res.ok) {
                cap.textContent = keys.display(res.value || value);
                A.bridge.emit("toast", { text: "✅ 已设置：" + cap.textContent, ok: true });
                if (res.hotkeys) {
                  A.state.hotkeys = res.hotkeys;
                  A.bridge.emit("hotkeys-changed", res.hotkeys);
                }
              } else {
                cap.textContent = oldText;
                A.bridge.emit("toast", {
                  text: "❌ " + ((res && res.error) || "该按键已被占用"),
                  ok: false,
                });
              }
            })
            .catch(function () {
              cap.textContent = oldText;
            });
        }

         function onKey(ev) {
           if (ev.key === "Escape" || ev.key === "Esc" || ev.code === "Escape") {
             ev.preventDefault();
             ev.stopPropagation();
             clearBinding();
             return;
          }
          /* 先拦截浏览器的 F1/F5/F6/F10 等默认动作，再解析并保存。 */
          if (/^F\d{1,2}$/i.test(ev.key || "") || /^F\d{1,2}$/i.test(ev.code || "")) {
            ev.preventDefault();
            ev.stopPropagation();
          }
          const value = keys.fromKeyEvent(ev);
          if (!value) return; // 纯修饰键，继续等
          ev.preventDefault();
          ev.stopPropagation();
          commit(value);
        }

         A.bridge.call("hotkey_capture_start", {});
         window.addEventListener("keydown", onKey, true);
         document.addEventListener("keydown", onKey, true);
       });

     });
  };

  A.keys = keys;
})();
