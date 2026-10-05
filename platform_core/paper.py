from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from typing import Any, Callable

from .details import random_reference
from .scoring import aggregate_queries, rank_query, ratio, text_contains, usable_text


def retrieval_summary(record: dict[str, Any]) -> str | None:
    """Use a valid refined summary, then the original; never search a null placeholder."""
    for key in ("refined_summary", "summary"):
        if usable_text(record.get(key)):
            return record[key]
    return None


def keyword_match(record: dict[str, Any], query: dict[str, Any], *, raw_title: bool = False) -> bool:
    if raw_title:
        titles = []
        for value in record.get("active_windows") or []:
            try:
                window = json.loads(value) if isinstance(value, str) else value
                if isinstance(window, dict):
                    titles.append(window.get("title", ""))
            except (TypeError, ValueError):
                continue
        text = "\n".join(titles)
    else:
        text = retrieval_summary(record)
    return all(text_contains(text, term) for term in query.get("keyword_terms", [query["keyword"]]))


def run_time_queries(dataset: dict[str, Any], cases: list[dict[str, Any]], store: Any,
                     id_map: dict[str, str], cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
    source = {row["id"]: row for row in cases}
    rows = []
    for offset in range(0, len(dataset["cases"]), 10):
        if cancelled():
            break
        expected = [case["id"] for case in dataset["cases"][offset:offset + 10]]
        row: dict[str, Any] = {"id": f"TIME-{offset // 10 + 1:02d}", "task": "time",
                               "query": "查询预先划定的采集时间范围", "relevant_ids": expected,
                               "expected_count": len(expected), "model_call_ids": []}
        if any(case_id not in source for case_id in expected):
            rows.append({**row, "status": "not_measured", "reason": "该时间段的预定输入尚未全部执行"})
            continue
        timestamps = [datetime.fromisoformat(source[case_id]["input"]["timestamp"]) for case_id in expected]
        start, end = min(timestamps), max(timestamps)
        row.update(time_start=start.isoformat(), time_end=end.isoformat())
        begun = time.perf_counter()
        try:
            found = store.get_activities_in_range(start, end, limit=len(dataset["cases"]) + 1)
            returned = [id_map.get(str(record["id"]), "unknown") for record in found]
            actual, gold = set(returned), set(expected)
            rows.append({**row, **rank_query(expected, returned, (time.perf_counter() - begun) * 1000),
                         "returned_count": len(returned), "set_precision": ratio(len(actual & gold), len(actual)),
                         "set_recall": ratio(len(actual & gold), len(gold)),
                         "exact_set_match": actual == gold and len(returned) == len(actual),
                         "duplicate_count": len(returned) - len(actual),
                         "returned_records": [{key: value for key, value in record.items() if key != "vector"}
                                              for record in found]})
        except Exception as error:
            rows.append({**row, **rank_query(expected, [], (time.perf_counter() - begun) * 1000, str(error))})
    return rows


def overview(dataset: dict[str, Any], cases: list[dict[str, Any]], queries: dict[str, Any],
             model_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if dataset.get("evaluation_profile") != "paper":
        return {"enabled": False}
    protocol = dataset["protocol"]
    tasks = {}
    for task in ("topic", "specific"):
        selected = [query for query in dataset["queries"] if query.get("task") == task]
        ids = {query["id"] for query in selected}
        methods = {}
        for method in ("semantic", "keyword", "title_keyword", "tags"):
            saved = {row["id"]: row for row in queries.get(method, [])}
            rows = [saved.get(query["id"], {"id": query["id"], "status": "not_measured"}) for query in selected]
            item = aggregate_queries(rows)
            item["completion_rate"] = ratio(item["scored"], len(ids))
            attempted = [row for row in rows if row.get("status") in {"done", "error"}]
            item["hit_at_5_all_questions"] = (ratio(sum(row.get("hit_at_5", 0) for row in rows), len(ids))
                                                  if attempted else None)
            methods[method] = item
        tasks[task] = {"questions": len(selected), "methods": methods,
                       "random_reference": random_reference(dataset, cases, selected)}
    timed = queries.get("time", [])
    done = [row for row in timed if row.get("status") == "done"]
    measured = [row for row in timed if row.get("status") in {"done", "error"}]
    time_result = {"planned": protocol["time_ranges"], "scored": len(done),
                   "errors": sum(row.get("status") == "error" for row in timed),
                   "completion_rate": ratio(len(done), protocol["time_ranges"]),
                   "exact_set_accuracy": ratio(sum(row["exact_set_match"] for row in done), len(done)),
                   "exact_set_accuracy_all_ranges": (ratio(sum(row["exact_set_match"] for row in done), protocol["time_ranges"])
                                                     if measured else None),
                   "mean_set_recall": statistics.mean(row["set_recall"] for row in done) if done else None,
                   "mean_set_precision": statistics.mean(row["set_precision"] or 0 for row in done) if done else None,
                   "queries": timed}
    latencies = [row["elapsed_ms"] for row in cases if row.get("elapsed_ms") is not None]
    api_times = {}
    for name, phase, operation in (("summary", "合成记录", "chat"), ("embedding", "合成记录", "embedding"),
                                    ("organization", "合成整理", "chat")):
        values = [row["elapsed_ms"] for row in model_calls or [] if row.get("status") == "done"
                  and row.get("operation") == operation and row.get("context", {}).get("phase") == phase
                  and row.get("elapsed_ms") is not None]
        api_times[name] = {"calls": len(values), "mean_ms": statistics.mean(values) if values else None}
    return {"enabled": True, "protocol": protocol, "tasks": tasks, "time_retrieval": time_result,
            "processing": {"cases": len(latencies), "mean_ms": statistics.mean(latencies) if latencies else None,
                           "median_ms": statistics.median(latencies) if latencies else None,
                           "max_ms": max(latencies) if latencies else None, "model_apis": api_times,
                           "scope": "单条总耗时包含TimeIndex处理及平台取证/核验；接口耗时单列，不等于纯模型计算时间"},
            "interpretation": "记录、整理前后主题线索、主题回忆、时间查询和隐私是主结果；指定章节、精确词表和聚类单列诊断，不设统一及格分"}
