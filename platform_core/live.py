from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import atomic_json, read_json
from .manifest import run_path
from .privacy import blacklist_exposure
from .scoring import recording


def actions(round_number: int) -> list[tuple[str, str]]:
    return [
        ("open", "A"), ("focus", "A"), ("rename", "A"),
        ("open", "B"), ("focus", "B"), ("rename", "B"),
        ("open", "C"), ("focus", "C"),
        ("close", "A"), ("close", "B"), ("close", "C"), ("observe", "idle"),
    ]


def _send(control: Path, action: str, value: str | None = None) -> None:
    atomic_json(control, {"action": action, "value": value, "nonce": time.time_ns()})


def _probe_blacklist(run_id: str, scene: Path) -> dict[str, Any]:
    from TimeIndex.daemon.wmi_monitor import WmiCollector

    collected: list[dict[str, Any]] = []
    collector = WmiCollector(interval=1, global_blacklist=["python.exe"])
    collector.add_callback(lambda snapshot: collected.append({
        "windows": [asdict(window) for window in snapshot.windows],
        "process_events": [asdict(event) for event in snapshot.process_events]}))
    title = f"TI-BLACKLIST-{run_id}"
    control = scene / "blacklist.control.json"
    ready = scene / "blacklist.ready.json"
    child: subprocess.Popen | None = None
    try:
        collector.start()
        child = subprocess.Popen([sys.executable, "-m", "platform_core.live_window",
                                  "--title", title, "--control", str(control), "--ready", str(ready)],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.1)
        if not ready.exists():
            return {"status": "failed", "reason": "黑名单探针窗口未能启动"}
        time.sleep(4)
    finally:
        if child and child.poll() is None:
            _send(control, "close")
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.terminate()
        collector.stop()
    result = blacklist_exposure(collected, ["python.exe"])
    result["status"] = "done" if collected else "failed"
    result["software"] = {"id": "BLACKLIST", "application": "Python Tkinter 黑名单探针", "kind": "blacklist_probe",
                          "executable_name": Path(sys.executable).name, "root_pid": child.pid if child else None,
                          "actually_launched": child is not None, "status": "started" if ready.exists() else "failed",
                          "marker": title, "document": "专用测试窗口", "purpose": "只测试采集屏蔽行为，不调用摘要模型",
                          "windows": [{"title": title, "pid": child.pid}] if ready.exists() and child else []}
    result["probe_title_observed"] = any(
        title == window.get("title") for snapshot in collected for window in snapshot["windows"])
    return result


def run_live(run_id: str, rounds: int = 3, dwell_s: float = 6.0, trace: Any = None) -> dict[str, Any]:
    from .real_desktop import run_desktop
    return run_desktop(run_id, rounds, dwell_s, trace)


