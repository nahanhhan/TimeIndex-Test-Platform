"""Run explicitly: python -m tests.integration_missing_embedding"""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.controller import start, status
from platform_core.manifest import run_path
from tests.mock_model import NoEmbeddingHandler


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), NoEmbeddingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        manifest = start("quick", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="missing-embedding")
        run_id = manifest["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("缺少向量模型的实验未结束")
        assert current["status"] == "partial", current
        result = json.loads((run_path(run_id) / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert result["retrieval"]["semantic"]["not_measured"] == 4
        assert result["retrieval"]["keyword"]["scored"] == 4
        assert result["retrieval"]["tags"]["scored"] == 4
        assert current["sections"]["recording"]["status"] == "done"
        assert current["sections"]["fallback"]["status"] == "done"
        assert result["organization"]["cases_done"] == 8
        print(f"missing embedding verified: {run_id}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

