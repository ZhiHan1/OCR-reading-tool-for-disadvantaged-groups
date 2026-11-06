/* ============================================================
   命名空间与全局常量
   所有模块统一挂在 window.APP 下，避免污染全局作用域。
   使用传统 <script> 而非 ES module：file:// 下 ES module 会被
   CORS 拦截，传统脚本不受影响。
   ============================================================ */
(function () {
  "use strict";

  const APP = (window.APP = window.APP || {});

  APP.version = "2.0";

  /* 页面注册表：路由名 -> { title, desc, icon, group, render(), mount() } */
  APP.pages = APP.pages || {};

  /* 侧边栏分组顺序 */
  APP.navGroups = ["识别", "处理", "系统"];

  /* 运行时状态（由后端 state() 回填） */
  APP.state = APP.state || {
    theme: "dark",          // dark | light
    frost: true,            // 毛玻璃
    enabled: {},            // 功能开关 func1/func2/func3/manga/game
    regions: {},            // 各模式是否已选区域
    running: {},            // 扫描模式是否运行中
    hotkeys: {},            // 热键显示文本
    asr: {},                // 语音输入配置
    translation: {},        // 翻译配置
    voices: {},             // 语音列表
    models: {},             // 翻译模型
    recognition: {},        // 识别设置
    ocr: {},                // OCR 设备等
    queueModes: {},         // 队列模式
  };

  /* 热键显示名（后端会推送真实值，这里是首屏兜底） */
  APP.DEFAULT_HOTKEYS = {
    func1_trigger: "F7",
    func2_select: "F9",
    func2_read: "F8",
    func3_toggle: "F4",
    func3_reselect: "F5",
    stop_speak: "F6",
  };

  /* 常用文案常量 */
  APP.TEXT = {
    sourceEdge: "微软在线语音 (Edge)",
    sourceSapi: "本机语音 (Windows)",
  };

  /* 小工具：把后端返回的键名映射成中文显示 */
  APP.HOTKEY_LABELS = {
    func1_trigger: "功能1 · 框选朗读",
    func2_select: "功能2 · 选择区域",
    func2_read: "功能2 · 朗读",
    func3_toggle: "功能3 · 启动/暂停",
    func3_reselect: "功能3 · 重新选择",
    stop_speak: "全局 · 停止朗读",
  };
})();
