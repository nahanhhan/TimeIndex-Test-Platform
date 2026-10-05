"""Protocol fixture for diagnostics, not a real model or desktop quality experiment."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer

from platform_core.bundling import build_report_zip
from platform_core.common import atomic_json, digest_source, read_json
from platform_core.controller import cancel, start, status
from platform_core.manifest import run_path
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


class NumericIDHandler(Handler):
    def _json(self, value):
        if isinstance(value, dict) and value.get("choices"):
            message = value["choices"][0]["message"]
            parsed = json.loads(message["content"])
            if isinstance(parsed, list):
                for row in parsed:
                    row["id"] = float(row["id"])
                message["content"] = json.dumps(parsed, ensure_ascii=False)
        super()._json(value)


def main() -> None:
    before = digest_source(), database_fingerprint()
    server = ThreadingHTTPServer(("127.0.0.1", 0), NumericIDHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run_id = None
    try:
        manifest = start("quick", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="fixture-model")
        run_id = manifest["run_id"]
        directory = run_path(run_id)
        atomic_json(directory / "evidence/fixture.json", {"kind": "numeric_id_protocol_fixture", "research_results": False})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("诊断模拟实验未结束")
        assert current["status"] in {"done", "partial"}, current
        summary = read_json(directory / "reports/summary.json")
        batch = summary["diagnostics"]["organization_batches"][0]
        assert (batch["model_response_count"], batch["exact_id_matches"], batch["text_id_matches"]) == (8, 0, 8), batch
        assert "core_valid_result_count" in read_json(directory / "evidence/retag_batches.json")["batches"][0]
        if summary["vector_integrity"]["status"] != "done":
            queries = read_json(directory / "evidence/queries.json")["semantic"]
            assert all(row["status"] == "not_measured" for row in queries)
            assert summary["retrieval"]["semantic"]["scored"] == 0
        if any(row["code"] == "invalid_refined_summary" for row in summary["diagnostics"]["findings"]):
            assert summary["retrieval"]["keyword"]["hit_at_5"] == 1
        with zipfile.ZipFile(io.BytesIO(build_report_zip(run_id))) as archive:
            assert json.loads(archive.read("reports/details.json"))["diagnostics"] == summary["diagnostics"]
            assert "请求完成与结果有效分开看" in archive.read("reports/report.html").decode("utf-8")
        assert (digest_source(), database_fingerprint()) == before
        print(f"numeric IDs, keyword fallback, query status and diagnostic ZIP verified: {run_id}")
    finally:
        if run_id and status(run_id)["status"] == "running":
            cancel(run_id)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
