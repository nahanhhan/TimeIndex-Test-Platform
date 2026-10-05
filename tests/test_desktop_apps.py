from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_core.common import ROOT
from platform_core.desktop_apps import AppSession, app_plan


class DesktopPlanTests(unittest.TestCase):
    def test_plan_creates_local_documents_without_launching_apps(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".desktop-test-") as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(ROOT))
            with patch("platform_core.desktop_apps.subprocess.Popen") as launch:
                planned = app_plan("test-run-12345678", 1, Path(folder))
                launch.assert_not_called()
            self.assertEqual(len(planned), 3)
            self.assertEqual({row["topic"] for row in planned}, {"Python 函数", "实验报告", "数据库索引"})
            self.assertTrue(list(Path(folder).glob("*.txt")))
            self.assertTrue(list(Path(folder).glob("*.html")))
            self.assertTrue(all(not AppSession(row).evidence["actually_launched"] for row in planned))

    def test_missing_application_is_not_faked_as_success(self) -> None:
        planned = {"id": "a", "application": "missing browser", "executable": None, "arguments": [], "marker": "a"}
        with patch("platform_core.desktop_apps.subprocess.Popen") as launch:
            result = AppSession(planned).open()
            launch.assert_not_called()
        self.assertEqual(result["status"], "not_available")
        self.assertFalse(result["actually_launched"])


if __name__ == "__main__":
    unittest.main()
