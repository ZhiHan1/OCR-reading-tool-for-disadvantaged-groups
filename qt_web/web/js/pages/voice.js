/* ============================================================
   语音输入页
   按住说话 → 松开自动识别；主按钮可终止旧任务并接管。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  const INPUT_MODES = [
    { value: 'manual', label: '手动结束', text: '点一次悬浮麦克风开始持续录音；再点一次停止并统一输出文字。' },
    { value: 'realtime', label: '实时分段', text: '每说完一段就追加文字，继续监听下一段；点击停止后暂停。' },
    { value: 'auto', label: '说完自动结束', text: '可以连续说多句话，中途短暂停顿不结束；较长静音后整轮结束。' },
  ];

  /* 闲置退出时间：0 = 常驻不退出（进程不回收内存），固定档位，常驻放最后 */
  const IDLE_EXIT_OPTIONS = [
    { value: '60', label: '1 分钟' },
    { value: '120', label: '2 分钟' },
    { value: '300', label: '5 分钟（默认）' },
    { value: '600', label: '10 分钟' },
    { value: '1800', label: '30 分钟' },
    { value: '0', label: '常驻（不退出）' },
  ];

  function optionList(items, selected) {
    return items
      .map(function (it) {
        return '<option value="' + A.esc(it.value) + '"' + (it.value === selected ? " selected" : "") + ">" + A.esc(it.label) + "</option>";
      })
      .join("");
  }

  A.pages.voice_input = {
    title: "语音输入",
    desc: "选择识别方式 · 悬浮麦克风按所选模式工作",
    icon: "🎤",
    group: "处理",

    render: function () {
      const a = A.state.asr || {};
      const models = (a.models || []).map(function (m) { return { value: m.id, label: m.name + (m.available ? '' : '（路径失效）') }; });
      const selectedModel = (a.models || []).find(function (m) { return m.id === a.local_model; });
      const mode = a.input_mode || 'manual';
      const selectedMode = INPUT_MODES.find(function (m) { return m.value === mode; }) || INPUT_MODES[0];
      const idleExit = a.idle_exit_seconds != null ? String(a.idle_exit_seconds) : '300';
      const optSwitch = function (label, on, id, key) {
        return '<span class="opt-item"><span class="opt-label">' + A.esc(label) + '</span>' +
          '<span class="switch' + (on ? ' on' : '') + '" id="' + id + '" data-switch-key="' + key + '"></span></span>';
      };

      const settings = C.card('', '',
        '<button type="button" class="settings-head" id="asr-settings-head">' +
        '<span class="settings-title">识别设置</span>' +
        '<span class="settings-hint">本地离线识别 · 无快捷键</span>' +
        '<span class="settings-caret">▾</span></button>' +
        '<div class="settings-body" id="asr-settings-body">' +
        '<div class="group-title">识别方式</div>' +
        '<div class="mode-switch" role="radiogroup" aria-label="语音识别方式">' +
        INPUT_MODES.map(function (m) {
          return '<label class="mode-option' + (mode === m.value ? ' active' : '') + '">' +
            '<input type="radio" name="asr-input-mode" value="' + m.value + '"' + (mode === m.value ? ' checked' : '') + '>' +
            '<span>' + m.label + '</span></label>';
        }).join('') + '</div>' +
        '<div class="mode-desc muted" id="asr-mode-desc">' + selectedMode.text + '</div>' +
        '<div class="mode-controls">' +
        '<label class="mode-control">分段停顿 <select class="select" id="asr-segment-silence">' + optionList([{value:'400',label:'0.4 秒'},{value:'600',label:'0.6 秒'},{value:'800',label:'0.8 秒（默认）'},{value:'1200',label:'1.2 秒'},{value:'2000',label:'2 秒'}], String(a.segment_silence_ms || 800)) + '</select></label>' +
        '<label class="mode-control">整轮结束静音 <select class="select" id="asr-turn-silence">' + optionList([{value:'2000',label:'2 秒'},{value:'3000',label:'3 秒（默认）'},{value:'4000',label:'4 秒'},{value:'5000',label:'5 秒'},{value:'8000',label:'8 秒'},{value:'10000',label:'10 秒'}], String(a.turn_silence_ms || 3000)) + '</select></label>' +
        '<div class="mode-actions">' + C.btn('开始所选模式', {tone:'primary',id:'asr-mode-start'}) + C.btn('停止', {tone:'warn',id:'asr-mode-stop'}) + '</div>' +
        '</div>' +
        '<div class="mode-caption">分段停顿不会结束整轮发言；自动模式先等您开口，再按整轮静音判定，15 秒未开口会暂停。</div>' +
        '<div class="mode-divider"></div>' +
        '<div class="group-title">模型</div>' +
        '<div class="model-row">' +
        '<span class="model-label">麦克风</span>' +
        '<select class="select" id="asr-device">' +
        '<option value=""' + (a.device_name ? '' : ' selected') + '>跟随系统默认输入</option>' +
        (a.devices || []).map(function (d) {
          const duplicated = (a.devices || []).filter(function (x) { return x.name === d.name; }).length > 1;
          const label = d.name + (duplicated && d.hostapi ? ' · ' + d.hostapi : '');
          return '<option value="' + A.esc(d.name) + '"' + (d.name === a.device_name ? ' selected' : '') + '>' + A.esc(label) + '</option>';
        }).join('') +
        '</select>' +
        C.btn("刷新", { tone: "ghost", size: "sm", id: "asr-device-refresh" }) +
        '<span class="hint-inline" id="asr-device-status">' +
        A.esc((a.devices || []).length ? '共 ' + a.devices.length + ' 个在线设备' : '未检测到在线麦克风') +
        '</span>' +
        '</div>' +
        '<div class="mode-caption">只列出 Windows 判定为「在线」的麦克风（与系统设置一致）；插拔设备后点「刷新」。留空则跟随 Windows 默认输入。</div>' +
        '<div class="model-row">' +
        '<span class="model-label">识别模型</span>' +
        '<select class="select" id="asr-model">' + (models.length ? optionList(models, a.local_model) : '<option value="">未找到语音模型</option>') + '</select>' +
        C.btn("加载", { tone: "primary", size: "sm", id: "asr-load" }) +
        '<span class="hint-inline" id="asr-model-status">' + A.esc(a.model_status || "首次识别时加载") + '</span>' +
        '</div>' +
        '<div class="mode-caption" id="asr-model-path">' + A.esc(selectedModel ? selectedModel.path : '把语音模型文件夹放进程序目录的 模型/语音/ 下，会自动识别') + '</div>' +
        '<div class="model-row">' +
        '<span class="model-label">闲置退出</span>' +
        '<select class="select" id="asr-idle-exit">' + optionList(IDLE_EXIT_OPTIONS, idleExit) + '</select>' +
        '<span class="hint-inline">超时后释放约 2GB 内存</span>' +
        '</div>' +
        '<div class="mode-caption">闲置超时后语音进程退出以释放内存，下次识别需重新加载（约 11~14 秒）；选「常驻」则一直驻留到关闭程序。</div>' +
        '<div class="mode-divider"></div>' +
        '<div class="group-title">输出与显示</div>' +
        '<div class="opt-group">' +
        optSwitch("识别后复制到剪贴板", a.copy_to_clipboard !== false, "asr-copy", "asr_copy") +
        optSwitch("识别后自动输入到光标", a.auto_input !== false, "asr-auto-input", "asr_auto_input") +
        optSwitch("显示置顶麦克风悬浮窗", a.floating_window === true, "asr-floating", "asr_floating") +
        '</div>' +
        '<div class="mode-caption">自动输入前先在目标应用点好输入光标；识别结果不会自动回车发送。</div>' +
        '</div>'
      );

      const card2 = C.card(
        "",
        "",
        '<div class="talk-bar">' +
        C.btn('🎤 按住说话', { tone: 'primary', id: 'talk-btn' }) +
        '<span class="hint-inline" id="talk-status">就绪 · 松开自动识别</span>' +
        '<span class="hint-inline" id="asr-delivery-status" role="status"></span>' +
        '</div>' +
        '<div class="mode-caption" style="margin-top:6px">悬浮麦克风卡住时，按住此按钮将结束旧任务并重新录音。</div>'
      );

      const card3 = C.card(
        "识别结果",
        "改完后点「保存修改」，系统会从中学习你的个人词库",
        '<textarea class="ta" id="asr-output" style="height:96px" placeholder="识别结果将显示在这里，可直接修改…"></textarea>' +
          '<div class="row mt-4">' +
          C.btn("💾 保存修改", { tone: "primary", size: "sm", id: "asr-save-btn" }) +
          C.btn("📋 复制结果", { tone: "success", size: "sm", id: "asr-copy-btn" }) +
          C.btn("🗑️ 清空", { tone: "warn", size: "sm", id: "asr-clear-btn" }) +
          "</div>"
      );

      return card2 + card3 + settings;
    },

    mount: function (root) {
      const talkBtn = A.qs("#talk-btn", root);
      const talkStatus = A.qs("#talk-status", root);
       const output = A.qs("#asr-output", root);
       const saveBtn = A.qs("#asr-save-btn", root);
       /* 最近一次模型原始输出；用户编辑后用它做 diff 学习 */
       let lastRaw = "";
       const modelStatus = A.qs("#asr-model-status", root);
       const loadBtn = A.qs("#asr-load", root);
       const modelSelect = A.qs('#asr-model', root);
       const modeStart = A.qs('#asr-mode-start', root);
       const modeStop = A.qs('#asr-mode-stop', root);
       const deviceSelect = A.qs('#asr-device', root);
       let voiceState = { phase: 'idle', busy: false };
       let modelItems = ((A.state.asr || {}).models || []).slice();
       let savingMode = false;

       /* 识别设置折叠：默认展开，点击标题可收起 */
       const settingsHead = A.qs('#asr-settings-head', root);
       const settingsBody = A.qs('#asr-settings-body', root);
       const settingsCard = settingsHead ? settingsHead.closest('.card') : null;
       function setSettingsOpen(open) {
         if (!settingsBody) return;
         settingsBody.classList.toggle('open', !!open);
         if (settingsHead) settingsHead.classList.toggle('open', !!open);
         if (settingsCard) settingsCard.classList.toggle('open', !!open);
       }
       if (settingsHead) {
         A.on(settingsHead, 'click', function () {
           setSettingsOpen(!settingsBody.classList.contains('open'));
         });
       }
       setSettingsOpen(true);

       function updateModels(res, scanned) {
         if (!res || !res.ok) {
           if (res && !res.cancelled) setTalkStatus(res.error || '操作失败', false);
           return;
         }
         modelItems = res.models;
         A.state.asr = Object.assign(A.state.asr || {}, {models: res.models, local_model: res.selected});
         modelSelect.innerHTML = res.models.length ? optionList(res.models.map(function (m) { return { value: m.id, label: m.name + (m.available ? '' : '（路径失效）') }; }), res.selected) : '<option value="">请导入本地模型目录</option>';
         showModelPath();
         if (modelStatus && !scanned) modelStatus.textContent = '等待加载';
       }
       function showModelPath() {
         const item = modelItems.find(function (m) { return m.id === modelSelect.value; });
         A.qs('#asr-model-path', root).textContent = item ? item.path : '把语音模型文件夹放进程序目录的 模型/语音/ 下，会自动识别';
       }
       A.bridge.on('model-folders-changed',function (msg) {
         if (modelSelect.isConnected) updateModels(msg.asr,true);
       });
       function saveOption(key, value, control) {
         savingMode = true;
         modeStart.disabled = true;
         const old = (A.state.asr || {})[key];
         A.bridge.call('set_config', {section:'asr',key:key,value:value}).then(function (res) {
           if (!res || !res.ok) {
             if (control) control.value = String(old);
             if (key === 'input_mode') {
               root.querySelectorAll('[name="asr-input-mode"]').forEach(function (radio) { radio.checked = radio.value === (old || 'manual'); });
               syncModeUI();
             }
             setTalkStatus(res && res.error || '保存失败', false);
             return;
           }
           A.state.asr[key] = value;
         }).catch(function (err) { setTalkStatus(err.message, false); }).finally(function () { savingMode = false; modeStart.disabled = !!voiceState.busy; });
       }
       function syncModeUI() {
         const checked = root.querySelector('[name="asr-input-mode"]:checked');
         const value = checked ? checked.value : 'manual';
         const item = INPUT_MODES.find(function (m) { return m.value === value; }) || INPUT_MODES[0];
         root.querySelectorAll('.mode-option').forEach(function (option) {
           const input = option.querySelector('input');
           option.classList.toggle('active', !!input && input.checked);
         });
         const desc = A.qs('#asr-mode-desc', root);
         if (desc) desc.textContent = item.text;
       }
       root.querySelectorAll('[name="asr-input-mode"]').forEach(function (radio) {
         A.on(radio, 'change', function () { if (radio.checked) { syncModeUI(); saveOption('input_mode', radio.value); } });
       });
       A.on(A.qs('#asr-segment-silence', root), 'change', function (e) { saveOption('segment_silence_ms', Number(e.target.value), e.target); });
       A.on(A.qs('#asr-turn-silence', root), 'change', function (e) { saveOption('turn_silence_ms', Number(e.target.value), e.target); });
       A.on(A.qs('#asr-idle-exit', root), 'change', function (e) { saveOption('idle_exit_seconds', Number(e.target.value), e.target); });

       /* ---- 麦克风设备：启动时由后端枚举，插拔后手动刷新 ---- */
       const deviceStatus = A.qs('#asr-device-status', root);
       function renderDevices(devices, selected) {
         if (!deviceSelect) return;
         const list = devices || [];
         const duplicated = function (name) {
           return list.filter(function (d) { return d.name === name; }).length > 1;
         };
         deviceSelect.innerHTML = '<option value="">跟随系统默认输入</option>' +
           list.map(function (d) {
             const label = d.name + (duplicated(d.name) && d.hostapi ? ' · ' + d.hostapi : '');
             return '<option value="' + A.esc(d.name) + '"' + (d.name === selected ? ' selected' : '') + '>' + A.esc(label) + '</option>';
           }).join('');
         deviceSelect.value = selected || '';
         if (deviceStatus) {
           deviceStatus.textContent = list.length ? '共 ' + list.length + ' 个输入设备' : '未检测到可用麦克风';
         }
         A.state.asr = Object.assign(A.state.asr || {}, { devices: list, device_name: selected || '' });
       }
       A.on(deviceSelect, 'change', function (e) {
         const value = e.target.value;
         const old = (A.state.asr || {}).device_name || '';
         deviceSelect.disabled = true;
         A.bridge.call('set_config', { section: 'asr', key: 'device_name', value: value }).then(function (res) {
           if (!res || !res.ok) {
             deviceSelect.value = old;
             setTalkStatus((res && res.error) || '麦克风切换失败', false);
             return;
           }
           A.state.asr.device_name = value;
           setTalkStatus(value ? '已切换到：' + value : '已改为跟随系统默认输入', false);
         }).catch(function (err) {
           deviceSelect.value = old;
           setTalkStatus(err.message, false);
         }).finally(function () { deviceSelect.disabled = !!voiceState.busy; });
       });
       A.on(A.qs('#asr-device-refresh', root), 'click', function () {
         if (deviceStatus) deviceStatus.textContent = '正在刷新…';
         A.bridge.call('asr_devices', {}).then(function (res) {
           if (!res || !res.ok) { if (deviceStatus) deviceStatus.textContent = '刷新失败'; return; }
           renderDevices(res.devices, res.selected);
           setTalkStatus('麦克风列表已刷新', false);
         }).catch(function (err) {
           if (deviceStatus) deviceStatus.textContent = '刷新失败：' + err.message;
         });
       });
       A.bridge.on('asr-devices', function (msg) { renderDevices(msg.devices, (A.state.asr || {}).device_name || ''); });
       A.on(modeStart, 'click', function () {
         if (savingMode) return;
         A.bridge.call('asr_start', {source:'float'}).then(function (res) { if (!res.ok) setTalkStatus(res.error || '当前任务进行中', false); });
       });
       A.on(modeStop, 'click', function () {
         A.bridge.call('asr_stop', {source:voiceState.source, session:voiceState.session}).catch(function (err) { setTalkStatus(err.message, false); });
       });

       function setTalkStatus(text, recording) {
         if (talkStatus) talkStatus.textContent = text;
         if (talkBtn) talkBtn.classList.toggle("recording", !!recording);
       }

      /* ---- 按住说话：按下开始、松开结束 ---- */
      let gesture = null;
      function release(g) {
        if (!g || g.stopped || g.session == null) return;
        g.stopped = true;
        A.bridge.call('asr_stop', { source: 'button', session: g.session }).catch(function (err) {
          if (!gesture) setTalkStatus('停止失败，可再次按住说话接管：' + err.message, false);
        });
      }
      function startRec(e) {
        if (e.button !== 0 || gesture) return;
        e.preventDefault();
        const g = { held: true, session: null, stopped: false };
        gesture = g;
        if (talkBtn.setPointerCapture) talkBtn.setPointerCapture(e.pointerId);
        setTalkStatus('正在启动录音；旧任务将被清理…', false);
        A.bridge.call('asr_start', { source: 'button' }).then(function (res) {
          if (!res || !res.ok) {
            if (gesture === g) gesture = null;
            setTalkStatus((res && res.error) || '麦克风未启动，可重试', false);
            return;
          }
          g.session = res.session;
          if (!g.held || !talkBtn.isConnected) release(g);
        }).catch(function (err) {
          if (gesture === g) gesture = null;
          setTalkStatus('启动失败，可再次按住说话：' + err.message, false);
        });
      }
      function stopRec() {
        const g = gesture;
        if (!g) return;
        gesture = null;
        g.held = false;
        release(g);
      }
      talkBtn.style.touchAction = 'none';
      A.on(talkBtn, 'pointerdown', startRec);
      A.on(talkBtn, 'pointerup', stopRec);
      A.on(talkBtn, 'pointercancel', stopRec);
      A.on(talkBtn, 'lostpointercapture', stopRec);
      A.on(window, 'blur', stopRec);
      A.on(document, 'visibilitychange', function () { if (document.hidden) stopRec(); });

      /* ---- 配置变更 ---- */
       A.on(A.qs("#asr-model", root), "change", function (e) {
         const selected = e.target.value;
         A.bridge.call("set_config", { section: "asr", key: "local_model", value: selected }).then(function (res) {
           if (!res.ok) { modelSelect.value = A.state.asr.local_model; setTalkStatus(res.error, false); return; }
           A.state.asr.local_model = selected;
           showModelPath();
           if (modelStatus) modelStatus.textContent = '已切换，等待加载';
         });
       });

       A.on(loadBtn, "click", function () {
         if (loadBtn) loadBtn.disabled = true;
         if (modelStatus) modelStatus.textContent = "正在加载…";
         A.bridge.call("asr_load_model", {}).then(function (res) {
           if (loadBtn) loadBtn.disabled = false;
           if (modelStatus) modelStatus.textContent = (res && res.status) || (res && res.error) || "加载完成";
         }).catch(function (e) {
           if (loadBtn) loadBtn.disabled = false;
           if (modelStatus) modelStatus.textContent = "加载失败：" + String(e && e.message || e);
         });
       });

       A.on(A.qs("#asr-copy", root), "switchchange", function (e) {
         A.bridge.call("set_config", { section: "asr", key: "copy_to_clipboard", value: e.detail.on });
       });
       A.on(A.qs('#asr-auto-input', root), 'switchchange', function (e) {
         A.bridge.call('set_config', {section:'asr',key:'auto_input',value:e.detail.on});
       });
       A.bridge.on('asr-delivery', function (msg) {
         const el = A.qs('#asr-delivery-status', root);
         if (!el || !el.isConnected) return;
         el.textContent = msg.text || '';
         el.style.color = msg.ok ? '#63c9b1' : '#efb46b';
       });

       A.on(A.qs("#asr-floating", root), "switchchange", function (e) {
         A.bridge.call("set_config", { section: "asr", key: "floating_window", value: e.detail.on });
       });

      /* ---- 结果操作 ---- */
      A.on(A.qs("#asr-copy-btn", root), "click", function () {
        if (!output || !output.value) return;
        /* QWebEngine 里 navigator.clipboard 常被权限拦掉，统一走 Python 剪贴板 */
        A.bridge.call("copy_text", { text: output.value }).then(function (res) {
          if (!res || !res.ok) {
            output.select();
            try {
              document.execCommand("copy");
            } catch (err) {
              /* ignore */
            }
          }
          setTalkStatus("已复制识别结果");
        });
      });

      A.on(A.qs("#asr-clear-btn", root), "click", function () {
        if (output) output.value = "";
      });

      /* ---- 后端推送：录音状态 / 识别结果 ---- */
       function applyVoiceState(msg) {
         if (!talkBtn.isConnected) return;
         voiceState = msg;
          if (msg.model_status && modelStatus) modelStatus.textContent = msg.model_status;
         talkBtn.disabled = false;
         if (loadBtn) loadBtn.disabled = !!msg.busy;
         modelSelect.disabled = !!msg.busy;
         modeStart.disabled = !!msg.busy || savingMode;
         modeStop.disabled = !['starting', 'recording'].includes(msg.phase);
         root.querySelectorAll('[name="asr-input-mode"], #asr-segment-silence, #asr-turn-silence').forEach(function (el) { el.disabled = !!msg.busy; });
         if (deviceSelect) deviceSelect.disabled = !!msg.busy;
         root.querySelectorAll('.mode-option').forEach(function (option) { option.classList.toggle('disabled', !!msg.busy); });
         setTalkStatus(msg.source === 'float' && msg.busy ? '悬浮窗任务进行中 · 按住此按钮可接管' : (msg.text || '就绪 · 松开自动识别'), msg.recording && msg.source === 'button');
       }
       A.bridge.on('asr-state', applyVoiceState);
       A.bridge.call('asr_state', {}).then(applyVoiceState).catch(function () {});

      /* 旧 UI 是追加（voice_input.py: insert(tk.END, text + "\n")），
         连续按住说话会累积成一段历史，这里保持一致，不要覆盖。 */
      A.bridge.on("asr-result", function (msg) {
        if (!output || msg.text == null) return;
        output.value = output.value ? output.value + "\n" + msg.text : msg.text;
        output.scrollTop = output.scrollHeight;
        /* 记住模型原始输出，供「保存修改」比对学习。
           只用最近一次结果，避免多段累积后 diff 失准。 */
        lastRaw = msg.text;
        if (!voiceState.busy) setTalkStatus('识别完成', false);
      });

      /* 保存修改：把「识别原文 → 用户最终文本」交给后端提取改动，
         只进候选池，不直接进正式词表（需在词库页确认）。 */
      if (saveBtn) {
        A.on(saveBtn, 'click', function () {
          if (!output || !lastRaw) { setTalkStatus('还没有可学习的识别结果', false); return; }
          const final = output.value.trim();
          if (!final || final === lastRaw) { setTalkStatus('内容未修改，无需学习', false); return; }
          A.bridge.call('hotwords_learn', { raw: lastRaw, final: final })
            .then(function (res) {
              if (!res || !res.ok) { setTalkStatus((res && res.error) || '学习失败', false); return; }
              const n = (res.pairs || []).length;
              if (n) {
                setTalkStatus('已提取 ' + n + ' 处改动，去「词库」页确认后生效', false);
                if (A.toast) A.toast('个人词库候选 +' + n);
              } else {
                setTalkStatus('没有可学习的改动（可能只是补标点或改措辞）', false);
              }
              lastRaw = final;
            })
            .catch(function (e) { setTalkStatus('学习失败：' + e, false); });
        });
      }

      setTalkStatus("就绪 · 松开自动识别", false);
      syncModeUI();
    },
  };
})();
