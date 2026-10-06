from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
_FILE_LOCKS: dict[str, Any] = {}
_FILE_LOCKS_MUTEX = threading.Lock()


@contextmanager
def file_lock(path: Path):
    """Hold a file lock across threads and processes; the OS releases it on exit."""
    identity = os.path.normcase(str(path.resolve()))
    with _FILE_LOCKS_MUTEX:
        mutex = _FILE_LOCKS.setdefault(identity, threading.RLock())
    path.parent.mkdir(parents=True, exist_ok=True)
    with mutex, path.open("a+b") as guard:
        if os.name == "nt":
            import msvcrt

            if guard.seek(0, os.SEEK_END) == 0:
                guard.write(b"\0")
                guard.flush()
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
        temp = Path(file.name)
    os.replace(temp, path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_source(source: Path | None = None) -> str:
    if source is None:
        from .project import resolve_project_dir

        source = resolve_project_dir() / "src" / "TimeIndex"
    if not source.is_dir():
        raise ValueError(f"TimeIndex 源码目录不存在：{source}")
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml"}:
            digest.update(str(path.relative_to(source)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def digest_platform() -> str:
    digest = hashlib.sha256()
    files = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "app.py", ROOT / "launch.ps1"]
    files.extend((ROOT / "platform_core").rglob("*.py"))
    for path in sorted(files):
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def host_info() -> dict[str, Any]:
    import psutil

    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "memory_total_bytes": psutil.virtual_memory().total,
    }

