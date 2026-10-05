from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RUNS = ROOT / "runs"


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
    source = source or REPO / "src" / "TimeIndex"
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

