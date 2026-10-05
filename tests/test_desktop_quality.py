from __future__ import annotations

import unittest
from unittest.mock import patch

from platform_core.controller import start
from platform_core.desktop_quality import evaluate


class DesktopQualityTests(unittest.TestCase):
    def test_gold_comes_from_actual_request_and_missing_refinement_is_a_miss(self) -> None:
        row = {"id": "LIVE-1", "record_id": "1", "input": {},
               "record": {"summary": "打开 Python函数 第1节 笔记"}, "refined": {}}
        inventory = [{"id": "R1-N", "marker": "TOKEN-N", "topic": "Python 函数"},
                     {"id": "R2-B", "marker": "TOKEN-B", "topic": "数据库索引"}]
        calls = [{"operation": "chat", "context": {"record_id": "1"},
                  "request": {"messages": [{"content": "Python 函数 第1节 TOKEN-N"}]}}]
        result = evaluate([row], inventory, calls)
        self.assertEqual(row["expected"]["source_scene_ids"], ["R1-N"])
        self.assertEqual(result["facts_before"]["coverage"], 1)
        self.assertEqual(result["details_before"]["coverage"], 1)
        self.assertEqual(result["facts_after"]["coverage"], 0)
        self.assertEqual(result["details_after"]["coverage"], 0)
        unobserved = {"id": "LIVE-2", "record_id": "2", "input": {}, "record": {}}
        result = evaluate([unobserved], inventory, [])
        self.assertEqual(result["status"], "not_measured")
        self.assertEqual(unobserved["expected"]["facts"], [])

    def test_missing_desktop_environment_never_starts_a_worker(self) -> None:
        with patch("platform_core.controller.prepare") as prepare, \
                patch("platform_core.manifest.host_info", return_value={}), \
                patch("platform_core.controller.cleanup"), \
                patch("platform_core.controller.check", return_value={"desktop_ready": False}), \
                patch("platform_core.controller.subprocess.Popen") as launch:
            from platform_core.common import RUNS
            prepare.side_effect = lambda run_id, *_args, **_kwargs: {"run": RUNS / run_id}
            with self.assertRaisesRegex(RuntimeError, "环境检查未通过"):
                start("desktop", dedicated_vm=True)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
