/* ============================================================
   关于页
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  const FEATS = [
    { t: "✅ 朗读模式", l: "框选朗读<br>固定区域朗读<br>实时扫描朗读" },
    { t: "🧠 智能处理", l: "智能去重检测<br>相似度过滤<br>多模式互斥" },
    { t: "⚡ 效率工具", l: "全局快捷键<br>系统托盘<br>自动翻译集成" },
    { t: "🏗️ 技术架构", l: "前后端分离<br>服务层模块化<br>控制器协调" },
  ];

  A.pages.about = {
    title: "关于",
    desc: "版本与信息",
    icon: "ℹ️",
    group: "系统",

    render: function () {
      const feats = FEATS.map(function (f) {
        return '<div class="feat"><div class="ft">' + f.t + '</div><div class="fl">' + f.l + "</div></div>";
      }).join("");

      return (
        '<div class="card about-hero">' +
        '<div class="about-logo">读</div>' +
        '<div class="about-name">OCR 文字朗读工具</div>' +
        '<div class="muted" style="margin-top:6px">版本 2.0 · 模块化架构 · 本地识别 · 文字朗读</div>' +
        '<div class="about-feats">' + feats + "</div>" +
        '<div class="muted" style="margin-top:24px">© 2024 OCR 朗读工具</div>' +
        "</div>"
      );
    },

    mount: function () {},
  };
})();
