"""Run explicitly: python -m tests.integration_lan_mock (requires a local LAN IPv4)."""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer

from platform_core.controller import start, status
from platform_core.manifest import run_path
from platform_core.privacy import is_private_lan
from tests.mock_model import Handler


def main() -> None:
    addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    host = next((address for address in addresses
                 if ipaddress.ip_address(address).version == 4 and
                 is_private_lan(f"http://{address}:1234/v1")), None)
    if host is None:
        raise RuntimeError("当前电脑没有可用的局域网 IPv4 地址")
    server = ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        manifest = start("quick", endpoint=f"http://{host}:{server.server_port}",
                         model="fixture-model", embedding_model="fixture-model",
                         allow_remote_model=True, resources=True)
        run_id = manifest["run_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = status(run_id)
            if current["status"] != "running":
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("局域网模拟批次未结束")
        assert current["status"] == "done", current
        run = run_path(run_id)
        preflight = json.loads((run / "evidence" / "preflight.json").read_text(encoding="utf-8"))
        report = json.loads((run / "reports" / "summary.json").read_text(encoding="utf-8"))
        assert preflight["endpoint_allowed"] and not preflight["loopback"]
        assert current["completed_cases"] == 8
        assert report["resources"]["model_status"] == "not_measured"
        print(f"LAN mock verified: {run_id}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
