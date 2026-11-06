/* ============================================================
   DOM 工具
   轻量封装：查询、转义、事件绑定、HTML 片段拼接。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;

  A.qs = function (sel, root) {
    return (root || document).querySelector(sel);
  };

  A.qsa = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  /* HTML 转义：所有来自后端/用户输入的文本进模板前必须过一遍 */
  const ESC_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };
  A.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ESC_MAP[c];
    });
  };

  A.on = function (node, ev, fn) {
    if (node) node.addEventListener(ev, fn);
    return node;
  };

  /* 绑定一组：A.bind(root, "[data-x]", "click", fn) */
  A.bind = function (root, sel, ev, fn) {
    A.qsa(sel, root).forEach(function (n) {
      n.addEventListener(ev, fn);
    });
  };

  /* 安全的 setTimeout 包装：页面切走后回调不再操作已销毁的节点 */
  A.defer = function (fn, ms) {
    return setTimeout(fn, ms || 0);
  };

  /* 生成唯一 id */
  let _uid = 0;
  A.uid = function (prefix) {
    _uid += 1;
    return (prefix || "id") + "-" + _uid;
  };

  /* 数值格式化 */
  A.fixed = function (v, n) {
    const num = Number(v);
    return isNaN(num) ? "0" : num.toFixed(n == null ? 2 : n);
  };

  /* 选中滑块：把 pill 平移到目标元素（侧边栏导航 / 设置页签共用） */
  A.movePill = function (pill, el, animate) {
    if (!pill || !el) return;
    if (animate === false) pill.style.transition = "none";
    pill.style.transform = "translate(" + el.offsetLeft + "px, " + el.offsetTop + "px)";
    pill.style.width = el.offsetWidth + "px";
    pill.style.height = el.offsetHeight + "px";
    if (animate === false) {
      void pill.offsetWidth; // 强制回流，避免首次定位出现滑动
      pill.style.transition = "";
    }
  };
})();
