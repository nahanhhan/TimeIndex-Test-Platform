"""Find the TimeIndex source project without importing it or touching its database."""
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections import deque
from pathlib import Path
from typing import Iterable

from .common import ROOT, atomic_json, read_json


PROJECT_ENV = "TIMEINDEX_PROJECT_DIR"
SETTINGS_FILE = ".timeindex-project.json"
REQUIRED_FILES = (
    "pyproject.toml", "src/TimeIndex/config.yaml", "src/TimeIndex/daemon/daemon.py",
    "src/TimeIndex/daemon/llm_processor.py", "src/TimeIndex/daemon/wmi_monitor.py",
    "src/TimeIndex/db/vector_store.py", "src/TimeIndex/db/embedding_provider.py",
    "src/TimeIndex/utils/config.py",
)
SKIP_DIRECTORIES = {"src", "runs", "dist", "build", "node_modules", "venv", "__pycache__"}


def validate_project(path: str | Path, *, platform_root: Path = ROOT) -> Path:
    raw = os.path.expandvars(str(path).strip().strip('"'))
    if not raw:
        raise ValueError("请填写 TimeIndex 本体项目根目录")
    directory = Path(raw).expanduser()
    if not directory.is_absolute():
        directory = platform_root / directory
    directory = directory.resolve()
    missing = [name for name in REQUIRED_FILES if not (directory / name).is_file()]
    if missing:
        raise ValueError(f"不是完整的 TimeIndex 项目目录：{directory}；缺少：{', '.join(missing)}")
    try:
        metadata = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"TimeIndex 项目配置无法读取：{directory}；{error}") from error
    project = metadata.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str) or name.casefold() != "timeindex":
        raise ValueError(f"该目录的项目名称不是 timeindex：{directory}")
    return directory


def discover_projects(*, platform_root: Path = ROOT,
                      search_roots: Iterable[Path] | None = None) -> list[Path]:
    """Inspect nearby folders and Desktop, at most two levels and 600 directories."""
    if search_roots is None:
        nearby = [platform_root.parent, platform_root.parent.parent, Path.home() / "Desktop"]
        search_roots = [path for path in nearby if path != Path(path.anchor)]
    queue = deque((Path(path), 0) for path in search_roots)
    visited: set[Path] = set()
    found: set[Path] = set()
    while queue and len(visited) < 600:
        directory, depth = queue.popleft()
        try:
            directory = directory.resolve()
            if directory in visited or directory == platform_root.resolve():
                continue
            visited.add(directory)
            if (directory / "pyproject.toml").is_file():
                try:
                    found.add(validate_project(directory, platform_root=platform_root))
                    continue
                except ValueError:
                    pass
            if depth >= 2:
                continue
            for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if child.name.startswith(".") or child.name.casefold() in SKIP_DIRECTORIES:
                    continue
                if not child.is_dir() or child.is_symlink():
                    continue
                if getattr(child.stat(), "st_file_attributes", 0) & 0x400:
                    continue  # Do not follow Windows directory junctions.
                queue.append((child, depth + 1))
        except OSError:
            continue
    return sorted(found, key=lambda path: str(path).casefold())


def resolve_project_dir(path: str | Path | None = None, *, platform_root: Path = ROOT) -> Path:
    explicit = path if path is not None and str(path).strip() else os.environ.get(PROJECT_ENV)
    if explicit:
        return validate_project(explicit, platform_root=platform_root)
    try:
        saved = read_json(platform_root / SETTINGS_FILE, {})
        if isinstance(saved, dict) and saved.get("path"):
            return validate_project(saved["path"], platform_root=platform_root)
    except (OSError, ValueError, TypeError):
        pass  # A copied platform may contain a path belonging to another computer.
    try:
        return validate_project(platform_root.parent, platform_root=platform_root)
    except ValueError:
        pass
    found = discover_projects(platform_root=platform_root)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ValueError("发现多个 TimeIndex 项目，请明确指定本次使用的目录：" + "；".join(map(str, found)))
    raise ValueError("没有找到 TimeIndex 本体项目。请在网页填写项目根目录，或设置 TIMEINDEX_PROJECT_DIR；"
                     "该目录应包含 pyproject.toml 和 src/TimeIndex。")


def save_project_dir(path: str | Path, *, platform_root: Path = ROOT) -> Path:
    directory = validate_project(path, platform_root=platform_root)
    atomic_json(platform_root / SETTINGS_FILE, {"path": str(directory)})
    return directory


def project_status(path: str | Path | None = None) -> dict[str, object]:
    try:
        directory = resolve_project_dir(path)
        return {"ready": True, "path": str(directory), "reason": None}
    except (OSError, ValueError) as error:
        return {"ready": False, "path": None, "reason": str(error)}


def main() -> None:
    parser = argparse.ArgumentParser(description="查找 TimeIndex 本体项目目录")
    parser.add_argument("--timeindex-project", type=Path)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    try:
        directory = resolve_project_dir(args.timeindex_project)
        if args.save:
            save_project_dir(directory)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    print(directory)


if __name__ == "__main__":
    main()
