from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class UITests(unittest.TestCase):
    def test_dashboard_loads_with_persisted_runs(self) -> None:
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("TimeIndex" in item.value for item in app.title))


if __name__ == "__main__":
    unittest.main()

