from __future__ import annotations

import json
from typing import Any

from .scoring import facts, record_alignment, ratio


def evaluate(examples: list[dict[str, Any]], inventory: list[dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge only controlled window facts that actually reached the inference request."""
    counts = {key: [0, 0] for key in ("facts_before", "facts_after", "details_before", "details_after")}
    alignments = []
    scored = 0
    for example in examples:
        requests = [call["request"] for call in calls if call["operation"] == "chat" and
                    call["context"].get("record_id") == example["record_id"]]
        text = json.dumps(requests, ensure_ascii=False)
        visible = [item for item in inventory if item.get("marker") and item["marker"] in text]
        topics, chapters = [], []
        for item in visible:
            topic = [item["topic"], item["topic"].replace(" ", "")]
            number = item["id"].split("-")[0][1:]
            chapter = [f"第{number}节", f"第{number}章", f"lesson {number}", f"section {number}"]
            if topic not in topics:
                topics.append(topic)
            if chapter not in chapters:
                chapters.append(chapter)
        original = example.get("record") or example.get("intent") or {}
        refined = example.get("refined") or {}
        checks = {"facts_before": facts(original.get("summary"), topics),
                  "facts_after": facts(refined.get("refined_summary"), topics),
                  "details_before": facts(original.get("summary"), chapters),
                  "details_after": facts(refined.get("refined_summary"), chapters)}
        example.update(expected={"facts": topics, "required_details": chapters,
                                 "source_scene_ids": [item["id"] for item in visible],
                                 "scope": "仅评分真实模型请求中出现的受控窗口主题与章节；不是文档正文理解测试"},
                       recording_check=record_alignment(example), **checks)
        if not visible:
            example["quality_note"] = "请求中未出现受控窗口；不为该条虚构内容标准答案"
        else:
            scored += 1
        for key, item in checks.items():
            counts[key][0] += item["matched"]
            counts[key][1] += item["total"]
        alignments.append(example["recording_check"])
    raw = {key: sum(item[key] for item in alignments) for key in
           ("windows_expected", "windows_preserved", "events_expected", "events_preserved", "timestamp_preserved")}
    return {"status": "done" if scored else "not_measured", "cases": len(examples), "scored_cases": scored,
            "scope": "真实请求里的受控窗口主题与章节，整理缺失按未命中计算；未评分的非测试窗口仍保留在本地证据",
            **{key: {"matched": values[0], "total": values[1], "coverage": ratio(*values)} for key, values in counts.items()},
            "raw_recording": raw}
