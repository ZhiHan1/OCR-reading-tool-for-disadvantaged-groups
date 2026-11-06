/* ============================================================
   弹窗模块
   所有页面共用：APP.modal.open(html) 返回弹窗根节点，便于绑定事件。
   支持：点遮罩关闭、ESC 关闭、[data-close] 关闭。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const mask = A.qs("#modal-mask");
  const box = A.qs("#modal-box");
  let onCloseCb = null;

  const modal = {
    open: function (html, onClose) {
      if (!mask || !box) return null;
      box.innerHTML = html;
      mask.classList.add("open");
      onCloseCb = onClose || null;
      // 弹窗内的开关/滑块/键帽沿用统一行为
      if (A.components && A.components.bindCommon) A.components.bindCommon(box);
      A.qsa("[data-close]", box).forEach(function (b) {
        b.addEventListener("click", modal.close);
      });
      return box;
    },

    close: function () {
      if (!mask || !box) return;
      mask.classList.remove("open");
      box.innerHTML = "";
      const cb = onCloseCb;
      onCloseCb = null;
      if (cb) {
        try {
          cb();
        } catch (e) {
          console.error("[modal] onClose error", e);
        }
      }
    },

    isOpen: function () {
      return !!(mask && mask.classList.contains("open"));
    },
  };

  A.modal = modal;

  if (mask) {
    mask.addEventListener("mousedown", function (e) {
      if (e.target === mask) modal.close();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal.isOpen()) modal.close();
  });
})();
