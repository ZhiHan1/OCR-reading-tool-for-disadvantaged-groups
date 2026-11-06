/* ============================================================
   结果区与状态栏
   所有 OCR 功能共用同一块结果显示区，以及底部状态栏。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const area = A.qs("#result-area");

  const result = {
    /* 构建结果区结构（只在启动时调用一次） */
    build: function () {
      if (!area) return;
      area.innerHTML =
        '<div class="result-head">' +
        '<span class="result-title">识别结果 <span class="tag">所有功能共用</span></span>' +
        '<div class="result-tools">' +
        '<button class="icon-btn" id="result-copy" title="复制">📋</button>' +
        '<button class="icon-btn" id="result-clear" title="清空">🗑️</button>' +
        "</div></div>" +
        '<textarea id="result-text" readonly placeholder="识别结果将显示在这里…"></textarea>';

      A.on(A.qs("#result-copy"), "click", function () {
        const ta = A.qs("#result-text");
        if (!ta || !ta.value) return;
        /* QWebEngine 里 navigator.clipboard 常被权限拦掉，统一走 Python 剪贴板 */
        A.bridge.call("copy_text", { text: ta.value }).then(function (res) {
          if (res && res.ok) {
            result.setStatus("已复制识别结果到剪贴板", "ok");
            return;
          }
          /* 兜底：后端不可用时退回选区 + execCommand */
          ta.select();
          try {
            document.execCommand("copy");
          } catch (e) {
            /* ignore */
          }
          result.setStatus("已复制识别结果到剪贴板", "ok");
        });
      });

      A.on(A.qs("#result-clear"), "click", function () {
        const ta = A.qs("#result-text");
        if (ta) ta.value = "";
        A.bridge.call("clear_result", {});
      });
    },

    show: function (visible) {
      if (area) area.style.display = visible ? "" : "none";
    },

    set: function (text, append) {
      const ta = A.qs("#result-text");
      if (!ta) return;
      if (append && ta.value) ta.value = ta.value + "\n" + text;
      else ta.value = text;
      ta.scrollTop = ta.scrollHeight;
    },

    get: function () {
      const ta = A.qs("#result-text");
      return ta ? ta.value : "";
    },

    clear: function () {
      const ta = A.qs("#result-text");
      if (ta) ta.value = "";
    },

    /* 状态栏文案：kind = "" | "ok" | "warn" | "err" */
    setStatus: function (text, kind) {
      const el = A.qs("#status-text");
      if (!el) return;
      const color =
        kind === "ok"
          ? "var(--ink-green)"
          : kind === "warn"
          ? "var(--ink-amber)"
          : kind === "err"
          ? "var(--ink-rose)"
          : "";
      el.innerHTML = '<span class="sdot"></span>' + A.esc(text);
      el.style.color = color;
    },
  };

  A.result = result;

  A.on(A.qs("#stop-speak"), "click", function () {
    A.bridge.call("stop_speak", {});
    result.setStatus("已停止朗读", "");
  });
})();
