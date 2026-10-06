from __future__ import annotations

import statistics
import math
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def tree(pid: int | None, *, include_children: bool = True) -> list[psutil.Process]:
    if pid is None:
        return []
    try:
        parent = psutil.Process(pid)
        return [parent, *parent.children(recursive=True)] if include_children else [parent]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def process_sample(pid: int | None, *, include_children: bool = True,
                   cache: dict[tuple[int, float], psutil.Process] | None = None) -> dict[str, Any] | None:
    processes = tree(pid, include_children=include_children)
    if not processes:
        return None
    cpu = rss = 0.0
    live = 0
    for process in processes:
        try:
            if cache is not None:
                identity = (process.pid, process.create_time())
                process = cache.setdefault(identity, process)
            measured_cpu = process.cpu_percent(interval=None)
            measured_rss = process.memory_info().rss
            cpu += measured_cpu
            rss += measured_rss
            live += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if not live:
        return None
    return {"cpu_percent_of_one_core": cpu, "rss_bytes": int(rss), "processes": live,
            "processes_expected": len(processes), "unavailable_processes": len(processes) - live}


def resource_summary(samples: list[dict[str, Any]], *, model_selected: bool) -> dict[str, Any]:
    """Derive metrics from valid reads, including saved samples from older runs."""
    def valid(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        processes = value.get("processes", 0)
        return (isinstance(processes, int) and not isinstance(processes, bool) and processes > 0 and
                all(isinstance(value.get(key), (int, float)) and math.isfinite(value[key]) and value[key] >= 0
                    for key in ("cpu_percent_of_one_core", "rss_bytes")))

    def collect(phase: str, key: str) -> dict[str, Any] | None:
        planned = [row.get(key) for row in samples if row.get("phase") == phase]
        measured = [item for item in planned if valid(item)]
        if not measured:
            return None
        unavailable = len(planned) - len(measured)
        partial = sum(item.get("unavailable_processes", 0) > 0 or
                      item.get("processes_expected", item["processes"]) > item["processes"] for item in measured)
        return {"status": "partial" if unavailable or partial else "done", "samples": len(measured),
                "expected_samples": len(planned), "unavailable_samples": unavailable, "partial_samples": partial,
                "mean_cpu_percent_of_one_core": statistics.mean(row["cpu_percent_of_one_core"] for row in measured),
                "peak_cpu_percent_of_one_core": max(row["cpu_percent_of_one_core"] for row in measured),
                "mean_rss_bytes": statistics.mean(row["rss_bytes"] for row in measured),
                "peak_rss_bytes": max(row["rss_bytes"] for row in measured)}

    baseline = {key: collect("baseline", key) for key in ("timeindex", "model")}
    active = {key: collect("active", key) for key in ("timeindex", "model")}
    states = {key: value["status"] if value else "not_measured" for key, value in active.items()}
    measured = any(active.values())
    complete = states["timeindex"] == "done" and (not model_selected or states["model"] == "done")
    status = "done" if complete else "partial" if measured else "not_measured"
    return {"status": status,
            "reason": None if status == "done" else "资源采样不完整或无法读取；缺失数据不计为零，统计只使用成功读取的数据",
            "timeindex_status": states["timeindex"], "model_status": states["model"],
            "model_reason": None if states["model"] == "done" else
                "部分模型进程或采样未能读取，统计只覆盖成功读取的数据" if states["model"] == "partial" else
                "模型进程未指定、已退出、不可见或资源读取失败，未测数据不计为零",
            "baseline": baseline, "active": active,
            "total": None if any(state != "done" for state in states.values()) else {
                "mean_cpu_percent_of_one_core": active["model"]["mean_cpu_percent_of_one_core"] +
                                                active["timeindex"]["mean_cpu_percent_of_one_core"],
                "mean_rss_bytes": active["model"]["mean_rss_bytes"] + active["timeindex"]["mean_rss_bytes"]}}


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


class ResourceSampler:
    def __init__(self, worker_pid: int, model_pid: int | None, database: Path, interval: float = 1.0,
                 extra_databases: tuple[Path, ...] = ()):
        self.worker_pid = worker_pid
        self.model_pid = model_pid
        self.database = database
        self.databases = (database, *extra_databases)
        self._cpu_cache: dict[str, dict[tuple[int, float], psutil.Process]] = {"timeindex": {}, "model": {}}
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.phase = "baseline"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_size = sum(directory_size(path) for path in self.databases)

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.samples.append({"time": time.time(), "phase": self.phase,
                                 "timeindex": process_sample(self.worker_pid, include_children=False, cache=self._cpu_cache["timeindex"]),
                                 "model": process_sample(self.model_pid, cache=self._cpu_cache["model"])})
            self._stop.wait(self.interval)

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {**resource_summary(self.samples, model_selected=self.model_pid is not None),
                "sample_interval_s": self.interval,
                "database_growth_bytes": sum(directory_size(path) for path in self.databases) - self._start_size,
                "scope": "TimeIndex为实验工作进程（含取证开销，不含打开的软件）；模型为接口进程及子进程；磁盘为所有本轮活动数据库",
                "samples": self.samples}

