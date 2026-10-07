from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil

from .common import ROOT, RUNS, atomic_json, file_lock, read_json
from .isolation import cleanup, prepare, preserve_database
from .manifest import TERMINAL_STATUSES, create_run, run_path, update_run
from .preflight import check
from .reporting import export


ACTIVE = RUNS / ".active"


def _active_guard():
    return file_lock(ACTIVE.with_name(ACTIVE.name + ".guard"))


def _owner_alive(owner: dict[str, Any]) -> bool:
    pid = owner.get("pid")
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        expected = owner.get("process_created_at")
        return process.is_running() and (expected is None or process.create_time() == expected)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _owner(run_id: str, pid: int) -> dict[str, Any]:
    try:
        created = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        created = None
    return {"run_id": run_id, "pid": pid, "process_created_at": created}


def _lock(run_id: str) -> None:
    with _active_guard():
        previous = read_json(ACTIVE, {})
        if _owner_alive(previous):
            raise RuntimeError(f"另一轮实验正在准备或运行：{previous.get('run_id')}")
        atomic_json(ACTIVE, _owner(run_id, os.getpid()))


def _publish_worker(run_id: str, pid: int) -> None:
    with _active_guard():
        if read_json(run_path(run_id) / "manifest.json", {}).get("status") in TERMINAL_STATUSES:
            return
        if read_json(ACTIVE, {}).get("run_id") != run_id:
            raise RuntimeError("实验启动锁的归属发生变化，拒绝覆盖另一轮实验")
        atomic_json(ACTIVE, _owner(run_id, pid))


def release_lock(run_id: str) -> None:
    with _active_guard():
        if read_json(ACTIVE, {}).get("run_id") == run_id:
            ACTIVE.unlink()


def start(mode: str, dataset_path: Path | None = None, **settings: Any) -> dict[str, Any]:
    api_key = settings.pop("api_key", None)
    manifest = create_run(mode, dataset_path, **settings)
    run_id = manifest["run_id"]
    try:
        _lock(run_id)
        paths = prepare(run_id, manifest, api_key=api_key)
        preflight = check(manifest["endpoint"], dedicated_vm=manifest["dedicated_vm"],
                          allow_remote_model=manifest["allow_remote_model"], api_key=api_key,
                          timeindex_project=manifest["timeindex_project"])
        atomic_json(paths["run"] / "evidence" / "preflight.json", preflight)
        if mode == "desktop" and not preflight["desktop_ready"]:
            raise RuntimeError("真实软件实验需要 Windows、WMI 和窗口采集组件；环境检查未通过，未启动实验")
        if not manifest["allow_no_model"]:
            if not preflight["endpoint_allowed"]:
                raise RuntimeError(preflight["model_detail"])
            if not preflight["model_reachable"]:
                location = ("127.0.0.1 指向当前测试电脑；若 LM Studio 在另一台电脑，"
                            "请填写它的局域网 IP 并勾选局域网模型选项。" if preflight["loopback"] else
                            "请在 LM Studio 开启 Serve on Local Network，并检查局域网 IP、端口和认证。")
                raise RuntimeError(f"模型服务不可用：{preflight['model_endpoint']}/models；"
                                   f"{preflight['model_detail']}。{location}")
        if manifest["resources"] and manifest.get("model_pid") is None:
            manifest = update_run(run_id, model_pid=preflight.get("model_pid_candidate"))
        env = os.environ.copy()
        env["USERPROFILE"] = str(paths["profile"])
        env["HOME"] = str(paths["profile"])
        env["PYTHONPATH"] = os.pathsep.join([str(paths["runtime"] / "src"), str(ROOT)])
        env["PYTHONUNBUFFERED"] = "1"
        model_host = urlparse(manifest["endpoint"]).hostname or ""
        bypass = ",".join(part for part in (env.get("NO_PROXY"), "localhost", "127.0.0.1", model_host)
                          if part)
        env["NO_PROXY"] = bypass
        env["no_proxy"] = bypass
        log = (paths["run"] / "worker.log").open("w", encoding="utf-8")
        try:
            update_run(run_id, status="running")
            process = subprocess.Popen(
                [sys.executable, "-m", "platform_core.worker", run_id], cwd=ROOT,
                env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            log.close()
        _publish_worker(run_id, process.pid)
        return update_run(run_id, worker_pid=process.pid)
    except Exception as error:
        update_run(run_id, status="failed", error=str(error))
        release_lock(run_id)
        try:
            cleanup(run_id)
        except Exception:
            pass
        raise


def status(run_id: str) -> dict[str, Any]:
    manifest = read_json(run_path(run_id) / "manifest.json")
    if not isinstance(manifest, dict):
        raise FileNotFoundError(run_id)
    pid = manifest.get("worker_pid")
    if manifest["status"] == "running" and pid and not psutil.pid_exists(pid):
        manifest = update_run(run_id, expected_status="running", status="interrupted", error="实验子进程意外结束")
        release_lock(run_id)
    return manifest


def cancel(run_id: str, timeout: float = 5.0) -> dict[str, Any]:
    manifest = status(run_id)
    if manifest["status"] != "running":
        return manifest
    directory = run_path(run_id)
    (directory / "cancel.flag").write_text("cancel\n", encoding="utf-8")
    pid = manifest.get("worker_pid")
    deadline = time.monotonic() + timeout
    while pid and psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if pid and psutil.pid_exists(pid):
        try:
            process = psutil.Process(pid)
            command = " ".join(process.cmdline())
            if "platform_core.worker" in command and run_id in command:
                children = process.children(recursive=True)
                for child in reversed(children):
                    child.terminate()
                process.terminate()
                _, alive = psutil.wait_procs([*children, process], timeout=2)
                for item in alive:
                    item.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    manifest = update_run(run_id, status="cancelled")
    preserved = False
    try:
        preserve_database(run_id)
        preserved = True
    except (OSError, RuntimeError) as error:
        update_run(run_id, preserve_error=str(error))
    if preserved:
        try:
            cleanup(run_id)
        except (OSError, RuntimeError) as error:
            update_run(run_id, cleanup_error=str(error))
    release_lock(run_id)
    try:
        export(run_id)
    except Exception as error:
        update_run(run_id, report_error=str(error))
    return status(run_id)


def replay(run_id: str) -> dict[str, Path]:
    return export(run_id)

