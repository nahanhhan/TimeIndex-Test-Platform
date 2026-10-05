"""Run explicitly: python -m tests.integration_smoke"""
from __future__ import annotations

import json
import time

from platform_core.controller import replay, start, status
from platform_core.dataset import load_dataset
from platform_core.manifest import run_path
from platform_core.privacy import assert_redacted
from tests.test_isolation import database_fingerprint


def main() -> None:
    before = database_fingerprint()
    try:
        start("quick", endpoint="http://127.0.0.1:18767/v1")
    except RuntimeError as error:
        assert "模型服务不可用" in str(error), error
    else:
        raise AssertionError("模型未连接时，普通实验不应启动")
    manifest = start("quick", endpoint="http://127.0.0.1:18767/v1", allow_no_model=True)
    run_id = manifest["run_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = status(run_id)
        if current["status"] != "running":
            break
        time.sleep(0.2)
    else:
        raise TimeoutError("无模型快速检查未结束")
    assert current["status"] == "diagnostic", current
    run = run_path(run_id)
    assert not (run / "runtime").exists()
    assert database_fingerprint() == before
    dataset, _ = load_dataset(run / "dataset.json")
    report = json.loads((run / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert report["conditions"]["platform_sha256"] == current["platform_sha256"]
    assert report["conditions"]["host"]["memory_total_bytes"] > 0
    assert current["sections"]["recording"]["status"] == "not_measured"
    for path in replay(run_id).values():
        assert_redacted(path.read_text(encoding="utf-8"), dataset, keep_test_titles=bool(dataset.get("synthetic")))
    print(f"no-model and replay verified: {run_id}")


if __name__ == "__main__":
    main()
