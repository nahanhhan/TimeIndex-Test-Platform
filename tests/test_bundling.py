from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from platform_core.bundling import build_report_zip, bundle_signature
from platform_core.common import ROOT, atomic_json
from platform_core.dataset import build_default, select


class BundleTests(unittest.TestCase):
    def test_saved_results_and_nested_titles_are_sanitized_without_changing_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".bundle-test-") as folder:
            directory = Path(folder)
            run_id = "bundle-fixture"
            dataset = select(build_default(), "quick")
            private_title = '私人资料 "张三" - 浏览器'
            title = dataset["cases"][0]["title"]
            atomic_json(directory / "dataset.json", dataset)
            atomic_json(directory / "manifest.json", {"run_id": run_id, "status": "done",
                        "source_sha256": "original-source", "api_key": "never-export-secret"})
            record = {"summary": "已保存的摘要", "vector": [1.0] * 768,
                      "active_windows": [json.dumps({"title": private_title}, ensure_ascii=False)]}
            atomic_json(directory / "evidence" / "cases.json", [{"id": "S01-01", "record": record,
                        "input": {"windows": [{"title": title}, {"title": private_title}]}}])
            atomic_json(directory / "evidence" / "model_calls.json", {"calls": [{"id": "M0001",
                        "request": {"messages": [{"content": private_title}],
                                    "extra_headers": {"Authorization": "never-export-secret"}},
                        "response": {"choices": [{"message": {"content": json.dumps(
                            {"summary": private_title}, ensure_ascii=False)}}]}}]})
            atomic_json(directory / "evidence" / "live_raw.json", {"snapshots": [{"windows": [
                        {"title": private_title}, {"title": "TI-CONTROLLED 测试窗口"}]}]})
            saved_title = title.replace(dataset["cases"][0]["canary"], "[测试敏感串-999]")
            atomic_json(directory / "reports" / "details.json", {"desktop_software": [{"marker": "TI-CONTROLLED"}],
                        "cases": [{"id": "S01-01", "input": {"windows": [{"title": saved_title}]}}]})
            atomic_json(directory / "reports" / "summary.json", {"run_id": run_id, "score": 0.25})
            (directory / "reports" / "report.html").write_text("<p>" + title + "</p>", encoding="utf-8")
            (directory / "reports" / "paper_metrics.csv").write_text("项目,值\n覆盖率,0.25\n", encoding="utf-8")
            atomic_json(directory / "runtime" / "profile" / "config.json", {"api_key": "never-export-secret"})
            (directory / "worker.log").write_text("never-export-secret", encoding="utf-8")
            atomic_json(directory / "evidence" / "unlisted.json", {"private": "never-export-secret"})
            before = {path.relative_to(directory).as_posix(): path.read_bytes()
                      for path in directory.rglob("*") if path.is_file()}
            with patch("platform_core.bundling.run_path", return_value=directory):
                signature = bundle_signature(run_id)
                data = build_report_zip(run_id)
                self.assertEqual(bundle_signature(run_id), signature)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())
                names = archive.namelist()
                self.assertTrue({"manifest.json", "dataset.json", "reports/report.html", "reports/details.json",
                                 "reports/summary.json", "reports/paper_metrics.csv", "evidence/cases.json",
                                 "evidence/model_calls.json", "bundle_manifest.json", "README.txt"}.issubset(names))
                for name in names:
                    text = archive.read(name).decode("utf-8")
                    self.assertNotIn("TEST-SECRET", text)
                    self.assertNotIn("never-export-secret", text)
                    self.assertNotIn("私人资料", text)
                self.assertNotIn("worker.log", names)
                self.assertNotIn("evidence/unlisted.json", names)
                self.assertFalse(any(name.startswith("runtime/") for name in names))
                stored = json.loads(archive.read("evidence/cases.json"))
                self.assertEqual(stored[0]["input"]["windows"][0]["title"], saved_title)
                self.assertEqual(json.loads(archive.read("dataset.json"))["cases"][0]["canary"], "[测试敏感串-999]")
                self.assertEqual(stored[0]["record"]["vector"], record["vector"])
                embedded = json.loads(stored[0]["record"]["active_windows"][0])
                self.assertEqual(embedded["title"], "[非测试窗口标题已隐藏]")
                calls = json.loads(archive.read("evidence/model_calls.json"))["calls"]
                reply = json.loads(calls[0]["response"]["choices"][0]["message"]["content"])
                self.assertEqual(reply["summary"], "[非测试窗口标题已隐藏]")
                live = json.loads(archive.read("evidence/live_raw.json"))
                self.assertEqual(live["snapshots"][0]["windows"][1]["title"], "TI-CONTROLLED 测试窗口")
                self.assertEqual(json.loads(archive.read("reports/summary.json"))["score"], 0.25)
                self.assertEqual(json.loads(archive.read("manifest.json"))["source_sha256"], "original-source")
                for item in json.loads(archive.read("bundle_manifest.json"))["files"]:
                    contents = archive.read(item["path"])
                    self.assertEqual(hashlib.sha256(contents).hexdigest(), item["sha256"])
                    self.assertEqual(len(contents), item["bytes"])
            after = {path.relative_to(directory).as_posix(): path.read_bytes()
                     for path in directory.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_partial_legacy_run_lists_missing_reports_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".bundle-test-") as folder:
            directory = Path(folder)
            atomic_json(directory / "dataset.json", select(build_default(), "quick"))
            atomic_json(directory / "manifest.json", {"run_id": "legacy-fixture", "status": "partial"})
            (directory / "reports").mkdir()
            (directory / "reports" / "report.html").write_text("<p>旧报告</p>", encoding="utf-8")
            with patch("platform_core.bundling.run_path", return_value=directory):
                data = build_report_zip("legacy-fixture")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                index = json.loads(archive.read("bundle_manifest.json"))
                self.assertEqual(index["run_status"], "partial")
                self.assertIn("reports/details.json", index["missing_reports"])
                self.assertIn("evidence/model_calls.json", index["missing_evidence"])
                self.assertNotIn("evidence/model_calls.json", archive.namelist())

    def test_active_run_cannot_export_an_inconsistent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".bundle-test-") as folder:
            directory = Path(folder)
            atomic_json(directory / "dataset.json", select(build_default(), "quick"))
            atomic_json(directory / "manifest.json", {"run_id": "active-fixture", "status": "running"})
            with patch("platform_core.bundling.run_path", return_value=directory):
                with self.assertRaisesRegex(ValueError, "等待本轮实验结束"):
                    build_report_zip("active-fixture")

    def test_startup_failure_can_export_its_error_without_completed_reports(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".bundle-test-") as folder:
            directory = Path(folder)
            atomic_json(directory / "dataset.json", select(build_default(), "quick"))
            atomic_json(directory / "manifest.json", {"run_id": "failed-fixture", "status": "failed",
                        "error": "模型服务不可用"})
            atomic_json(directory / "evidence" / "preflight.json", {"model_reachable": False})
            with patch("platform_core.bundling.run_path", return_value=directory):
                data = build_report_zip("failed-fixture")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json"))["error"], "模型服务不可用")
                self.assertEqual(len(json.loads(archive.read("bundle_manifest.json"))["missing_reports"]), 4)
                self.assertFalse(json.loads(archive.read("evidence/preflight.json"))["model_reachable"])

    def test_dashboard_offers_a_zip_download_for_a_completed_run(self) -> None:
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".bundle-test-") as folder:
            platform = Path(folder)
            run_id = "ui-bundle-fixture"
            directory = platform / "runs" / run_id
            manifest = {"run_id": run_id, "status": "done", "mode": "quick", "completed_cases": 8,
                        "total_cases": 8, "sections": {}}
            atomic_json(directory / "dataset.json", select(build_default(), "quick"))
            atomic_json(directory / "manifest.json", manifest)
            atomic_json(directory / "reports" / "summary.json", {"run_id": run_id})
            with patch("platform_core.common.ROOT", platform), \
                    patch("platform_core.manifest.list_runs", return_value=[manifest]), \
                    patch("platform_core.controller.status", return_value=manifest), \
                    patch("platform_core.bundling.run_path", return_value=directory):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                self.assertFalse(app.error)
                self.assertTrue(any(element.proto.label == "一键下载诊断报告（ZIP）"
                                    for element in app.get("download_button")))


if __name__ == "__main__":
    unittest.main()
