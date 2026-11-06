/* ============================================================
   可复用 UI 组件
   统一产出与参考模板一致的 HTML 片段与 class 名，保证样式零差异。
   所有页面都通过这些工厂拼装，避免重复手写结构。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const esc = A.esc;

  /* ---------- 基础按钮 ---------- */
  /* btn("触发", { tone:"primary", size:"sm", id:"x", attrs:'data-a="1"', disabled:true }) */
  function btn(label, o) {
    o = o || {};
    const cls = ["btn"];
    if (o.tone) cls.push(o.tone);
    if (o.size) cls.push(o.size);
    if (o.cls) cls.push(o.cls);
    let attrs = "";
    if (o.id) attrs += ' id="' + esc(o.id) + '"';
    if (o.attrs) attrs += " " + o.attrs;
    if (o.disabled) attrs += " disabled";
    if (o.title) attrs += ' title="' + esc(o.title) + '"';
    return "<button class=\"" + cls.join(" ") + "\"" + attrs + ">" + label + "</button>";
  }

  /* ---------- 分区标题 ---------- */
  function sectionHead(title, desc, style) {
    return (
      '<div class="section-head"' +
      (style ? ' style="' + style + '"' : "") +
      '><span class="st">' +
      esc(title) +
      "</span>" +
      (desc ? '<span class="sd">' + esc(desc) + "</span>" : "") +
      "</div>"
    );
  }

  /* ---------- 玻璃卡片 ---------- */
  function card(title, hint, body, o) {
    o = o || {};
    let html = '<div class="card' + (o.cls ? " " + o.cls : "") + '"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ">";
    if (title) html += '<div class="card-title">' + title + "</div>";
    if (hint) html += '<div class="card-hint">' + hint + "</div>";
    html += body || "";
    html += "</div>";
    return html;
  }

  /* ---------- 字段行 ---------- */
  /* field("语音来源", ctlHtml, { labelSmall:"支持 %Y%m%d", style:"..." }) */
  function field(label, ctl, o) {
    o = o || {};
    const small = o.labelSmall ? " <small>" + o.labelSmall + "</small>" : "";
    return (
      '<div class="field"' +
      (o.style ? ' style="' + o.style + '"' : "") +
      (o.id ? ' id="' + esc(o.id) + '"' : "") +
      ">" +
      '<span class="field-label">' +
      label +
      small +
      "</span>" +
      '<div class="field-ctl">' +
      ctl +
      "</div></div>"
    );
  }

  /* ---------- 开关行 ---------- */
  /* switchRow("功能1", true, { id:"sw-func1", key:"func1" }) */
  function switchRow(label, on, o) {
    o = o || {};
    return field(
      label,
      '<div class="switch' +
        (on ? " on" : "") +
        '"' +
        (o.id ? ' id="' + esc(o.id) + '"' : "") +
        (o.key ? ' data-switch-key="' + esc(o.key) + '"' : "") +
        "></div>"
    );
  }

  /* ---------- 数值输入行 ---------- */
  function inputRow(label, val, width, o) {
    o = o || {};
    return field(
      label,
      '<input class="select" style="width:' +
        (width || 100) +
        'px;min-width:0;text-align:center" value="' +
        esc(val) +
        '"' +
        (o.id ? ' id="' + esc(o.id) + '"' : "") +
        (o.type ? ' type="' + esc(o.type) + '"' : "") +
        (o.step ? ' step="' + esc(o.step) + '"' : "") +
        ">"
    );
  }

  /* ---------- 滑块行 ---------- */
  /* sliderRow("语速", -10, 10, 0, "0", { id:"sl-speed", dec:false }) */
  function sliderRow(label, min, max, val, display, o) {
    o = o || {};
    return field(
      label,
      '<input type="range" min="' +
        min +
        '" max="' +
        max +
        '" value="' +
        val +
        '"' +
        (o.id ? ' id="' + esc(o.id) + '"' : "") +
        (o.dec ? ' data-dec="1"' : "") +
        '><span class="range-val">' +
        esc(display == null ? val : display) +
        "</span>"
    );
  }

  /* ---------- 键帽行（热键设置） ---------- */
  /* keyRow("功能1 · 框选朗读", "F7", { key:"func1_trigger" }) */
  function keyRow(label, key, o) {
    o = o || {};
    return field(
      label,
      '<kbd class="cap"' +
        (o.key ? ' data-hotkey="' + esc(o.key) + '"' : "") +
        (o.id ? ' id="' + esc(o.id) + '"' : "") +
        ">" +
        esc(key) +
        "</kbd>"
    );
  }

  /* ---------- 徽章 ---------- */
  function badge(text, cls, id) {
    return (
      '<span class="badge ' +
      (cls || "idle") +
      '"' +
      (id ? ' id="' + esc(id) + '"' : "") +
      ">" +
      esc(text) +
      "</span>"
    );
  }

  /* ---------- 功能卡片（首页五个 OCR 卡片） ---------- */
  /* funcCard({ icon, tone, glow, name, desc, badge, badgeClass, badgeId, actions, hotkey }) */
  function funcCard(o) {
    return (
      '<div class="func-card" style="--glow:' +
      (o.glow || "var(--cyan)") +
      '">' +
      '<div class="func-top">' +
      '<div class="func-icon ' +
      (o.tone || "c") +
      '">' +
      (o.icon || "") +
      "</div>" +
      '<div class="func-meta">' +
      '<div class="func-name">' +
      esc(o.name) +
      "</div>" +
      '<div class="func-desc">' +
      esc(o.desc) +
      "</div>" +
      "</div>" +
      (o.badge ? badge(o.badge, o.badgeClass, o.badgeId) : "") +
      "</div>" +
      '<div class="func-foot">' +
      (o.actions || "") +
      '<span class="spacer"></span>' +
      (o.hotkey ? '<span class="hotkey-hint"><kbd>' + esc(o.hotkey) + "</kbd></span>" : "") +
      "</div></div>"
    );
  }

  /* ---------- 设置子分区 ---------- */
  function subsec(emoji, title, body, hint) {
    return (
      '<div class="subsec">' +
      '<div class="subsec-title"><span class="em">' +
      emoji +
      "</span>" +
      title +
      "</div>" +
      (hint ? '<div class="subsec-hint">' + hint + "</div>" : "") +
      body +
      "</div>"
    );
  }

  /* 第二个参数两种写法都支持：
     - 字符串：直接当 style（历史写法，保持兼容）
     - 对象：{ id, style, cls } —— 需要后续用 A.qs("#xxx") 改文字的提示必须给 id，
       否则会渲染出一个没有 id 的 div，页面里 A.qs("#xxx") 拿到 null，
       后面一句 textContent 赋值就会抛异常，把整个回调（含后续刷新逻辑）打断。 */
  function hintInline(text, o) {
    let id = "";
    let style = "";
    let cls = "";
    if (typeof o === "string") {
      style = o;
    } else if (o && typeof o === "object") {
      id = o.id || "";
      style = o.style || "";
      cls = o.cls || "";
    }
    return (
      '<div class="hint-inline' +
      (cls ? " " + esc(cls) : "") +
      '"' +
      (id ? ' id="' + esc(id) + '"' : "") +
      (style ? ' style="' + style + '"' : "") +
      ">" +
      text +
      "</div>"
    );
  }

  /* 底部操作行（保存/恢复默认） */
  function actionRow(html) {
    return '<div class="row mt-4" style="padding-top:16px;border-top:1px solid var(--line)">' + html + "</div>";
  }

  /* ---------- 语言下拉 ---------- */
  const LANGS = ["自动检测", "英语", "中文", "日语", "韩语", "法语", "德语", "西班牙语", "俄语"];
  function langOptions(selected) {
    return LANGS.map(function (l) {
      return '<option' + (l === selected ? " selected" : "") + ">" + esc(l) + "</option>";
    }).join("");
  }

  /* ---------- 通用行为绑定 ---------- */
  /* 开关点击切换、滑块数值显示、键帽点击反馈。
     页面若需要真实业务逻辑，自行监听 switchchange / rangechange 事件。 */
  function bindCommon(root) {
    root = root || document;

    A.qsa(".switch", root).forEach(function (sw) {
      if (sw.dataset.bound) return;
      sw.dataset.bound = "1";
      sw.addEventListener("click", function () {
        sw.classList.toggle("on");
        sw.dispatchEvent(
          new CustomEvent("switchchange", {
            bubbles: true,
            detail: { on: sw.classList.contains("on"), key: sw.dataset.switchKey || "" },
          })
        );
      });
    });

    A.qsa('input[type="range"]', root).forEach(function (r) {
      if (r.dataset.bound) return;
      r.dataset.bound = "1";
      const valEl = r.parentElement ? r.parentElement.querySelector(".range-val") : null;
      const fmt = function () {
        if (!valEl) return;
        let v = r.value;
        if (r.dataset.dec) v = (v / 100).toFixed(2);
        valEl.textContent = v;
      };
      fmt();
      r.addEventListener("input", function () {
        fmt();
        r.dispatchEvent(
          new CustomEvent("rangechange", { bubbles: true, detail: { value: Number(r.value) } })
        );
      });
    });

    /* 键帽默认只做视觉反馈；带 data-hotkey 的键帽由页面调用
       A.keys.bindCapture() 接管（见 pages/settings.js、pages/voice.js） */
    A.qsa("kbd.cap", root).forEach(function (k) {
      if (k.dataset.bound) return;
      if (k.dataset.hotkey) return; /* 真实录制键帽，跳过纯视觉反馈 */
      k.dataset.bound = "1";
      k.addEventListener("click", function () {
        if (k.dataset.capturing) return;
        const old = k.textContent;
        k.textContent = "按键…";
        setTimeout(function () {
          k.textContent = old;
        }, 900);
      });
    });
  }

  A.components = {
    btn: btn,
    sectionHead: sectionHead,
    card: card,
    field: field,
    switchRow: switchRow,
    inputRow: inputRow,
    sliderRow: sliderRow,
    keyRow: keyRow,
    badge: badge,
    funcCard: funcCard,
    subsec: subsec,
    hintInline: hintInline,
    actionRow: actionRow,
    langOptions: langOptions,
    LANGS: LANGS,
    bindCommon: bindCommon,
  };
})();
