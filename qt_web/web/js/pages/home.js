/* ============================================================
   首页
   五张 OCR 功能卡片：功能1 / 功能2 / 功能3 / 漫画 / 游戏
   状态全部由后端 state 驱动（启用开关、区域是否已选、是否运行中）。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  /* 卡片元数据：名称/图标/色调/说明 */
  const META = {
    func1: { icon: "🖱️", tone: "c", glow: "var(--cyan)", name: "功能1 · 框选朗读", desc: "框选屏幕任意区域，立即识别并朗读，每次触发都响应。" },
    func2: { icon: "📐", tone: "g", glow: "var(--green)", name: "功能2 · 固定区域朗读", desc: "先框选一个固定区域，之后随时按键读取该区域内容。" },
    func3: { icon: "📡", tone: "v", glow: "var(--violet)", name: "功能3 · 实时扫描朗读", desc: "持续监控区域内容变化，出现新文字立即朗读。" },
    manga: { icon: "💬", tone: "a", glow: "var(--amber)", name: "漫画实时识别", desc: "框选区域实时识别，只显示最新出现的对话文字。" },
    game: { icon: "🎮", tone: "r", glow: "var(--rose)", name: "游戏实时识别", desc: "智能去重相同文字，适合文字冒险 / 视觉小说类游戏。" },
  };

  /* hotkeyOf 返回空串时不显示热键徽章（漫画 / 游戏模式没有全局热键） */
  function hotkeyOf(key) {
    if (!key) return "";
    const st = A.state.hotkeys || {};
    return st[key] || A.DEFAULT_HOTKEYS[key] || "";
  }

  /* 徽章文案与样式 */
  function badgeOf(mode) {
    const enabled = A.state.enabled[mode] !== false;
    if (!enabled) return { text: "已禁用", cls: "idle" };
    if (mode === "func1") return { text: "待命", cls: "idle" };
    if (mode === "func2") {
      return A.state.regions.func2
        ? { text: "已就绪", cls: "ready" }
        : { text: "未选区域", cls: "idle" };
    }
    return A.state.running[mode]
      ? { text: "运行中", cls: "run" }
      : { text: "已停止", cls: "idle" };
  }

  function actionsOf(mode) {
    const enabled = A.state.enabled[mode] !== false;

    if (mode === "func1") {
      return C.btn("触发", { tone: "primary", size: "sm", attrs: 'data-act="func1"', disabled: !enabled });
    }

    if (mode === "func2") {
      const has = !!A.state.regions.func2;
      return (
        C.btn("选择", { tone: "success", size: "sm", attrs: 'data-act="func2-select"', disabled: !enabled }) +
        C.btn("朗读", { tone: "primary", size: "sm", attrs: 'data-act="func2-read"', disabled: !enabled || !has }) +
        C.btn("清除", { tone: "danger", size: "sm", attrs: 'data-act="func2-clear"', disabled: !enabled || !has })
      );
    }

    /* 扫描三兄弟
       按钮可用条件对齐 tkinter 原逻辑（main_modular_win11.py:3955 _update_mode_buttons）：
         is_running            → 重选/清除 都可用
         未运行但已选区域       → 重选/清除 都可用
         未运行且没区域         → 重选/清除 都禁用（先点「启动」去框选）
       所以条件 = 运行中 或 已有区域。 */
    const running = !!A.state.running[mode];
    const has = !!A.state.regions[mode];
    const canUse = enabled && (running || has);
    return (
      C.btn(running ? "暂停" : "启动", {
        tone: running ? "warn" : "success",
        size: "sm",
        attrs: 'data-act="toggle" data-mode="' + mode + '"',
        disabled: !enabled,
      }) +
      C.btn("重选", { tone: "primary", size: "sm", attrs: 'data-act="reselect" data-mode="' + mode + '"', disabled: !canUse }) +
      C.btn("清除", { tone: "danger", size: "sm", attrs: 'data-act="clear" data-mode="' + mode + '"', disabled: !canUse })
    );
  }

  function cardHtml(mode) {
    const m = META[mode];
    const b = badgeOf(mode);
    return C.funcCard({
      icon: m.icon,
      tone: m.tone,
      glow: m.glow,
      name: m.name,
      desc: m.desc,
      badge: b.text,
      badgeClass: b.cls,
      badgeId: mode + "-badge",
      actions: actionsOf(mode),
      hotkey: hotkeyOf(
        mode === "func1" ? "func1_trigger" : mode === "func2" ? "func2_select" : mode === "func3" ? "func3_toggle" : ""
      ),
    });
  }

  /* 禁用的功能给一句解释 */
  function disabledHint() {
    const anyDisabled = ["func1", "func2", "func3", "manga", "game"].some(function (k) {
      return A.state.enabled[k] === false;
    });
    return anyDisabled
      ? C.hintInline("⛔ 灰掉的卡片已在「设置 → 功能设置」中禁用，开启后即可使用。", "margin-top:6px")
      : "";
  }

  A.pages.home = {
    title: "首页",
    desc: "选择一种识别方式开始朗读",
    icon: "📋",
    group: "识别",

    render: function () {
      return (
        C.sectionHead("即时朗读", "手动触发，识别一次读一次") +
        '<div class="grid grid-2">' + cardHtml("func1") + cardHtml("func2") + "</div>" +
        disabledHint() +
        C.sectionHead("实时扫描", "持续监控区域，出现新文字即朗读（三者互斥）", "margin-top:8px") +
        '<div class="grid grid-3">' + cardHtml("func3") + cardHtml("manga") + cardHtml("game") + "</div>"
      );
    },

    mount: function (root) {
      /* 所有卡片按钮统一走事件委托，避免每张卡片重复绑定。
         注意：委托挂在 #content 上，而这个容器**永不销毁**；router.refresh()
         （扫描状态 / 区域变化 / 启用开关变化都会触发）会重新调用 mount()。
         所以必须幂等 —— 否则每刷新一次就多一个监听器，点一次「重选」会发出
         N 次 reselect_scan，选区窗口连弹 N 次（看起来像"一直让我重新框选"），
         点一次「启动」也会被切换 N 次（偶数次等于没动）。 */
      if (root.dataset.homeBound === "1") return;
      root.dataset.homeBound = "1";

      root.addEventListener("click", function (e) {
        const btn = e.target.closest("button[data-act]");
        if (!btn || btn.disabled) return;

        const act = btn.dataset.act;
        const mode = btn.dataset.mode || "";

        if (act === "func1") {
          A.bridge.call("trigger_func1", {});
        } else if (act === "func2-select") {
          A.bridge.call("func2_select", {});
        } else if (act === "func2-read") {
          A.bridge.call("func2_read", {});
        } else if (act === "func2-clear") {
          A.bridge.call("func2_clear", {});
        } else if (act === "toggle") {
          A.bridge.call("toggle_scan", { mode: mode });
        } else if (act === "reselect") {
          A.bridge.call("reselect_scan", { mode: mode });
        } else if (act === "clear") {
          A.bridge.call("clear_scan", { mode: mode });
        }
      });
    },
  };
})();
