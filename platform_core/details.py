from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .common import read_json
from .scoring import facts, pairwise_clusters, ratio, record_alignment


def build_details(directory: Path, dataset: dict[str, Any], cases: list[dict[str, Any]],
                  queries: dict[str, Any]) -> dict[str, Any]:
    traces = read_json(directory / "evidence" / "model_calls.json")
    calls = traces.get("calls", []) if traces else []
    batches = read_json(directory / "evidence" / "retag_batches.json", {})
    desktop = read_json(directory / "evidence" / "live_raw.json", {})
    inventory = read_json(directory / "evidence" / "desktop_apps.json", {"applications": []})
    live_cases = read_json(directory / "evidence" / "live_cases.json", [])
    for row in live_cases:
        row["model_call_ids"] = [call["id"] for call in calls if call["context"].get("record_id") == row["record_id"] or
                                 row["record_id"] in call["context"].get("record_ids", [])]
        row["raw_response_available"] = bool(row["model_call_ids"])
    gold = {row["id"]: row for row in dataset["cases"]}
    applications: dict[str, Any] = {}
    examples = []
    for row in cases:
        expected = gold.get(row["id"], {})
        process = expected.get("process", "未知")
        application = expected.get("application") or expected.get("title", "").rsplit(" - ", 1)[-1] or process
        applications.setdefault(process, {"application": application, "process": process, "actually_launched": False,
                                          "kind": "合成快照中的应用名称", "case_ids": []})["case_ids"].append(row["id"])
        intent = {key: value for key, value in (row.get("intent") or {}).items() if key != "vector"}
        original = row.get("record") or intent
        related = [call["id"] for call in calls if call["context"].get("case_id") == row["id"] or
                   row["id"] in call["context"].get("case_ids", [])]
        examples.append({**{key: value for key, value in row.items() if key != "intent"}, "intent": intent,
                         "expected": {key: expected.get(key) for key in ("facts", "required_details", "app_aliases", "tags", "tag_aliases", "group", "observable_scope")},
                         "facts_before": facts(original.get("summary"), expected.get("facts", [])),
                         "facts_after": facts((row.get("refined") or {}).get("refined_summary"), expected.get("facts", [])),
                         "recording_check": record_alignment(row),
                         "model_call_ids": related, "raw_response_available": bool(related),
                         "raw_response_note": None if related else "旧证据只保存了解析结果，原始模型回复和完整请求无法复原"})
    for call in calls:
        call["case_ids"] = ([call["context"]["case_id"]] if call["context"].get("case_id") else
                            call["context"].get("case_ids", []))
    return {"schema_version": 3, "synthetic_software": list(applications.values()),
            "desktop_software": inventory.get("applications", []), "desktop_inventory": inventory,
            "cases": examples, "queries": queries, "batches": batches, "model_calls": calls,
            "model_trace_available": traces is not None,
            "live": read_json(directory / "evidence" / "live.json", {}),
            "desktop_settings": read_json(directory / "evidence" / "desktop_settings.json", {}),
            "live_cases": live_cases,
            "live_actions": desktop.get("actions", []), "fault_injection": read_json(directory / "evidence" / "fallback.json", {}),
            "desktop_queries": read_json(directory / "evidence" / "desktop_queries.json", {}),
            "evidence_limits": traces.get("capture_errors", []) if traces else ["原始模型请求和回复未保存；报告展示解析结果，不冒充原始回复"]}


def hide_uncontrolled_windows(value: str, directory: Path, details: dict[str, Any]) -> str:
    raw = read_json(directory / "evidence" / "live_raw.json", {})
    markers = [row["marker"] for row in details["desktop_software"] if row.get("marker")]
    titles = set()
    for snapshot in raw.get("snapshots", []):
        for window in snapshot.get("windows", []):
            title = window.get("title", "")
            if title and not any(marker in title for marker in markers):
                titles.add(title)
    def clean_text(text: str) -> str:
        for title in sorted(titles, key=len, reverse=True):
            for escaped in (json.dumps(title, ensure_ascii=False)[1:-1], json.dumps(title, ensure_ascii=True)[1:-1], title):
                text = text.replace(escaped, "[非测试窗口标题已隐藏]")
        return text

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: clean(content) for key, content in item.items()}
        if isinstance(item, list):
            return [clean(content) for content in item]
        return clean_text(item) if isinstance(item, str) else item

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return clean_text(value)
    # Some stored fields and model replies contain JSON inside a JSON string.
    # Redact each string before serialization so quotes remain valid at every level.
    return json.dumps(clean(parsed), ensure_ascii=False)


def batch_cluster_score(dataset: dict[str, Any], cases: list[dict[str, Any]], batches: dict[str, Any]) -> dict[str, Any]:
    gold = {row["id"]: row["group"] for row in dataset["cases"]}
    predicted = {row["id"]: (row.get("refined") or {}).get("cluster_id") for row in cases}
    scores = []
    for batch in batches.get("batches", []):
        ids = [key for key in batch["input_ids"] if key in gold]
        value = pairwise_clusters({key: gold[key] for key in ids}, {key: predicted.get(key) for key in ids})
        scores.append({"batch_id": batch.get("id"), "cases": len(ids), **value})
    tp = sum(value.get("tp", 0) for value in scores)
    fp = sum(value.get("fp", 0) for value in scores)
    fn = sum(value.get("fn", 0) for value in scores)
    return {"status": "not_measured" if not scores else "partial" if any(value["status"] == "partial" for value in scores) else "done",
            "tp": tp, "fp": fp, "fn": fn, "f1": ratio(2 * tp, 2 * tp + fp + fn), "batches": scores,
            "scope": "仅比较同一次 TimeIndex 整理请求内的记录，不把跨批同名 cluster_id 当作全局聚类"}


def random_reference(dataset: dict[str, Any], cases: list[dict[str, Any]], queries: list[dict[str, Any]]) -> dict[str, Any]:
    ids = {row["id"] for row in cases if row.get("status") == "done"}
    n, k = len(ids), min(5, len(ids))
    probabilities = []
    for query in queries:
        relevant = len(ids & set(query["relevant_ids"]))
        probabilities.append(1 - math.comb(n - relevant, k) / math.comb(n, k) if n and relevant else 0.0)
    return {"corpus_size": n, "top_k": k, "queries": len(queries),
            "expected_random_hit_at_5": sum(probabilities) / len(probabilities) if probabilities and n else None,
            "scope": "均匀随机取不重复记录的理论参考，不是实测检索成绩；快速模式只用于流程检查"}


def compact_response(call: dict[str, Any]) -> dict[str, Any]:
    response = call.get("response") or {}
    if call.get("operation") != "embedding":
        return response
    rows = []
    for item in response.get("data", []):
        vector = item.get("embedding") or []
        rows.append({"index": item.get("index"), "dimension": len(vector), "first_eight_values": vector[:8],
                     "sha256": hashlib.sha256(json.dumps(vector).encode()).hexdigest()})
    return {"model": response.get("model"), "data": rows, "usage": response.get("usage"),
            "note": "完整向量保存在逐例明细 JSON 的原始模型回复中"}
