from __future__ import annotations

import shutil
import hashlib
from pathlib import Path
from typing import Any

import yaml

from .common import REPO, atomic_json, digest_source, inside, read_json
from .manifest import run_path


def runtime_paths(run_id: str) -> dict[str, Path]:
    run = run_path(run_id)
    runtime = run / "runtime"
    return {"run": run, "runtime": runtime, "source": runtime / "src" / "TimeIndex",
            "profile": runtime / "profile", "database": runtime / "src" / "TimeIndex" / ".lancedb"}


def prepare(run_id: str, manifest: dict[str, Any], *, api_key: str | None = None) -> dict[str, Path]:
    paths = runtime_paths(run_id)
    source = REPO / "src" / "TimeIndex"
    target = paths["source"]
    if paths["runtime"].exists():
        raise FileExistsError("本轮临时副本已存在")
    if digest_source(source) != manifest["source_sha256"]:
        raise RuntimeError("源码自创建运行清单后已变化")
    for item in source.rglob("*"):
        if not item.is_file() or item.suffix not in {".py", ".yaml"}:
            continue
        destination = target / item.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
    if digest_source(target) != manifest["source_sha256"]:
        raise RuntimeError("临时副本校验失败")
    profile_config = paths["profile"] / ".timeindex" / "config.yaml"
    profile_config.parent.mkdir(parents=True, exist_ok=True)
    settings = yaml.safe_load((source / "config.yaml").read_text(encoding="utf-8")) or {}
    settings.update({"LLM_BASE_URL": manifest["endpoint"], "LLM_MODEL": manifest["model"],
                     "EMBEDDING_MODEL": manifest["embedding_model"], "USER_DEBUG": False})
    if api_key:
        settings["LLM_API_KEY"] = api_key
    profile_config.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    atomic_json(paths["runtime"] / "isolation.json",
                {"run_id": run_id, "source_sha256": manifest["source_sha256"],
                 "profile": str(paths["profile"]), "database": str(paths["database"])})
    if paths["database"].exists():
        raise RuntimeError("副本在导入前已包含数据库")
    return paths


def verify_import(run_id: str) -> dict[str, str]:
    """Called inside the isolated child before any experiment work."""
    paths = runtime_paths(run_id)
    import TimeIndex
    package_paths = list(TimeIndex.__path__)
    if not package_paths or not inside(Path(package_paths[0]), paths["source"]):
        raise RuntimeError(f"TimeIndex 导入路径未隔离: {package_paths}")
    # TimeIndex is a namespace package; keep later imports inside the copied tree.
    TimeIndex.__path__ = [str(paths["source"])]
    from TimeIndex.db.vector_store import DEFAULT_LANCEDB_PATH
    from TimeIndex.utils.config import config

    actual = {"package": str(paths["source"]), "database": DEFAULT_LANCEDB_PATH,
              "config": config._config_path}
    if Path(actual["database"]).resolve() != paths["database"].resolve():
        raise RuntimeError("数据库路径未隔离")
    if not inside(Path(actual["config"]), paths["profile"]):
        raise RuntimeError("配置路径未隔离")
    return actual


def preserve_database(run_id: str) -> Path | None:
    paths = runtime_paths(run_id)
    if not paths["database"].exists():
        return None
    destination = paths["run"] / "evidence" / "timeindex_db"
    marker = paths["run"] / "evidence" / "timeindex_db.complete.json"
    if destination.exists():
        if read_json(marker, {}).get("run_id") == run_id:
            return destination
        raise RuntimeError("数据库证据目录存在但复制未确认完成；保留临时副本供恢复")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name("timeindex_db.partial")
    if staging.exists():
        raise RuntimeError("数据库证据的上次复制未完成；保留临时副本供恢复")
    shutil.copytree(paths["database"], staging)
    def fingerprint(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(directory)).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    source_hash = fingerprint(paths["database"])
    if fingerprint(staging) != source_hash:
        raise RuntimeError("数据库证据复制校验失败；保留临时副本供恢复")
    staging.rename(destination)
    atomic_json(marker, {"run_id": run_id, "database_sha256": source_hash})
    return destination


def cleanup(run_id: str) -> None:
    paths = runtime_paths(run_id)
    marker = paths["runtime"] / "isolation.json"
    data = read_json(marker)
    if not paths["runtime"].exists():
        return
    if not isinstance(data, dict) or data.get("run_id") != run_id:
        raise RuntimeError("缺少匹配的隔离标记，拒绝清理")
    if paths["runtime"].resolve() != (paths["run"] / "runtime").resolve():
        raise RuntimeError("清理目标路径无效")
    if not inside(paths["runtime"], paths["run"]):
        raise RuntimeError("清理目标不在本轮目录内")
    shutil.rmtree(paths["runtime"])

