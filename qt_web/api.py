"""WebEngine 前端 ↔ Python 后端桥接。

设计：
- JS 侧只有一个入口 ``api.request(action, payloadJson, callback)``，全部动作
  走 JSON 字符串，返回也是 JSON 字符串，避免 QWebChannel 参数类型摩擦。
- 后端 → 前端推送统一走 ``event`` Qt 信号（payload 为 JSON 字符串），
  QWebChannel 会自动把它暴露成 JS 里的 ``api.event.connect(...)``。
- 后端（core / controllers / services）一行不改，这里只做「翻译官」。

动作清单见 WebApi._dispatch。
"""
import json
import os
import threading
import copy
import yaml
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from utils.paths import model_drop_root

from core.config_manager import load_defaults, validate_config
from core.model_manager import ModelManager
from qt_web.hotkeys import normalize_hotkey

# 页面模式 -> AppController 属性名
SCAN_ATTRS = {"func3": "func3", "manga": "manga_mode", "game": "game_mode"}
# 功能2 也是「有区域 / 可清除」的控制器，一起走 _scan_controller() 取用
MODE_ATTRS = {"func2": "func2", **SCAN_ATTRS}
MODES = ("func1", "func2", "func3", "manga", "game")

# ModelManager 实例缓存（与旧 UI 一致：全局单例，避免重复加载 models_config.json）
_MODEL_MANAGER: Optional["ModelManager"] = None

# 当前 MainWindow 的 AppController，供 WebApi._mode_flags 之类的静态工具读取
_CURRENT_APP = None


