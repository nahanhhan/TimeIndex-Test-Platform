"""Run explicitly: python -m tests.integration_cancel"""
from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer

import psutil

from platform_core.controller import cancel, start, status
from platform_core.isolation import runtime_paths
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = database_fingerprint()
    try:
        manifest = start("full", endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                         model="fixture-model", embedding_model="fixture-model")
        run_id = manifest["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = status(run_id)
            if current.get("completed_cases", 0) >= 3:
                break
            if current["status"] != "running":
                raise AssertionError(f"实验过早结束：{current['status']}")
            time.sleep(0.2)
        else:
            raise TimeoutError("实验没有在一分钟内处理三条场景")
        stopped = cancel(run_id)
        assert stopped["status"] == "cancelled", stopped
        assert not psutil.pid_exists(manifest["worker_pid"])
        assert not runtime_paths(run_id)["runtime"].exists()
        assert (runtime_paths(run_id)["run"] / "evidence" / "cases.json").exists()
        assert database_fingerprint() == before
        print(f"cancel verified: {run_id}, completed={stopped['completed_cases']}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