def run_window_probe(run_id: str, rounds: int = 3, dwell_s: float = 6.0) -> dict[str, Any]:
    """Run only inside a dedicated interactive Windows VM session."""
    from TimeIndex.daemon.daemon import Daemon

    run = run_path(run_id)
    manifest = read_json(run / "manifest.json")
    if not manifest.get("dedicated_vm"):
        return {"status": "not_measured", "reason": "未声明专用虚拟机模式"}
    scene = run / "live_control"
    scene.mkdir(exist_ok=True)
    snapshots: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    expected_events: list[dict[str, Any]] = []
    expected_windows: list[dict[str, Any]] = []
    action_log: list[dict[str, Any]] = []
    children: dict[str, subprocess.Popen] = {}
    titles: dict[str, str] = {}
    cancelled = False
    daemon = Daemon(wmi_interval=2, idle_threshold=999999)

    def on_snapshot(snapshot: Any) -> None:
        snapshots.append({"timestamp": snapshot.timestamp.isoformat(), "time": snapshot.timestamp.timestamp(),
                          "windows": [asdict(item) for item in snapshot.windows],
                          "process_events": [asdict(item) for item in snapshot.process_events]})

    def on_event(event: Any) -> None:
        events.append({"kind": "process", "event_type": event.event_type, "pid": event.pid,
                       "time": event.timestamp.timestamp(), "process": event.process_name})

    daemon.wmi_collector.add_callback(on_snapshot)
    daemon.wmi_collector.add_event_callback(on_event)
    try:
        daemon.start()
        for round_number in range(1, rounds + 1):
            for position, (action, label) in enumerate(actions(round_number), 1):
                if (run / "cancel.flag").exists():
                    cancelled = True
                    break
                key = f"R{round_number}-{label}"
                title = titles.get(key, f"TI-LIVE-{run_id}-R{round_number}-{label}")
                timestamp = time.time()
                if action == "open":
                    control = scene / f"{key}.control.json"
                    ready = scene / f"{key}.ready.json"
                    child = subprocess.Popen([sys.executable, "-m", "platform_core.live_window",
                                              "--title", title, "--control", str(control),
                                              "--ready", str(ready)],
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    children[key] = child
                    deadline = time.monotonic() + 10
                    while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
                        time.sleep(0.1)
                    if not ready.exists():
                        raise RuntimeError(f"测试窗口 {key} 未能启动")
                    expected_events.append({"kind": "process", "event_type": "created",
                                            "pid": child.pid, "time": timestamp})
                    expected_windows.append({"kind": "window", "title": title,
                                             "pid": child.pid, "time": timestamp})
                    titles[key] = title
                elif action == "focus":
                    _send(scene / f"{key}.control.json", "focus")
                    expected_windows.append({"kind": "window", "title": title,
                                             "pid": children[key].pid, "time": timestamp})
                elif action == "rename":
                    title = f"{title}-RENAMED"
                    _send(scene / f"{key}.control.json", "title", title)
                    titles[key] = title
                    expected_windows.append({"kind": "window", "title": title,
                                             "pid": children[key].pid, "time": timestamp})
                elif action == "close":
                    expected_events.append({"kind": "process", "event_type": "exited",
                                            "pid": children[key].pid, "time": timestamp})
                    _send(scene / f"{key}.control.json", "close")
                action_log.append({"round": round_number, "position": position,
                                   "action": action, "window": key, "time": timestamp,
                                   "title": title if action not in {"close", "observe"} else None})
                time.sleep(dwell_s)
            if cancelled:
                break
        time.sleep(3)
    finally:
        for key, child in children.items():
            if child.poll() is None:
                try:
                    _send(scene / f"{key}.control.json", "close")
                    child.wait(timeout=2)
                except Exception:
                    child.terminate()
        daemon.stop()

    blacklist_probe = None if cancelled else _probe_blacklist(run_id, scene)
    if not snapshots:
        result = {"status": "cancelled" if cancelled else "failed", "reason": "WMI/窗口采集未产生快照",
                  "actions": action_log, "events": events}
        atomic_json(run / "evidence" / "live_raw.json", result)
        return result
    observed_windows = []
    test_pids = {child.pid for child in children.values()}
    for snapshot in snapshots:
        for window in snapshot["windows"]:
            if window["pid"] in test_pids:
                observed_windows.append({"kind": "window", "title": window["title"],
                                         "pid": window["pid"], "time": snapshot["time"]})
    actual_events = [event for event in events if event["pid"] in test_pids]
    events_score = recording(expected_events, actual_events)
    windows_score = recording(expected_windows, observed_windows)
    rows = daemon.db_store.store.get_table().to_arrow().to_pylist()
    persisted = []
    for row in rows:
        for item in row.get("active_windows") or []:
            try:
                window = json.loads(item)
            except (ValueError, TypeError):
                continue
            if window.get("pid") in test_pids:
                persisted.append({"kind": "window", "pid": window["pid"],
                                  "title": window.get("title"),
                                  "time": datetime.fromisoformat(row["timestamp"]).timestamp(),
                                  "record_id": row["id"]})
    db_score = recording(expected_windows, persisted)
    raw = {"actions": action_log, "expected_events": expected_events,
           "expected_windows": expected_windows, "snapshots": snapshots,
           "events": events, "persisted": persisted}
    atomic_json(run / "evidence" / "live_raw.json", raw)
    return {"status": "cancelled" if cancelled else "done", "actions": len(action_log), "rounds": rounds,
            "raw_events": events_score, "raw_windows": windows_score,
            "database": db_score,
            "blacklist_probe": blacklist_probe,
            "recording": {"expected": db_score["expected"], "matched": db_score["matched"],
                          "recall": db_score["recall"],
                          "duplicate_count": db_score["duplicate_count"],
                          "false_positive_count": db_score["false_positive_count"]}}

