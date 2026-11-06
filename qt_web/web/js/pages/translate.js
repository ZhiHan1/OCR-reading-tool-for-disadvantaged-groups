/* ============================================================
   翻译页
   手动输入 → 翻译 → 朗读译文。语言选择与后端 LANGUAGES 对应。
   ============================================================ */
(function () {
  "use strict";

  const A = window.APP;
  const C = A.components;

  /* 显示名 → 语言代码（与后端 core/language_utils.py 的 LANG_NAMES 一致） */
  const CODES = {
    自动检测: "auto",
    英语: "en",
    中文: "zh",
    日语: "ja",
    韩语: "ko",
    法语: "fr",
    德语: "de",
    西班牙语: "es",
    俄语: "ru",
  };

  function nameOf(code) {
    const hit = Object.keys(CODES).filter(function (k) {
      return CODES[k] === code;
    });
    return hit.length ? hit[0] : "自动检测";
  }

  A.pages.translate = {
    title: "翻译",
    desc: "识别或输入文字，翻译为指定语言",
    icon: "🌐",
    group: "处理",

    render: function () {
      const t = A.state.translation || {};
      const src = nameOf(t.source_lang || "auto");
      const dst = nameOf(t.target_lang || "zh");

      const langs =
        '<div class="field" style="grid-template-columns:100px 1fr 100px 1fr;padding-top:14px">' +
        '<span class="field-label">源语言</span>' +
        '<select class="select" id="tr-source">' + C.langOptions(src) + "</select>" +
        '<span class="field-label">目标语言</span>' +
        '<select class="select" id="tr-target">' + C.langOptions(dst) + "</select>" +
        "</div>";

      const row =
        '<div class="row mt-4">' +
        C.btn("🔄 翻译", { tone: "primary", id: "tr-go" }) +
        C.btn("🔊 朗读译文", { tone: "success", id: "tr-speak" }) +
        C.btn("🗑️ 清空", { tone: "warn", id: "tr-clear" }) +
        '<span class="spacer" style="flex:1"></span>' +
        '<span class="hint-inline" id="tr-status" style="align-self:center">就绪</span>' +
        "</div>";

      return (
        C.card(
          "原文",
          "输入要翻译的文字",
          '<textarea class="ta" id="tr-input" style="height:120px" placeholder="在此输入或粘贴要翻译的内容…"></textarea>' +
            langs +
            row
        ) +
        C.card(
          "译文",
          "翻译结果（只读）",
          '<textarea class="ta" id="tr-output" readonly style="height:120px" placeholder="翻译结果将显示在这里…"></textarea>',
          { cls: "enter-up" }
        )
      );
    },

    mount: function (root) {
      const input = A.qs("#tr-input", root);
      const output = A.qs("#tr-output", root);
      const status = A.qs("#tr-status", root);

      function setStatus(text, kind) {
        if (!status) return;
        status.textContent = text;
        status.style.color =
          kind === "err" ? "var(--ink-rose)" : kind === "ok" ? "var(--ink-green)" : "";
      }

      /* 注意：旧 UI 的翻译页没有「从结果区带入原文」这个功能，
         原文只能手动输入/粘贴。这里不再保留无人写入的 pendingTranslateText。 */

      A.on(A.qs("#tr-go", root), "click", function () {
        const text = (input.value || "").trim();
        if (!text) {
          setStatus("请先输入要翻译的文字", "err");
          return;
        }
        setStatus("翻译中…");
        A.bridge
          .call("translate", {
            text: text,
            source: CODES[A.qs("#tr-source", root).value] || "auto",
            target: CODES[A.qs("#tr-target", root).value] || "zh",
          })
          .then(function (res) {
            if (!res || !res.ok) {
              setStatus((res && res.error) || "翻译失败", "err");
              return;
            }
            /* 后端把请求交给翻译工作线程，结果通过 translate-result 事件推回 */
            if (res.pending) return;
            if (output) output.value = res.text || "";
            setStatus("翻译完成", "ok");
          })
          .catch(function (e) {
            setStatus(String(e && e.message ? e.message : e), "err");
          });
      });

      /* 手动翻译结果（后端主线程轮询结果队列后推送） */
      A.bridge.on("translate-result", function (msg) {
        if (!msg) return;
        if (msg.ok && msg.text) {
          if (output) output.value = msg.text;
          setStatus("翻译完成", "ok");
        } else {
          setStatus(msg.error || "翻译失败：没有返回结果", "err");
        }
      });

      A.on(A.qs("#tr-speak", root), "click", function () {
        const text = (output.value || "").trim();
        if (!text) {
          setStatus("还没有译文可朗读", "err");
          return;
        }
        A.bridge.call("speak", { text: text });
        setStatus("正在朗读译文", "ok");
      });

      A.on(A.qs("#tr-clear", root), "click", function () {
        if (input) input.value = "";
        if (output) output.value = "";
        setStatus("就绪");
      });
    },
  };
})();
