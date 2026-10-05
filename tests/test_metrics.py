from __future__ import annotations

import unittest

from platform_core.dataset import build_default, select, validate_dataset
from platform_core.scoring import facts, pairwise_clusters, rank_query, recording, summarize_cases, tag_totals, text_contains
from platform_core.validation import audit_vectors


class MetricTests(unittest.TestCase):
    def test_dataset_and_bad_reference(self) -> None:
        data = build_default()
        validate_dataset(data)
        self.assertEqual((len(data["cases"]), len(data["queries"])), (60, 30))
        self.assertEqual(len({row["group"] for row in select(data, "quick")["cases"]}), 6)
        data["queries"][0]["relevant_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            validate_dataset(data)

    def test_fact_tag_and_cluster_scores(self) -> None:
        self.assertEqual(facts("正在用 VS Code 编写 Python", [["VS Code", "编辑器"], ["Python"]])["coverage"], 1)
        tags = tag_totals([(["coding", "reading"], ["coding", "other"])])
        self.assertEqual((tags["tp"], tags["fp"], tags["fn"], tags["f1"]), (1, 1, 1, 0.5))
        self.assertEqual(tag_totals([(["coding"], [])])["f1"], 0)
        clusters = pairwise_clusters({"a": "one", "b": "one", "c": "two"},
                                     {"a": "x", "b": "x", "c": "y"})
        self.assertEqual(clusters["f1"], 1)
        self.assertEqual(pairwise_clusters({"a": "one"}, {"a": "x"})["status"], "not_measured")

    def test_retrieval_multiple_gold_and_miss(self) -> None:
        scored = rank_query(["b", "c"], ["x", "c", "a"], 12.5)
        self.assertEqual((scored["hit_at_1"], scored["hit_at_5"], scored["mrr_at_5"]), (0, 1, 0.5))
        self.assertEqual(rank_query(["b"], ["a", "c"], 1)["mrr_at_5"], 0)

    def test_chinese_layout_spaces_and_english_boundaries(self) -> None:
        self.assertTrue(text_contains("正在查看Python函数第 1 节", "Python 函数"))
        self.assertTrue(text_contains("正在查看Python函数第 1 节", "第1节"))
        self.assertFalse(text_contains("notebook", "note book"))
        self.assertEqual(facts("NaN", [["Python"]])["matched"], 0)

    def test_missing_refinement_keeps_comparison_population(self) -> None:
        dataset = {"cases": [{"id": item, "facts": [["Python 函数"]], "tags": ["coding"],
                               "group": "same", "process": "code.exe", "title": "Python 函数 第1节 - Code"}
                              for item in ("a", "b")]}
        rows = [{"id": item, "status": "done", "record": {"summary": "Python函数第1节", "tags": ["coding"],
                                                                "primary_app": "Code"},
                 "refined": {"refined_summary": "Python函数第1节", "refined_tags": ["coding"], "cluster_id": "x"}}
                for item in ("a", "b")]
        rows[1]["refined"] = {"refined_summary": "NaN", "refined_tags": None, "cluster_id": "NaN"}
        result = summarize_cases(dataset, rows)
        self.assertEqual((result["retag_complete"], result["retag_total"]), (1, 2))
        self.assertEqual(result["facts_before"]["total"], result["facts_after"]["total"])
        self.assertEqual(result["tags_after"]["cases"], 2)
        self.assertEqual(result["tags_after"]["fn"], 1)
        self.assertAlmostEqual(result["tags_after"]["f1"], 2 / 3)
        self.assertEqual(result["paired"]["tags_before"]["f1"], result["paired"]["tags_after"]["f1"])
        self.assertEqual(result["cluster"]["status"], "partial")
        self.assertEqual(result["issues"][0]["id"], "b")

    def test_vector_corruption_is_detected(self) -> None:
        original = [{"id": "a", "vector": [1.0] * 768}]
        self.assertEqual(audit_vectors(original, original)["status"], "done")
        for bad in ([0.0] * 768, [float("nan")] * 768, [1.0] * 2):
            result = audit_vectors(original, [{"id": "a", "vector": bad}])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["valid"], 0)

    def test_free_tags_are_not_reduced_to_canonical_vocabulary(self) -> None:
        dataset = {"cases": [{"id": "a", "facts": [["Python函数"]], "tags": ["coding"],
                               "group": "same", "process": "code.exe", "title": "Python函数 - Code"}]}
        rows = [{"id": "a", "status": "done", "record": {"summary": "Python函数", "tags": ["Python", "code"], "primary_app": "Code"},
                 "refined": {"refined_summary": "Python函数", "refined_tags": ["programming"], "cluster_id": "0"}}]
        score = summarize_cases(dataset, rows)
        self.assertEqual(score["tags_before"]["f1"], 0)
        self.assertEqual(score["tag_cues_before"]["coverage"], 1)
        self.assertEqual(score["tag_cues_after"]["coverage"], 1)

    def test_eight_case_retag_regression(self) -> None:
        dataset = {"cases": [{"id": str(i), "facts": [["Python 函数"], ["代码练习", "Code"]],
                               "tags": ["coding"], "group": "same", "process": "code.exe", "title": "Python 函数 - Code"}
                              for i in range(8)]}
        rows = [{"id": str(i), "status": "done", "record": {"summary": "Python代码", "tags":
                 ["coding", "writing"] if i == 0 else ["coding", "reading", "writing"]},
                 "refined": {"refined_summary": "Python代码", "refined_tags":
                 ["coding", "writing"] if i == 0 else ["coding", "reading", "writing"], "cluster_id": "0"}}
                for i in range(8)]
        rows[-1]["refined"] = {"refined_summary": "NaN", "refined_tags": None, "cluster_id": "NaN"}
        result = summarize_cases(dataset, rows)
        self.assertAlmostEqual(result["tags_before"]["f1"], 16 / 31)
        self.assertEqual(result["tags_after"]["f1"], 0.5)
        self.assertAlmostEqual(result["paired"]["tags_before"]["f1"], 14 / 27)
        self.assertEqual(result["paired"]["tags_before"], result["paired"]["tags_after"])

    def test_recording_miss_extra_duplicate_and_event_type(self) -> None:
        expected = [{"kind": "process", "pid": 1, "event_type": "created", "time": 100},
                    {"kind": "window", "title": "A", "time": 100},
                    {"kind": "window", "title": "B", "time": 100}]
        observed = [{"kind": "process", "pid": 1, "event_type": "created", "time": 101},
                    {"kind": "process", "pid": 1, "event_type": "exited", "time": 102},
                    {"kind": "window", "title": "A", "time": 101},
                    {"kind": "window", "title": "A", "time": 102}]
        result = recording(expected, observed)
        self.assertEqual((result["matched"], len(result["missed"]), result["duplicate_count"],
                          result["false_positive_count"]), (2, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()

