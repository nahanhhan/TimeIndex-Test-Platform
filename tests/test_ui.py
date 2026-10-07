from __future__ import annotations

import unittest
import io
import json
import inspect
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from platform_core.common import ROOT, atomic_json
from platform_core.manifest import create_run, run_path
from platform_core.preflight import check


class UITests(unittest.TestCase):
    def test_environment_button_calls_real_check_without_experiment_settings(self) -> None:
        class Reply(io.BytesIO):
            status = 200

        reply = Reply(json.dumps({"data": [{"id": "gemma-4-e4b"},
                                         {"id": "text-embedding-embeddinggemma-300m"}]}).encode())
        with patch("platform_core.manifest.list_runs", return_value=[]), \
                patch("platform_core.preflight.urllib.request.build_opener") as opener, \
                patch("platform_core.preflight.discover_model_pid", return_value=None), \
                patch("platform_core.preflight.check", wraps=check) as checked:
            opener.return_value.open.return_value.__enter__.return_value = reply
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            app.number_input[0].set_value(45)
            next(item for item in app.checkbox if "附加本体修复" in item.label).check()
            next(item for item in app.button if item.label == "检查当前环境").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(any("已连接模型接口" in item.value for item in app.success))
            checked.assert_called_once()
            self.assertNotIn("model_timeout_s", checked.call_args.kwargs)
            self.assertNotIn("verify_core_repairs", checked.call_args.kwargs)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 3)

    def test_start_button_passes_selected_wait_limit_and_repair_setting(self) -> None:
        from platform_core.controller import start
        from platform_core.manifest import create_run

        captured = {}
        def launch(mode, dataset_path=None, **settings):
            # Validate against the actual settings accepted by experiment creation.
            inspect.signature(create_run).bind(mode, dataset_path, **{key: value for key, value in settings.items() if key != "api_key"})
            captured.update(settings)
            return {"run_id": "ui-start-fixture"}

        with patch("platform_core.manifest.list_runs", return_value=[]), \
                patch("platform_core.controller.start", autospec=start, side_effect=launch) as started:
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            app.number_input[0].set_value(45)
            next(item for item in app.checkbox if "附加本体修复" in item.label).check()
            next(item for item in app.button if item.label == "开始批量实验").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            started.assert_called_once()
            self.assertEqual(captured["model_timeout_s"], 45)
            self.assertTrue(captured["verify_core_repairs"])
            self.assertTrue(any("已启动实验：ui-start-fixture" in item.value for item in app.success))

    def test_dashboard_loads_with_persisted_runs(self) -> None:
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("TimeIndex" in item.value for item in app.title))

    def test_finished_legacy_run_does_not_show_a_running_phase(self) -> None:
        manifest = create_run("quick")
        manifest.update(status="partial", current_phase="正在生成实验表格和逐例报告")
        atomic_json(run_path(manifest["run_id"]) / "manifest.json", manifest)
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        app.selectbox[0].set_value(manifest["run_id"]).run()
        self.assertFalse(app.exception)
        self.assertFalse(any("正在生成实验表格" in row.value for row in app.caption))


if __name__ == "__main__":
    unittest.main()

