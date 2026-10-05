from __future__ import annotations

import statistics
import threading
import time
from pathlib import Path
from typing import Any

import psutil


def tree(pid: int | None) -> list[psutil.Process]:
    if pid is None:
        return []
    try:
        parent = psutil.Process(pid)
        return [parent, *parent.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def process_sample(pid: int | None, *, include_children: bool = True,
                   cache: dict[tuple[int, float], psutil.Process] | None = None) -> dict[str, Any] | None:
    processes = tree(pid)
    if not include_children:
        processes = processes[:1]
    if not processes:
        return None
    cpu = rss = 0.0
    live = 0
    for process in processes:
        try:
            if cache is not None:
                identity = (process.pid, process.create_time())
                process = cache.setdefault(identity, process)
            cpu += process.cpu_percent(interval=None)
            rss += process.memory_info().rss
            live += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"cpu_percent_of_one_core": cpu, "rss_bytes": int(rss), "processes": live}


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
        def collect(phase: str, key: str) -> dict[str, Any] | None:
            samples = [row[key] for row in self.samples if row["phase"] == phase and row[key]]
            if not samples:
                return None
            return {"samples": len(samples),
                    "mean_cpu_percent_of_one_core": statistics.mean(row["cpu_percent_of_one_core"] for row in samples),
                    "peak_cpu_percent_of_one_core": max(row["cpu_percent_of_one_core"] for row in samples),
                    "mean_rss_bytes": statistics.mean(row["rss_bytes"] for row in samples),
                    "peak_rss_bytes": max(row["rss_bytes"] for row in samples)}

        baseline = {key: collect("baseline", key) for key in ("timeindex", "model")}
        active = {key: collect("active", key) for key in ("timeindex", "model")}
        return {"status": "done", "sample_interval_s": self.interval,
                "model_status": "done" if active["model"] else "not_measured",
                "model_reason": None if active["model"] else "模型进程未指定、不可见或位于另一台电脑／虚拟机外",
                "baseline": baseline, "active": active,
                "database_growth_bytes": sum(directory_size(path) for path in self.databases) - self._start_size,
                "scope": "TimeIndex为实验工作进程（含取证开销，不含打开的软件）；模型为接口进程及子进程；磁盘为所有本轮活动数据库",
                "total": None if active["model"] is None or active["timeindex"] is None else {
                    "mean_cpu_percent_of_one_core": active["model"]["mean_cpu_percent_of_one_core"] +
                                                    active["timeindex"]["mean_cpu_percent_of_one_core"],
                    "mean_rss_bytes": active["model"]["mean_rss_bytes"] + active["timeindex"]["mean_rss_bytes"]},
                "samples": self.samples}

