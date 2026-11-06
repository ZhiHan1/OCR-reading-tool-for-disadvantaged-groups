/* ============================================================
   主题模块（深色 / 亮色 + 毛玻璃）
   侧边栏、顶栏按钮、设置页主题页签都调用同一套 API：
     APP.theme.set("dark" | "light")
     APP.theme.toggle()
     APP.theme.setFrost(bool)
     APP.theme.current()
   持久化交给后端配置，本模块只负责立即生效 + 通知订阅者。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const root = document.documentElement;
  const subs = [];

  function current() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function frostOn() {
    return !root.classList.contains("no-frost");
  }

  function notify() {
    const info = { theme: current(), frost: frostOn() };
    for (let i = 0; i < subs.length; i++) {
      try {
        subs[i](info);
      } catch (e) {
        console.error("[theme] subscriber error", e);
      }
    }
    A.bridge.emit("theme-changed", info);
  }

  const theme = {
    current: current,
    frostOn: frostOn,

    /* 订阅主题变化：fn({theme, frost}) */
    subscribe: function (fn) {
      subs.push(fn);
      return function () {
        const i = subs.indexOf(fn);
        if (i >= 0) subs.splice(i, 1);
      };
    },

    set: function (mode, silent) {
      if (mode === "light") root.setAttribute("data-theme", "light");
      else root.removeAttribute("data-theme");
      if (!silent) {
        A.bridge.call("set_theme", { theme: current() });
      }
      syncControls();
      notify();
    },

    toggle: function () {
      theme.set(current() === "light" ? "dark" : "light");
    },

    /* 主题切换的圆形揭示动画：从点击位置 (x, y) 向外扩展一个圆，
       圆内显示目标主题的背景色，圆外仍是当前主题，形成「颜色随圆扩散」的效果。
       揭示层盖在背景光球之上、内容之下，因此卡片文字始终可见。
       动画展开到中段时切换主题，随后移除揭示层，实现无缝过渡。 */
    animateTransition: function (x, y, target) {
      const dark = target === "dark";
      // 目标主题的背景渐变，与 body 背景一致（用固定色值，避免变量在切换前被覆盖）
      const bg = dark
        ? "linear-gradient(158deg, #111527, #0b0d16 58%, #171c33)"
        : "linear-gradient(158deg, #eff4ff, #d3dbee 58%, #dae1f1)";

      const layer = document.createElement("div");
      layer.className = "theme-ripple";
      layer.style.cssText =
        "position:fixed;inset:0;z-index:1;pointer-events:none;" +
        "background:" + bg + ";" +
        "clip-path:circle(0% at " + x + "px " + y + "px);" +
        "will-change:clip-path;";

      // 插入到 .app 之前（背景光球之后），确保内容仍显示在上层
      const app = document.querySelector(".app");
      if (app) document.body.insertBefore(layer, app);
      else document.body.appendChild(layer);

      // 立即切换主题：卡片颜色第一时间就位，圆扩散只是视觉揭示。
      // 若等到中段才切换，前半段卡片仍是旧主题色，会「闪一下旧色」。
      theme.set(target);

      layer.animate(
        [
          { clipPath: "circle(0% at " + x + "px " + y + "px)" },
          { clipPath: "circle(150% at " + x + "px " + y + "px)" },
        ],
        {
          duration: 760,
          easing: "cubic-bezier(.22,1,.36,1)",
          fill: "forwards",
        }
      ).onfinish = function () {
        layer.remove();
      };
    },

    setFrost: function (on, silent) {
      root.classList.toggle("no-frost", !on);
      if (!silent) {
        A.bridge.call("set_frost", { frost: !!on });
      }
      syncControls();
      notify();
    },

    /* 把当前主题同步到所有相关控件（顶栏按钮 / 设置页模式卡 / 毛玻璃开关） */
    syncControls: syncControls,
  };

  function syncControls() {
    const btn = A.qs("#theme-toggle");
    if (btn) btn.textContent = current() === "light" ? "🌙 深色" : "☀️ 亮色";

    A.qsa(".theme-mode").forEach(function (el) {
      el.classList.toggle("active", el.dataset.mode === current());
    });

    const fs = A.qs("#frost-switch");
    if (fs) fs.classList.toggle("on", frostOn());
  }

  A.theme = theme;

  /* 顶栏主题按钮 */
  A.on(A.qs("#theme-toggle"), "click", function (e) {
    const target = current() === "light" ? "dark" : "light";
    const x = e.clientX != null ? e.clientX : window.innerWidth / 2;
    const y = e.clientY != null ? e.clientY : window.innerHeight / 2;
    theme.animateTransition(x, y, target);
  });

  /* 启动时应用后端给的初始值（app.js 在拿到 state 后调用） */
  A.theme.applyState = function (state) {
    if (state && state.theme) theme.set(state.theme, true);
    if (state && typeof state.frost === "boolean") theme.setFrost(state.frost, true);
    syncControls();
  };
})();
