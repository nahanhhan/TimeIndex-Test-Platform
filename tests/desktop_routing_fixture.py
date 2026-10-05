"""Pipeline acceptance only: app/WMI events are injected; no desktop software is opened."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from unittest.mock import patch

from platform_core.common import atomic_json, read_json
from platform_core.isolation import verify_import
from platform_core.manifest import run_path
from platform_core.model_trace import ModelTrace
from platform_core.real_desktop import run_desktop
from platform_core.reporting import export


def main(run_id: str) -> None:
    verify_import(run_id)
    from TimeIndex.daemon.wmi_monitor import WmiCollector, SystemSnapshot, WindowInfo, ProcessEvent

    collector = None
    def no_capture(self):
        nonlocal collector
        collector = self

    class FakeSession:
        def __init__(self, plan):
            self.plan, self.child = plan, None
            self.evidence = {**plan, "status": "not_started", "actually_launched": False}
        def open(self):
            stamp = time.time()
            self.evidence.update(status="started", actually_launched=True, root_pid=42001,
                                 opened_at=stamp, visible_at=stamp, executable_name="fixture.exe",
                                 windows=[{"title": "Python 函数 第1节 FIXTURE-N", "pid": 42001}])
            event = ProcessEvent(datetime.now(), "created", "fixture.exe", 42001)
            snapshot = SystemSnapshot(datetime.now(), process_events=[event],
                                      windows=[WindowInfo(0, self.evidence["windows"][0]["title"], 42001, "fixture.exe")])
            for callback in collector._callbacks:
                callback(snapshot)
            for callback in collector._event_callbacks:
                callback(event)
            return self.evidence
        def focus(self):
            return {"status": "done"}
        def close(self):
            self.evidence["closed_at"] = time.time()

    plan = {"id": "R1-N", "application": "FIXTURE ONLY", "marker": "FIXTURE-N",
            "document": "fixture.txt", "topic": "Python 函数"}
    run = run_path(run_id)
    atomic_json(run / "evidence" / "fixture.json", {"scope": "NO REAL DESKTOP OR MODEL; protocol and routing fixture"})
    trace = ModelTrace(run / "evidence" / "model_calls.json")
    with patch.object(WmiCollector, "start", no_capture), patch.object(WmiCollector, "stop"), \
            patch("platform_core.real_desktop.AppSession", FakeSession), \
            patch("platform_core.real_desktop.app_plan", return_value=[plan]), \
            patch("platform_core.live._probe_blacklist", return_value={"status": "not_measured", "reason": "fixture"}):
        result = run_desktop(run_id, rounds=1, dwell_s=0.01, trace=trace)
    assert result["status"] == "done", result
    assert result["settings"]["wmi_interval_s"] == 5
    assert result["settings"]["retag_batch_size"] == 20
    assert result["quality"]["facts_before"]["coverage"] == 1
    assert result["quality"]["facts_after"]["coverage"] == 0
    assert result["vector_integrity"]["preserved"] == 1
    assert all(item["scored"] == 1 for item in result["retrieval"].values())
    atomic_json(run / "evidence" / "live.json", result)
    paths = export(run_id)
    details = read_json(paths["details"])
    assert len(details["live_cases"]) == 1
    assert details["live_cases"][0]["model_call_ids"]
    assert details["live_cases"][0]["recording_check"]["windows_preserved"] == 1
    assert "合成阶段未执行" in paths["html"].read_text(encoding="utf-8")
    print("Desktop routing, actual-request gold, independent DB, organization, query evidence and report verified with injected events.")


if __name__ == "__main__":
    main(sys.argv[1])
