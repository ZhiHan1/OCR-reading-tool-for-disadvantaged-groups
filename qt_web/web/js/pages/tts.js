/* ============================================================
   文字转语音页
   输入文字 → 选语音参数 → 试听 / 导出 MP3
   语音列表与朗读功能共用同一套后端（tts 服务）。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  const SRC_EDGE = A.TEXT.sourceEdge;
  const SRC_SAPI = A.TEXT.sourceSapi;

  function sourceOf(label) {
    return String(label || "").indexOf("Edge") >= 0 ? "edge" : "sapi";
  }

  function voiceOptions(source, selected) {
    const list = (A.state.voices && A.state.voices[source]) || [];
    if (!list.length) return '<option value="">（暂无可用语音）</option>';
    return list
      .map(function (v) {
        const value = typeof v === "string" ? v : v.value || v.name || "";
        const text = typeof v === "string" ? v : v.display || v.value || v.name || "";
        return '<option value="' + A.esc(value) + '"' + (value === selected ? " selected" : "") + ">" + A.esc(text) + "</option>";
      })
      .join("");
  }

  A.pages.tts = {
    title: "文字转语音",
    desc: "输入文字，试听或导出为 MP3",
    icon: "🔊",
    group: "处理",

    render: function () {
      const t = A.state.tts || {};
      const srcLabel = (t.backend || "sapi") === "edge" ? SRC_EDGE : SRC_SAPI;
      const curVoice = (t.backend || "sapi") === "edge" ? t.edge_voice : t.voice_name;

      const inputCard = C.card(
        "输入文字",
        "要合成语音的文字内容",
        '<textarea class="ta" id="tts-text" style="height:120px" placeholder="在此输入要朗读的文字…"></textarea>'
      );

      const paramCard = C.card(
        "🎙️ 语音参数",
        "本页设置仅对本页生效，不影响朗读功能",
        C.field(
          "语音来源",
          '<select class="select" id="tts-source" style="flex:1">' +
            "<option" + (srcLabel === SRC_EDGE ? " selected" : "") + ">" + A.esc(SRC_EDGE) + "</option>" +
            "<option" + (srcLabel === SRC_SAPI ? " selected" : "") + ">" + A.esc(SRC_SAPI) + "</option>" +
            "</select>"
        ) +
          C.field("语音", '<select class="select" id="tts-voice" style="flex:1">' + voiceOptions(sourceOf(srcLabel), curVoice) + "</select>") +
          '<div class="subsec-hint" id="tts-source-hint" style="margin:-4px 0 6px 162px">' +
          (sourceOf(srcLabel) === "edge"
            ? "需要联网，首次合成稍有延迟；失败会自动回退到本机语音"
            : "使用系统已安装的语音，无需联网") +
          "</div>" +
          C.sliderRow("语速", -10, 10, t.rate != null ? t.rate : 0, String(t.rate != null ? t.rate : 0), { id: "tts-rate" }) +
          C.sliderRow("语调", -10, 10, t.pitch != null ? t.pitch : 0, String(t.pitch != null ? t.pitch : 0), { id: "tts-pitch" }),
        { cls: "enter-up" }
      );

      const playCard = C.card(
        "试听与导出",
        "试听与导出共用同一份合成结果",
        '<div class="progress-wrap mt-3">' +
          '<div class="progress"><i id="tts-bar"></i></div>' +
          '<span class="progress-time" id="tts-time">0:00 / 0:00</span>' +
          "</div>" +
          '<div class="row mt-4">' +
          C.btn("▶ 试听", { tone: "primary", id: "tts-play" }) +
          C.btn("⏹ 停止", { tone: "danger", id: "tts-stop" }) +
          C.btn("💾 导出 MP3", { tone: "warn", id: "tts-export" }) +
          '<span class="spacer" style="flex:1"></span>' +
          '<span class="hint-inline" id="tts-status" style="align-self:center">就绪</span>' +
          "</div>" +
          '<div class="divider"></div>' +
          C.field(
            "导出文件夹",
            '<input class="select" id="tts-dir" style="flex:1;min-width:0" value="' +
              A.esc(t.export_dir || "") +
              '" placeholder="默认保存到文档目录">' +
              C.btn("选择", { tone: "ghost", size: "sm", id: "tts-dir-pick" }) +
              C.btn("打开", { tone: "ghost", size: "sm", id: "tts-dir-open" }),
            { style: "padding-top:0" }
          ) +
          C.field(
            "文件名",
            '<input class="select" id="tts-filename" style="flex:1;min-width:0" value="' +
              A.esc(t.filename_pattern || "") +
              '" placeholder="留空则自动命名">',
            { labelSmall: "支持 %Y%m%d 等占位符" }
          )
      );

      return inputCard + paramCard + playCard;
    },

    mount: function (root) {
      const status = A.qs("#tts-status", root);
      const sourceSel = A.qs("#tts-source", root);
      const voiceSel = A.qs("#tts-voice", root);
      const hint = A.qs("#tts-source-hint", root);

      function setStatus(text, kind) {
        if (!status) return;
        status.textContent = text;
        status.style.color = kind === "err" ? "var(--ink-rose)" : kind === "ok" ? "var(--ink-green)" : "";
      }

      /* 进度条 + 右侧文字（试听/导出共用） */
      function setBar(ratio, text) {
        const bar = A.qs("#tts-bar", root);
        const time = A.qs("#tts-time", root);
        const pct = Math.max(0, Math.min(1, Number(ratio) || 0));
        if (bar) bar.style.width = Math.round(pct * 100) + "%";
        if (time && text != null) time.textContent = text;
      }

      /* 语音来源切换 → 重建语音列表（两个来源是完全不同的两套列表） */
      A.on(sourceSel, "change", function () {
        const src = sourceOf(sourceSel.value);
        voiceSel.innerHTML = voiceOptions(src, null);
        if (hint) {
          hint.textContent =
            src === "edge"
              ? "需要联网，首次合成稍有延迟；失败会自动回退到本机语音"
              : "使用系统已安装的语音，无需联网";
        }
      });

      function payload() {
        const text = (A.qs("#tts-text", root).value || "").trim();
        return {
          text: text,
          source: sourceOf(sourceSel.value),
          voice: voiceSel.value,
          rate: Number(A.qs("#tts-rate", root).value),
          pitch: Number(A.qs("#tts-pitch", root).value),
        };
      }

      A.on(A.qs("#tts-play", root), "click", function () {
        const p = payload();
        if (!p.text) {
          setStatus("请先输入要朗读的文字", "err");
          return;
        }
        setStatus("合成中…");
        A.bridge.call("tts_preview", p).then(function (res) {
          if (res && res.ok) setStatus("正在试听", "ok");
          else setStatus((res && res.error) || "合成失败", "err");
        });
      });

      A.on(A.qs("#tts-stop", root), "click", function () {
        A.bridge.call("stop_speak", {});
        setStatus("已停止");
      });

      A.on(A.qs("#tts-export", root), "click", function () {
        const p = payload();
        if (!p.text) {
          setStatus("请先输入要朗读的文字", "err");
          return;
        }
        p.dir = A.qs("#tts-dir", root).value.trim();
        p.filename = A.qs("#tts-filename", root).value.trim();

        const btn = A.qs("#tts-export", root);
        btn.disabled = true;
        setStatus("导出中…");
        setBar(0.02, "准备中…");

        A.bridge.call("tts_export", p).then(function (res) {
          if (!res || !res.ok) {
            btn.disabled = false;
            setBar(0, "0:00 / 0:00");
            setStatus((res && res.error) || "导出失败", "err");
          }
          /* 成功时等 tts-export-done 事件回来再收尾 */
        }).catch(function (e) {
          btn.disabled = false;
          setBar(0, "0:00 / 0:00");
          setStatus(String(e && e.message ? e.message : e), "err");
        });
      });

      A.on(A.qs("#tts-dir-pick", root), "click", function () {
        A.bridge.call("pick_folder", {}).then(function (res) {
          if (res && res.ok && res.path) A.qs("#tts-dir", root).value = res.path;
        });
      });

      A.on(A.qs("#tts-dir-open", root), "click", function () {
        A.bridge.call("open_folder", { path: A.qs("#tts-dir", root).value.trim() });
      });

      /* 播放/导出进度由后端推送 */
      A.bridge.on("tts-progress", function (msg) {
        if (msg && msg.ratio != null) setBar(msg.ratio, msg.text);
      });

      /* 导出完成（后台线程合成结束后推送） */
      A.bridge.on("tts-export-done", function (msg) {
        const btn = A.qs("#tts-export", root);
        if (btn) btn.disabled = false;

        if (msg && msg.ok) {
          setBar(1, "导出完成");
          const kb = msg.size ? "（" + Math.round(msg.size / 1024) + " KB）" : "";
          setStatus("✅ 已导出" + kb + "：" + (msg.path || ""), "ok");
          /* 顺手把导出目录填上，方便下次直接用 */
          const dirEl = A.qs("#tts-dir", root);
          if (dirEl && msg.path) {
            const idx = msg.path.lastIndexOf("\\") >= 0 ? msg.path.lastIndexOf("\\") : msg.path.lastIndexOf("/");
            if (idx > 0) dirEl.value = msg.path.slice(0, idx);
          }
          A.toast("✅ 已导出 MP3：" + (msg.path || ""), true);
        } else {
          setBar(0, "0:00 / 0:00");
          setStatus((msg && msg.error) || "导出失败", "err");
          A.toast("❌ " + ((msg && msg.error) || "导出失败"), false);
        }
      });
    },
  };
})();
