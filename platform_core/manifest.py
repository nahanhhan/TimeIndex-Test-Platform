from __future__ import annotations

import secrets
import math
from pathlib import Path
from typing import Any, Callable

from .common import RUNS, atomic_json, digest_platform, digest_source, file_lock, host_info, now, read_json
from .dataset import DEFAULT_DATASET, PAPER_DATASET, load_dataset, select
from .privacy import normalize_endpoint
from .project import resolve_project_dir
from .scoring import SCORING_VERSION


TERMINAL_STATUSES = {"done", "partial", "failed", "diagnostic", "cancelled", "interrupted"}


def run_path(run_id: str) -> Path:
    if not run_id or any(char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_" for char in run_id):
        raise ValueError("运行 ID 无效")
    return RUNS / run_id


def create_run(mode: str, dataset_path: Path | None = None, *, resources: bool = False,
               dedicated_vm: bool = False, endpoint: str = "http://127.0.0.1:1234/v1",
               model: str = "gemma-4-e4b", embedding_model: str = "text-embedding-embeddinggemma-300m",
               model_pid: int | None = None, allow_remote_model: bool = False,
               allow_no_model: bool = False, timeindex_project: str | Path | None = None,
               model_timeout_s: float = 120.0, verify_core_repairs: bool = False) -> dict[str, Any]:
    if isinstance(model_timeout_s, bool) or not isinstance(model_timeout_s, (int, float)) or not math.isfinite(model_timeout_s) or model_timeout_s <= 0:
        raise ValueError("模型等待上限必须是大于零的有限秒数")
    if mode == "custom" and dataset_path is None:
        raise ValueError("自定义模式需要数据集文件")
    if mode == "desktop" and not dedicated_vm:
        raise ValueError("真实软件实验需要确认专用测试桌面；会采集该桌面的所有可见窗口")
    if allow_no_model and mode != "quick":
        raise ValueError("无模型自检仅适用于快速检查")
    if allow_remote_model and dedicated_vm:
        raise ValueError("真实桌面采集不能使用局域网模型接口")
    if allow_remote_model and model_pid is not None:
        raise ValueError("另一台电脑的模型进程不能用本机 PID 测量")
    endpoint = normalize_endpoint(endpoint)
    dataset_path = dataset_path or (PAPER_DATASET if mode == "paper" else DEFAULT_DATASET)
    data, dataset_sha256 = load_dataset(dataset_path)
    if mode == "paper" and data.get("evaluation_profile") != "paper":
        raise ValueError("论文模式需要带固定实验方案的论文数据集；快速调试样本请使用 quick 模式")
    selected = select(data, mode)
    if allow_remote_model and selected.get("synthetic") is not True:
        raise ValueError("局域网模型只接受明确标为 synthetic 的虚构数据集")
    if dedicated_vm and mode in {"full", "desktop", "paper"}:
        selected["privacy_markers"] = list(set(selected.get("privacy_markers", [])) | {"TEST-SECRET-DESKTOP"})
    project = resolve_project_dir(timeindex_project)
    source_sha256 = digest_source(project / "src" / "TimeIndex")
    run_id = now()[:19].replace(":", "-") + "-" + secrets.token_hex(4)
    directory = run_path(run_id)
    directory.mkdir(parents=True, exist_ok=False)
    atomic_json(directory / "dataset.json", selected)
    manifest = {
        "run_id": run_id,
        "mode": mode,
        "status": "created",
        "created_at": now(),
        "updated_at": now(),
        "dataset_version": data["version"],
        "evaluation_profile": data.get("evaluation_profile", "diagnostic"),
        "scoring_version": SCORING_VERSION,
        "evidence_schema_version": 4,
        "desktop_mode": "real_applications" if dedicated_vm and mode in {"full", "desktop", "paper"} else "not_selected",
        "dataset_sha256": dataset_sha256,
        "seed": data.get("seed"),
        "selected_cases": len(selected["cases"]),
        "selected_queries": len(selected["queries"]),
        "timeindex_project": str(project),
        "source_sha256": source_sha256,
        "platform_sha256": digest_platform(),
        "host": host_info(),
        "resources": bool(resources),
        "dedicated_vm": bool(dedicated_vm),
        "allow_remote_model": bool(allow_remote_model),
        "allow_no_model": bool(allow_no_model),
        "endpoint": endpoint,
        "model": model,
        "embedding_model": embedding_model,
        "model_pid": model_pid,
        "model_timeout_s": float(model_timeout_s),
        "verify_core_repairs": bool(verify_core_repairs),
        "sections": {},
        "completed_cases": 0,
        "total_cases": 9 if mode == "desktop" else len(selected["cases"]),
    }
    atomic_json(directory / "manifest.json", manifest)
    return manifest


def _mutate_run(run_id: str, mutate: Callable[[dict[str, Any]], None],
                expected_status: str | None = None) -> dict[str, Any]:
    path = run_path(run_id) / "manifest.json"
    if not path.parent.is_dir():
        raise FileNotFoundError(path)
    with file_lock(path.with_name(path.name + ".guard")):
        manifest = read_json(path)
        if not isinstance(manifest, dict):
            raise FileNotFoundError(path)
        if expected_status is not None and manifest.get("status") != expected_status:
            return manifest
        mutate(manifest)
        if manifest.get("status") in TERMINAL_STATUSES:
            manifest["current_phase"] = None
            manifest["current_model_call"] = None
        manifest["updated_at"] = now()
        atomic_json(path, manifest)
        return manifest


def update_run(run_id: str, *, expected_status: str | None = None, **changes: Any) -> dict[str, Any]:
    def apply(manifest: dict[str, Any]) -> None:
        applied = dict(changes)
        if manifest.get("status") in TERMINAL_STATUSES:
            # Late controller/progress updates cannot reopen or reclassify a finished run.
            applied.pop("status", None)
        manifest.update(applied)

    return _mutate_run(run_id, apply, expected_status)


def section(run_id: str, name: str, status: str, reason: str | None = None) -> None:
    def apply(manifest: dict[str, Any]) -> None:
        manifest.setdefault("sections", {})[name] = {"status": status, "reason": reason}

    _mutate_run(run_id, apply)


def list_runs() -> list[dict[str, Any]]:
    if not RUNS.exists():
        return []
    return [value for path in sorted(RUNS.glob("*/manifest.json"), reverse=True)
            if isinstance((value := read_json(path)), dict)]

