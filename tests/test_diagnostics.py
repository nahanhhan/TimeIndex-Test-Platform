from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_core.common import ROOT, atomic_json, read_json
from platform_core.dataset import build_default, select
from platform_core.diagnostics import batch_diagnostics, diagnose
from platform_core.paper import keyword_match, retrieval_summary
from platform_core.report_html import diagnostic_section, query_cards, tag_text
from platform_core.reporting import export


def fixture() -> dict:
    cases = [{"id": "S01-01", "record_id": "1791202814.105918", "status": "done",
              "record": {"summary": "Python函数第1节", "tags": ["coding"], "primary_app": "Code"},
              "refined": {"refined_summary": "NaN", "refined_tags": None, "cluster_id": "NaN"}}]
    returned = [{"id": 1791202814.105918, "refined_summary": "Python函数第1节",
                 "refined_tags": ["coding"], "cluster_id": 1}]
    calls = [{"id": "M1", "operation": "chat", "status": "done", "context": {"batch_id": "B001"},
              "request": {"model": "fixture", "messages": []}, "response": {"choices": [
                  {"message": {"content": json.dumps(returned)}}]}}]
    batch = {"id": "B001", "input_ids": ["S01-01"], "returned_ids": ["S01-01"], "written": 1}
    return {"cases": cases, "model_calls": calls, "batches": {"batches": [batch]},
            "fault_injection": {"status": "done", "recorded": False}}


class DiagnosticTests(unittest.TestCase):
    def test_numeric_ids_and_update_count_do_not_become_successful_retag(self) -> None:
        details = fixture()
        before = copy.deepcopy(details)
        result = diagnose(details, {"status": "not_measured"})
        batch = result["organization_batches"][0]
        self.assertEqual((batch["model_response_count"], batch["model_valid_fields_count"],
                          batch["core_returned_count"], batch["database_update_count"], batch["persisted_valid_count"]), (1, 1, 1, 1, 0))
        self.assertEqual((batch["exact_id_matches"], batch["text_id_matches"]), (0, 1))
        self.assertEqual({row["code"] for row in result["findings"]},
                         {"retag_id_types", "retag_incomplete", "invalid_refined_summary", "fallback_not_recorded"})
        self.assertEqual(details, before)

    def test_core_that_handles_numeric_ids_is_not_reported_as_failed(self) -> None:
        details = fixture()
        details["cases"][0]["refined"] = {"refined_summary": "Python函数", "refined_tags": [], "cluster_id": "1"}
        details["fault_injection"]["recorded"] = True
        result = diagnose(details, {"status": "done"})
        self.assertEqual(result["organization_batches"][0]["persisted_valid_count"], 1)
        self.assertEqual([(row["code"], row["severity"]) for row in result["findings"]], [("retag_id_types", "info")])

    def test_missing_reply_is_unknown_and_number_precision_is_not_invented(self) -> None:
        details = fixture()
        batch = details["batches"]["batches"][0]
        result = batch_diagnostics(batch, details["cases"], [], "合成实验")
        self.assertIsNone(result["model_response_count"])
        self.assertIsNone(result["exact_id_matches"])
        details["cases"][0]["record_id"] = "9007199254740993"
        details["model_calls"][0]["response"]["choices"][0]["message"]["content"] = '[{"id": 9007199254740992.0}]'
        self.assertEqual(batch_diagnostics(batch, details["cases"], details["model_calls"], "合成实验")["text_id_matches"], 0)

    def test_successful_embeddings_and_overwritten_vectors_are_distinguished(self) -> None:
        details = fixture()
        details["model_calls"].append({"id": "M2", "operation": "embedding", "status": "done",
            "context": {"phase": "合成记录"}, "response": {"data": [{"embedding": [0.25] * 768}]}})
        vectors = {"status": "failed", "total": 1, "valid": 0, "preserved": 0, "dimension": 768,
                   "issues": [{"id": "S01-01", "reasons": ["检索向量全部为零", "整理写回改变了原始检索向量"]}]}
        result = diagnose(details, vectors)
        item = next(row for row in result["findings"] if row["code"] == "vector_integrity")
        self.assertEqual(item["evidence"]["valid_embedding_responses"], 1)
        self.assertIn("未测", item["title"])
        html = diagnostic_section({"diagnostics": result})
        self.assertIn("数据库更新操作数", html)
        self.assertIn("最终有效整理数", html)

    def test_keyword_baseline_falls_back_only_for_invalid_refined_text(self) -> None:
        query = {"keyword": "实验报告"}
        for invalid in (None, "NaN", " nan ", "null", "NONE", "", float("nan")):
            record = {"summary": "编辑实验报告", "refined_summary": invalid}
            self.assertTrue(keyword_match(record, query))
            self.assertEqual(retrieval_summary(record), record["summary"])
        self.assertFalse(keyword_match({"summary": "实验报告", "refined_summary": "有效但不相关的摘要"}, query))
        self.assertFalse(keyword_match({"summary": "NaN", "refined_summary": None}, query))

    def test_string_tags_keep_their_original_representation(self) -> None:
        value = "['coding' 'writing']"
        self.assertEqual(tag_text(value), value)
        self.assertEqual(tag_text(["coding", "writing"]), "coding, writing")
        html = query_cards({"tags": [{"id": "q", "query": "test", "returned_records": [
            {"id": "1", "tags": value, "summary": "<script>unsafe</script>"}]}]})
        self.assertIn("[&#x27;coding&#x27; &#x27;writing&#x27;]", html)
        self.assertNotIn("<script>", html)

    def test_replay_preserves_historical_queries_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".diagnostic-test-") as folder:
            directory = Path(folder).resolve()
            self.assertTrue(directory.is_relative_to(ROOT))
            details = fixture()
            atomic_json(directory / "manifest.json", {"run_id": "fixture", "mode": "quick", "scoring_version": "4.0", "sections": {}})
            atomic_json(directory / "dataset.json", select(build_default(), "quick"))
            for name, value in (("cases.json", details["cases"]), ("model_calls.json", {"calls": details["model_calls"]}),
                                ("retag_batches.json", details["batches"]), ("fallback.json", details["fault_injection"]),
                                ("vector_integrity.json", {"status": "not_measured"}),
                                ("queries.json", {"keyword": [{"id": "q", "status": "done", "hit_at_1": 0, "hit_at_5": 0, "mrr_at_5": 0, "latency_ms": 1}]})):
                atomic_json(directory / "evidence" / name, value)
            before = {p: p.read_bytes() for p in (directory / "evidence").iterdir()}
            with patch("platform_core.reporting.run_path", return_value=directory):
                paths = export("fixture")
            result = read_json(paths["summary"])
            self.assertEqual((result["original_scoring_version"], result["scoring_version"]), ("4.0", "4.1"))
            self.assertEqual(result["retrieval"]["keyword"]["hit_at_5"], 0)
            self.assertEqual(result["sections"]["fallback"]["status"], "failed")
            self.assertEqual(read_json(paths["details"])["diagnostics"], result["diagnostics"])
            self.assertEqual(before, {p: p.read_bytes() for p in (directory / "evidence").iterdir()})


if __name__ == "__main__":
    unittest.main()
