from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from platform_core.common import ROOT, atomic_json
from platform_core.manifest import create_run, run_path


class UITests(unittest.TestCase):
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

