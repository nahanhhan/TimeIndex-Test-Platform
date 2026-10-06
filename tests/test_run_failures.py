from __future__ import annotations

import copy
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from platform_core.common import ROOT, atomic_json, read_json
from platform_core.manifest import TERMINAL_STATUSES, update_run
from platform_core.scoring import complete_query_rows, query_completion, rank_query, summarize_cases
from platform_core.worker import _final_status, _organize_records
from platform_core.real_desktop import _organize_desktop_records


class RunFailureTests(unittest.TestCase):
    def test_terminal_status_clears_running_phase(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".failure-test-") as folder:
            directory = Path(folder)
            with patch("platform_core.manifest.run_path", return_value=directory):
                for status in TERMINAL_STATUSES:
                    with self.subTest(status=status):
                        atomic_json(directory / "manifest.json", {"status": "running", "current_phase": "正在生成报告"})
                        result = update_run("fixture", status=status)
                        self.assertIsNone(result["current_phase"])

    def test_query_errors_and_missing_questions_affect_completion(self) -> None:
        planned = [{"id": f"Q{i}", "text": f"question{i}", "relevant_ids": ["S1"]} for i in range(4)]
        saved = [{"id": "Q0", **rank_query(["S1"], ["S1"], 1)}]
        original = copy.deepcopy(saved)
        rows = complete_query_rows(planned, saved)
        self.assertEqual(len(rows), 4)
        self.assertEqual(query_completion(rows)["status"], "partial")
        self.assertEqual(saved, original)
        for statuses, expected in [(["error"], "failed"), (["done", "error"], "partial"),
                                   (["not_measured"], "not_measured"), ([], "not_measured")]:
            with self.subTest(statuses=statuses):
                self.assertEqual(query_completion([{"status": value} for value in statuses])["status"], expected)
        # A zero-hit result is a completed query, not an execution failure.
        self.assertEqual(query_completion([rank_query(["S1"], [], 1)])["status"], "done")

    def test_baseline_errors_change_final_run_status(self) -> None:
        for status in ("failed", "partial"):
            with self.subTest(status=status):
                self.assertEqual(_final_status({}, {"cases": []}, [],
                                               {"retrieval_baselines": {"status": status}}, True, False), "partial")
        self.assertEqual(_final_status({}, {"cases": []}, [],
                                       {"retrieval_baselines": {"status": "done"}}, True, False), "done")
        self.assertEqual(_final_status({}, {"cases": []}, [], {}, True, True), "cancelled")

    def _run_retag_failure(self, folder: str, *, fail_after_write: bool) -> tuple[list, dict, dict]:
        # Platform-only doubles; no TimeIndex source is imported or patched here.
        records = [{"id": f"r{i}", "summary": f"topic{i}", "tags": ["coding"], "vector": [1.0] * 768}
                   for i in (1, 2)]
        cases = [{"id": f"S{i}", "record_id": f"r{i}", "status": "done",
                  "record": {"summary": f"topic{i}", "tags": ["coding"], "primary_app": "code"}}
                 for i in (1, 2)]
        dataset = {"cases": [{"id": f"S{i}", "title": f"topic{i}", "process": "code.exe",
                              "facts": [[f"topic{i}"]], "tags": ["coding"], "group": "G"} for i in (1, 2)]}

        class Store:
            def get_count(self):
                return len(records)

            def get_pending_retag(self, _count):
                return copy.deepcopy(records)

            def update_retag_records(self, updated):
                for row in updated:
                    next(item for item in records if item["id"] == row["id"]).update(row)
                if fail_after_write and updated[0]["id"] == "r2":
                    raise RuntimeError("fixture write failed after saving")
                return len(updated)

        class Processor:
            def retag_cluster(self, batch):
                if not fail_after_write and batch[0]["id"] == "r2":
                    raise RuntimeError("fixture second batch failed")
                return [{**row, "refined_summary": row["summary"], "refined_tags": ["coding"], "cluster_id": "G"}
                        for row in batch]

        daemon = SimpleNamespace(db_store=Store(), llm_processor=Processor(), _retag_batch_size=1)
        trace = SimpleNamespace(calls=[], scope=lambda **kwargs: nullcontext())
        sections = {}
        directory = Path(folder)
        with patch("platform_core.worker.run_path", return_value=directory), \
                patch("platform_core.worker._records", side_effect=lambda _daemon: copy.deepcopy(records)), \
                patch("platform_core.worker.section", side_effect=lambda run, name, status, reason=None:
                      sections.update({name: {"status": status, "reason": reason}})):
            _organize_records("fixture", daemon, dataset, cases, {"r1": "S1", "r2": "S2"}, trace)
        batches = read_json(directory / "evidence" / "retag_batches.json")["batches"]
        expected = 2 if fail_after_write else 1
        self.assertEqual(summarize_cases(dataset, cases)["retag_complete"], expected)
        self.assertEqual(read_json(directory / "evidence" / "cases.json"), cases)
        self.assertEqual([batch["status"] for batch in batches], ["done", "failed"])
        self.assertEqual(batches[0]["written"], 1)
        self.assertNotIn("written", batches[1])  # A raised update has no known operation count.
        self.assertEqual(sections["organization"]["status"], "failed")
        return cases, batches[1], sections

    def test_later_batch_error_preserves_earlier_success_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".failure-test-") as folder:
            _, failed, _ = self._run_retag_failure(folder, fail_after_write=False)
        self.assertEqual(failed["stage"], "model")
        self.assertIn("second batch failed", failed["error"])

    def test_update_error_reads_actual_saved_results(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".failure-test-") as folder:
            _, failed, _ = self._run_retag_failure(folder, fail_after_write=True)
        self.assertEqual(failed["stage"], "database_update")
        self.assertEqual(failed["core_valid_result_count"], 1)

    def test_desktop_batch_error_keeps_previous_results_and_error_details(self) -> None:
        records = [{"id": "r1"}, {"id": "r2"}]
        examples = [{"id": "LIVE-" + row["id"], "record_id": row["id"]} for row in records]

        class Store:
            def __init__(self):
                table = SimpleNamespace(to_arrow=lambda: SimpleNamespace(to_pylist=lambda: copy.deepcopy(records)))
                self.store = SimpleNamespace(get_table=lambda: table)

            def get_count(self):
                return len(records)

            def get_pending_retag(self, _count):
                return copy.deepcopy(records)

            def update_retag_records(self, updated):
                for row in updated:
                    next(item for item in records if item["id"] == row["id"]).update(row)
                return len(updated)

        class Processor:
            def retag_cluster(self, batch):
                if batch[0]["id"] == "r2":
                    raise RuntimeError("fixture desktop second batch failed")
                return [{**row, "refined_summary": "saved", "refined_tags": ["coding"], "cluster_id": "G"}
                        for row in batch]

        daemon = SimpleNamespace(db_store=Store(), llm_processor=Processor(), _retag_batch_size=1)
        trace = SimpleNamespace(calls=[], scope=lambda **kwargs: nullcontext())
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".failure-test-") as folder:
            run = Path(folder)
            batches, error, cancelled = _organize_desktop_records(run, daemon, examples, trace)
            self.assertEqual(read_json(run / "evidence" / "desktop_batches.json"), batches)
            self.assertEqual(read_json(run / "evidence" / "live_cases.json"), examples)
        self.assertEqual(examples[0]["refined"]["refined_summary"], "saved")
        self.assertIsNone(examples[1]["refined"]["refined_summary"])
        self.assertEqual([batch["status"] for batch in batches], ["done", "failed"])
        self.assertIn("desktop second batch failed", error)
        self.assertFalse(cancelled)


if __name__ == "__main__":
    unittest.main()
