/* ============================================================
   启动装配
   1. 渲染侧边栏
   2. 拉取后端状态并回填
   3. 订阅后端推送（状态栏 / 识别结果 / 扫描状态 / 录音状态）
   4. 打开首页
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;

  /* ---------- 简易 toast（保存提示） ---------- */
  function toast(text, ok) {
    let box = A.qs("#toast-box");
    if (!box) {
      box = document.createElement("div");
      box.id = "toast-box";
      box.style.cssText =
        "position:fixed;right:24px;bottom:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
      document.body.appendChild(box);
    }
    const item = document.createElement("div");
    item.textContent = text;
    item.style.cssText =
      "padding:10px 16px;border-radius:12px;font-size:12.5px;font-weight:600;" +
      "background:var(--modal-bg);border:1px solid var(--line-strong);" +
      "box-shadow:var(--sh);color:" +
      (ok === false ? "var(--ink-rose)" : "var(--ink-green)") +
      ";opacity:0;transform:translateY(8px);transition:all .22s ease;";
    box.appendChild(item);
    requestAnimationFrame(function () {
      item.style.opacity = "1";
      item.style.transform = "translateY(0)";
    });
    setTimeout(function () {
      item.style.opacity = "0";
      item.style.transform = "translateY(8px)";
      setTimeout(function () {
        item.remove();
      }, 260);
    }, 2200);
  }

  A.toast = toast;

  /* 模型缺失的提示停留更久（用户需要照着路径去放文件）。 */
  function alert(text) {
    let box = A.qs("#toast-box");
    if (!box) {
      box = document.createElement("div");
      box.id = "toast-box";
      box.style.cssText =
        "position:fixed;right:24px;bottom:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
      document.body.appendChild(box);
    }
    const item = document.createElement("div");
    item.textContent = text;
    item.style.cssText =
      "max-width:420px;white-space:pre-wrap;word-break:break-all;" +
      "padding:12px 16px;border-radius:12px;font-size:12.5px;font-weight:600;line-height:1.7;" +
      "background:var(--modal-bg);border:1px solid var(--line-strong);" +
      "box-shadow:var(--sh);color:var(--ink-rose);" +
      "opacity:0;transform:translateY(8px);transition:all .22s ease;";
    box.appendChild(item);
    requestAnimationFrame(function () {
      item.style.opacity = "1";
      item.style.transform = "translateY(0)";
    });
    setTimeout(function () {
      item.style.opacity = "0";
      item.style.transform = "translateY(8px)";
      setTimeout(function () {
        item.remove();
      }, 260);
    }, 12000);
  }

  /* ---------- 后端状态回填 ---------- */
  function applyState(state) {
    if (!state) return;
    Object.keys(state).forEach(function (k) {
      A.state[k] = state[k];
    });

    /* 热键显示值 */
    if (state.hotkeys) A.state.hotkeys = state.hotkeys;

    /* 主题与毛玻璃 */
    A.theme.applyState(state);
  }

  /* ---------- 后端推送订阅 ---------- */
  function subscribe() {
    A.bridge.on('model-folders-changed', function (msg) {
      A.state.models = msg.models;
      A.state.asr = Object.assign(A.state.asr || {}, {models:msg.asr.models,local_model:msg.asr.selected});
    });
    A.bridge.on('models-missing', function (msg) {
      const paths = (msg && msg.missing) || [];
      if (!paths.length) return;
      alert('缺少模型文件，语音识别不可用。\n请把模型放到：\n' + paths.join('\n'));
    });
    A.bridge.on('config-current', function (msg) {
      const c = msg.config || {};
      ['tts','recognition','translation','asr','hotkeys'].forEach(function (key) {
        if (c[key]) A.state[key] = Object.assign(A.state[key] || {}, c[key]);
      });
      if (c.ocr) A.state.ocr = Object.assign(A.state.ocr || {}, c.ocr);
      if (c.tts) A.state.queueModes = {func1:!!c.tts.func1_queue_mode, func2:c.tts.func2_queue_mode !== false};
    });
    A.bridge.on('config-replaced', function (msg) {
      applyState(msg.state);
      A.router.refresh();
    });
    /* 状态栏 */
    A.bridge.on("status", function (msg) {
      A.result.setStatus(msg.text || "", msg.kind || "");
      if (msg.engine) A.sidebar.setEngineLabel(msg.engine);
    });

    /* 识别结果 */
    A.bridge.on("result", function (msg) {
      if (msg.text == null) return;
      A.result.set(msg.text, !!msg.append);
      A.result.setStatus(msg.status || "识别完成", "ok");
    });

    /* 扫描模式运行状态变化 */
    A.bridge.on("scan-state", function (msg) {
      if (!msg || !msg.mode) return;
      A.state.running[msg.mode] = !!msg.running;
      if (A.router.current() === "home") A.router.refresh();
    });

    /* 区域变化 */
    A.bridge.on("region-changed", function (msg) {
      if (!msg || !msg.mode) return;
      A.state.regions[msg.mode] = !!msg.has_region;
      if (A.router.current() === "home") A.router.refresh();
    });

    /* 功能启用状态变化 */
    A.bridge.on("enabled-changed", function (msg) {
      if (!msg || !msg.mode) return;
      A.state.enabled[msg.mode] = !!msg.enabled;
      if (A.router.current() === "home") A.router.refresh();
    });

    /* 热键变化 */
    A.bridge.on("hotkeys-changed", function (hk) {
      if (hk) A.state.hotkeys = hk;
    });

    /* 保存提示 */
    A.bridge.on("toast", function (msg) {
      toast(msg.text, msg.ok);
    });

    /* 主题被后端改动（例如别处切换）
       注意：后端推送是「增量」的 —— 切主题只带 theme，切毛玻璃只带 frost。
       必须逐字段判断是否存在，不能写 msg.theme || "dark"，否则只改毛玻璃时
       会把主题强行重置成深色。 */
    A.bridge.on("theme", function (msg) {
      if (!msg) return;
      if (msg.theme === "light" || msg.theme === "dark") {
        A.theme.set(msg.theme, true);
      }
      if (typeof msg.frost === "boolean") {
        A.theme.setFrost(msg.frost, true);
      }
    });
  }

  /* ---------- 启动 ---------- */
  function boot() {
    A.sidebar.render();
    A.result.build();
    A.components.bindCommon(document);
    subscribe();

    A.bridge
      .call("state", {})
      .then(function (res) {
        if (res && res.ok && res.state) applyState(res.state);
      })
      .catch(function (e) {
        console.error("[app] 拉取状态失败", e);
        A.result.setStatus("后端未连接（离线预览模式）", "warn");
      })
      .then(function () {
        A.router.go("home");
        A.result.setStatus("就绪", "");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  /* 诊断入口：打包版没有 DevTools，从后端触发页面检查（release_check 用） */
  A.inspectWindow = {
    radios: function () {
      return document.querySelectorAll('[name="asr-input-mode"]').length;
    },
    talkBtn: function () {
      return !!document.querySelector("#talk-btn");
    }
  };
})();
