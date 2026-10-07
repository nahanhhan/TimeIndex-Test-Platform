"""New isolated runs for aligned reporting, mock repair checks and slow requests."""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.common import digest_source, read_json
from platform_core.controller import cancel, start, status
from platform_core.manifest import run_path
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


class ScenarioHandler(Handler):
    def _json(self, value):
        if value.get("choices"):
            message = value["choices"][0]["message"]
            answer = json.loads(message["content"])
            if isinstance(answer, dict) and "Excel" in answer.get("summary", ""):
                answer["tags"] = ["data"]
                message["content"] = json.dumps(answer, ensure_ascii=False)
            if isinstance(answer, dict) and answer.get("summary") != "正在查看 测试活动":
                self.server.formal_calls += 1
                if self.server.slow and self.server.formal_calls >= 3:
                    time.sleep(4)
        try:
            super()._json(value)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def wait_finished(run_id, limit=90):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        current = status(run_id)
        if current["status"] != "running":
            # Terminal state can be published before the final partial report export.
            while time.monotonic() < deadline:
                if not (run_path(run_id) / "runtime").exists() and (run_path(run_id) / "reports" / "report.html").exists():
                    return current
                time.sleep(0.1)
            break
        time.sleep(0.1)
    raise TimeoutError(f"run did not finish: {run_id}")


def main():
    before = digest_source(), database_fingerprint()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScenarioHandler)
    server.formal_calls, server.slow = 0, False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    active = None
    try:
        options = {"endpoint": f"http://127.0.0.1:{server.server_port}/v1", "model": "fixture-model",
                   "embedding_model": "fixture-model"}
        active = start("quick", **options, verify_core_repairs=True)["run_id"]
        assert wait_finished(active)["status"] == "done"
        directory = run_path(active)
        summary = read_json(directory / "reports" / "summary.json")
        evidence = read_json(directory / "evidence" / "queries.json")
        excel = next(row for row in evidence["tags"] if row["id"] == "Q05-01")
        assert "data" in excel["tags_requested"] and excel["hit_at_1"] == 1
        assert all(not row["model_call_ids"] for method in ("keyword", "tags") for row in evidence[method])
        assert all(row["model_call_ids"] for row in evidence["semantic"])
        checks = summary["core_repair_checks"]["checks"]
        assert all(checks[key]["status"] == "passed" for key in ("numeric_ids", "code_block", "failed_update")), checks
        assert checks["blacklist"]["status"] == "not_measured"
        assert len(read_json(directory / "evidence" / "model_calls.json")["calls"]) == 23
        assert len(read_json(directory / "evidence" / "core_repairs_calls.json")["calls"]) == 2
        print(f"aligned data, method associations and isolated core repair checks verified: {active}")

        server.formal_calls, server.slow = 0, True
        active = start("quick", **options, model_timeout_s=0.6)["run_id"]
        assert wait_finished(active)["status"] == "partial"
        directory = run_path(active)
        cases = read_json(directory / "evidence" / "cases.json")
        assert len(cases) == 2 and all(row["status"] == "done" for row in cases), cases
        calls = read_json(directory / "evidence" / "model_calls.json")["calls"]
        assert calls[-1]["error_kind"] == "timeout" and calls[-1]["response"] is None
        report = read_json(directory / "reports" / "summary.json")
        assert report["organization"]["cases_done"] == 2
        assert report["sections"]["organization"]["status"] == "not_measured"
        assert read_json(directory / "evidence" / "model_wait.json")["status"] == "timeout"
        print(f"slow response timeout retained two completed records and partial report: {active}")

        server.formal_calls = 0
        active = start("quick", **options, model_timeout_s=10)["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = status(active)
            if current.get("completed_cases") == 2 and (current.get("current_model_call") or {}).get("phase") == "合成记录":
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("cancel fixture never entered model wait")
        assert cancel(active)["status"] == "cancelled"
        assert wait_finished(active)["status"] == "cancelled"
        directory = run_path(active)
        assert len(read_json(directory / "evidence" / "cases.json")) == 2
        assert read_json(directory / "reports" / "summary.json")["organization"]["cases_done"] == 2
        print(f"cancel during model wait retained completed records and partial report: {active}")
        assert (digest_source(), database_fingerprint()) == before
    finally:
        if active and status(active)["status"] == "running":
            cancel(active)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
