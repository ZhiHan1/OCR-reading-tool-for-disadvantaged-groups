"""守门测试：翻译设置页的模型操作区（2026-11-15 用户要求）。

三项要求：
1. 删掉「移除记录」按钮（失效记录改由扫描自动清理）
2. 「打开翻译模型文件夹」「重新扫描」移到「禁用」右边
3. 「重新扫描」点了必须有反应（后端同步连扫两轮并带回列表）

第 3 点容易回归：`ModelDiscovery` 要求文件稳定两轮才登记，只扫一轮找不到
任何东西；推送又按签名去重，列表没变就不发事件。两个因素叠加，
用户看到的就是「点了没效果」。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SETTINGS_JS = ROOT / "qt_web" / "web" / "js" / "pages" / "settings.js"
API_PY = ROOT / "qt_web" / "api.py"


class TranslationModelPanelTest(unittest.TestCase):
    def setUp(self):
        self.js = SETTINGS_JS.read_text(encoding="utf-8")
        self.api = API_PY.read_text(encoding="utf-8")

    # ---- 要求 1：移除记录按钮下线 --------------------------------

    def test_remove_record_button_is_gone(self):
        """DOM 里不能再有 local-delete 按钮。"""
        self.assertNotIn(
            'id: "local-delete"', self.js,
            "「移除记录」按钮已按用户要求删除，不能在面板里重新渲染",
        )

    def test_no_dangling_local_delete_binding(self):
        """不能残留 A.qs('#local-delete') —— 拿到 null 会让 A.on 抛异常，
        把它后面的所有绑定（添加模型等）全部吞掉。"""
        hits = re.findall(r'qs\(\s*["\']#local-delete["\']', self.js)
        self.assertEqual(
            hits, [],
            f"还有 {len(hits)} 处 #local-delete 引用；按钮已删除，"
            "这些 A.qs 会返回 null 并中断后续绑定",
        )

    def test_remove_record_call_not_issued(self):
        """前端不应再发 remove_local_model 请求。"""
        self.assertNotIn(
            '"remove_local_model"', self.js,
            "「移除记录」已下线，前端不应再调用 remove_local_model",
        )

    # ---- 要求 2：按钮顺序 ----------------------------------------

    def test_button_order_puts_folder_and_scan_right_of_disable(self):
        """面板按钮顺序必须是 添加模型 → 禁用 → 打开文件夹 → 重新扫描。"""
        panel = self.js[self.js.index('id="model-panel-local"'):]
        panel = panel[:panel.index('id="model-panel-online"')]
        order = re.findall(r'C\.btn\([^)]*?id: "([a-z\-]+)"', panel)
        self.assertEqual(
            order,
            ["local-add", "local-disable",
             "translation-open-folder", "translation-scan-folder"],
            f"按钮顺序不对：{order}",
        )

    def test_folder_and_scan_buttons_exist(self):
        for bid in ("translation-open-folder", "translation-scan-folder"):
            self.assertIn(f'id: "{bid}"', self.js, f"缺少按钮 {bid}")

    # ---- 要求 3：重新扫描必须真的有效果 --------------------------

    def test_scan_action_scans_twice(self):
        """后端必须连扫两轮：ModelDiscovery 稳定两轮才登记。"""
        body = self.api[self.api.index("def do_scan_model_folders"):]
        body = body[:body.index("def _check_model_health")]
        self.assertGreaterEqual(
            body.count(".scan()"), 2,
            "重新扫描只扫一轮找不到任何模型（ModelDiscovery 要求稳定两轮）",
        )

    def test_scan_action_returns_models(self):
        """响应体里必须带回最新模型列表，前端才能就地刷新下拉。"""
        body = self.api[self.api.index("def do_scan_model_folders"):]
        body = body[:body.index("def _check_model_health")]
        self.assertIn("'models': state", body)
        self.assertIn("'count'", body)

    def test_frontend_handles_scan_response(self):
        """前端必须处理响应并给出提示，否则用户看到的是「点了没效果」。"""
        seg = self.js[self.js.index('"#translation-scan-folder"'):]
        seg = seg[:seg.index("bridge.on")]
        self.assertIn("res.ok", seg, "前端没有处理扫描结果")
        self.assertIn("refreshModelLists()", seg, "扫描后必须就地刷新下拉")
        self.assertIn("#local-hint", seg, "扫描后必须给用户可见反馈")


if __name__ == "__main__":
    unittest.main()
