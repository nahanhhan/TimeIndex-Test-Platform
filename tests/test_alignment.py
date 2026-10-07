from __future__ import annotations

import copy
import unittest

from platform_core.common import atomic_json, read_json
from platform_core.dataset import DEFAULT_DATASET, PAPER_DATASET, build_default, validate_dataset
from platform_core.details import associate_queries
from platform_core.manifest import create_run, run_path, section
from platform_core.paper_dataset import build_paper
from platform_core.reporting import _metric_rows, export
from platform_core.scoring import rank_query
from platform_core.privacy import blacklist_verification


class AlignmentTests(unittest.TestCase):
    def test_generated_and_frozen_data_include_data_without_forcing_output_tags(self):
        for path, build, version in [(DEFAULT_DATASET, build_default, "2.1"), (PAPER_DATASET, build_paper, "paper-1.1")]:
            actual = read_json(path)
            validate_dataset(actual)
            self.assertEqual(actual, build())
            self.assertEqual(actual["version"], version)
            analysis = {row["id"] for row in actual["cases"] if "analysis" in row["tags"]}
            for row in actual["queries"]:
                if analysis.intersection(row["relevant_ids"]):
                    self.assertIn("data", row["tags"])
            self.assertTrue(all(row["tags"] == ["analysis"] for row in actual["cases"] if row["id"] in analysis))

    def test_query_association_requires_query_id_and_method_and_keeps_saved_evidence(self):
        calls = [{"id": "M1", "context": {"query_id": "Q1", "method": "semantic"}},
                 {"id": "M2", "context": {"query_id": "Q2", "method": "semantic"}},
                 {"id": "M3", "context": {"query_id": "Q1"}}]
        queries = {method: [{"id": "Q1", "model_call_ids": ["M1"]}] for method in ("semantic", "keyword", "tags", "time")}
        before = copy.deepcopy((queries, calls))
        result = associate_queries(queries, calls)
        self.assertEqual(result["semantic"][0]["model_call_ids"], ["M1"])
        for method in ("keyword", "tags", "time"):
            self.assertEqual(result[method][0]["model_call_ids"], [])
            self.assertEqual(result[method][0]["saved_model_call_ids"], ["M1"])
            self.assertIn("未调用模型", result[method][0]["model_call_note"])
        self.assertEqual((queries, calls), before)

    def test_coarse_summaries_and_zero_hit_queries_are_observations_not_execution_failures(self):
        directory = run_path(create_run("quick")["run_id"])
        dataset = read_json(directory / "dataset.json")
        cases = [{"id": row["id"], "status": "done",
                  "record": {"summary": "Python", "tags": ["free-cue"], "primary_app": "different name"},
                  "refined": {"refined_summary": "Python", "refined_tags": ["free-cue"], "cluster_id": "coarse"}}
                 for row in dataset["cases"]]
        queries = {method: [{"id": q["id"], **rank_query(q["relevant_ids"], [], 1)} for q in dataset["queries"]]
                   for method in ("semantic", "keyword", "tags")}
        atomic_json(directory / "evidence" / "cases.json", cases)
        atomic_json(directory / "evidence" / "queries.json", queries)
        atomic_json(directory / "evidence" / "vector_integrity.json", {"status": "done", "total": 8, "valid": 8, "preserved": 8, "issues": []})
        section(directory.name, "organization", "done")
        before = {path.name: path.read_bytes() for path in (directory / "evidence").glob("*.json")}
        paths = export(directory.name)
        summary = read_json(paths["summary"])
        self.assertEqual(summary["failures"], [])
        self.assertTrue(summary["quality_observations"])
        self.assertEqual(summary["organization"]["details_before"]["coverage"], 0)
        self.assertEqual(summary["sections"]["retrieval_baselines"]["status"], "done")
        self.assertEqual(summary["core_repair_checks"]["status"], "not_measured")
        metrics = {row["项目"]: row for row in _metric_rows(summary)}
        self.assertIn("不作为本体及格标准", metrics["摘要编号保留率"]["条件"])
        self.assertIn("平台摘要关键词对照", metrics["keyword hit_at_5"]["条件"])
        html = paths["html"].read_text(encoding="utf-8")
        self.assertIn("流程完成", html)
        self.assertIn("未测 / 未验证", html)
        self.assertIn("不能标作本体关键词接口成绩", html)
        self.assertEqual(before, {path.name: path.read_bytes() for path in (directory / "evidence").glob("*.json")})

    def test_timeout_setting_rejects_invalid_values_before_creating_run(self):
        for value in (0, -1, float("nan"), float("inf"), True, "120"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "等待上限"):
                create_run("quick", model_timeout_s=value)

    def test_blacklist_requires_observed_window_and_event_controls(self):
        target = {"windows": [{"pid": 7, "title": "controlled"}], "process_events": [{"pid": 7}]}
        for blocked, control, expected in [([], [], "not_measured"), ([], [target], "passed"),
                                           ([target], [target], "failed")]:
            with self.subTest(expected=expected):
                result = blacklist_verification(blocked, control, 7, "controlled")
                self.assertEqual(result["window_check"]["status"], expected)
                self.assertEqual(result["event_check"]["status"], expected)
        only_window = blacklist_verification([], [{"windows": target["windows"]}], 7, "controlled")
        self.assertEqual(only_window["window_check"]["status"], "passed")
        self.assertEqual(only_window["event_check"]["status"], "not_measured")


if __name__ == "__main__":
    unittest.main()
