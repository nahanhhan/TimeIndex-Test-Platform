"""Validate paper-mode plumbing with a local mock; scores are not research results."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer

from platform_core.bundling import PAPER_FILES, build_report_zip
from platform_core.common import atomic_json, digest_source
from platform_core.controller import cancel, start, status
from platform_core.manifest import run_path
from platform_core.privacy import canaries
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    before = digest_source(), database_fingerprint()
    run_id = None
    try:
        manifest = start("paper", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="fixture-model")
        run_id = manifest["run_id"]
        directory = run_path(run_id)
        atomic_json(directory / "evidence" / "fixture.json", {"kind": "mock_model_validation", "research_results": False})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.25)
        else:
            cancel(run_id)
            raise TimeoutError("论文模拟实验未结束")
        assert current["status"] == "done", current
        result = json.loads((directory / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert result["paper"]["enabled"]
        assert result["paper"]["time_retrieval"]["exact_set_accuracy_all_ranges"] == 1
        assert result["paper"]["time_retrieval"]["scored"] == 6
        assert result["paper"]["tasks"]["topic"]["questions"] == 12
        assert result["paper"]["tasks"]["specific"]["questions"] == 18
        assert result["paper"]["tasks"]["topic"]["methods"]["title_keyword"]["hit_at_5"] == 1
        assert result["vector_integrity"]["preserved"] == 60
        assert result["privacy"]["stages"]["export"]["exposed_markers"] == 0
        assert result["desktop"]["status"] == "not_measured"
        dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
        with zipfile.ZipFile(io.BytesIO(build_report_zip(run_id))) as archive:
            assert archive.testzip() is None
            assert all(name in archive.namelist() for name in PAPER_FILES)
            assert len(json.loads(archive.read("evidence/time_queries.json"))) == 6
            assert len(json.loads(archive.read("reports/details.json"))["model_calls"]) == 155
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                assert not any(marker in text for marker in canaries(dataset)), name
        assert (digest_source(), database_fingerprint()) == before
        print(f"paper mock and ZIP verified: {run_id}; 60 cases, 30 questions, 6 time ranges")
    finally:
        if run_id and status(run_id)["status"] == "running":
            cancel(run_id)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
