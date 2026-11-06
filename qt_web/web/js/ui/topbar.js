/* ============================================================
   顶栏（页面标题 / 描述）
   主题按钮在 core/theme.js 里绑定，这里只负责标题文案。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;

  const topbar = {
    set: function (title, desc) {
      const t = A.qs("#page-title");
      const d = A.qs("#page-desc");
      if (t) t.innerHTML = '<span class="accent">' + A.esc(title) + "</span>";
      if (d) d.textContent = desc || "";
    },
  };

  A.topbar = topbar;
})();
