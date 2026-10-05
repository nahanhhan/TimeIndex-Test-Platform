from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from .common import digest_platform, inside, now, read_json
from .details import hide_uncontrolled_windows
from .manifest import run_path
from .privacy import assert_redacted, canaries, redact


REPORT_FILES = (
    "reports/report.html", "reports/summary.json", "reports/details.json", "reports/paper_metrics.csv",
)
PAPER_FILES = ("reports/paper_main_metrics.csv", "reports/retrieval_tasks.csv",
               "reports/privacy_stages.csv", "reports/time_queries.csv")
EVIDENCE_FILES = (
    "evidence/cases.json", "evidence/queries.json", "evidence/model_calls.json",
    "evidence/vector_integrity.json", "evidence/retag_batches.json", "evidence/embedding_checks.json",
    "evidence/fallback.json", "evidence/chat_probe.json", "evidence/embedding_probe.json",
    "evidence/preflight.json", "evidence/privacy_checks.json", "evidence/resources.json",
    "evidence/live.json", "evidence/live_raw.json", "evidence/live_cases.json",
    "evidence/desktop_apps.json", "evidence/desktop_settings.json", "evidence/desktop_batches.json",
    "evidence/desktop_queries.json", "evidence/retrieval_corpus.json", "evidence/fixture.json",
    "evidence/timeindex_db.complete.json",
    "evidence/time_queries.json",
)
BUNDLE_FILES = ("manifest.json", "dataset.json", *REPORT_FILES, *PAPER_FILES, *EVIDENCE_FILES)
_CREDENTIAL_FIELDS = {"apikey", "llmapikey", "accesstoken", "authorization", "headers", "extraheaders",
                      "cookie", "cookies"}


def _without_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_credentials(item) for key, item in value.items()
                if str(key).replace("_", "").replace("-", "").casefold() not in _CREDENTIAL_FIELDS}
    if isinstance(value, list):
        return [_without_credentials(item) for item in value]
    return value


def bundle_signature(run_id: str) -> tuple[tuple[str, int, int], ...]:
    directory = run_path(run_id)
    result = []
    for name in BUNDLE_FILES:
        path = directory / name
        if path.is_file():
            if not inside(path, directory):
                raise ValueError("报告文件不在本轮实验目录内")
            info = path.stat()
            result.append((name, info.st_mtime_ns, info.st_size))
    return tuple(result)


def _saved_marker_replacements(directory: Path, dataset: dict[str, Any], details: dict[str, Any]) -> dict[str, str]:
    markers = canaries(dataset)
    replacements: dict[str, str] = {}

    def remember(original: str, exported: str) -> None:
        for marker in markers:
            if marker not in original:
                continue
            before, after = original.split(marker, 1)
            if exported.startswith(before) and exported.endswith(after):
                label = exported[len(before):len(exported) - len(after) if after else None]
                if re.fullmatch(r"\[测试敏感串-\d+\]", label):
                    replacements[marker] = label

    gold = {case["id"]: case for case in dataset["cases"]}
    for case in details.get("cases", []):
        original = gold.get(case["id"], {}).get("title", "")
        for window in (case.get("input") or {}).get("windows", []):
            remember(original, window.get("title", ""))
    raw_live = {row["record_id"]: row for row in read_json(directory / "evidence" / "live_cases.json", [])}
    for case in details.get("live_cases", []):
        original = raw_live.get(case["record_id"], {}).get("input", {}).get("windows", [])
        exported = case.get("input", {}).get("windows", [])
        for left, right in zip(original, exported):
            remember(left.get("title", ""), right.get("title", ""))
    used = set(replacements.values())
    number = 1
    for marker in markers:
        if marker in replacements:
            continue
        while f"[测试敏感串-{number:03d}]" in used:
            number += 1
        label = f"[测试敏感串-{number:03d}]"
        replacements[marker] = label
        used.add(label)
    return replacements


def build_report_zip(run_id: str) -> bytes:
    """Package saved results without replaying an experiment or copying its private runtime."""
    directory = run_path(run_id)
    signature = bundle_signature(run_id)
    manifest = read_json(directory / "manifest.json")
    dataset = read_json(directory / "dataset.json")
    if not isinstance(manifest, dict) or not isinstance(dataset, dict):
        raise ValueError("缺少运行清单或数据集，无法生成诊断报告 ZIP")
    if manifest.get("run_id") != run_id:
        raise ValueError("运行清单与所选实验不匹配")
    if manifest.get("status") in {"created", "running"}:
        raise ValueError("请等待本轮实验结束后再导出诊断报告 ZIP")
    details = read_json(directory / "reports" / "details.json", {})
    if not details.get("desktop_software"):
        inventory = read_json(directory / "evidence" / "desktop_apps.json", {})
        details["desktop_software"] = inventory.get("applications", [])
    keep_titles = bool(dataset.get("synthetic"))
    replacements = _saved_marker_replacements(directory, dataset, details)
    contents: dict[str, bytes] = {}
    for name, _, _ in signature:
        path = directory / name
        if path.suffix == ".json":
            text = json.dumps(_without_credentials(read_json(path)), ensure_ascii=False, indent=2, default=str)
        else:
            text = path.read_text(encoding="utf-8")
        text = hide_uncontrolled_windows(text, directory, details)
        text = redact(text, dataset, keep_test_titles=keep_titles, marker_replacements=replacements)
        assert_redacted(text, dataset, keep_test_titles=keep_titles)
        contents[name] = text.encode("utf-8")
    if bundle_signature(run_id) != signature:
        raise RuntimeError("报告在打包时发生变化，请再次下载")
    index = {
        "schema_version": 1, "run_id": run_id, "exported_at": now(), "run_status": manifest.get("status"),
        "exporter_platform_sha256": digest_platform(),
        "redaction": "遮罩测试敏感串和非测试窗口标题；排除访问令牌字段、请求头、日志、配置和数据库文件",
        "missing_reports": [name for name in REPORT_FILES if name not in contents],
        "missing_paper_tables": [name for name in PAPER_FILES if name not in contents] if manifest.get("evaluation_profile") == "paper" else [],
        "missing_evidence": [name for name in EVIDENCE_FILES if name not in contents],
        "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in contents.items()],
    }
    contents["bundle_manifest.json"] = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
    contents["README.txt"] = (
        f"TimeIndex 诊断报告包\n运行编号：{run_id}\n\n"
        "查看结果：若报告已生成，打开 reports/report.html；启动失败等情况先查看 manifest.json。\n"
        "分析指标：reports/summary.json 和 reports/paper_metrics.csv。\n"
        "核对模型：reports/details.json 及 evidence/model_calls.json 保存请求、回复和关联编号。\n"
        "定位问题：manifest.json、dataset.json 和 evidence/ 中的逐例、查询、整理及向量检查记录。\n"
        "bundle_manifest.json 列出实际文件、校验值和缺失材料；历史实验未保存的证据不会补造。\n\n"
        "报告与证据沿用导出脱敏规则。运行配置、访问令牌、日志和数据库不打包。\n"
        "原始实验校验值仍保留在运行清单中；包内脱敏文件的校验值另见 bundle_manifest.json。\n"
        "打包只读取保存结果，不重新调用模型，不采集桌面，不重算或改写原实验。\n"
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return output.getvalue()
