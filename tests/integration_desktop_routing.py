"""Verify desktop orchestration safely; this does not verify real Windows capture."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

from platform_core.common import ROOT, digest_source
from platform_core.isolation import prepare, cleanup
from platform_core.manifest import create_run
from tests.mock_model import Handler
from tests.test_isolation import database_fingerprint


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    before = database_fingerprint(), digest_source()
    manifest = create_run("desktop", dedicated_vm=True, endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                          model="fixture-model", embedding_model="fixture-model")
    paths = prepare(manifest["run_id"], manifest)
    env = os.environ.copy()
    env.update(USERPROFILE=str(paths["profile"]),
               PYTHONPATH=os.pathsep.join([str(paths["runtime"] / "src"), str(ROOT)]),
               NO_PROXY="localhost,127.0.0.1")
    try:
        result = subprocess.run([sys.executable, "-m", "tests.desktop_routing_fixture", manifest["run_id"]],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (database_fingerprint(), digest_source()) == before
        print(result.stdout.strip(), manifest["run_id"])
    finally:
        cleanup(manifest["run_id"])
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
