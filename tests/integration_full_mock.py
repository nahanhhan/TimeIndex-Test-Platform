"""Run explicitly: python -m tests.integration_full_mock"""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.common import digest_source
from platform_core.controller import start, status
from platform_core.manifest import run_path
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source_before = digest_source()
    database_before = database_fingerprint()
    try:
        manifest = start("full", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="fixture-model", resources=True)
        run_id = manifest["run_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("完整模拟批次未结束")
        assert current["status"] == "done", current
        run = run_path(run_id)
        cases = json.loads((run / "evidence" / "cases.json").read_text(encoding="utf-8"))
        report = json.loads((run / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert len(cases) == 60
        assert report["retrieval"]["semantic"]["total"] == 30
        assert report["retrieval"]["keyword"]["total"] == 30
        assert report["retrieval"]["tags"]["total"] == 30
        assert report["resources"]["status"] == "done"
        assert report["resources"]["active"]["timeindex"]["peak_cpu_percent_of_one_core"] > 0
        assert current["sections"]["fallback"]["status"] == "done"
        assert report["organization"]["retag_complete"] == 60
        assert report["vector_integrity"]["valid"] == 60
        assert report["vector_integrity"]["preserved"] == 60
        details = json.loads((run / "reports" / "details.json").read_text(encoding="utf-8"))
        assert all(item["model_call_ids"] and item["raw_response_available"] for item in details["cases"])
        assert len(details["synthetic_software"]) == 6
        assert not any(item["actually_launched"] for item in details["synthetic_software"])
        assert all(call.get("request") and call.get("response") for call in details["model_calls"] if call["status"] == "done")
        assert len(details["batches"]["batches"]) == 3  # Real TimeIndex default: 20 records per call.
        assert all("returned_records" in row for rows in details["queries"].values() for row in rows)
        for filename in ("report.html", "details.json", "summary.json", "paper_metrics.csv"):
            assert "TEST-SECRET" not in (run / "reports" / filename).read_text(encoding="utf-8")
        assert (run / "evidence" / "timeindex_db").exists()
        assert not (run / "runtime").exists()
        assert digest_source() == source_before
        assert database_fingerprint() == database_before
        print(f"full mock batch verified: {run_id}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
