from __future__ import annotations

import unittest

from platform_core.common import atomic_json, read_json
from platform_core.manifest import create_run, run_path, section
from platform_core.reporting import _metric_rows, calculate, export
from platform_core.details import hide_uncontrolled_windows


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
                    {"semantic": [{"id": "q", "status": "done", "hit_at_1": 1, "hit_at_5": 1,
                                   "mrr_at_5": 1.0, "latency_ms": 2.0}]})
        section(run_id, "organization", "done")
        result = calculate(run_id)
        self.assertEqual(result["sections"]["organization"]["status"], "partial")
        self.assertEqual(result["organization"]["retag_complete"], 7)
        self.assertIsNone(result["retrieval"]["semantic"]["hit_at_5"])
        self.assertEqual(result["retrieval"]["semantic"]["not_measured"], 1)
        rows = {row["项目"]: row for row in _metric_rows(result)}
        self.assertEqual(rows["整理后固定词表标签 F1（辅助诊断）"]["样本量"], 8)
        self.assertEqual(rows["共同完成样本整理后标签 F1"]["样本量"], 7)
        self.assertEqual(rows["semantic hit_at_5"]["样本量"], 0)
        self.assertTrue(any(row["kind"] == "organization" for row in result["failures"]))

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