class WebApi(QObject):
    """暴露给 JS 的唯一对象：channel.objects.api"""

    # 后端推送（JSON 字符串：{"type": "...", ...}）
    event = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.app = win.app
        self.config = win.config
        self.translation_manager = win.translation_manager
        self.bridge = win.bridge
        self.router = win.router
        self._asr_loading = False
        self._model_scan_snapshot = None
        # 模型目录轮询：语音页/设置页才需要「目录一有变化就刷新」，
        # 其它页面只是空转（每轮 registry.scan + ModelManager.scan）。
        # 页面不可见时降到 30 秒一次；切回语音/设置页时由 NavBridge 触发立即扫描。
        self._model_scan_timer = QTimer(self)
        self._model_scan_timer.setInterval(3000)
        self._model_scan_timer.timeout.connect(self._scan_model_folders)
        self._model_scan_timer.start()
        QTimer.singleShot(0, self._scan_model_folders)
        # 麦克风列表在启动时枚举一次并缓存：之后的下拉框都读这份快照，
        # 不会因为谁调用 _build_state 而反复打开音频子系统。
        self._input_devices = []
        self._refresh_input_devices()

    def _refresh_input_devices(self):
        try:
            from core.audio_capture import input_devices

            self._input_devices = input_devices()
        except Exception as exc:  # noqa: BLE001
            print(f'[麦克风] 设备列表加载失败: {exc}')
            self._input_devices = []
        return self._input_devices

    def _set_model_scan_active(self, active: bool):
        """页面切换时调节模型目录轮询频率（3000ms 快 / 30000ms 慢）。"""
        interval = 3000 if active else 30000
        if self._model_scan_timer.interval() != interval:
            self._model_scan_timer.setInterval(interval)
        if active:
            self._scan_model_folders()

    def _scan_model_folders(self):
        try:
            self.win.voice.registry.scan()
            self._model_manager().scan()
            payload = {'type':'model-folders-changed', 'asr':self.do_asr_models({}), 'models':self._models_state()}
            signature = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if signature != self._model_scan_snapshot:
                self._model_scan_snapshot = signature
                self._push(payload)
            return {'ok':True}
        except Exception as exc:
            print(f'[模型扫描] {exc}')
            return {'ok':False,'error':str(exc)}

    def do_scan_model_folders(self, p):
        return self._scan_model_folders()

    def do_open_model_folder(self, p):
        kind = str(p.get('kind') or 'voice')
        if kind not in ('voice','translation'): return {'ok':False,'error':'未知模型类型'}
        path = model_drop_root(kind)
        path.mkdir(parents=True,exist_ok=True)
        return self.do_open_folder({'path':str(path)})

    # ================= 对 JS 的唯一入口 =================

    @Slot(str, str, result=str)
    def request(self, action: str, payload_json: str) -> str:
        """JS: api.request(action, payloadJson, cb) -> cb(resultJson)"""
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except Exception:
            payload = {}
        try:
            result = self._dispatch(action, payload)
            if result is None:
                result = {"ok": True}
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            result = {"ok": False, "error": str(e)}
        return json.dumps(result, ensure_ascii=False, default=str)

    # ================= 动作分发 =================

    def _dispatch(self, action: str, p: Dict[str, Any]) -> Optional[dict]:
        handler = getattr(self, "do_" + action, None)
        if handler is None:
            return {"ok": False, "error": f"未知动作: {action}"}
        return handler(p)

    # ---- 状态与主题 ----

    def do_state(self, p):
        return {"ok": True, "state": self._build_state()}

    def do_set_theme(self, p):
        theme = "light" if p.get("theme") == "light" else "dark"
        self.config.set("ui", "web_theme", value=theme)
        self.config.save()
        self._push_theme()
        return {"ok": True}

    def do_set_frost(self, p):
        on = bool(p.get("frost"))
        self.config.set("ui", "web_frost", value=bool(on))
        self.config.save()
        self._push_theme()
        return {"ok": True}

    def _push_theme(self):
        """推送**完整**主题状态。

        前端是增量应用（有哪个字段改哪个），但推完整状态可以避免
        「只推 frost 时前端误把 theme 重置成默认值」这类问题。
        """
        c = self.config
        self._push({
            "type": "theme",
            "theme": c.get("ui", "web_theme", default="dark"),
            "frost": bool(c.get("ui", "web_frost", default=True)),
        })

    def do_clear_result(self, p):
        return {"ok": True}

    def do_stop_speak(self, p):
        self.app.tts_service.stop_current()
        return {"ok": True}

    # ---- 首页 OCR 功能 ----

    def do_trigger_func1(self, p):
        if not self.win.enabled("func1"):
            return {"ok": False, "error": "功能1已禁用"}
        self.app.stop_scan_modes()
        threading.Thread(
            target=lambda: self.app.func1.execute(
                interrupt_mode=not self.win.queue_mode("func1")
            ),
            daemon=True,
            name="WebFunc1",
        ).start()
        return {"ok": True}

    def do_func2_select(self, p):
        if not self.win.enabled("func2"):
            return {"ok": False, "error": "功能2已禁用"}
        self._scan_controller("func2").select_region()
        # 框选完必须把区域状态推回前端，否则卡片徽章停在「未选区域」、
        # 「清除」按钮一直是灰的（点了没反应）。
        self._push_regions()
        return {"ok": True}

    def do_func2_read(self, p):
        if not self.win.enabled("func2"):
            return {"ok": False, "error": "功能2已禁用"}
        threading.Thread(
            target=lambda: self._scan_controller("func2").read_region(
                interrupt_mode=not self.win.queue_mode("func2")
            ),
            daemon=True,
            name="WebFunc2Read",
        ).start()
        return {"ok": True}

    def do_func2_clear(self, p):
        self.app.tts_service.stop_current()
        self._scan_controller("func2").clear_region()
        self._push_regions()
        return {"ok": True}

    def do_toggle_scan(self, p):
        mode = p.get("mode", "")
        controller = self._scan_controller(mode)
        if controller is None:
            return {"ok": False, "error": f"未知模式: {mode}"}
        if controller.is_running:
            controller.stop()
        else:
            self.app.stop_scan_modes(except_mode=SCAN_ATTRS[mode])
            controller.start()
        # start() 在没有区域时会先让用户框选，框选结果同样要推回前端
        self._push_regions()
        self._push_running()
        return {"ok": True}

    def do_reselect_scan(self, p):
        controller = self._scan_controller(p.get("mode", ""))
        if controller is None:
            return {"ok": False, "error": "未知模式"}
        controller.reselect_region()
        # 扫描类控制器没有 on_region_changed 回调（只在 func2 上接了），
        # 这里手动补一次推送，否则重选成功后前端仍认为「未选区域」。
        self._push_regions()
        self._push_running()
        return {"ok": True}

    def do_clear_scan(self, p):
        mode = p.get("mode", "")
        controller = self._scan_controller(mode)
        if controller is None:
            return {"ok": False, "error": "未知模式"}
        self.app.tts_service.stop_current()
        controller.clear_region()
        self._push_regions()
        return {"ok": True}

    # ---- 翻译页 ----

    def do_translate(self, p):
        text = str(p.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "没有要翻译的内容"}

        # 与旧 UI 的「翻译」按钮（_on_translate_click）完全一致：
        # 请求交给翻译工作线程（enqueue_manual），结果经结果队列由主线程轮询，
        # 再以 translate-result 事件推回前端。
        #
        # 不能调 TranslationController.translate()：它没有返回值，结果只通过
        # on_result 回调送出，直接取返回值永远是空 —— 这正是翻译页「点翻译
        # 显示翻译完成但译文空白」的原因。
        source = str(p.get("source") or self.config.get("translation", "source_lang", default="auto"))
        target = str(p.get("target") or self.config.get("translation", "target_lang", default="zh"))
        try:
            self.translation_manager.enqueue_manual(text, source, target)
        except Exception as e:  # noqa: BLE001
            print(f"[Web翻译] 提交翻译请求失败: {e}")
            return {"ok": False, "error": f"提交翻译请求失败: {e}"}
        return {"ok": True, "pending": True}

    def do_speak(self, p):
        text = str(p.get("text", "")).strip()
        if text:
            # 旧 UI 的「朗读译文」「试听语音」都是打断模式（interrupt=True），
            # 用默认的排队模式会出现「点了没反应，等前面读完才响」。
            self.app.tts_service.speak(text, interrupt=True)
        return {"ok": True}

    def do_copy_text(self, p):
        """把文本写入系统剪贴板。

        QWebEngine 里 navigator.clipboard 常被权限拦掉，前端复制一律走这里。
        """
        text = str(p.get("text") or "")
        if not text:
            return {"ok": False, "error": "没有要复制的内容"}
        try:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(text)
        except Exception as e:  # noqa: BLE001
            print(f"[剪贴板] 复制失败: {e}")
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    # ---- TTS 页 ----

    def do_tts_preview(self, p):
        text = str(p.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "请先输入要朗读的文字"}
        self._apply_tts_params(p)
        self.app.tts_service.speak(text)
        return {"ok": True}

    def do_tts_export(self, p):
        """导出 MP3：合成 + 编码耗时，放到后台线程，结果用事件推送回前端。"""
        text = str(p.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "请先输入要导出的文字"}

        # 同一时间只允许一个导出任务
        if getattr(self, "_export_thread", None) is not None and self._export_thread.is_alive():
            return {"ok": False, "error": "已有导出任务正在进行"}

        params = {
            "text": text,
            "backend": str(p.get("source") or "sapi"),
            "voice": str(p.get("voice") or ""),
            "rate": p.get("rate", 0),
            "pitch": p.get("pitch", 0),
            "volume": self._page_volume(),
            "out_dir": str(p.get("dir") or ""),
            "filename": str(p.get("filename") or ""),
        }

        self._export_thread = threading.Thread(
            target=self._tts_export_worker,
            args=(params,),
            daemon=True,
            name="WebTTSExport",
        )
        self._export_thread.start()
        return {"ok": True, "pending": True}

    def do_pick_folder(self, p):
        from PySide6.QtWidgets import QFileDialog
        from utils.paths import models_root

        path = QFileDialog.getExistingDirectory(
            self.win, str(p.get('title') or '选择导出文件夹'), str(p.get('directory') or models_root())
        )
        self.win.activateWindow()
        self.win.view.setFocus()
        return {"ok": True, "path": path} if path else {"ok": False, "cancelled": True, "error": "未选择"}

    def do_open_folder(self, p):
        path = str(p.get("path") or "").strip()
        if path and os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        return {"ok": True}

    def do_pick_file(self, p):
        from PySide6.QtWidgets import QFileDialog

        filt = str(p.get("filter") or "所有文件 (*.*)")
        path, _ = QFileDialog.getOpenFileName(self.win, "选择文件", "", filt)
        # 原生模态对话框关闭后，QWebEngineView 不会自动拿回键盘焦点，
        # 表现为「弹窗里的输入框点着却打不进字」。这里主动把焦点还给网页。
        try:
            self.win.activateWindow()
            self.win.view.setFocus()
        except Exception as e:  # noqa: BLE001
            print(f"[Web设置] 恢复网页焦点失败: {e}")
        return {"ok": True, "path": path} if path else {"ok": False, "error": "未选择"}

    # ---- 语音输入 ----

    def do_asr_start(self, p):
        source = str(p.get("source") or "button")
        return self.win.voice.start(source)

    def do_asr_stop(self, p):
        return self.win.voice.stop(str(p.get('source') or 'button'), p.get('session'))

    def do_asr_state(self, p):
        return self.win.voice.snapshot()

    def do_asr_load_model(self, p):
        return self.win.voice.load_model()

    def do_asr_models(self, p):
        return {'ok': True, 'models': self.win.voice.registry.list(),
                'selected': self.config.get('asr', 'local_model', default='paraformer_large')}

    def do_asr_devices(self, p):
        """刷新麦克风列表。只在用户打开下拉/换设备时调用，枚举本身不走定时器。"""
        devices = self._refresh_input_devices()
        self._push({'type': 'asr-devices', 'devices': devices})
        return {'ok': True, 'devices': devices,
                'selected': str(self.config.get('asr', 'device_name', default='') or '')}

    # ---- 个人词库（热词）----

    def _hotword_payload(self, extra=None):
        """热词相关动作的统一返回结构。"""
        from core import hotword_learning

        words = list(self.config.get('asr', 'hotwords', default=None) or [])
        data = {
            'ok': True,
            'hotwords': [str(w) for w in words],
            'candidates': hotword_learning.list_candidates(),
            'threshold': float(self.config.get('asr', 'hotword_threshold', default=0.8) or 0.8),
            'fuzzy': bool(self.config.get('asr', 'hotword_fuzzy', default=True)),
        }
        if extra:
            data.update(extra)
        return data

    def do_hotwords(self, p):
        """读取正式词表 + 候选池。"""
        return self._hotword_payload()

    def do_hotwords_add(self, p):
        """批量导入词表（手动粘贴/文件内容）。"""
        text = str(p.get('text') or '')
        if not text.strip():
            return {'ok': False, 'error': '内容为空'}
        from core import hotword_learning

        count, added = hotword_learning.merge_into_hotwords(text)
        self.config.save()
        from core.hotwords import reload_hotwords

        reload_hotwords()
        return self._hotword_payload({'added': count, 'entries': added,
                                      'message': f'已新增 {count} 条' if count else '没有新增条目'})

    def do_hotwords_save(self, p):
        """整体保存词表（界面里编辑后保存）。"""
        words = p.get('hotwords')
        if not isinstance(words, list):
            return {'ok': False, 'error': '词表格式不正确'}
        cleaned = []
        seen = set()
        for w in words:
            w = str(w).strip()
            if not w or w in seen:
                continue
            seen.add(w)
            cleaned.append(w)
        self.config.set('asr', 'hotwords', value=cleaned)
        self.config.save()
        from core.hotwords import reload_hotwords

        reload_hotwords()
        return self._hotword_payload({'message': f'已保存 {len(cleaned)} 条'})

    def do_hotwords_accept(self, p):
        """确认候选：并入正式词表后从候选池移除。"""
        rights = p.get('rights') or []
        if isinstance(rights, str):
            rights = [rights]
        rights = [str(r).strip() for r in rights if str(r).strip()]
        if not rights:
            return {'ok': False, 'error': '未选择候选'}
        from core import hotword_learning

        current = [str(w) for w in (self.config.get('asr', 'hotwords', default=None) or [])]
        known = set(current)
        candidates = {str(c.get('right', '')): c for c in hotword_learning.list_candidates()}
        added = []
        for right in rights:
            cand = candidates.get(right)
            if cand is None:
                continue
            wrong = str(cand.get('wrong') or '').strip()
            entry = f'{wrong}->{right}' if wrong else right
            if entry in known:
                continue
            known.add(entry)
            current.append(entry)
            added.append(entry)
        if added:
            self.config.set('asr', 'hotwords', value=current)
            self.config.save()
            from core.hotwords import reload_hotwords

            reload_hotwords()
        hotword_learning.dismiss_candidates(rights)
        return self._hotword_payload({'accepted': len(added),
                                      'message': f'已并入 {len(added)} 条到正式词表'})

    def do_hotwords_dismiss(self, p):
        """拒绝候选：移出候选池并记录，之后不再出现。"""
        rights = p.get('rights') or []
        if isinstance(rights, str):
            rights = [rights]
        from core import hotword_learning

        n = hotword_learning.dismiss_candidates(rights)
        return self._hotword_payload({'message': f'已忽略 {n} 条'})

    def do_hotwords_clear_candidates(self, p):
        from core import hotword_learning

        n = hotword_learning.clear_candidates()
        return self._hotword_payload({'message': f'已清空 {n} 条候选'})

    def do_hotwords_learn(self, p):
        """从一次「识别原文 -> 用户确认文本」的修改中学习候选。

        参数：raw（识别原文）、final（用户最终文本）。
        只进候选池，不直接改词表。
        """
        raw = str(p.get('raw') or '')
        final = str(p.get('final') or '')
        from core import hotword_learning

        pairs = hotword_learning.extract_edits(raw, final)
        added = hotword_learning.add_candidates(pairs, source='confirm')
        return self._hotword_payload({
            'pairs': [{'wrong': w, 'right': r} for w, r in pairs],
            'added': added,
            'message': f'提取到 {len(pairs)} 处改动' if pairs else '没有可学习的改动',
        })

    def do_hotwords_export(self, p):
        """导出词表文本到文件。"""
        from core import hotword_learning

        text = hotword_learning.export_hotwords()
        if not text.strip():
            return {'ok': False, 'error': '词表为空'}
        path = self._save_text_file(text, 'hotwords.txt', '导出个人词库')
        return {'ok': True, 'path': path}

    def _save_text_file(self, text, default_name, title):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(self.win, title, default_name,
                                              '文本文件 (*.txt);;所有文件 (*)')
        if not path:
            return ''
        with open(path, 'wb') as fh:
            fh.write(str(text).encode('utf-8'))
        return path

    def do_asr_import_model(self, p):
        if self.win.voice.phase != 'idle':
            return {'ok': False, 'error': '请先停止语音输入，再导入模型'}
        path = str(p.get('path') or '')
        if not path:
            picked = self.do_pick_folder({'title': '选择本地 FunASR 模型文件夹（只登记路径）'})
            if not picked.get('ok'):
                return picked
            path = picked['path']
        if not path:
            return {'ok': False, 'cancelled': True}
        item = self.win.voice.registry.add(path, str(p.get('name') or ''))
        if not self.win.voice.unload():
            return {'ok': False, 'error': '旧模型未能卸载，请重试'}
        self.config.set('asr', 'local_model', value=item['id'])
        self.config.save()
        return self.do_asr_models({})

    def do_asr_remove_model(self, p):
        if self.win.voice.phase != 'idle':
            return {'ok': False, 'error': '请先停止语音输入，再移除模型'}
        model_id = str(p.get('id') or '')
        if not p.get('confirmed'):
            return {'ok': False, 'error': '请先确认从列表移除'}
        selected = self.config.get('asr', 'local_model', default='paraformer_large')
        if selected == model_id and not self.win.voice.unload():
            return {'ok': False, 'error': '模型未能卸载，请重试'}
        self.win.voice.registry.remove(model_id)
        if selected == model_id:
            remaining = self.win.voice.registry.list()
            replacement = next((m['id'] for m in remaining if m['available']), '')
            self.config.set('asr', 'local_model', value=replacement)
            self.config.save()
        return self.do_asr_models({})

    # ---- 配置 ----

    def do_set_config(self, p):
        section = str(p.get("section", ""))
        key = str(p.get("key", ""))
        if not section or not key:
            return {"ok": False, "error": "参数不完整"}
        if section == 'asr' and key in ('local_model', 'input_mode', 'segment_silence_ms', 'turn_silence_ms'):
            if self.win.voice.phase != 'idle':
                return {'ok': False, 'error': '请先停止当前录音，再切换模式或模型'}
            value = p.get('value')
            if key == 'input_mode' and value not in ('manual', 'realtime', 'auto'):
                return {'ok': False, 'error': '未知语音模式'}
            if key in ('segment_silence_ms', 'turn_silence_ms'):
                bounds = (400, 2000) if key == 'segment_silence_ms' else (2000, 10000)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not bounds[0] <= value <= bounds[1]:
                    return {'ok': False, 'error': '静音判定时间超出范围'}
                p['value'] = int(value)
            if key == 'local_model':
                self.win.voice.registry.get(str(value))
                if not self.win.voice.unload():
                    return {'ok': False, 'error': '旧模型未能卸载'}
        before = copy.deepcopy(self.config.data)
        candidate = copy.deepcopy(before)
        candidate.setdefault(section, {})[key] = p.get('value')
        validate_config(candidate)
        self.config.set(section, key, value=p.get("value"))
        self.config.save()
        self._apply_current(before)
        if section == "asr" and key == "floating_window":
            if bool(p.get("value")):
                self.win.show_voice_float()
            else:
                self.win.hide_voice_float()
        if section == "asr" and key == "local_model":
            # 只持久化选择；GUI 进程不加载 ASR 权重（识别跑在独立 worker 里），
            # 下一次识别/点「加载当前模型」时 worker 会按这里的值加载。
            self.win.voice.registry.scan()
        return {"ok": True}

    def do_set_config_nested(self, p):
        section = str(p.get("section", "ocr"))
        sub = str(p.get("sub", ""))
        key = str(p.get("key", ""))
        if not sub or not key:
            return {"ok": False, "error": "参数不完整"}
        before = copy.deepcopy(self.config.data)
        candidate = copy.deepcopy(before)
        candidate.setdefault(section, {}).setdefault(sub, {})[key] = p.get('value')
        validate_config(candidate)
        self.config.set(section, sub, key, value=p.get("value"))
        self.config.save()
        self._apply_current(before)
        return {"ok": True}

    def do_set_ocr_device(self, p):
        device = str(p.get("device", "cpu"))
        self.config.set("ocr", "device", value=device)
        self.config.save()
        try:
            self.app.ocr_service.reload_device(device)
        except Exception as e:  # noqa: BLE001
            print(f"[设备] 切换失败: {e}")
        return {"ok": True}

    def do_set_mode_enabled(self, p):
        mode = str(p.get("mode", ""))
        enabled = bool(p.get("enabled"))
        if mode not in MODES:
            return {"ok": False, "error": "未知功能"}
        self.win.set_mode_enabled(mode, enabled)
        self._push({"type": "enabled-changed", "mode": mode, "enabled": enabled})
        return {"ok": True}

    def do_save_config(self, p):
        self.config.save()
        self._apply_current()
        return {"ok": True}

    def _apply_current(self, before=None, full=False):
        self.app.reload_config()
        c = self.config
        self.win._enabled = {mode: bool(c.get('ui', mode+'_enabled', default=True)) for mode in MODES}
        self.win._queue_mode = {mode: bool(c.get('tts', mode+'_queue_mode', default=(mode=='func2'))) for mode in ('func1','func2')}
        old = before or {}
        if full or old.get('hotkeys') != c.data.get('hotkeys'):
            self.win.apply_hotkeys()
        if full or old.get('ocr',{}).get('device') != c.get('ocr','device',default='cpu'):
            self.app.ocr_service.reload_device(c.get('ocr','device',default='cpu'))
        if full or old.get('asr',{}).get('floating_window') != c.get('asr','floating_window',default=False):
            if c.get('asr','floating_window',default=False): self.win.show_voice_float()
            else: self.win.hide_voice_float()
        if full or old.get('translation') != c.data.get('translation'):
            self._release_models()
        self._push_theme()
        if full:
            self._push({'type':'config-replaced','state':self._build_state()})
        else:
            self._push({'type':'config-current','config':copy.deepcopy(c.data)})

    def _replace_current(self, candidate):
        candidate = validate_config(candidate)
        from qt_web.hotkeys import normalize_hotkey
        used = set()
        for key, value in candidate.get('hotkeys', {}).items():
            if value:
                normalized = normalize_hotkey(value)
                if normalized in used: raise ValueError('配置中的快捷键存在冲突')
                used.add(normalized)
        before = copy.deepcopy(self.config.data)
        self.config.replace(candidate)
        # 整份配置替换时结束旧会话，防止旧识别结果继续输入。
        self.win.cursor_input.cancel()
        self.win.voice.close()
        self.win.voice._state('idle', '配置已更新，点击重新开始')
        self.app.stop_scan_modes()
        self._apply_current(before, full=True)
        return {'ok':True, 'state':self._build_state()}

    def do_reset_defaults(self, p):
        if not p.get('confirmed'):
            return {'ok':False,'error':'请先确认覆盖当前配置'}
        return self._replace_current(_keep_hotwords(load_defaults(), self.config.data))

    def do_import_config(self, p):
        if not p.get('confirmed'):
            return {'ok':False,'error':'请先确认导入并覆盖当前配置'}
        from pathlib import Path
        source = Path(str(p.get('path') or ''))
        if not source.is_file() or source.stat().st_size > 2*1024*1024:
            return {'ok':False,'error':'请选择小于 2MB 的配置文件'}
        candidate = yaml.safe_load(source.read_text(encoding='utf-8-sig'))
        if not isinstance(candidate, dict):
            return {'ok':False,'error':'配置文件格式不正确'}
        return self._replace_current(
            _keep_hotwords(candidate, self.config.data, only_if_missing=True)
        )

    def do_export_config(self, p):
        from PySide6.QtWidgets import QFileDialog
        path = str(p.get('path') or '')
        if not path:
            path, _ = QFileDialog.getSaveFileName(self.win, '导出当前配置', str(self.config.config_path.parent/'settings-export.yaml'), '配置文件 (*.yaml *.yml)')
            self.win.view.setFocus()
        if not path: return {'ok':False,'cancelled':True}
        return {'ok':True,'path':self.config.export(path)}

    # ---- 快捷键 ----

    def do_set_hotkey(self, p):
        key = str(p.get("key", ""))
        raw_value = str(p.get("value", ""))
        try:
            value = normalize_hotkey(raw_value) if raw_value.strip() else ""
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        conflict = self.win.hotkey_conflict(key, value)
        if conflict:
            return {"ok": False, "error": f"与 {conflict} 冲突"}
        if key == "asr_talk":
            return {'ok': False, 'error': '语音输入快捷键已移除'}
        else:
            self.config.set("hotkeys", key, value=value)
            self.config.save()
            self.win.apply_hotkeys()
        return {"ok": True, "value": value, "hotkeys": self._hotkeys()}

    def do_reset_hotkeys(self, p):
        if not p.get('confirmed'): return {'ok':False,'error':'请先确认恢复默认按键'}
        self.config.data['hotkeys'] = load_defaults()['hotkeys']
        self.config.save()
        self.win.apply_hotkeys()
        return {"ok": True, "hotkeys": self._hotkeys()}

    def do_hotkey_capture_start(self, p):
        """录制期间暂停全局热键，避免按 F7 触发功能1。"""
        self.win.pause_hotkeys(True)
        return {"ok": True}

    def do_hotkey_capture_end(self, p):
        self.win.pause_hotkeys(False)
        return {"ok": True}

    # ---- 翻译模型（本地 GGUF 走 ModelManager，在线服务商走 translation.online_providers） ----

    def _model_manager(self) -> ModelManager:
        global _MODEL_MANAGER
        if _MODEL_MANAGER is None:
            _MODEL_MANAGER = ModelManager()
        return _MODEL_MANAGER

    def _release_models(self):
        """模型变更后卸载缓存（对应旧 UI 的 _release_translation_models）。"""
        service = getattr(self.app, "translation_service", None)
        if service is not None:
            try:
                service.unload_model()
            except Exception as e:  # noqa: BLE001
                print(f"[Web设置] 释放翻译服务模型失败: {e}")
        try:
            self.translation_manager.reset_model()
        except Exception as e:  # noqa: BLE001
            print(f"[Web设置] 释放自动翻译模型失败: {e}")

    def do_enable_local_model(self, p):
        mm = self._model_manager()
        mid = str(p.get("id", ""))
        model = next((m for m in mm.get_all_models() if m.get("id") == mid), None)
        if model is None:
            return {"ok": False, "error": "本地模型不存在"}
        if not mm.enable_model(mid):
            # enable_model 最常见的失败原因就是模型文件已被删除/移动，
            # 直接说清楚路径比「启用失败」有用得多。
            path = str(model.get("path") or "")
            if path and not os.path.exists(path):
                return {"ok": False, "error": f"模型文件不存在：{path}"}
            return {"ok": False, "error": "启用本地模型失败"}
        self.config.set("translation", "model", value="gguf")
        self.config.set("translation", "active_provider", value="")
        self.config.save()
        try:
            self.app.translation.switch_model("gguf")
        except Exception as e:  # noqa: BLE001
            print(f"[Web设置] 切换翻译模型失败: {e}")
        self._release_models()
        return {"ok": True, "name": model.get("name", mid)}

    def do_disable_local_model(self, p):
        mm = self._model_manager()
        ok = mm.disable_model(str(p.get("id", "")))
        self._release_models()
        return {"ok": bool(ok), "error": None if ok else "禁用失败"}

    def do_remove_local_model(self, p):
        mm = self._model_manager()
        mid = str(p.get("id", ""))
        # 先确认真的在列表里。若已经删掉了（用户连点、或列表没刷新），
        # 直接回 ok=True + gone 标记，让前端把列表刷新到最新，而不是抛
        # 「删除失败（模型可能仍被占用）」这种会让人以为删不掉的话。
        if not any(m.get("id") == mid for m in mm.get_all_models()):
            return {"ok": True, "gone": True, "error": None}
        # 与旧 UI 的 _on_delete_local_model 一致：先卸载已加载的模型再删文件。
        # Windows 上文件被 llama.cpp 占用时删除会失败，顺序反了就是「删不掉」。
        self._release_models()
        ok = mm.remove_model(mid, delete_file=False)
        if not ok:
            return {"ok": False, "error": "删除失败：模型文件可能仍被占用，请先禁用模型后重试"}
        return {"ok": True, "gone": True, "error": None}

    def do_add_local_model(self, p):
        mm = self._model_manager()
        path = str(p.get("path", ""))
        name = str(p.get("name") or "").strip()
        ok = mm.add_model(path, model_name=name or None, copy_to_dir=False)
        if not ok:
            return {"ok": False, "error": "添加本地模型失败（需为存在的 .gguf 文件，且未重复添加）"}
        # 模型 id 就是文件名（去扩展名），回传给前端就地选中
        model_id = os.path.splitext(os.path.basename(path))[0]
        added = next((m for m in mm.get_all_models() if m.get("id") == model_id), None)
        return {
            "ok": True,
            "id": model_id,
            "name": str((added or {}).get("name", model_id)),
        }

    def do_enable_online_model(self, p):
        name = str(p.get("name", ""))
        providers = self.config.get("translation", "online_providers", default=None) or []
        if not any(pv.get("name") == name for pv in providers):
            return {"ok": False, "error": "在线模型不存在"}
        self.config.set("translation", "active_provider", value=name)
        self.config.set("translation", "model", value="custom_openai")
        self.config.save()
        try:
            self.app.translation.switch_model("custom_openai")
        except Exception as e:  # noqa: BLE001
            print(f"[Web设置] 切换在线翻译失败: {e}")
        self._release_models()
        return {"ok": True, "name": name}

    def do_remove_online_model(self, p):
        name = str(p.get("name", ""))
        remaining = [
            pv
            for pv in (self.config.get("translation", "online_providers", default=None) or [])
            if pv.get("name") != name
        ]
        self.config.set("translation", "online_providers", value=remaining)
        if str(self.config.get("translation", "active_provider", default="")) == name:
            self.config.set("translation", "active_provider", value="")
            self.config.set("translation", "model", value="gguf")
        self.config.save()
        self._release_models()
        return {"ok": True}

    def do_add_online_model(self, p):
        name = str(p.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "名称不能为空"}
        provider = {
            "name": name,
            "preset": str(p.get("preset", "")),
            "base_url": str(p.get("base_url", "")),
            "api_key": str(p.get("api_key", "")),
            "model": str(p.get("model", "")),
            "app_id": str(p.get("app_id", "")),
            "secret": str(p.get("secret", "")),
            "region": str(p.get("region", "")),
            "body_template": str(p.get("body_template", "")),
            "response_path": str(p.get("response_path", "")),
        }
        current = [
            pv
            for pv in (self.config.get("translation", "online_providers", default=None) or [])
            if pv.get("name") != name
        ]
        current.append(provider)
        self.config.set("translation", "online_providers", value=current)
        self.config.save()
        # 与旧 UI 一致：保存后直接启用
        return self.do_enable_online_model({"name": name})

    def do_test_online_model(self, p):
        """对已保存的在线服务商做真实连通性测试。"""
        name = str(p.get("name", ""))
        providers = {
            pv.get("name"): pv
            for pv in (self.config.get("translation", "online_providers", default=None) or [])
        }
        if name not in providers:
            return {"ok": False, "error": "在线模型不存在"}
        return self._test_provider(providers[name])

    def do_test_online_config(self, p):
        """测试弹窗里尚未保存的配置。"""
        return self._test_provider(
            {
                "name": str(p.get("name", "")),
                "preset": str(p.get("preset", "")),
                "base_url": str(p.get("base_url", "")),
                "api_key": str(p.get("api_key", "")),
                "model": str(p.get("model", "")),
                "app_id": str(p.get("app_id", "")),
                "secret": str(p.get("secret", "")),
                "region": str(p.get("region", "")),
                "body_template": str(p.get("body_template", "")),
                "response_path": str(p.get("response_path", "")),
            }
        )

    def _test_provider(self, provider: dict):
        """临时把配置指向该服务商 → 试译一句 → 还原（与旧 UI 的测试逻辑一致）。"""
        saved_model = self.config.get("translation", "model", default="gguf")
        saved_provider = self.config.get("translation", "active_provider", default="")
        saved_providers = self.config.get("translation", "online_providers", default=None) or []

        tmp = list(saved_providers)
        name = provider.get("name") or "__web_test__"
        tmp = [pv for pv in tmp if pv.get("name") != name]
        tmp.append(provider)
        self.config.set("translation", "online_providers", value=tmp)
        self.config.set("translation", "active_provider", value=name)
        self.config.set("translation", "model", value="custom_openai")
        try:
            try:
                self.app.translation.switch_model("custom_openai")
            except Exception as e:  # noqa: BLE001
                print(f"[Web设置] 测试切换失败: {e}")
            result = self.app.translation_service.translate("Hello, world.")
            text = str(result or "")
            ok = bool(text.strip()) and "失败" not in text
            if ok:
                return {"ok": True, "message": f"连接成功：{text[:40]}"}
            return {"ok": False, "error": text or "无返回内容"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        finally:
            self.config.set("translation", "online_providers", value=saved_providers)
            self.config.set("translation", "active_provider", value=saved_provider)
            self.config.set("translation", "model", value=saved_model)
            try:
                self.app.translation.switch_model(saved_model or "gguf")
            except Exception:
                pass
            self._release_models()

    # ================= 状态构建 =================

    def _build_state(self) -> dict:
        c = self.config
        tts = {
            "backend": c.get("tts", "backend", default="sapi"),
            "voice_name": c.get("tts", "voice_name", default=""),
            "edge_voice": c.get("tts", "edge_voice", default=""),
            "rate": c.get("tts", "rate", default=0),
            "volume": c.get("tts", "volume", default=1.0),
            "pitch": c.get("tts", "pitch", default=0),
            "test_text": c.get('tts', 'test_text', default='你好，这是语音测试，今天的天气非常不错。'),
        }

        voices = {"sapi": [], "edge": []}
        try:
            # 本机语音：core.tts.get_available_voices() -> {name, culture, gender, age}
            from core.tts import get_available_voices

            for v in get_available_voices():
                name = str(v.get("name", ""))
                voices["sapi"].append(
                    {
                        "value": name,
                        "display": f"{name} · {v.get('gender', '')} · {v.get('culture', '')}".strip(" ·"),
                        "gender": str(v.get("gender", "")),
                        "age": str(v.get("age", "")),
                    }
                )
        except Exception as e:  # noqa: BLE001
            print(f"[语音列表] 本机语音加载失败: {e}")

        try:
            # 在线语音：edge_tts_backend.get_voices() -> {name, label, gender, locale}
            from core.edge_tts_backend import get_voices as get_edge_voices

            for v in get_edge_voices():
                voices["edge"].append(
                    {
                        "value": str(v.get("name", "")),
                        "display": f"{v.get('label', '')} · {v.get('gender', '')} · {v.get('locale', '')}".strip(" ·"),
                    }
                )
        except Exception as e:  # noqa: BLE001
            print(f"[语音列表] 在线语音加载失败: {e}")

        # 可选推理设备：CPU 永远第一，其后每块可用 GPU
        devices = [{"value": "cpu", "label": "CPU（处理器）"}]
        try:
            from core import device_manager

            for d in device_manager.list_selectable_devices()[1:]:
                devices.append({"value": d.get("id", ""), "label": device_manager.device_display_name(d)})
        except Exception as e:  # noqa: BLE001
            print(f"[设备列表] 加载失败: {e}")
            devices.append({"value": "cuda:0", "label": "GPU 0"})

        return {
            "theme": c.get("ui", "web_theme", default="dark"),
            "frost": bool(c.get("ui", "web_frost", default=True)),
            "enabled": dict(self.win._enabled),
            "queueModes": dict(self.win._queue_mode),
            "regions": self._mode_flags("has_region"),
            "running": self._mode_flags("is_running", SCAN_ATTRS),
            "hotkeys": self._hotkeys(),
            "asr": {
                "backend": c.get("asr", "backend", default="local"),
                "local_model": c.get("asr", "local_model", default="paraformer_large"),
                "copy_to_clipboard": bool(c.get("asr", "copy_to_clipboard", default=True)),
                "auto_input": bool(c.get('asr', 'auto_input', default=True)),
                "floating_window": bool(c.get("asr", "floating_window", default=False)),
                "model_status": self.win.voice.model_status,
                "models": self.win.voice.registry.list(),
                "input_mode": c.get('asr', 'input_mode', default='manual'),
                "segment_silence_ms": c.get('asr', 'segment_silence_ms', default=800),
                "turn_silence_ms": c.get('asr', 'turn_silence_ms', default=3000),
                "idle_exit_seconds": int(c.get('asr', 'idle_exit_seconds', default=300)),
                "device_name": str(c.get('asr', 'device_name', default='') or ''),
                "devices": list(self._input_devices),
            },
            "translation": {
                "source_lang": c.get("translation", "source_lang", default="auto"),
                "target_lang": c.get("translation", "target_lang", default="zh"),
                "func1_enabled": bool(c.get("translation", "func1_enabled", default=False)),
                "func2_enabled": bool(c.get("translation", "func2_enabled", default=False)),
                "func3_enabled": bool(c.get("translation", "func3_enabled", default=False)),
                "manga_enabled": bool(c.get("translation", "manga_enabled", default=False)),
                "game_enabled": bool(c.get("translation", "game_enabled", default=False)),
            },
            "tts": tts,
            "voices": voices,
            "models": self._models_state(),
            "recognition": {
                "func1_copy": bool(c.get("recognition", "func1_copy", default=False)),
                "func2_copy": bool(c.get("recognition", "func2_copy", default=False)),
                "func1_read": bool(c.get("recognition", "func1_read", default=True)),
                "func2_read": bool(c.get("recognition", "func2_read", default=True)),
            },
            "ocr": {
                "device": c.get("ocr", "device", default="cpu"),
                "devices": devices,
                "func3": {
                    "repeat_cooldown": c.get("ocr", "func3", "repeat_cooldown", default=3.0),
                    "similarity_threshold": c.get("ocr", "func3", "similarity_threshold", default=0.85),
                    "min_length_for_similarity": c.get("ocr", "func3", "min_length_for_similarity", default=3),
                    "stable_frames": c.get("ocr", "func3", "stable_frames", default=2),
                    "short_text_max_len": c.get("ocr", "func3", "short_text_max_len", default=2),
                },
                "manga_mode": {
                    "scan_interval": c.get("ocr", "manga_mode", "scan_interval", default=0.4),
                    "stable_frames": c.get("ocr", "manga_mode", "stable_frames", default=2),
                    "history_size": c.get("ocr", "manga_mode", "history_size", default=6),
                },
                "game_mode": {
                    "scan_interval": c.get("ocr", "game_mode", "scan_interval", default=0.3),
                    "stable_frames": c.get("ocr", "game_mode", "stable_frames", default=2),
                    "history_size": c.get("ocr", "game_mode", "history_size", default=5),
                },
            },
        }

    def _hotkeys(self) -> dict:
        keys = ["func1_trigger", "func2_select", "func2_read", "func3_toggle", "func3_reselect", "stop_speak"]
        out = {}
        for k in keys:
            out[k] = self.config.get("hotkeys", k, default="")
        return out

    def _models_state(self) -> dict:
        """本地 GGUF 模型来自 ModelManager；在线服务商来自 translation.online_providers。"""
        local = []
        try:
            mm = self._model_manager()
            for m in mm.get_all_models():
                local.append(
                    {
                        "id": str(m.get("id") or m.get("name") or ""),
                        "name": str(m.get("name") or "未命名"),
                        "active": bool(m.get("is_active")),
                    }
                )
        except Exception as e:  # noqa: BLE001
            print(f"[模型列表] 本地模型加载失败: {e}")

        online = []
        active_provider = str(self.config.get("translation", "active_provider", default="") or "")
        for pv in self.config.get("translation", "online_providers", default=None) or []:
            name = str(pv.get("name", "未命名"))
            online.append(
                {
                    "name": name,
                    "preset": str(pv.get("preset", "")),
                    "active": name == active_provider,
                }
            )

        return {"local": local, "online": online}

    def translation_usable(self, mode: str = "") -> bool:
        """翻译链路当前是否真的能翻（有已启用的本地模型，或配好了在线服务商）。

        只用于 GUI 侧兜底：自动翻译开关打开但没有任何可用模型时，后端翻译会
        失败且**不产生任何结果**，这一帧既没有原文（原文被跳过显示）也没有译文，
        用户侧表现就是「识别到了，但文字不显示、也不朗读」。这种情况下退回
        「显示 + 朗读原文」，行为与翻译关闭时一致。
        """
        try:
            if str(self.config.get("translation", "active_provider", default="") or ""):
                return True
            for m in self._model_manager().get_all_models():
                if m.get("is_active"):
                    return True
        except Exception as e:  # noqa: BLE001
            print(f"[翻译可用性] 判断失败: {e}")
            return True  # 判断不出来就别阻断，保持原行为
        return False

    # ================= 推送 =================

    def _push(self, msg: dict):
        self.event.emit(json.dumps(msg, ensure_ascii=False, default=str))

    def push_status(self, text: str, kind: str = ""):
        self._push({"type": "status", "text": text, "kind": kind})

    def push_result(self, text: str, status: str = "", append: bool = False):
        self._push({"type": "result", "text": text, "status": status, "append": append})

    def push_translate_result(self, text: str, error: str = ""):
        """手动翻译结果（翻译页输出框专用，不进结果区）。"""
        self._push(
            {
                "type": "translate-result",
                "ok": bool(text),
                "text": text or "",
                "error": "" if text else (error or "翻译失败：没有返回结果（请检查翻译模型是否可用）"),
            }
        )

    def push_asr_result(self, text: str):
        """语音输入识别结果（语音输入页的专用结果框）。"""
        self._push({"type": "asr-result", "text": text or ""})

    def push_scan_state(self, mode: str, running: bool):
        self._push({"type": "scan-state", "mode": mode, "running": running})

    def push_region(self, mode: str, has_region: bool):
        self._push({"type": "region-changed", "mode": mode, "has_region": has_region})

    def _push_running(self):
        flags = self._mode_flags("is_running", SCAN_ATTRS)
        self._push({"type": "running", **flags})
        for mode, running in flags.items():
            self._push({"type": "scan-state", "mode": mode, "running": running})

    def _push_regions(self):
        flags = self._mode_flags("has_region")
        self._push({"type": "regions", **flags})
        for mode, has_region in flags.items():
            self._push({"type": "region-changed", "mode": mode, "has_region": has_region})

    # ================= 内部工具 =================

    @staticmethod
    def _mode_flags(probe: str, attrs: dict = None) -> dict:
        """{页面模式: bool(探针)}，probe 可以是属性（is_running）或方法（has_region）。

        只对**已构造**的控制器取值——懒构造下没构造过就不可能正在扫描/已选区域，
        没必要为了拼一个 False 去把控制器造出来。
        """
        attrs = MODE_ATTRS if attrs is None else attrs
        app = _CURRENT_APP
        built = getattr(app, "_lazy", {}) if app is not None else {}
        out = {}
        for mode, attr in attrs.items():
            controller = built.get(attr)
            value = getattr(controller, probe, False) if controller is not None else False
            out[mode] = bool(value() if callable(value) else value)
        return out

    def _scan_controller(self, mode: str):
        attr = MODE_ATTRS.get(mode)
        return getattr(self.app, attr, None) if attr else None

    def _apply_tts_params(self, p: dict):
        """TTS 页的参数只对本页生效，不改全局配置。"""
        kwargs = {}
        if p.get("rate") is not None:
            kwargs["rate"] = int(p["rate"])
        if p.get("pitch") is not None:
            kwargs["pitch"] = int(p["pitch"])
        source = str(p.get("source") or "").strip()
        if source in ("sapi", "edge"):
            kwargs["backend"] = source
        voice = str(p.get("voice") or "").strip()
        if voice:
            if source == "edge":
                kwargs["edge_voice"] = voice
            else:
                kwargs["voice_name"] = voice
        if not kwargs:
            return
        try:
            self.app.tts_service.update_settings(**kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS] 参数应用失败: {e}")

    # ---------- 导出 MP3 ----------

    def _page_volume(self) -> float:
        """TTS 页没有音量滑块，导出时沿用全局音量配置。"""
        try:
            return float(self.config.get("tts", "volume", default=1.0))
        except Exception:
            return 1.0

    def _tts_export_worker(self, params: dict):
        """后台线程：合成 → 编码 → 落盘，过程与结果都推给前端。"""
        from qt_web.tts_export import export_mp3

        def progress(ratio, label):
            self._push({"type": "tts-progress", "ratio": float(ratio), "text": label})

        try:
            result = export_mp3(progress=progress, **params)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)}

        if result.get("ok"):
            self._push(
                {
                    "type": "tts-export-done",
                    "ok": True,
                    "path": result.get("path", ""),
                    "size": result.get("size", 0),
                }
            )
        else:
            self._push(
                {"type": "tts-export-done", "ok": False, "error": result.get("error") or "导出失败"}
            )


def _keep_hotwords(target: dict, current: dict, only_if_missing: bool = False) -> dict:
    """导入 / 恢复默认时保留当前热词。

    热词没有前端编辑入口，一旦被覆盖就只能手改 YAML；除非导入文件
    自己带了 asr.hotwords，否则沿用当前词表。
    """
    current_asr = current.get('asr') if isinstance(current, dict) else None
    hotwords = current_asr.get('hotwords') if isinstance(current_asr, dict) else None
    if not hotwords:
        return target
    asr = target.get('asr')
    if not isinstance(asr, dict):
        asr = {}
        target['asr'] = asr
    if only_if_missing and 'hotwords' in asr:
        return target
    asr['hotwords'] = copy.deepcopy(hotwords)
    return target
