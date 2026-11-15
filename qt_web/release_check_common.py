"""发布版自检的公共工具。

`release_gui_check`（GUI 进程自检）与 `release_worker_check`（语音工作进程自检）
是两个独立 exe 的自检入口，各自只需要跑自己那部分依赖，但**写报告与探测导入**
的逻辑完全一样。历史上这两段在两边各写了一份逐字相同的实现（`save` / `imports`），
改一边容易漏另一边，这里收拢到一处。

注意：本模块会被 `web_entry.py` / `voice_worker_entry.py` 在两个独立进程中导入，
必须只依赖标准库，且不能引入任何重型包（torch / PySide6 / onnxruntime）。
"""


def save(report, path):
    """把报告写成 UTF-8 JSON（保留中文，不转义）。"""
    import json
    from pathlib import Path

    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def imports(names):
    """逐个导入并记录结果：成功记 'ok'，失败记完整 traceback。"""
    import importlib
    import traceback

    out = {}
    for name in names:
        try:
            importlib.import_module(name)
            out[name] = "ok"
        except Exception:
            out[name] = traceback.format_exc()
    return out
