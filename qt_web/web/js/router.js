/* ============================================================
   路由
   页面通过 APP.pages 注册（见 js/pages/*.js），这里负责：
   切页 → 渲染标题 → 注入内容 → 调用页面的 mount 绑定事件。
   切页动画：旧页淡出 → 换内容 → 新页卡片逐个弹入（登入）。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const content = A.qs("#content");
  let current = null;
  let disposeScope = null;

  /* 快速连点保护：只让最后一次切页落地 */
  let navToken = 0;
  let leaveTimer = null;

  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* ============================================================
     共享：收集参与动画的元素 + 排序分组
     ============================================================ */

  /* 最外层卡片 + 分区标题（排除「卡片套卡片」的嵌套）。 */
  function collectItems(root) {
    const all = A.qsa(".func-card, .card, .section-head", root);
    return all.filter(function (el) {
      const inCard = el.closest(".card, .func-card");
      return inCard === null || inCard === el;
    });
  }

  /* 把元素拆成「向下弹入」和「向上弹入(enter-up)」两组；
     首页的 #result-area（content 外）也归入向上组，保持登入/退场对称。 */
  function splitGroups(items) {
    const down = [];
    const up = [];
    items.forEach(function (el) {
      if (el.classList.contains("enter-up")) up.push(el);
      else down.push(el);
    });
    const resultArea = A.qs("#result-area");
    if (resultArea && resultArea.style.display !== "none" && up.indexOf(resultArea) < 0) {
      up.push(resultArea);
    }
    return { down: down, up: up };
  }

  /* 按视觉 Y 从上到下分组成行（同一排 top 差 < 24px 为一行）。 */
  function groupRows(items) {
    const sorted = items.slice().sort(function (a, b) {
      return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
    });
    const rows = [];
    let lastTop = -Infinity;
    sorted.forEach(function (el) {
      const top = el.getBoundingClientRect().top;
      if (rows.length === 0 || top - lastTop > 24) {
        rows.push([]);
        lastTop = top;
      }
      rows[rows.length - 1].push(el);
    });
    return rows;
  }

  /* 旧页退场：卡片做「反向收起」动画，与登入严格相反。
     - 方向：向下弹入的卡片向上收回，enter-up 卡片向下收回。
     - 顺序：登入是「向下弹各行（从上到下）→ enter-up 卡片（最后）」；
       退场反转成「enter-up 卡片先收 → 向下弹各行从下到上逐行收」。
     位移为主导，淡出为次要；fill:forwards 保持结束态避免闪回。 */
  function animateLeave(done) {
    if (!content || reduceMotion()) {
      done();
      return;
    }

    const groups = splitGroups(collectItems(content));
    const downRows = groupRows(groups.down);

    /* 构造严格反转的退场顺序：{el, dy} 数组 */
    const seq = [];
    groups.up.slice().reverse().forEach(function (el) {
      seq.push({ el: el, dy: 26 }); /* 向上弹入的往下收回 */
    });
    downRows.slice().reverse().forEach(function (row) {
      row.slice().reverse().forEach(function (el) {
        const isTitle = el.classList.contains("section-head");
        seq.push({ el: el, dy: isTitle ? -10 : -26 }); /* 向下弹入的往上收回 */
      });
    });

    if (!seq.length) {
      /* 页面没有任何卡片时，退化为整体淡出 */
      content.animate(
        [{ opacity: 1 }, { opacity: 0 }],
        { duration: 160, easing: "cubic-bezier(.4,0,.7,.2)", fill: "forwards" }
      );
      if (leaveTimer) clearTimeout(leaveTimer);
      leaveTimer = setTimeout(function () {
        leaveTimer = null;
        content.getAnimations().forEach(function (a) { a.cancel(); });
        content.style.opacity = "";
        done();
      }, 160);
      return;
    }

    const DURATION = 200;
    const STAGGER = 25;

    seq.forEach(function (item, i) {
      item.el.animate(
        [
          { opacity: 1, transform: "translateY(0) scale(1)" },
          { opacity: 0, transform: "translateY(" + item.dy + "px) scale(0.98)" },
        ],
        {
          duration: DURATION,
          delay: i * STAGGER,
          easing: "cubic-bezier(.4,0,.7,.2)",
          fill: "forwards",
        }
      );
    });

    const total = DURATION + seq.length * STAGGER;
    if (leaveTimer) clearTimeout(leaveTimer);
    leaveTimer = setTimeout(function () {
      leaveTimer = null;
      /* 换内容前清掉 forwards 冻结，避免新内容（及 #result-area）继承透明 */
      content.getAnimations().forEach(function (a) { a.cancel(); });
      const ra = A.qs("#result-area");
      if (ra) ra.getAnimations().forEach(function (a) { a.cancel(); });
      content.style.opacity = "";
      done();
    }, total);
  }

  /* 新页登入：分两阶段弹入。
     第一阶段：普通卡片按视觉位置从上往下、逐行「向下弹出」。
     第二阶段：enter-up 卡片（结果/输出/底部配置类）在向下弹完后「向上弹出」。 */
  function animateEnter() {
    if (!content || reduceMotion()) return;

    const groups = splitGroups(collectItems(content));
    const downRows = groupRows(groups.down);
    const upItems = groups.up;

    const DURATION = 240;      /* 单卡弹入时长（业界 200~250ms） */
    const STAGGER = 45;        /* 行间交错步进（Material choreography 40~50ms） */
    const DURATION_UP = 250;

    /* ---- 向下弹的卡片：逐行交错叠加 ---- */
    downRows.forEach(function (row, r) {
      row.forEach(function (el) {
        const isTitle = el.classList.contains("section-head");
        el.animate(
          [
            { opacity: 0, transform: "translateY(" + (isTitle ? -8 : -22) + "px) scale(" + (isTitle ? 0.995 : 0.98) + ")" },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          {
            duration: DURATION,
            delay: r * STAGGER,
            easing: "cubic-bezier(.2,.8,.2,1)",
            fill: "backwards",
          }
        );
      });
    });

    /* 向下弹全部结束后（末行开始时间 + 时长），再依次向上弹 */
    const downDoneAt = (downRows.length ? (downRows.length - 1) * STAGGER : 0) + DURATION;

    /* ---- 向上弹的卡片 ---- */
    upItems.forEach(function (el, i) {
      el.animate(
        [
          { opacity: 0, transform: "translateY(24px) scale(.98)" },
          { opacity: 1, transform: "translateY(0) scale(1)" },
        ],
        {
          duration: DURATION_UP,
          delay: downDoneAt + i * STAGGER,
          easing: "cubic-bezier(.2,.8,.2,1)",
          fill: "backwards",
        }
      );
    });
  }

  /* 释放当前页注册的事件订阅 */
  function disposeCurrent() {
    if (!disposeScope) return;
    try {
      disposeScope();
    } catch (e) {
      console.error("[router] scope dispose error", e);
    }
    disposeScope = null;
  }

  /* 真正把页面渲染进容器（含 mount / 订阅 / 选中态） */
  function commit(name, page, animate) {
    A.topbar.set(page.title, page.desc);

    content.innerHTML = page.render ? page.render() : "";
    A.components.bindCommon(content);

    /* 本页的订阅在切走时自动释放 */
    disposeScope = A.bridge.beginScope();

    if (page.mount) {
      try {
        page.mount(content);
      } catch (e) {
        console.error("[router] mount error on " + name, e);
      }
    }

    /* 结果区只在首页显示 */
    A.result.show(name === "home");

    /* 侧边栏选中态与滑块 */
    A.sidebar.setActive(name, true);

    /* 回到顶部 */
    const scroller = content.parentElement;
    if (scroller) scroller.scrollTop = 0;

    A.bridge.emit("page-changed", { page: name });

    /* 通知后端当前页：语音/设置页才需要高频扫描模型目录 */
    if (A.nav && A.nav.page) {
      try { A.nav.page(name); } catch (e) { /* 通道未就绪时忽略 */ }
    }

    if (animate !== false) animateEnter();
  }

  const router = {
    current: function () {
      return current;
    },

    go: function (name, opts) {
      const page = A.pages[name];
      if (!page || !content) return;
      if (current === name) return;

      opts = opts || {};
      const prev = current;
      const animate = opts.animate !== false;
      const token = ++navToken;

      current = name;

      /* 释放上一页注册的事件订阅 */
      disposeCurrent();

      const run = function () {
        /* 连点期间已被更新的切页请求取代，丢弃这次渲染 */
        if (token !== navToken) return;
        commit(name, page, animate);
      };

      /* 首次进入不淡出；refresh 也不淡出 */
      if (prev && animate) animateLeave(run);
      else run();
    },

    /* 数据变化后重绘当前页（保持滚动位置，不播放切页动画） */
    refresh: function () {
      if (!current) return;
      const page = A.pages[current];
      if (!page) return;
      const scroller = content ? content.parentElement : null;
      const top = scroller ? scroller.scrollTop : 0;
      navToken++; /* 作废进行中的切页动画 */
      if (leaveTimer) {
        clearTimeout(leaveTimer);
        leaveTimer = null;
      }
      disposeCurrent();
      commit(current, page, false);
      if (scroller) scroller.scrollTop = top;
    },
  };

  A.router = router;
})();
