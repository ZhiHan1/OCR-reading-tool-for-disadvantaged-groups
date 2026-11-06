/* ============================================================
   Python 桥接层
   - 前端 → 后端：APP.bridge.call(action, payload) 返回 Promise
   - 后端 → 前端：APP.bridge.on(event, fn) 订阅推送
   通过 QWebChannel 通信；若在普通浏览器里打开（无 QWebChannel），
   自动降级为 mock 模式，方便单独调样式。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;

  const handlers = {};   // event -> [fn]
  let api = null;        // QWebChannel 暴露的 Python 对象
  let readyResolve;
  let offline = false;

  /* 订阅作用域：页面每次 mount 都新建一个，切页时统一释放，
     避免反复进出页面累积重复处理器（否则一次事件会触发多次）。 */
  let activeScope = null;

  const bridge = {
    ready: new Promise(function (res) {
      readyResolve = res;
    }),

    isOffline: function () {
      return offline;
    },

    /* 开始一个订阅作用域，返回释放函数 */
    beginScope: function () {
      const scope = [];
      activeScope = scope;
      return function () {
        for (let i = 0; i < scope.length; i++) {
          try {
            scope[i]();
          } catch (e) {
            /* 忽略 */
          }
        }
        if (activeScope === scope) activeScope = null;
      };
    },

    /* ---- 事件订阅 ---- */
    on: function (evt, fn) {
      const list = (handlers[evt] = handlers[evt] || []);
      list.push(fn);

      const off = function () {
        const i = list.indexOf(fn);
        if (i >= 0) list.splice(i, 1);
      };
      if (activeScope) activeScope.push(off);
      return bridge;
    },

    emit: function (evt, payload) {
      const list = handlers[evt] || [];
      for (let i = 0; i < list.length; i++) {
        try {
          list[i](payload);
        } catch (e) {
          console.error("[bridge] handler error on " + evt, e);
        }
      }
    },

    /* ---- 调用后端 ---- */
    call: function (action, payload) {
      return bridge.ready.then(function () {
        if (offline) return mock(action, payload);
        return new Promise(function (resolve, reject) {
          let done = false;
          // 用户选目录/文件时不应被普通请求的 20 秒超时打断。
          const isPicker = action === 'pick_folder' || action === 'pick_file' || action === 'export_config' || (action === 'asr_import_model' && !(payload && payload.path));
          const timer = isPicker ? null : setTimeout(function () {
            if (!done) {
              done = true;
              reject(new Error("后端超时: " + action));
            }
          }, 20000);

          try {
            api.request(
              String(action),
              JSON.stringify(payload == null ? {} : payload),
              function (resultJson) {
                if (done) return;
                done = true;
                clearTimeout(timer);
                let data = null;
                try {
                  data = resultJson ? JSON.parse(resultJson) : null;
                } catch (e) {
                  data = { ok: false, error: "返回内容不是 JSON: " + resultJson };
                }
                resolve(data);
              }
            );
          } catch (e) {
            done = true;
            clearTimeout(timer);
            reject(e);
          }
        });
      });
    },
  };

  A.bridge = bridge;

  /* ---- mock：仅用于浏览器预览样式 ---- */
  function mock(action, payload) {
    console.log("[bridge:mock]", action, payload);
    if (action === "state") return Promise.resolve({ ok: true, state: {} });
    return Promise.resolve({ ok: true });
  }

  /* ---- 连接 ---- */
  function connect() {
    if (typeof QWebChannel === "undefined" || typeof qt === "undefined" || !qt.webChannelTransport) {
      offline = true;
      console.warn("[bridge] 未检测到 QWebChannel，进入离线预览模式");
      readyResolve();
      bridge.emit("offline");
      return;
    }

    new QWebChannel(qt.webChannelTransport, function (channel) {
      api = channel.objects.api;
      /* 路由通道：切页时告诉后端当前页（调节模型目录扫描频率） */
      if (channel.objects.nav) {
        try {
          A.nav = channel.objects.nav;
          if (A.nav.page) A.nav.page(A.router.current() || "home");
        } catch (e) {
          console.warn("[bridge] nav 通道不可用", e);
        }
      }
      if (!api) {
        offline = true;
        readyResolve();
        bridge.emit("offline");
        return;
      }

      /* 后端推送：统一走 event 信号，payload 是 JSON 字符串 */
      if (api.event) {
        api.event.connect(function (payloadJson) {
          let msg = null;
          try {
            msg = JSON.parse(payloadJson);
          } catch (e) {
            return;
          }
          if (msg && msg.type) bridge.emit(msg.type, msg);
        });
      }

      readyResolve();
      bridge.emit("ready");
    });
  }

  connect();
})();
