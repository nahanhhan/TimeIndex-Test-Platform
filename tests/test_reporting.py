from __future__ import annotations

import unittest

from platform_core.common import atomic_json, read_json
from platform_core.manifest import create_run, run_path, section
from platform_core.reporting import _metric_rows, calculate, export
from platform_core.details import hide_uncontrolled_windows
from platform_core.scoring import rank_query


class ReportRegressionTests(unittest.TestCase):
    def test_incomplete_retag_and_bad_vectors_do_not_look_successful(self) -> None:
        manifest = create_run("quick")
        run_id = manifest["run_id"]
        directory = run_path(run_id)
        selected = read_json(directory / "dataset.json")["cases"]
        cases = [{"id": item["id"], "status": "done",
                  "record": {"summary": item["title"], "tags": item["tags"], "primary_app": item["process"]},
                  "refined": {"refined_summary": item["title"], "refined_tags": item["tags"], "cluster_id": "a"}}
                 for item in selected]
        cases[-1]["refined"] = {"refined_summary": "NaN", "refined_tags": None, "cluster_id": "NaN"}
        atomic_json(directory / "evidence" / "cases.json", cases)
        atomic_json(directory / "evidence" / "vector_integrity.json",
                    {"status": "failed", "total": 8, "valid": 0, "issues": []})
        atomic_json(directory / "evidence" / "queries.json",
                    {"semantic": [{"id": read_json(directory / "dataset.json")["queries"][0]["id"], "status": "done", "hit_at_1": 1, "hit_at_5": 1,
                                   "mrr_at_5": 1.0, "latency_ms": 2.0}]})
        section(run_id, "organization", "done")
        result = calculate(run_id)
        self.assertEqual(result["sections"]["organization"]["status"], "partial")
        self.assertEqual(result["organization"]["retag_complete"], 7)
        self.assertIsNone(result["retrieval"]["semantic"]["hit_at_5"])
        self.assertEqual(result["retrieval"]["semantic"]["not_measured"], 4)
        rows = {row["项目"]: row for row in _metric_rows(result)}
        self.assertEqual(rows["整理后固定词表标签 F1（辅助诊断）"]["样本量"], 8)
        self.assertEqual(rows["共同完成样本整理后标签 F1"]["样本量"], 7)
        self.assertEqual(rows["semantic hit_at_5"]["样本量"], 0)
        self.assertTrue(any(row["kind"] == "organization" for row in result["failures"]))

    def test_incomplete_query_totals_and_errors_preserve_saved_evidence(self) -> None:
        directory = run_path(create_run("quick")["run_id"])
        dataset = read_json(directory / "dataset.json")
        first = dataset["queries"][0]
        queries = {"keyword": [{"id": first["id"], **rank_query(first["relevant_ids"], first["relevant_ids"], 1)}],
                   "tags": [{"id": first["id"], **rank_query(first["relevant_ids"], [], 2, "fixture tag error")}]}
        path = directory / "evidence" / "queries.json"
        atomic_json(path, queries)
        before = path.read_bytes()
        section(directory.name, "retrieval_baselines", "done")
        result = calculate(directory.name)
        self.assertEqual((result["retrieval"]["keyword"]["total"], result["retrieval"]["keyword"]["scored"],
                          result["retrieval"]["keyword"]["not_measured"]), (4, 1, 3))
        self.assertEqual(result["retrieval"]["keyword"]["hit_at_5"], 1)
        self.assertEqual(result["sections"]["retrieval_baselines"]["status"], "partial")
        self.assertEqual(result["retrieval"]["tags"]["errors"], 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(any(row["reason"] == "fixture tag error" for row in result["failures"]))
        output = export(directory.name)
        self.assertIn("1 / 4", output["html"].read_text(encoding="utf-8"))
        self.assertEqual(read_json(output["details"])["queries"], queries)
        self.assertEqual(path.read_bytes(), before)

    def test_organization_error_is_kept_alongside_successful_fields(self) -> None:
        directory = run_path(create_run("quick")["run_id"])
        selected = read_json(directory / "dataset.json")["cases"]
        cases = [{"id": row["id"], "status": "done", "record": {"summary": row["title"], "tags": row["tags"]}}
                 for row in selected]
        cases[0]["refined"] = {"refined_summary": "saved summary", "refined_tags": ["coding"], "cluster_id": "G"}
        atomic_json(directory / "evidence" / "cases.json", cases)
        atomic_json(directory / "evidence" / "retag_batches.json", {"batches": [
            {"id": "B1", "input_ids": [cases[0]["id"]], "status": "done", "written": 1},
            {"id": "B2", "input_ids": [cases[1]["id"]], "status": "failed", "stage": "model", "error": "fixture batch error"}]})
        section(directory.name, "organization", "failed", "fixture batch error")
        result = calculate(directory.name)
        self.assertEqual(result["organization"]["retag_complete"], 1)
        self.assertIn("fixture batch error", result["sections"]["organization"]["reason"])
        self.assertTrue(any(row["kind"] == "organization_batch" and row["reason"] == "fixture batch error"
                            for row in result["failures"]))
        self.assertTrue(any(row["code"] == "retag_batch_error" for row in result["diagnostics"]["findings"]))

    def test_report_shows_inputs_original_reply_and_legacy_evidence_limits(self) -> None:
        manifest = create_run("quick")
        directory = run_path(manifest["run_id"])
        expected = read_json(directory / "dataset.json")["cases"][0]
        example = {"id": expected["id"], "status": "done", "input": {"windows": [
            {"title": expected["title"], "process_name": expected["process"], "pid": 10000}]},
                   "intent": {"summary": "parsed summary"}, "record": {"summary": "parsed summary", "tags": []}}
        atomic_json(directory / "evidence" / "cases.json", [example])
        legacy = export(manifest["run_id"])
        self.assertIn("旧证据未保存", legacy["html"].read_text(encoding="utf-8"))
        atomic_json(directory / "evidence" / "model_calls.json", {"calls": [{"id": "M0001", "operation": "chat",
            "context": {"phase": "合成记录", "case_id": expected["id"]}, "status": "done",
            "request": {"model": "requested", "messages": [{"role": "system", "content": "SYSTEM_AUDIT_LINE"}]},
            "response": {"model": "returned", "choices": [{"message": {"content": '<script>RAW_AUDIT_LINE</script>'}}]}}]})
        actual = export(manifest["run_id"])
        text = actual["html"].read_text(encoding="utf-8")
        self.assertIn("SYSTEM_AUDIT_LINE", text)
        self.assertIn("RAW_AUDIT_LINE", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn("TEST-SECRET", text)
        self.assertIn("没有实际打开业务软件", text)
        details = read_json(actual["details"])
        self.assertEqual(details["cases"][0]["model_call_ids"], ["M0001"])
        self.assertFalse(details["synthetic_software"][0]["actually_launched"])

    def test_uncontrolled_titles_with_quotes_are_hidden_in_valid_json(self) -> None:
        import json
        directory = run_path(create_run("quick")["run_id"])
        title = '私人资料 "张三" - 浏览器'
        atomic_json(directory / "evidence" / "live_raw.json", {"snapshots": [{"windows": [{"title": title}]}]})
        sanitized = hide_uncontrolled_windows(json.dumps({"title": title}, ensure_ascii=False), directory,
                                               {"desktop_software": []})
        self.assertEqual(json.loads(sanitized)["title"], "[非测试窗口标题已隐藏]")


if __name__ == "__main__":
    unittest.main()
