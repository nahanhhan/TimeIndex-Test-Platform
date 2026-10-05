"""A query embedding failure must not be scored as semantic success via keyword fallback."""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.controller import start, status
from platform_core.manifest import run_path
from tests.mock_model import Handler


class BadQueryEmbeddingHandler(Handler):
    bad_query_embeddings = True


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BadQueryEmbeddingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        manifest = start("quick", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="fixture-model")
        run_id = manifest["run_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("Query embedding fixture timed out")
        assert current["status"] == "partial", current
        report = json.loads((run_path(run_id) / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert report["vector_integrity"]["valid"] == 8
        assert report["organization"]["retag_complete"] == 8
        assert report["retrieval"]["semantic"]["errors"] == 4
        assert report["retrieval"]["semantic"]["scored"] == 0
        assert report["retrieval"]["semantic"]["hit_at_5"] is None
        print(f"Query embedding failures correctly excluded from semantic scores: {run_id}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
