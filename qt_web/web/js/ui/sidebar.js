/* ============================================================
   侧边栏
   导航项不再写死在 HTML 里，而是依据 APP.pages 路由表 + 分组
   自动生成 —— 以后新增页面只要注册到 APP.pages 就会自动出现。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const nav = A.qs("#nav");

  function render() {
    if (!nav) return;

    let html = '<div class="nav-pill" id="nav-pill"></div>';

    A.navGroups.forEach(function (group) {
      const keys = Object.keys(A.pages).filter(function (k) {
        return A.pages[k].group === group;
      });
      if (!keys.length) return;

      html += '<div class="nav-group">' + A.esc(group) + "</div>";
      keys.forEach(function (key) {
        const p = A.pages[key];
        html +=
          '<div class="nav-item" data-page="' +
          A.esc(key) +
          '"><span class="ico">' +
          (p.icon || "") +
          '</span><span class="txt">' +
          A.esc(p.title) +
          "</span></div>";
      });
    });

    nav.innerHTML = html;

    A.qsa(".nav-item", nav).forEach(function (item) {
      item.addEventListener("click", function () {
        A.router.go(item.dataset.page);
      });
    });
  }

  const sidebar = {
    render: render,

    setActive: function (key, animate) {
      if (!nav) return;
      A.qsa(".nav-item", nav).forEach(function (i) {
        i.classList.toggle("active", i.dataset.page === key);
      });
      sidebar.syncPill(animate);
    },

    syncPill: function (animate) {
      const pill = A.qs("#nav-pill", nav);
      const active = A.qs(".nav-item.active", nav);
      A.movePill(pill, active, animate);
    },

    setEngineLabel: function (text) {
      const el = A.qs("#engine-label");
      if (el) el.textContent = text;
    },
  };

  A.sidebar = sidebar;

  window.addEventListener("resize", function () {
    sidebar.syncPill(false);
  });
})();
