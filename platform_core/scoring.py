from __future__ import annotations

import re
import unicodedata
import json
from datetime import datetime
from itertools import combinations
from typing import Any
from .dataset import TAG_ALIASES

SCORING_VERSION = "4.4"


def norm(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def usable_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in {"", "nan", "null", "none"}


def text_norm(value: Any) -> str:
    text = re.sub(r"\s+", " ", norm(value)).strip()
    # Ignore layout spaces next to Chinese characters, while retaining English word boundaries.
    return re.sub(r"(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])", "", text)


def text_contains(text: Any, phrase: Any) -> bool:
    return usable_text(text) and bool(text_norm(phrase)) and text_norm(phrase) in text_norm(text)


def refinement_issues(refined: dict[str, Any]) -> list[str]:
    problems = []
    if not usable_text(refined.get("refined_summary")):
        problems.append("整理摘要缺失或无效")
    tags = refined.get("refined_tags")
    if not isinstance(tags, list) or not all(usable_text(tag) for tag in tags):
        problems.append("整理标签缺失或无效")
    cluster = refined.get("cluster_id")
    if (not isinstance(cluster, (str, int)) or isinstance(cluster, bool)
            or not usable_text(str(cluster))):
        problems.append("整理分组缺失或无效")
    return problems


def record_alignment(row: dict[str, Any]) -> dict[str, Any]:
    source, stored = row.get("input") or {}, row.get("record") or {}
    def decode(values: list[Any]) -> list[dict[str, Any]]:
        result = []
        for value in values:
            try:
                item = json.loads(value) if isinstance(value, str) else value
                if isinstance(item, dict):
                    result.append(item)
            except (TypeError, ValueError):
                pass
        return result
    windows = decode(stored.get("active_windows") or [])
    events = decode(stored.get("process_events") or [])
    expected_windows, expected_events = source.get("windows") or [], source.get("process_events") or []
    window_matches = sum(any(value.get("title") == item.get("title") and value.get("pid") == item.get("pid") and
                             value.get("process", value.get("process_name")) == item.get("process_name") for value in windows)
                         for item in expected_windows)
    event_matches = sum(any(value.get("type", value.get("event_type")) == item.get("event_type") and
                            value.get("pid") == item.get("pid") and value.get("process", value.get("process_name")) == item.get("process_name")
                            for value in events) for item in expected_events)
    try:
        timestamp_match = datetime.fromisoformat(str(source["timestamp"])) == datetime.fromisoformat(str(stored["timestamp"]))
    except (KeyError, TypeError, ValueError):
        timestamp_match = False
    return {"windows_expected": len(expected_windows), "windows_preserved": window_matches,
            "events_expected": len(expected_events), "events_preserved": event_matches,
            "timestamp_preserved": timestamp_match}


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def facts(summary: str | None, expected: list[list[str]]) -> dict[str, Any]:
    missing = [alternatives for alternatives in expected
               if not any(text_contains(summary, word) for word in alternatives)]
    return {"matched": len(expected) - len(missing), "total": len(expected),
            "coverage": ratio(len(expected) - len(missing), len(expected)),
            "missing": missing, "rule_scope": "仅评估预先列出的事实与同义表达"}


def tag_totals(rows: list[tuple[list[str], list[str]]]) -> dict[str, Any]:
    tp = fp = fn = 0
    for expected, actual in rows:
        gold, predicted = set(map(norm, expected)), set(map(norm, actual))
        tp += len(gold & predicted)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    return {"cases": len(rows), "tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": ratio(2 * tp, 2 * tp + fp + fn)}


def pairwise_clusters(gold: dict[str, str], predicted: dict[str, str | None]) -> dict[str, Any]:
    ids = sorted(gold)
    if len(ids) < 2:
        return {"status": "not_measured", "reason": "至少需要两条记录"}
    tp = fp = fn = 0
    missing = []
    for left, right in combinations(ids, 2):
        if not usable_text(str(predicted.get(left))) or not usable_text(str(predicted.get(right))):
            missing.append([left, right])
            continue
        same_gold = gold[left] == gold[right]
        same_predicted = predicted[left] == predicted[right]
        tp += int(same_gold and same_predicted)
        fp += int(not same_gold and same_predicted)
        fn += int(same_gold and not same_predicted)
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    return {"status": "done" if not missing else "partial", "tp": tp, "fp": fp,
            "fn": fn, "missing_pairs": missing, "precision": precision,
            "recall": recall, "f1": ratio(2 * tp, 2 * tp + fp + fn)}


def rank_query(relevant_ids: list[str], returned_ids: list[str], latency_ms: float | None,
               error: str | None = None) -> dict[str, Any]:
    if error:
        return {"status": "error", "error": error, "returned_ids": returned_ids,
                "latency_ms": latency_ms}
    targets = set(relevant_ids)
    rank = next((number for number, item in enumerate(returned_ids[:5], 1) if item in targets), None)
    return {"status": "done", "returned_ids": returned_ids, "latency_ms": latency_ms,
            "hit_at_1": int(bool(returned_ids) and returned_ids[0] in targets),
            "hit_at_5": int(rank is not None), "mrr_at_5": 1 / rank if rank else 0.0}


def complete_query_rows(planned: list[dict[str, Any]], saved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add unmeasured analysis rows; never change the saved query evidence."""
    saved_ids = {row["id"] for row in saved}
    missing = [{"id": query["id"], "query": query["text"], "relevant_ids": query["relevant_ids"],
                "status": "not_measured", "reason": "未保存本题的执行结果，无法评分",
                "execution_evidence_missing": True}
               for query in planned if query["id"] not in saved_ids]
    return [dict(row) for row in saved] + missing


def query_completion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = sum(row.get("status") == "done" for row in rows)
    errors = sum(row.get("status") == "error" for row in rows)
    if rows and complete == len(rows):
        return {"status": "done", "reason": None}
    return {"status": "partial" if complete else "failed" if errors else "not_measured",
            "reason": f"查询完成 {complete}/{len(rows)}；错误 {errors}；未完成或未测 {len(rows) - complete - errors}"}


def aggregate_queries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row.get("status") == "done"]
    return {"total": len(rows), "scored": len(done),
            "errors": sum(row.get("status") == "error" for row in rows),
            "not_measured": sum(row.get("status") == "not_measured" for row in rows),
            "hit_at_1": ratio(sum(row["hit_at_1"] for row in done), len(done)),
            "hit_at_5": ratio(sum(row["hit_at_5"] for row in done), len(done)),
            "mrr_at_5": ratio(sum(row["mrr_at_5"] for row in done), len(done)),
            "mean_latency_ms": (sum(row["latency_ms"] for row in done if row["latency_ms"] is not None) /
                                len([row for row in done if row["latency_ms"] is not None])
                                if any(row["latency_ms"] is not None for row in done) else None)}


def recording(expected: list[dict[str, Any]], observed: list[dict[str, Any]],
              *, tolerance_s: float = 12.0) -> dict[str, Any]:
    """Match by kind, PID/title and time; retain duplicates as a separate count."""
    remaining = set(range(len(observed)))
    matches = []
    misses = []
    duplicate_ids = set()
    for target in expected:
        candidates = []
        for index in remaining:
            actual = observed[index]
            if target.get("kind") != actual.get("kind"):
                continue
            if target.get("pid") is not None and target["pid"] != actual.get("pid"):
                continue
            if target.get("event_type") and target["event_type"] != actual.get("event_type"):
                continue
            if target.get("title") and norm(target["title"]) != norm(actual.get("title")):
                continue
            delta = float(actual.get("time", 0)) - float(target.get("time", 0))
            if 0 <= delta <= tolerance_s:
                candidates.append((delta, index))
        if not candidates:
            misses.append(target)
            continue
        _, chosen = min(candidates)
        remaining.remove(chosen)
        matches.append({"expected": target, "observed": observed[chosen],
                        "latency_s": float(observed[chosen]["time"]) - float(target["time"])})
        for _, index in candidates:
            if index in remaining:
                remaining.remove(index)
                duplicate_ids.add(index)
    extras = [observed[index] for index in sorted(remaining)]
    return {"expected": len(expected), "matched": len(matches), "recall": ratio(len(matches), len(expected)),
            "missed": misses, "false_positives": extras, "false_positive_count": len(extras),
            "duplicate_count": len(duplicate_ids), "duplicates": [observed[i] for i in sorted(duplicate_ids)],
            "matches": matches}


def summarize_cases(dataset: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    gold = {case["id"]: case for case in dataset["cases"]}
    comparable = [row for row in cases if row.get("status") in {"done", "record_error"} and row["id"] in gold]
    scored = [row for row in comparable if not row.get("fallback")]
    completed = [row for row in scored if not refinement_issues(row.get("refined") or {})]

    def fact_totals(rows: list[dict[str, Any]], after: bool) -> dict[str, Any]:
        results = [facts((row.get("refined") or {}).get("refined_summary") if after else
                         (row.get("record") or row.get("intent") or {}).get("summary"),
                         gold[row["id"]]["facts"]) for row in rows]
        matched, total = sum(r["matched"] for r in results), sum(r["total"] for r in results)
        return {"matched": matched, "total": total, "coverage": ratio(matched, total)}

    def tag_scores(rows: list[dict[str, Any]], after: bool) -> dict[str, Any]:
        pairs = []
        for row in rows:
            values = ((row.get("refined") or {}).get("refined_tags") if after else
                      (row.get("record") or row.get("intent") or {}).get("tags"))
            pairs.append((gold[row["id"]]["tags"], values if isinstance(values, list) else []))
        return tag_totals(pairs)

    issues = [{"id": row["id"], "reasons": refinement_issues(row.get("refined") or {})}
              for row in scored if refinement_issues(row.get("refined") or {})]
    app_matches = 0
    cue_before = cue_after = 0
    cue_details = []
    detail_before = detail_after = detail_total = 0
    app_issues = []
    for row in scored:
        expected = gold[row["id"]]
        original = row.get("record") or row.get("intent") or {}
        accepted = expected.get("tag_aliases") or [alias for tag in expected["tags"] for alias in TAG_ALIASES.get(tag, [tag])]
        matches_before = [tag for tag in original.get("tags") or [] if any(text_contains(tag, alias) for alias in accepted)]
        matches_after = [tag for tag in (row.get("refined") or {}).get("refined_tags") or []
                         if any(text_contains(tag, alias) for alias in accepted)]
        cue_before += int(bool(matches_before))
        cue_after += int(bool(matches_after))
        cue_details.append({"id": row["id"], "accepted_cues": accepted,
                            "before_matches": matches_before, "after_matches": matches_after})
        aliases = expected.get("app_aliases") or [expected["process"].removesuffix(".exe"),
                                                   expected["title"].rsplit(" - ", 1)[-1]]
        matched = any(text_contains(original.get("primary_app"), alias) for alias in aliases)
        app_matches += int(matched)
        if not matched:
            app_issues.append({"id": row["id"], "reason": "主要应用未匹配输入进程或窗口应用名称"})
        details = expected.get("required_details") or re.findall(r"第\s*\d+\s*节", expected["title"])
        for detail in details:
            detail_total += 1
            detail_before += int(text_contains(original.get("summary"), detail))
            detail_after += int(text_contains((row.get("refined") or {}).get("refined_summary"), detail))
    return {"cases_total": len(dataset["cases"]), "cases_processed": len(cases),
            "cases_done": sum(row.get("status") == "done" for row in cases), "cases_scored": len(scored),
            "raw_recording": {"windows_expected": sum(record_alignment(row)["windows_expected"] for row in cases),
                              "windows_preserved": sum(record_alignment(row)["windows_preserved"] for row in cases),
                              "events_expected": sum(record_alignment(row)["events_expected"] for row in cases),
                              "events_preserved": sum(record_alignment(row)["events_preserved"] for row in cases),
                              "timestamps_preserved": sum(record_alignment(row)["timestamp_preserved"] for row in cases)},
            "comparison_ids": [row["id"] for row in scored],
            "facts_before": fact_totals(scored, False), "facts_after": fact_totals(scored, True),
            "tags_before": tag_scores(scored, False), "tags_after": tag_scores(scored, True),
            "retag_complete": len(completed), "retag_total": len(scored), "issues": issues,
            "paired": {"cases": len(completed), "ids": [row["id"] for row in completed],
                       "facts_before": fact_totals(completed, False), "facts_after": fact_totals(completed, True),
                       "tags_before": tag_scores(completed, False), "tags_after": tag_scores(completed, True)},
            "primary_app": {"matched": app_matches, "total": len(scored),
                            "accuracy": ratio(app_matches, len(scored)), "issues": app_issues},
            "tag_cues_before": {"matched": cue_before, "total": len(scored), "coverage": ratio(cue_before, len(scored))},
            "tag_cues_after": {"matched": cue_after, "total": len(scored), "coverage": ratio(cue_after, len(scored))},
            "tag_cue_details": cue_details,
            "details_before": {"matched": detail_before, "total": detail_total,
                               "coverage": ratio(detail_before, detail_total)},
            "details_after": {"matched": detail_after, "total": detail_total,
                              "coverage": ratio(detail_after, detail_total)},
            "cluster": pairwise_clusters({row["id"]: gold[row["id"]]["group"] for row in scored},
                                         {row["id"]: (row.get("refined") or {}).get("cluster_id") for row in scored})}

