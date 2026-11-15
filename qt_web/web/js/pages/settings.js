/* ============================================================
   设置页
   六个页签各自是一个独立模块（render + mount），通过 TABS 注册表
   统一装配；新增页签只要往 TABS 里加一项。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  /* ---------- 公共小工具 ---------- */

  /* 读取 OCR 子配置，缺省兜底 */
  function ocr(sec, key, dft) {
    const s = (A.state.ocr && A.state.ocr[sec]) || {};
    return s[key] != null ? s[key] : dft;
  }

  /* 保存提示 */
  function flash(text, ok) {
    A.bridge.emit("toast", { text: text, ok: ok !== false });
  }

  function confirmReset(title, message, onConfirm) {
    const box = A.modal.open(
      '<div class="modal-head">' +
        '<div><div class="modal-title">' + A.esc(title) + '</div>' +
        '<div class="modal-desc">' + A.esc(message) + '</div></div>' +
        '<button class="modal-close" data-close>✕</button></div>' +
        '<div class="modal-foot">' +
        C.btn("取消", { tone: "ghost", attrs: "data-close" }) +
        C.btn("确认恢复", { tone: "warn", id: "confirm-reset-ok" }) +
        "</div>"
    );
    if (!box) return;
    A.on(A.qs("#confirm-reset-ok", box), "click", function () {
      A.modal.close();
      if (onConfirm) onConfirm();
    });
  }

  function applySetting(action, payload) {
    return A.bridge.call(action, payload).then(function (res) {
      if (res && res.ok) {
        A.result.setStatus('已自动保存并生效', 'ok');
      } else {
        flash("❌ 保存失败：" + ((res && res.error) || "未知错误"), false);
      }
      return res;
    }).catch(function (err) { flash('保存失败：' + err.message, false); });
  }

  function bindImmediate(el, action, payload) {
    if (!el) return;
    let last = el.value;
    const send = function () {
      if (el.value === last || el.value.trim() === '') return;
      const data = payload(el.value);
      if (typeof data.value === 'number' && !Number.isFinite(data.value)) return;
      last = el.value;
      applySetting(action, data).then(function (res) {
        if (res && !res.ok) last = null;
      });
    };
    A.on(el, 'input', send);
    A.on(el, 'change', send);
  }

  function configResult(res, success) {
    if (res && res.cancelled) return;
    if (!res || !res.ok) {
      flash((res && res.error) || '配置操作失败', false);
      return;
    }
    flash(success, true);
  }

  function mountConfigActions(root) {
    A.on(A.qs('#config-reset-all', root), 'click', function () {
      confirmReset('恢复全部默认设置', '将用 defaults.yaml 完整覆盖当前 config.yaml。正在进行的录音和扫描会停止，模型文件不会删除。', function () {
        A.bridge.call('reset_defaults', {confirmed:true}).then(function (res) { configResult(res, '已恢复全部默认配置'); });
      });
    });
    A.on(A.qs('#config-export', root), 'click', function () {
      A.bridge.call('export_config', {}).then(function (res) { configResult(res, '已导出当前配置'); });
    });
    A.on(A.qs('#config-import', root), 'click', function () {
      A.bridge.call('pick_file', {filter:'配置文件 (*.yaml *.yml)'}).then(function (picked) {
        if (!picked || !picked.ok) return;
        confirmReset('导入配置', '导入内容将完整覆盖当前配置，并立即生效。当前录音和扫描会停止。是否继续？', function () {
          A.bridge.call('import_config', {path:picked.path,confirmed:true}).then(function (res) { configResult(res, '已导入并应用配置'); });
        });
      });
    });
  }

  /* 键帽录制：实现在 core/keys.js（A.keys.bindCapture），
     语音输入页也复用同一份，避免两处逻辑走偏。 */
  function bindHotkeyCapture(root) {
    A.keys.bindCapture(root);
  }

  /* ============================================================
     页签 1：功能设置
     ============================================================ */
  function renderFunc() {
    const en = A.state.enabled || {};
    const q = A.state.queueModes || {};

    const switches =
      C.switchRow("功能1 · 框选朗读", en.func1 !== false, { key: "func1" }) +
      C.switchRow("功能2 · 固定区域朗读", en.func2 !== false, { key: "func2" }) +
      C.switchRow("功能3 · 实时扫描朗读", en.func3 !== false, { key: "func3" }) +
      C.switchRow("📖 漫画模式", en.manga !== false, { key: "manga" }) +
      C.switchRow("🎮 游戏模式", en.game !== false, { key: "game" });

    const queue =
      C.switchRow("功能1 启用队列模式", q.func1 === true, { key: "queue_func1" }) +
      C.switchRow("功能2 启用队列模式", q.func2 !== false, { key: "queue_func2" });

    const device = A.state.ocr && A.state.ocr.device ? A.state.ocr.device : "cpu";
    const devices = (A.state.ocr && A.state.ocr.devices) || [
      { value: "cpu", label: "CPU（处理器）" },
      { value: "cuda:0", label: "GPU 0" },
    ];
    const deviceOptions = devices
      .map(function (d) {
        return (
          '<option value="' + A.esc(d.value) + '"' + (d.value === device ? " selected" : "") + ">" +
          A.esc(d.label || d.value) + "</option>"
        );
      })
      .join("");

    return (
      C.subsec("🎛️", "功能开关", switches) +
      C.subsec(
        "🔀",
        "队列模式",
        queue,
        "关闭队列模式后，新的朗读请求会立即打断当前正在进行的朗读"
      ) +
      C.hintInline("💡 功能3 / 漫画 / 游戏模式始终使用队列模式（不打断）", "margin-top:6px") +
      C.subsec(
        "🔄",
        "去重设置（仅功能3）",
        C.inputRow("功能3 冷却时间（秒）", ocr("func3", "repeat_cooldown", 3.0), 100, { id: "set-f3-cooldown" }),
        "功能1 / 功能2 为手动触发的一次性朗读，不走去重，点击即朗读"
      ) +
      C.subsec(
        "🎯",
        "功能3 相似度检测",
        C.sliderRow(
          "相似度阈值",
          70,
          95,
          Math.round(ocr("func3", "similarity_threshold", 0.85) * 100),
          A.fixed(ocr("func3", "similarity_threshold", 0.85), 2),
          { id: "set-f3-sim", dec: true }
        ) +
          C.inputRow("确认帧数（1-10）", ocr("func3", "stable_frames", 2), 100, { id: "set-f3-frames" }) +
          C.inputRow("短句即读字数阈值（0-10）", ocr("func3", "short_text_max_len", 2), 100, { id: "set-f3-short" }),
        "相似度阈值只对 11 字以上的文本生效；10 字以内固定按 0.75 判定，不受此滑块影响"
      ) +
      C.subsec(
        "📖",
        "漫画模式设置",
        C.inputRow("扫描间隔（秒）", ocr("manga_mode", "scan_interval", 0.4), 100, { id: "set-manga-interval" }) +
          C.inputRow("确认帧数（1-10）", ocr("manga_mode", "stable_frames", 2), 100, { id: "set-manga-frames" }) +
          C.inputRow("历史记录数量（1-20）", ocr("manga_mode", "history_size", 6), 100, { id: "set-manga-history" }),
        "手动下滑页面时，只朗读最新出现的新增文字（滚动中间态会被过滤）"
      ) +
      C.subsec(
        "🎮",
        "游戏模式设置",
        C.inputRow("扫描间隔（秒）", ocr("game_mode", "scan_interval", 0.3), 100, { id: "set-game-interval" }) +
          C.inputRow("确认帧数（1-10）", ocr("game_mode", "stable_frames", 2), 100, { id: "set-game-frames" }) +
          C.inputRow("历史记录数量（1-20）", ocr("game_mode", "history_size", 5), 100, { id: "set-game-history" }),
        "等待打字机式文字输出结束后再朗读整句，避免读一半"
      ) +
      C.subsec(
        "⚡",
        "推理设备",
        C.field("计算设备", '<select class="select" id="set-device" style="flex:1">' + deviceOptions + "</select>") +
          C.hintInline(
            device === "cpu"
              ? "ℹ️ 当前使用 CPU 推理；有可用 NVIDIA 显卡时可切到 GPU 提速。"
              : "ℹ️ 已选择 GPU 推理，大区域识别更快。",
            "margin-top:4px"
          ),
        "选择文字识别（OCR）使用的计算设备：CPU 兼容性最好，GPU 在大区域识别时更快"
      )
    );
  }

  function mountFunc(root) {
    /* 功能开关：立即写配置 */
    A.qsa(".switch[data-switch-key]", root).forEach(function (sw) {
      sw.addEventListener("switchchange", function (e) {
        const key = sw.dataset.switchKey;
        if (["func1", "func2", "func3", "manga", "game"].indexOf(key) >= 0) {
          A.state.enabled[key] = e.detail.on;
          A.bridge.call("set_mode_enabled", { mode: key, enabled: e.detail.on });
        } else if (key.indexOf("queue_") === 0) {
          const mode = key.slice(6);
          A.state.queueModes[mode] = e.detail.on;
          A.bridge.call("set_config", { section: "tts", key: mode + "_queue_mode", value: e.detail.on });
        }
      });
    });

    /* 相似度滑块实时显示 */
    const sim = A.qs("#set-f3-sim", root);
    if (sim) {
      sim.addEventListener("rangechange", function () {
        const el = sim.parentElement.querySelector(".range-val");
        if (el) el.textContent = A.fixed(Number(sim.value) / 100, 2);
      });
    }

      /* 数值输入即生效，不等保存按钮。 */
      const nums = [
        ["func3", "repeat_cooldown", "#set-f3-cooldown", Number],
        ["func3", "similarity_threshold", "#set-f3-sim", function (v) { return Number(v) / 100; }],
        ["func3", "stable_frames", "#set-f3-frames", function (v) { return parseInt(v, 10); }],
        ["func3", "short_text_max_len", "#set-f3-short", function (v) { return parseInt(v, 10); }],
        ["manga_mode", "scan_interval", "#set-manga-interval", Number],
        ["manga_mode", "stable_frames", "#set-manga-frames", function (v) { return parseInt(v, 10); }],
        ["manga_mode", "history_size", "#set-manga-history", function (v) { return parseInt(v, 10); }],
        ["game_mode", "scan_interval", "#set-game-interval", Number],
        ["game_mode", "stable_frames", "#set-game-frames", function (v) { return parseInt(v, 10); }],
        ["game_mode", "history_size", "#set-game-history", function (v) { return parseInt(v, 10); }],
      ];
      nums.forEach(function (item) {
        const el = A.qs(item[2], root);
        if (!el) return;
        bindImmediate(el, 'set_config_nested', function (raw) {
          return {section:'ocr',sub:item[0],key:item[1],value:item[3](raw)};
        });
      });

      const dev = A.qs("#set-device", root);
      A.on(dev, 'change', function () { applySetting('set_config', {section:'ocr',key:'device',value:dev.value}); });
  }

  /* ============================================================
     页签 2：语音设置
     ============================================================ */
  function sapiListByRole(role) {
    const all = (A.state.voices && A.state.voices.sapi) || [];
    if (!role || role === "全部") return all;
    const parts = role.split(" - ");
    return all.filter(function (v) {
      return v.gender === parts[0] && v.age === parts[1];
    });
  }

  function renderVoice() {
    const t = A.state.tts || {};
    const isEdge = (t.backend || "sapi") === "edge";

    const sourceSel = C.field(
      "语音来源",
      '<select class="select" id="vs-source" style="flex:1">' +
        '<option' + (isEdge ? " selected" : "") + ">" + A.esc(A.TEXT.sourceEdge) + "</option>" +
        '<option' + (!isEdge ? " selected" : "") + ">" + A.esc(A.TEXT.sourceSapi) + "</option>" +
        "</select>"
    );

    /* 角色筛选项按真实语音的「性别 - 年龄」动态生成（与旧 UI 一致）。
       不能写死中文选项：后端 get_available_voices() 返回的是 Female / Adult
       这类英文值，写死「男 - 青年」永远匹配不上，一选就变「无匹配语音」。 */
    const roleSet = {};
    ((A.state.voices && A.state.voices.sapi) || []).forEach(function (v) {
      roleSet[(v.gender || "未知") + " - " + (v.age || "未知")] = true;
    });
    const roleOptions = ["全部"].concat(Object.keys(roleSet).sort());

    const sapiBox =
      '<div id="vs-sapi"' + (isEdge ? ' style="display:none"' : "") + ">" +
      C.field(
        "角色筛选",
        '<select class="select" id="vs-role" style="flex:1">' +
          roleOptions
            .map(function (r) {
              return "<option>" + A.esc(r) + "</option>";
            })
            .join("") +
          "</select>"
      ) +
      C.field("选择语音", '<select class="select" id="vs-sapi-voice" style="flex:1">' + sapiOptions("全部", t.voice_name) + "</select>") +
      '<div class="subsec-hint" style="margin:-4px 0 6px 162px">使用系统已安装的语音，无需联网</div>' +
      "</div>";

    const edgeBox =
      '<div id="vs-edge"' + (!isEdge ? ' style="display:none"' : "") + ">" +
      C.field("在线语音", '<select class="select" id="vs-edge-voice" style="flex:1">' + edgeOptions(t.edge_voice) + "</select>") +
      '<div class="subsec-hint" style="margin:-4px 0 6px 162px">需要联网，首次合成稍有延迟；失败会自动回退到本机语音</div>' +
      "</div>";

    return (
      C.subsec(
        "🎤",
        "语音设置",
        sourceSel +
          sapiBox +
          edgeBox +
          C.sliderRow("音色（-10 到 10）", -10, 10, t.pitch != null ? t.pitch : 0, String(t.pitch != null ? t.pitch : 0), { id: "vs-pitch" }) +
          C.sliderRow("语速（-10 到 10）", -10, 10, t.rate != null ? t.rate : 0, String(t.rate != null ? t.rate : 0), { id: "vs-rate" }) +
          C.sliderRow("音量（0.0 到 1.0）", 0, 100, Math.round((t.volume != null ? t.volume : 1) * 100), A.fixed(t.volume != null ? t.volume : 1, 2), { id: "vs-volume", dec: true }) +
          C.field(
            "测试文本",
            '<input class="select" id="vs-test-text" style="flex:1;min-width:0" value="' +
              A.esc(t.test_text || "你好，这是语音测试，今天的天气非常不错。") +
              '">' +
              C.btn("🔊 测试", { tone: "primary", size: "sm", id: "vs-test-btn" })
          ),
        "选择语音后会立即生效，可以点击测试按钮试听"
      )
    );
  }

  function sapiOptions(role, selected) {
    const list = sapiListByRole(role);
    if (!list.length) return '<option value="">无匹配语音</option>';
    return list
      .map(function (v) {
        const value = v.value || v.name || "";
        return (
          '<option value="' + A.esc(value) + '"' + (value === selected ? " selected" : "") + ">" +
          A.esc(v.display || value) + "</option>"
        );
      })
      .join("");
  }

  function edgeOptions(selected) {
    const list = (A.state.voices && A.state.voices.edge) || [];
    if (!list.length) return '<option value="">（暂无在线语音）</option>';
    return list
      .map(function (v) {
        const value = typeof v === "string" ? v : v.value || v.name || "";
        const text = typeof v === "string" ? v : v.display || value;
        return '<option value="' + A.esc(value) + '"' + (value === selected ? " selected" : "") + ">" + A.esc(text) + "</option>";
      })
      .join("");
  }

  function mountVoice(root) {
    const src = A.qs("#vs-source", root);
    const role = A.qs("#vs-role", root);
    const sapiVoice = A.qs("#vs-sapi-voice", root);
    const edgeVoice = A.qs("#vs-edge-voice", root);

    function applySource() {
      const isEdge = String(src.value).indexOf("Edge") >= 0;
      A.qs("#vs-sapi", root).style.display = isEdge ? "none" : "";
      A.qs("#vs-edge", root).style.display = isEdge ? "" : "none";
      A.bridge.call("set_config", { section: "tts", key: "backend", value: isEdge ? "edge" : "sapi" });
    }

    A.on(src, "change", applySource);

    A.on(role, "change", function () {
      sapiVoice.innerHTML = sapiOptions(role.value, null);
      /* 与旧 UI 一致：筛选后立刻选中并应用第一个语音，避免下拉显示 A、
         实际还在用 B */
      const first = sapiVoice.value;
      if (first) {
        A.bridge.call("set_config", { section: "tts", key: "voice_name", value: first });
      }
    });

    A.on(sapiVoice, "change", function () {
      A.bridge.call("set_config", { section: "tts", key: "voice_name", value: sapiVoice.value });
    });

    A.on(edgeVoice, "change", function () {
      A.bridge.call("set_config", { section: "tts", key: "edge_voice", value: edgeVoice.value });
    });

    A.on(A.qs("#vs-test-btn", root), "click", function () {
      const text = A.qs("#vs-test-text", root).value.trim() || "你好，这是语音测试。";
      A.bridge.call("speak", { text: text });
      flash("🔊 正在试听", true);
    });

    /* 音色 / 语速 / 音量：旧 UI 是「拖动即生效」（滑块 command → update_settings），
       Web 版原来只在点「保存设置」时才写配置，导致试听听到的还是旧值。
       这里改成拖动即 set_config（带防抖，避免每个像素都写一次配置文件）。 */
    [
      ["#vs-pitch", "pitch", 1],
      ["#vs-rate", "rate", 1],
      ["#vs-volume", "volume", 0.01],
    ].forEach(function (item) {
      const el = A.qs(item[0], root);
      if (!el) return;
      bindImmediate(el, 'set_config', function (raw) { return {section:'tts', key:item[1], value:Number(raw)*item[2]}; });
    });

    bindImmediate(A.qs('#vs-test-text', root), 'set_config', function (value) { return {section:'tts',key:'test_text',value:value}; });
  }

  /* ============================================================
     页签 3：按键设置
     ============================================================ */
  const HOTKEY_ITEMS = [
    ["func1_trigger", "功能1 · 框选朗读"],
    ["func2_select", "功能2 · 选择区域"],
    ["func2_read", "功能2 · 朗读"],
    ["func3_toggle", "功能3 · 启动/暂停"],
    ["func3_reselect", "功能3 · 重新选择"],
    ["stop_speak", "全局 · 停止朗读"],
  ];

  function renderHotkey() {
    const hk = A.state.hotkeys || {};
    const rows = HOTKEY_ITEMS.map(function (item) {
      const key = item[0];
      const label = item[1];
       const val = hk[key] != null ? hk[key] : (A.DEFAULT_HOTKEYS[key] || "");
      return C.keyRow(label, A.keys.display(val), { key: key });
    }).join("");

    return (
      C.subsec(
        "⌨️",
        "快捷键设置",
        rows,
        "支持单键、Ctrl/Alt/Shift/Win 组合键；按 ESC 取消，设置后自动保存"
      ) + C.actionRow(C.btn("🔄 恢复默认按键", { tone: "warn", id: "set-hk-reset" }))
    );
  }

  function mountHotkey(root) {
    bindHotkeyCapture(root);

    A.on(A.qs("#set-hk-reset", root), "click", function () {
      confirmReset("恢复默认按键", "所有功能快捷键将恢复为默认值，当前按键设置会被覆盖。确定继续吗？", function () {
        A.bridge.call("reset_hotkeys", {confirmed:true}).then(function (res) {
          if (res && res.ok) {
            const hk = res.hotkeys || {};
            A.state.hotkeys = hk;
            /* 就地刷新键帽，不整页重载（重载会跳回第一个页签） */
            A.qsa("kbd.cap[data-hotkey]", root).forEach(function (cap) {
              const key = cap.dataset.hotkey;
              cap.textContent = A.keys.display(hk[key] != null ? hk[key] : (A.DEFAULT_HOTKEYS[key] || ""));
            });
            flash("✅ 已恢复默认按键", true);
          } else {
            flash("❌ 恢复失败", false);
          }
        });
      });
    });
  }

  /* ============================================================
     页签 4：翻译设置
     ============================================================ */
  function renderTrans() {
    const t = A.state.translation || {};
    const m = A.state.models || {};
    const locals = m.local || [];
    const onlines = m.online || [];

    const localOpts = locals.length
      ? locals
          .map(function (x) {
            return (
              '<option value="' + A.esc(x.id) + '"' + (x.active ? " selected" : "") + ">" +
              (x.active ? "🟢 " : "⚪ ") + A.esc(x.name) + "</option>"
            );
          })
          .join("")
      : '<option value="">（暂无本地模型）</option>';

    const onlineOpts = onlines.length
      ? onlines
          .map(function (p) {
            return '<option value="' + A.esc(p.name) + '">' + A.esc(p.name) + "</option>";
          })
          .join("")
      : '<option value="">（暂无在线模型，请点上方添加）</option>';

    const modelNav =
      '<div class="model-nav">' +
      C.btn("💻 本地模型", { tone: "primary", size: "sm", id: "model-nav-local" }) +
      C.btn("🌐 在线模型", { tone: "ghost", size: "sm", id: "model-nav-online" }) +
      '<span style="flex:1"></span>' +
      C.btn("✅ 启用本地模型", { tone: "success", size: "sm", id: "model-apply", disabled: true }) +
      "</div>";

    const localPanel =
      '<div id="model-panel-local">' +
      '<div class="muted" style="margin-bottom:12px">投放位置：模型/翻译/。放入 GGUF 文件后自动加入列表，选择并启用即可。</div>' +
      C.field("本地模型", '<select class="select" id="local-model" style="flex:1">' + localOpts + "</select>") +
      '<div class="row">' +
      C.btn("➕ 添加模型", { tone: "success", size: "sm", id: "local-add" }) +
      C.btn("❌ 禁用", { tone: "warn", size: "sm", id: "local-disable", disabled: true }) +
      C.btn("📂 打开翻译模型文件夹", { tone: "ghost", size: "sm", id: "translation-open-folder" }) +
      C.btn("🔄 重新扫描", { tone: "ghost", size: "sm", id: "translation-scan-folder" }) +
      "</div>" +
      C.hintInline("", { id: "local-hint", style: "margin-top:10px" }) +
      "</div>";

    const onlinePanel =
      '<div id="model-panel-online" style="display:none">' +
      '<div class="row" style="margin-bottom:12px">' +
      C.btn("➕ 添加在线模型", { tone: "success", size: "sm", id: "online-add" }) +
      "</div>" +
      C.field("在线模型", '<select class="select" id="online-model" style="flex:1">' + onlineOpts + "</select>") +
      '<div class="row">' +
      C.btn("🗑️ 删除", { tone: "warn", size: "sm", id: "online-delete", disabled: true }) +
      C.btn("🔌 测试连接", { tone: "info", size: "sm", id: "online-test", disabled: true }) +
      C.btn("📋 复制错误信息", { tone: "ghost", size: "sm", id: "online-copy", disabled: true }) +
      "</div>" +
      C.hintInline("", { id: "online-msg", style: "margin-top:10px" }) +
      "</div>";

    const langRows =
      C.field("源语言", '<select class="select" id="trans-source" style="flex:1">' + C.langOptions(langName(t.source_lang || "auto")) + "</select>") +
      C.field("目标语言", '<select class="select" id="trans-target" style="flex:1">' + C.langOptions(langName(t.target_lang || "zh")) + "</select>");

    const integRows =
      C.switchRow("功能1 启用翻译", t.func1_enabled === true, { key: "trans_func1" }) +
      C.switchRow("功能2 启用翻译", t.func2_enabled === true, { key: "trans_func2" }) +
      C.switchRow("功能3 启用翻译", t.func3_enabled === true, { key: "trans_func3" }) +
      C.switchRow("📖 漫画模式 启用翻译", t.manga_enabled === true, { key: "trans_manga" }) +
      C.switchRow("🎮 游戏模式 启用翻译", t.game_enabled === true, { key: "trans_game" });

    return (
      C.subsec(
        "🤖",
        "翻译模型",
        modelNav + localPanel + onlinePanel + C.hintInline("", { id: "model-hint", style: "margin-top:12px" }),
        "本地 GGUF 模型与在线翻译服务二选一，点上方按钮切换"
      ) +
      C.subsec("🌍", "语言设置", langRows, "仅用于 OCR 识别后的自动翻译（翻译页面有独立的语言选择）") +
      C.subsec("🔗", "OCR 翻译集成", integRows, "设置各功能识别出的文字是否自动翻译")
    );
  }

  const LANG_CODES = { 自动检测: "auto", 英语: "en", 中文: "zh", 日语: "ja", 韩语: "ko", 法语: "fr", 德语: "de", 西班牙语: "es", 俄语: "ru" };

  function langName(code) {
    const hit = Object.keys(LANG_CODES).filter(function (k) {
      return LANG_CODES[k] === code;
    });
    return hit.length ? hit[0] : "自动检测";
  }

  function mountTrans(root) {
    const navLocal = A.qs("#model-nav-local", root);
    const navOnline = A.qs("#model-nav-online", root);
    const panelLocal = A.qs("#model-panel-local", root);
    const panelOnline = A.qs("#model-panel-online", root);
    const applyBtn = A.qs("#model-apply", root);
    const localSel = A.qs("#local-model", root);
    const onlineSel = A.qs("#online-model", root);

    let nav = "local";

    A.on(A.qs("#translation-open-folder", root), "click", function () {
      A.bridge.call("open_model_folder", { kind: "translation" }).then(function (res) {
        if (res && res.ok === false) setText("#local-hint", "❌ " + (res.error || "打开文件夹失败"));
      });
    });

    /* 重新扫描：后端同步连扫两轮并把最新列表带回来。
       以前只 call 不处理响应，而推送按签名去重、列表没变就不发，
       用户点下去完全没有反馈，看起来就是「点了没效果」。 */
    A.on(A.qs("#translation-scan-folder", root), "click", function () {
      const btn = A.qs("#translation-scan-folder", root);
      if (btn && btn.disabled) return;
      if (btn) btn.disabled = true;
      setText("#local-hint", "正在扫描 模型/翻译/ …");
      A.bridge.call("scan_model_folders", {}).then(function (res) {
        if (btn) btn.disabled = false;
        if (!res || res.ok === false) {
          setText("#local-hint", "❌ 扫描失败：" + ((res && res.error) || "未知错误"));
          return;
        }
        if (res.models) A.state.models = res.models;
        refreshModelLists();
        const n = res.count || 0;
        setText("#local-hint", n
          ? "✅ 扫描完成，共 " + n + " 个本地模型"
          : "⚠️ 扫描完成，但 模型/翻译/ 里没有找到 GGUF 文件");
      }).catch(function (e) {
        if (btn) btn.disabled = false;
        setText("#local-hint", "❌ 扫描失败：" + e);
      });
    });

    A.bridge.on('model-folders-changed', function (msg) {
      if (!localSel.isConnected) return;
      A.state.models = msg.models;
      refreshModelLists();
    });

    function syncNav() {
      const isLocal = nav === "local";
      navLocal.className = isLocal ? "btn primary sm" : "btn ghost sm";
      navOnline.className = isLocal ? "btn ghost sm" : "btn primary sm";
      panelLocal.style.display = isLocal ? "" : "none";
      panelOnline.style.display = isLocal ? "none" : "";
      applyBtn.textContent = isLocal ? "✅ 启用本地模型" : "✅ 启用在线模型";
    }

    function syncButtons() {
      const hasLocal = !!localSel.value;
      const hasOnline = !!onlineSel.value;
      const locals = (A.state.models && A.state.models.local) || [];
      const cur = locals.filter(function (x) {
        return x.id === localSel.value;
      })[0];

      /* 与 tkinter 版 _sync_model_buttons_by_display 一致：
         禁用 → 只有选中的正好是「当前启用的模型」才能点
                （未启用的模型本来就没启用，禁用是空操作） */
      A.qs("#local-disable", root).disabled = !cur || !cur.active;
      A.qs("#online-delete", root).disabled = !hasOnline;
      A.qs("#online-test", root).disabled = !hasOnline;
      A.qs("#online-copy", root).disabled = true;

      const activeLocal = locals.filter(function (x) {
        return x.active;
      })[0];
      const applied = nav === "local" ? !!activeLocal && activeLocal.id === localSel.value : false;
      applyBtn.disabled = applied || (nav === "local" ? !hasLocal : !hasOnline);
    }

    A.on(navLocal, "click", function () {
      nav = "local";
      syncNav();
      syncButtons();
    });
    A.on(navOnline, "click", function () {
      nav = "online";
      syncNav();
      syncButtons();
    });

    /* 就地重建两个下拉框的选项。
       模型增删启停后只能改这里，绝不能 A.router.refresh() —— 那会整页重载，
       直接跳回第一个页签（用户刚操作的「翻译设置」被弹走，看起来像没生效）。 */
    function refreshModelLists(preferLocal, preferOnline) {
      const m = A.state.models || {};
      const locals = m.local || [];
      const onlines = m.online || [];

      const keepLocal = localSel.value;
      const keepOnline = onlineSel.value;

      localSel.innerHTML = locals.length
        ? locals
            .map(function (x) {
              return (
                '<option value="' + A.esc(x.id) + '"' + (x.active ? " selected" : "") + ">" +
                (x.active ? "🟢 " : "⚪ ") + A.esc(x.name) + "</option>"
              );
            })
            .join("")
        : '<option value="">（暂无本地模型）</option>';

      onlineSel.innerHTML = onlines.length
        ? onlines
            .map(function (p) {
              return '<option value="' + A.esc(p.name) + '">' + A.esc(p.name) + "</option>";
            })
            .join("")
        : '<option value="">（暂无在线模型，请点上方添加）</option>';

      /* 选中的优先级：显式指定 → 原选择 → 当前启用项 → 第一项 */
      function applySelect(el, list, keyOf, prefer, keep) {
        if (!list.length) return;
        const has = function (v) {
          return !!v && list.some(function (x) {
            return keyOf(x) === v;
          });
        };
        const active = list.filter(function (x) {
          return x.active;
        })[0];
        el.value = has(prefer) ? prefer : has(keep) ? keep : active ? keyOf(active) : keyOf(list[0]);
      }

      applySelect(localSel, locals, function (x) { return x.id; }, preferLocal, keepLocal);
      applySelect(onlineSel, onlines, function (x) { return x.name; }, preferOnline, keepOnline);

      syncButtons();
    }

    /* 重新拉一次模型状态并就地刷新（不整页重载） */
    function reloadModels(preferLocal, preferOnline) {
      A.bridge.call("state", {}).then(function (r) {
        if (r && r.ok) A.state.models = (r.state || {}).models || A.state.models;
        refreshModelLists(preferLocal, preferOnline);
      });
    }

    A.on(localSel, "change", syncButtons);
    A.on(onlineSel, "change", syncButtons);

    /* 写提示文字：元素不存在就静默跳过。
       提示只是「附带信息」，绝不能因为它抛异常把后面的 reloadModels()/syncButtons()
       打断 —— 之前 #local-hint 因为没渲染出 id 而拿到 null，正好把删除/禁用后的
       整段刷新逻辑吞掉，表现就是「删不掉、下拉框不更新、按钮卡死」。 */
    function setText(sel, text) {
      const el = A.qs(sel, root);
      if (el) el.textContent = text;
    }

    A.on(applyBtn, "click", function () {
      if (applyBtn.disabled) return;
      if (nav === "local") {
        A.bridge.call("enable_local_model", { id: localSel.value }).then(function (res) {
          if (res && res.ok) {
            setText("#local-hint", "✅ 已启用：" + (res.name || localSel.value));
            reloadModels(localSel.value);
          } else {
            setText("#local-hint", "❌ " + ((res && res.error) || "启用失败"));
          }
        });
      } else {
        A.bridge.call("enable_online_model", { name: onlineSel.value }).then(function (res) {
          if (res && res.ok) {
            setText("#online-msg", "✅ 已启用：" + onlineSel.value);
            reloadModels(null, onlineSel.value);
          } else {
            setText("#online-msg", "❌ " + ((res && res.error) || "启用失败"));
          }
        });
      }
    });

    A.on(A.qs("#local-disable", root), "click", function () {
      const disBtn = A.qs("#local-disable", root);
      const targetId = localSel.value;
      if (!targetId || disBtn.disabled) return;
      disBtn.disabled = true;
      A.bridge.call("disable_local_model", { id: targetId }).then(function (res) {
        if (res && res.ok) {
          setText("#local-hint", "已禁用：" + targetId);
          reloadModels();
        } else {
          setText("#local-hint", "❌ " + ((res && res.error) || "禁用失败"));
          syncButtons();
        }
      });
    });

    /* 「移除记录」按钮已按用户要求下线（2026-11-15）：失效记录由扫描自动清理，
       不再需要手动移除。删掉按钮后绑定的 A.qs 会拿到 null，
       A.on(null) 会抛异常把后面的绑定全吞掉 —— 所以整段一起删干净。 */

    A.on(A.qs("#local-add", root), "click", function () {
      openAddLocalModel(reloadModels);
    });

    A.on(A.qs("#online-add", root), "click", function () {
      openAddOnlineModel(reloadModels);
    });

    A.on(A.qs("#online-delete", root), "click", function () {
      A.bridge.call("remove_online_model", { name: onlineSel.value }).then(function (res) {
        if (res && res.ok) {
          setText("#online-msg", "已删除");
          reloadModels();
        } else {
          setText("#online-msg", "❌ " + ((res && res.error) || "删除失败"));
        }
      });
    });

    A.on(A.qs("#online-test", root), "click", function () {
      const msg = A.qs("#online-msg", root);
      if (!msg) return;
      msg.style.color = "";
      msg.textContent = "正在测试连接…";
      A.bridge.call("test_online_model", { name: onlineSel.value }).then(function (res) {
        if (res && res.ok) {
          msg.style.color = "var(--ink-green)";
          msg.textContent = "✅ " + (res.message || "连接成功");
        } else {
          msg.style.color = "var(--ink-rose)";
          msg.textContent = "❌ " + ((res && res.error) || "连接失败");
          A.qs("#online-copy", root).disabled = false;
          A.qs("#online-copy", root).dataset.msg = msg.textContent;
        }
      });
    });

    A.on(A.qs("#online-copy", root), "click", function () {
      const el = A.qs("#online-copy", root);
      const msg = el.dataset.msg || "";
      if (!msg) return;
      /* QWebEngine 里 navigator.clipboard 常被权限拦掉，统一走 Python 剪贴板 */
      A.bridge.call("copy_text", { text: msg }).then(function (res) {
        setText(
          "#online-msg",
          res && res.ok ? "📋 错误信息已复制到剪贴板" : "❌ 复制失败：" + ((res && res.error) || "剪贴板不可用")
        );
      });
    });

    /* OCR 翻译集成开关 */
    A.qsa(".switch[data-switch-key^='trans_']", root).forEach(function (sw) {
      sw.addEventListener("switchchange", function (e) {
        const mode = sw.dataset.switchKey.slice(6);
        /* 必须同步 A.state.translation —— 它是启动时的快照，只写配置不回写状态的话，
           切页签 / 重渲染时 renderTrans() 又按旧快照画一遍，开关会「自己弹回去」，
           看起来就像开关没生效（磁盘其实已经写进去了）。 */
        if (!A.state.translation) A.state.translation = {};
        A.state.translation[mode + "_enabled"] = !!e.detail.on;
        A.bridge.call("set_config", { section: "translation", key: mode + "_enabled", value: e.detail.on });
      });
    });

    [['#trans-source','source_lang','auto'],['#trans-target','target_lang','zh']].forEach(function (item) {
      const el = A.qs(item[0],root);
      A.on(el,'change',function () { applySetting('set_config',{section:'translation',key:item[1],value:LANG_CODES[el.value] || item[2]}); });
    });

    syncNav();
    syncButtons();
  }

  /* 添加本地模型弹窗（onDone：添加成功后由页面就地刷新下拉框） */
  function openAddLocalModel(onDone) {
    const box = A.modal.open(
      '<div class="modal-head">' +
        '<div><div class="modal-title">添加本地模型</div>' +
        '<div class="modal-desc">选择本地的 GGUF 模型文件，加入本地模型列表。</div></div>' +
        '<button class="modal-close" data-close>✕</button></div>' +
        C.field("显示名称", '<input class="select" id="lm-name" style="flex:1;min-width:0" placeholder="如：HY-MT1.5-1.8B">') +
        C.field(
          "模型文件",
          '<input class="select" id="lm-path" style="flex:1;min-width:0" placeholder="C:\\Models\\xxx.gguf">' +
            C.btn("浏览…", { tone: "ghost", size: "sm", id: "lm-browse" })
        ) +
        '<div class="modal-note">💡 仅支持 llama.cpp 的 GGUF 格式。模型越大翻译质量越好，但占用内存也更多。</div>' +
        '<div class="modal-foot">' +
        C.btn("取消", { tone: "ghost", attrs: "data-close" }) +
        C.btn("➕ 添加", { tone: "success", id: "lm-ok" }) +
        "</div>"
    );
    if (!box) return;

    const nameEl = A.qs("#lm-name", box);
    const pathEl = A.qs("#lm-path", box);

    A.on(A.qs("#lm-browse", box), "click", function () {
      A.bridge.call("pick_file", { filter: "GGUF 模型 (*.gguf)" }).then(function (res) {
        if (res && res.ok && res.path) {
          pathEl.value = res.path;
          if (!nameEl.value.trim()) {
            const base = res.path.split(/[\\/]/).pop().replace(/\.gguf$/i, "");
            nameEl.value = base;
          }
          /* 选完文件后把焦点放回「显示名称」并全选，
             这样用户直接打字就能改名（原生文件对话框会抢走键盘焦点）。 */
          try {
            nameEl.focus();
            nameEl.select();
          } catch (err) {}
        }
      });
    });

    A.on(A.qs("#lm-ok", box), "click", function () {
      const name = nameEl.value.trim();
      const path = pathEl.value.trim();
      if (!path) {
        A.qs("#lm-name", box).placeholder = "请先选择模型文件";
        return;
      }
      A.bridge.call("add_local_model", { name: name || "未命名模型", path: path }).then(function (res) {
        if (res && res.ok) {
          A.modal.close();
          flash("✅ 已添加模型", true);
          if (onDone) onDone(res.id || null);
        } else {
          flash("❌ " + ((res && res.error) || "添加失败"), false);
        }
      });
    });
  }

  /* 添加在线模型弹窗（onDone：保存成功后由页面就地刷新下拉框） */
  const PRESETS = [
    { key: "", label: "自定义 (OpenAI兼容)", base: "https://api.deepseek.com/v1/chat/completions", resp: "choices.0.message.content" },
    { key: "baidu", label: "百度翻译", base: "https://fanyi-api.baidu.com/api/trans/vip/translate", resp: "trans_result.0.dst" },
    { key: "microsoft", label: "微软翻译 (Azure)", base: "https://api.cognitive.microsofttranslator.com/translate", resp: "0.translations.0.text" },
    { key: "google", label: "谷歌翻译", base: "https://translation.googleapis.com/language/translate/v2", resp: "data.translations.0.translatedText" },
  ];

  function openAddOnlineModel(onDone) {
    const presetOpts = PRESETS.map(function (p) {
      return '<option value="' + p.key + '">' + A.esc(p.label) + "</option>";
    }).join("");

    const box = A.modal.open(
      '<div class="modal-head">' +
        '<div><div class="modal-title">添加在线模型</div>' +
        '<div class="modal-desc">选择预设（百度 / 微软 / 谷歌）或自定义 OpenAI 兼容接口，填写凭证后保存。</div></div>' +
        '<button class="modal-close" data-close>✕</button></div>' +
        C.field("名称", '<input class="select" id="om-name" style="flex:1;min-width:0" placeholder="如：我的百度翻译">') +
        C.field("预设类型", '<select class="select" id="om-preset" style="flex:1">' + presetOpts + "</select>") +
        C.field("接口地址", '<input class="select" id="om-baseurl" style="flex:1;min-width:0">') +
        C.field("API Key", '<input class="select" type="password" id="om-apikey" style="flex:1;min-width:0">') +
        C.field("模型名", '<input class="select" id="om-model" style="flex:1;min-width:0" placeholder="如：deepseek-chat">') +
        C.field("APP ID", '<input class="select" id="om-appid" style="flex:1;min-width:0">') +
        C.field("密钥 Secret", '<input class="select" type="password" id="om-secret" style="flex:1;min-width:0">') +
        C.field("区域 Region", '<input class="select" id="om-region" style="flex:1;min-width:0" value="eastasia">') +
        '<div id="om-adv">' +
        '<div class="subsec-hint" style="margin:12px 0 6px">请求体模板（占位符：{text} {source_lang} {target_lang} {api_key} {model} {prompt}）</div>' +
        '<textarea class="ta" id="om-body" style="height:88px">{"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}]}</textarea>' +
        C.field("响应提取路径", '<input class="select" id="om-resp" style="flex:1;min-width:0" value="choices.0.message.content">') +
        "</div>" +
        '<div class="modal-note" id="om-help"></div>' +
        C.hintInline("", { id: "om-test-msg", style: "margin-top:10px" }) +
        '<div class="modal-foot">' +
        C.btn("🔌 测试连接", { tone: "info", size: "sm", id: "om-test" }) +
        '<span style="flex:1"></span>' +
        C.btn("取消", { tone: "ghost", attrs: "data-close" }) +
        C.btn("✅ 添加并启用", { tone: "success", id: "om-save" }) +
        "</div>"
    );
    if (!box) return;

    const presetSel = A.qs("#om-preset", box);

    function applyPreset() {
      const key = presetSel.value;
      const p = PRESETS.filter(function (x) {
        return x.key === key;
      })[0] || PRESETS[0];

      A.qs("#om-baseurl", box).value = p.base;
      A.qs("#om-resp", box).value = p.resp;

      const isCustom = key === "";
      A.qs("#om-apikey", box).parentElement.parentElement.style.display =
        key === "microsoft" || key === "google" || isCustom ? "" : "none";
      A.qs("#om-model", box).parentElement.parentElement.style.display = isCustom ? "" : "none";
      A.qs("#om-appid", box).parentElement.parentElement.style.display = key === "baidu" ? "" : "none";
      A.qs("#om-secret", box).parentElement.parentElement.style.display = key === "baidu" ? "" : "none";
      A.qs("#om-region", box).parentElement.parentElement.style.display = key === "microsoft" ? "" : "none";
      A.qs("#om-adv", box).style.display = isCustom ? "" : "none";

      const help = {
        baidu: "💡 百度翻译：登录 fanyi-api.baidu.com → 控制台「我的服务」申请「通用翻译 API」，拿到 APP ID 和密钥（Secret）填入上方。标准版约 5 万字符/月，国内直连。",
        microsoft: "💡 微软 Azure：portal.azure.com 创建「Azure AI Translator」资源，拿到订阅密钥（Key）和区域（Region，如 eastasia）。免费层 200 万字符/月。",
        google: "💡 谷歌翻译：console.cloud.google.com 启用 Cloud Translation API，创建 API Key 填入上方。注意：国内需代理才能访问。",
        "": "💡 自定义：填写任意 OpenAI 兼容接口（DeepSeek / 通义 / 火山方舟等），接口地址、密钥、模型名必填；请求体模板可按需修改。",
      };
      A.qs("#om-help", box).textContent = help[key] || help[""];
      A.qs("#om-test-msg", box).textContent = "";
    }

    A.on(presetSel, "change", applyPreset);
    applyPreset();

    function collect() {
      const key = presetSel.value;
      return {
        name: A.qs("#om-name", box).value.trim() || presetSel.options[presetSel.selectedIndex].text,
        preset: key,
        base_url: A.qs("#om-baseurl", box).value.trim(),
        api_key: A.qs("#om-apikey", box).value.trim(),
        model: A.qs("#om-model", box).value.trim(),
        app_id: A.qs("#om-appid", box).value.trim(),
        secret: A.qs("#om-secret", box).value.trim(),
        region: A.qs("#om-region", box).value.trim(),
        body_template: A.qs("#om-body", box).value,
        response_path: A.qs("#om-resp", box).value.trim(),
      };
    }

    A.on(A.qs("#om-test", box), "click", function () {
      const msg = A.qs("#om-test-msg", box);
      msg.style.color = "";
      msg.textContent = "正在测试连接…";
      A.bridge.call("test_online_config", collect()).then(function (res) {
        if (res && res.ok) {
          msg.style.color = "var(--ink-green)";
          msg.textContent = "✅ " + (res.message || "连接成功");
        } else {
          msg.style.color = "var(--ink-rose)";
          msg.textContent = "❌ " + ((res && res.error) || "连接失败");
        }
      });
    });

    A.on(A.qs("#om-save", box), "click", function () {
      const data = collect();
      A.bridge.call("add_online_model", data).then(function (res) {
        if (res && res.ok) {
          A.modal.close();
          flash("✅ 已添加并启用：" + data.name, true);
          if (onDone) onDone(null, data.name);
        } else {
          flash("❌ " + ((res && res.error) || "保存失败"), false);
        }
      });
    });
  }

  /* ============================================================
     页签 5：识别设置
     ============================================================ */
  function renderRecog() {
    const r = A.state.recognition || {};
    return (
      C.subsec(
        "🔍",
        "识别设置",
        '<div class="subsec-title" style="color:var(--ink-cyan);margin-top:10px">功能1 · 框选朗读</div>' +
          C.switchRow("剪贴板复制", r.func1_copy === true, { key: "recog_func1_copy" }) +
          C.hintInline("开启后，功能1 识别到的原文自动复制到系统剪贴板", "margin:-4px 0 8px 0") +
          C.switchRow("朗读原文", r.func1_read !== false, { key: "recog_func1_read" }) +
          C.hintInline("关闭后，功能1 识别结果只复制/显示，不朗读（含译文）", "margin:-4px 0 8px 0") +
          '<div class="subsec-title" style="color:var(--ink-cyan);margin-top:10px">功能2 · 固定区域朗读</div>' +
          C.switchRow("剪贴板复制", r.func2_copy === true, { key: "recog_func2_copy" }) +
          C.hintInline("开启后，功能2 识别到的原文自动复制到系统剪贴板", "margin:-4px 0 8px 0") +
          C.switchRow("朗读原文", r.func2_read !== false, { key: "recog_func2_read" }) +
          C.hintInline("关闭后，功能2 识别结果只复制/显示，不朗读（含译文）", "margin:-4px 0 8px 0"),
        "控制功能1 / 功能2 识别后的剪贴板复制与朗读行为"
      )
    );
  }

  function mountRecog(root) {
    A.qsa(".switch[data-switch-key^='recog_']", root).forEach(function (sw) {
      sw.addEventListener("switchchange", function (e) {
        const key = sw.dataset.switchKey.slice(6); // func1_copy 等
        A.bridge.call("set_config", { section: "recognition", key: key, value: e.detail.on });
      });
    });

  }

  /* ============================================================
     页签 6：个人词库
     ============================================================ */
  let hotwordCache = { hotwords: [], candidates: [] };

  function renderHotwords() {
    return (
      C.subsec(
        "📚",
        "正式词表",
        '<textarea class="ta" id="hw-list" style="height:150px" ' +
          'placeholder="每行一条。写成「错词=正确词」可强制替换，例如：音机铜=阴极铜"></textarea>' +
          '<div class="row mt-4">' +
          C.btn("💾 保存词表", { tone: "primary", size: "sm", id: "hw-save" }) +
          C.btn("📥 导入", { tone: "info", size: "sm", id: "hw-import" }) +
          C.btn("📤 导出", { tone: "ghost", size: "sm", id: "hw-export" }) +
          '<span style="flex:1"></span>' +
          C.hintInline("", { id: "hw-msg" }) +
          "</div>" +
          '<div class="hint-inline" id="hw-count"></div>' +
          C.hintInline("💡 两字词（如 TP1）建议写成「错误=正确」显式映射；三字以上才参与拼音模糊纠错", "margin-top:6px"),
        "这些词在每次识别后自动纠错"
      ) +
      C.subsec(
        "🧠",
        "候选词（待确认）",
        '<div id="hw-candidates"></div>',
        "从你修改识别结果时自动学习，确认后才会并入正式词表"
      )
    );
  }

  function renderCandidateList(list) {
    if (!list || !list.length) {
      return '<div class="hint-inline">暂无候选。在语音页识别后修改结果并点「保存修改」，改动会自动出现在这里。</div>';
    }
    const rows = list.map(function (c) {
      const wrong = c.wrong ? A.esc(c.wrong) + " → " : "";
      const w = c.weight > 1 ? '<span class="hotkey-hint" style="margin-left:6px">×' + c.weight + "</span>" : "";
      return (
        '<div class="field" data-right="' + A.esc(c.right) + '">' +
        '<span class="field-label">' + wrong + "<b>" + A.esc(c.right) + "</b>" + w + "</span>" +
        '<div class="field-ctl">' +
        C.btn("✅ 加入", { tone: "success", size: "sm", cls: "hw-accept" }) +
        C.btn("✕ 忽略", { tone: "ghost", size: "sm", cls: "hw-dismiss" }) +
        "</div></div>"
      );
    });
    return rows.join("");
  }

  function refreshHotwords(root) {
    return A.bridge.call("hotwords", {}).then(function (res) {
      if (!res || !res.ok) return;
      hotwordCache = res;
      const listEl = A.qs("#hw-list", root);
      if (listEl) listEl.value = (res.hotwords || []).join("\n");
      const box = A.qs("#hw-candidates", root);
      if (box) box.innerHTML = renderCandidateList(res.candidates);
      bindCandidateButtons(box, root);
      setText("#hw-count", "共 " + (res.hotwords || []).length + " 条");
    });
  }

  function bindCandidateButtons(box, root) {
    if (!box) return;
    A.qsa(".hw-accept", box).forEach(function (btn) {
      btn.addEventListener("click", function () {
        const right = btn.closest(".field").dataset.right;
        A.bridge.call("hotwords_accept", { rights: [right] }).then(function (res) {
          if (res && res.ok) setText("#hw-msg", res.message || "已加入");
          refreshHotwords(root);
        });
      });
    });
    A.qsa(".hw-dismiss", box).forEach(function (btn) {
      btn.addEventListener("click", function () {
        const right = btn.closest(".field").dataset.right;
        A.bridge.call("hotwords_dismiss", { rights: [right] }).then(function (res) {
          if (res && res.ok) setText("#hw-msg", res.message || "已忽略");
          refreshHotwords(root);
        });
      });
    });
  }

  function mountHotwords(root) {
    refreshHotwords(root);

    A.on(A.qs("#hw-save", root), "click", function () {
      const listEl = A.qs("#hw-list", root);
      const words = (listEl.value || "").split("\n")
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s; });
      A.bridge.call("hotwords_save", { hotwords: words }).then(function (res) {
        setText("#hw-msg", (res && res.message) || (res && res.error) || "保存失败");
        refreshHotwords(root);
      });
    });

    A.on(A.qs("#hw-import", root), "click", function () {
      /* 导入即并入：把文本框内容和现有词表一起提交，由后端去重合并 */
      const listEl = A.qs("#hw-list", root);
      A.bridge.call("hotwords_add", { text: listEl.value }).then(function (res) {
        setText("#hw-msg", (res && res.message) || (res && res.error) || "导入失败");
        refreshHotwords(root);
      });
    });

    A.on(A.qs("#hw-export", root), "click", function () {
      A.bridge.call("hotwords_export", {}).then(function (res) {
        if (res && res.ok) setText("#hw-msg", "已导出到 " + (res.path || ""));
        else setText("#hw-msg", (res && res.error) || "导出失败");
      });
    });
  }

  /* ============================================================
     页签 7：主题
     ============================================================ */
  function renderTheme() {
    const cur = A.theme.current();
    const frost = A.theme.frostOn();

    return (
      C.subsec(
        "🌗",
        "外观模式",
        '<div class="theme-modes">' +
          '<div class="theme-mode' + (cur === "dark" ? " active" : "") + '" data-mode="dark">' +
          '<div class="tm-preview tm-dark"><i></i><i></i><i></i></div>' +
          '<div class="tm-name">🌙 深色</div></div>' +
          '<div class="theme-mode' + (cur === "light" ? " active" : "") + '" data-mode="light">' +
          '<div class="tm-preview tm-light"><i></i><i></i><i></i></div>' +
          '<div class="tm-name">☀️ 亮色</div></div>' +
          "</div>",
        "切换后立即生效"
      ) +
      C.subsec(
        "🧊",
        "窗口效果",
        '<div class="field"><span class="field-label">毛玻璃效果</span>' +
          '<div class="field-ctl"><div class="switch' + (frost ? " on" : "") + '" id="frost-switch"></div></div></div>' +
          C.hintInline("💡 开启后卡片会模糊底层的光球与背景，呈现通透的磨砂质感", "margin-top:4px"),
        ""
      )
    );
  }

  function mountTheme(root) {
    A.qsa(".theme-mode", root).forEach(function (el) {
      el.addEventListener("click", function (e) {
        const mode = el.dataset.mode;
        if (A.theme.current() === mode) return;
        const x = e.clientX != null ? e.clientX : window.innerWidth / 2;
        const y = e.clientY != null ? e.clientY : window.innerHeight / 2;
        A.theme.animateTransition(x, y, mode);
        A.qsa(".theme-mode", root).forEach(function (x) {
          x.classList.toggle("active", x === el);
        });
      });
    });

    const fs = A.qs("#frost-switch", root);
    if (fs) {
      /* 开关本体的视觉切换由 components.bindCommon 统一负责（它会 toggle .on
         并派发 switchchange），这里只接业务语义，避免两处各自 toggle 互相抵消。 */
      fs.addEventListener("switchchange", function (e) {
        A.theme.setFrost(!!e.detail.on);
      });

      /* 点击整行（标签文字区域）也能切换：转成一次开关点击，走同一条路径。 */
      const row = fs.closest(".field");
      if (row) {
        row.style.cursor = "pointer";
        row.addEventListener("click", function (e) {
          if (e.target === fs || fs.contains(e.target)) return; // 点开关本体，已由开关处理
          fs.click();
        });
      }
    }
  }

  /* ============================================================
     装配
     ============================================================ */
  const TABS = [
    { key: "func", label: "功能设置", render: renderFunc, mount: mountFunc },
    { key: "voice", label: "语音设置", render: renderVoice, mount: mountVoice },
    { key: "hotkey", label: "按键设置", render: renderHotkey, mount: mountHotkey },
    { key: "trans", label: "翻译设置", render: renderTrans, mount: mountTrans },
    { key: "recog", label: "识别设置", render: renderRecog, mount: mountRecog },
    { key: "hotwords", label: "个人词库", render: renderHotwords, mount: mountHotwords },
    { key: "theme", label: "主题", render: renderTheme, mount: mountTheme },
  ];

  A.pages.settings = {
    title: "设置",
    desc: "全局配置管理",
    icon: "⚙️",
    group: "系统",

    render: function () {
      const tabsHtml = TABS.map(function (t, i) {
        return '<div class="tab' + (i === 0 ? " active" : "") + '" data-tab="' + t.key + '">' + A.esc(t.label) + "</div>";
      }).join("");

      return C.card(
        "",
        "",
        '<div class="tabs" id="stabs"><div class="tab-pill" id="tab-pill"></div>' + tabsHtml + "</div>" +
          '<div id="sbody"></div>'
      ) + C.card('', '',
        '<div class="config-bar">' +
        C.btn('导入配置', {tone:'ghost',size:'sm',id:'config-import'}) +
        C.btn('导出配置', {tone:'ghost',size:'sm',id:'config-export'}) +
        C.btn('恢复全部默认设置', {tone:'ghost',size:'sm',id:'config-reset-all'}) +
        '</div>', { cls: 'config-footer enter-up' });
    },

    mount: function (root) {
      mountConfigActions(root);
      const body = A.qs("#sbody", root);
      const tabsBox = A.qs("#stabs", root);
      const pill = A.qs("#tab-pill", root);

      let activeKey = TABS[0].key;
      let switching = false;

      /* 轨道容器：body 内放一个横向滚动的 track，pane 相邻排列。
         切换时把「旧标签到新标签之间」的所有标签 pane 都放进轨道，
         整条轨道快速滚过，中间每个页面依次掠过（不是空白）。 */
      body.style.overflow = "hidden";
      body.style.position = "relative";
      const track = document.createElement("div");
      track.style.display = "flex";
      track.style.width = "100%";
      body.appendChild(track);

      function makePane(tabKey) {
        const def = TABS.filter(function (t) { return t.key === tabKey; })[0];
        const pane = document.createElement("div");
        pane.className = "sbody-pane";
        pane.dataset.tab = tabKey;
        pane.style.flex = "0 0 100%";
        pane.style.minWidth = "0";
        pane.innerHTML = def ? def.render() : "";
        C.bindCommon(pane);
        if (def && def.mount) {
          try {
            def.mount(pane);
          } catch (e) {
            console.error("[settings] mount error on " + tabKey, e);
          }
        }
        return pane;
      }

      /* 首次：直接放第一个标签的 pane，不滚动 */
      track.appendChild(makePane(TABS[0].key));

      function showTab(tabKey, newIdx, oldIdx) {
        activeKey = tabKey;

        const tabEl = A.qs('.tab[data-tab="' + tabKey + '"]', tabsBox);
        A.qsa(".tab", tabsBox).forEach(function (t) {
          t.classList.toggle("active", t === tabEl);
        });
        A.movePill(pill, tabEl, true);

        /* delta = 索引差（带符号）。 */
        const delta = newIdx - oldIdx;
        const dist = Math.abs(delta);

        /* 轨道里一律按「阅读顺序」铺 pane：从 min(old,new) 到 max(old,new)。
           视口里看到哪一页只由 translateX 决定：
           - 向右（delta > 0）：旧页在轨道第 0 格，视口从 0 推到末格；
           - 向左（delta < 0）：旧页在轨道末格，视口从末格拉回 0。
           旧代码向左时把视口直接放到第 0 格（= 目标页），动画一开始就显示目标页，
           结束时 onfinish 又换成旧页，于是用户看到「先闪一下之前选中的页面」。 */
        const lo = Math.min(oldIdx, newIdx);
        const hi = Math.max(oldIdx, newIdx);
        const order = [];
        for (let i = lo; i <= hi; i++) {
          order.push(TABS[i].key);
        }
        /* 旧页 / 目标页在轨道里的格子号 */
        const fromIdx = oldIdx - lo;
        const toIdx = newIdx - lo;

        track.innerHTML = "";
        order.forEach(function (key) {
          track.appendChild(makePane(key));
        });

        /* 中间掠过的 pane 加轻微模糊：首尾（当前页/目标页）保持清晰，
           中间经过的页面在快速掠过时略微模糊，强化「掠过」的视觉。 */
        Array.prototype.forEach.call(track.children, function (c, idx) {
          if (idx !== fromIdx && idx !== toIdx) {
            c.style.filter = "blur(2px)";
            c.style.opacity = "0.8";
          }
        });

        /* 整条轨道快速滚过：视口从旧页所在格移动到目标页所在格。 */
        const duration = 260 + dist * 70;
        const from = "translateX(-" + fromIdx + "00%)";
        const to = "translateX(-" + toIdx + "00%)";

        /* 关键：动画启动前先同步设置 inline transform 到起始偏移态，
           避免 animate() 异步启动的间隙里出现一帧旧内容（尤其向左滚动）。 */
        track.style.transform = from;
        track.style.willChange = "transform";

        const anim = track.animate(
          [{ transform: from }, { transform: to }],
          { duration: duration, easing: "cubic-bezier(.33,0,.2,1)", fill: "forwards" }
        );
        anim.onfinish = function () {
          /* 先在动画还覆盖着视口时提交 DOM（只留目标 pane 并归零），
             再 cancel；顺序反了会露出「旧页归零」的一帧。 */
          const target = track.children[toIdx];
          track.innerHTML = "";
          if (target) track.appendChild(target);
          track.style.transform = "none";
          track.style.willChange = "";
          anim.cancel();
        };
      }

      A.qsa(".tab", tabsBox).forEach(function (t) {
        t.addEventListener("click", function () {
          if (t.classList.contains("active")) return;
          if (switching) return;
          const newKey = t.dataset.tab;
          const oldIdx = TABS.findIndex(function (x) { return x.key === activeKey; });
          const newIdx = TABS.findIndex(function (x) { return x.key === newKey; });
          switching = true;
          showTab(newKey, newIdx, oldIdx);
          /* 滚动结束后解锁（按距离估算时长 + 缓冲） */
          const dist = Math.abs(newIdx - oldIdx);
          setTimeout(function () { switching = false; }, 320 + dist * 70);
        });
      });

      /* 首次定位 pill（不带动画） */
      setTimeout(function () {
        A.movePill(pill, A.qs(".tab.active", tabsBox), false);
      }, 0);
    },
  };
})();
