"""End-to-end fixture: omit one record in every retag response."""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.controller import start, status
from platform_core.manifest import run_path
from tests.mock_model import Handler


class PartialRetagHandler(Handler):
    def _json(self, value: object) -> None:
        if isinstance(value, dict) and value.get("choices"):
            message = value["choices"][0]["message"]
            answer = json.loads(message["content"])
            if isinstance(answer, list):
                message["content"] = json.dumps(answer[:-1], ensure_ascii=False)
        super()._json(value)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), PartialRetagHandler)
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
            raise TimeoutError("Partial retag fixture timed out")
        assert current["status"] == "partial", current
        assert current["sections"]["organization"]["status"] == "partial"
        report = json.loads((run_path(run_id) / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert report["organization"]["retag_complete"] == 7
        assert report["organization"]["tags_after"]["cases"] == 8
        assert report["organization"]["tags_after"]["fn"] >= 1
        assert report["vector_integrity"]["valid"] == 8
        assert report["vector_integrity"]["preserved"] == 8
        print(f"Incomplete retag, scoring population and preserved vectors verified: {run_id}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
