from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from platform_core.resources import process_sample, resource_summary
from platform_core.worker import _final_status


def denied(*args, **kwargs):
    raise psutil.AccessDenied(pid=123)


def sample(cpu=0.0, rss=1024):
    return {"cpu_percent_of_one_core": cpu, "rss_bytes": rss, "processes": 1}


class ResourceReadTests(unittest.TestCase):
    def test_unreadable_process_is_not_reported_as_zero_usage(self) -> None:
        with patch("platform_core.resources.tree", return_value=[SimpleNamespace(cpu_percent=denied)]):
            self.assertIsNone(process_sample(123))
        rows = [{"phase": "active", "timeindex": sample(), "model": None}]
        result = resource_summary(rows, model_selected=True)
        self.assertEqual(result["model_status"], "not_measured")
        self.assertIsNone(result["active"]["model"])
        self.assertIsNone(result["total"])
        self.assertEqual(result["status"], "partial")

    def test_failed_memory_read_does_not_add_its_cpu_to_successful_processes(self) -> None:
        successful = SimpleNamespace(cpu_percent=lambda **kwargs: 3.0, memory_info=lambda: SimpleNamespace(rss=2048))
        unreadable = SimpleNamespace(cpu_percent=lambda **kwargs: 99.0, memory_info=denied)
        with patch("platform_core.resources.tree", return_value=[successful, unreadable]):
            value = process_sample(123)
        self.assertEqual(value["cpu_percent_of_one_core"], 3)
        self.assertEqual(value["rss_bytes"], 2048)
        self.assertEqual((value["processes"], value["unavailable_processes"]), (1, 1))
        result = resource_summary([{"phase": "active", "timeindex": sample(), "model": value}], model_selected=True)
        self.assertEqual(result["model_status"], "partial")
        self.assertIsNone(result["total"])

    def test_real_zero_cpu_usage_remains_a_valid_measurement(self) -> None:
        process = SimpleNamespace(cpu_percent=lambda **kwargs: 0.0, memory_info=lambda: SimpleNamespace(rss=1024))
        with patch("platform_core.resources.tree", return_value=[process]):
            value = process_sample(123)
        result = resource_summary([{"phase": "active", "timeindex": value, "model": value}], model_selected=True)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["model_status"], "done")
        self.assertEqual(result["total"]["mean_cpu_percent_of_one_core"], 0)

    def test_legacy_zero_process_samples_are_excluded_without_changing_evidence(self) -> None:
        rows = [{"phase": "active", "timeindex": sample(2), "model": sample(4)},
                {"phase": "active", "timeindex": sample(2), "model":
                 {"cpu_percent_of_one_core": 0.0, "rss_bytes": 0, "processes": 0}}]
        original = copy.deepcopy(rows)
        result = resource_summary(rows, model_selected=True)
        self.assertEqual(result["model_status"], "partial")
        self.assertEqual(result["active"]["model"]["mean_cpu_percent_of_one_core"], 4)
        self.assertEqual(result["active"]["model"]["unavailable_samples"], 1)
        self.assertIsNone(result["total"])
        self.assertEqual(rows, original)

    def test_no_valid_samples_do_not_generate_a_zero_total(self) -> None:
        result = resource_summary([{"phase": "active", "timeindex": None, "model": None}], model_selected=True)
        self.assertEqual(result["status"], "not_measured")
        self.assertIsNone(result["total"])

    def test_optional_unselected_model_does_not_fail_worker_measurement(self) -> None:
        result = resource_summary([{"phase": "active", "timeindex": sample(), "model": None}], model_selected=False)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["model_status"], "not_measured")

    def test_selected_unmeasured_resources_make_the_run_partial(self) -> None:
        for status in ("partial", "not_measured"):
            with self.subTest(status=status):
                self.assertEqual(_final_status({"resources": True}, {"cases": []}, [],
                                               {"resources": {"status": status}}, True, False), "partial")


if __name__ == "__main__":
    unittest.main()
