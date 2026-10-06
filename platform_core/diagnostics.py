"""Explain saved observations without repairing TimeIndex or changing its evidence."""
from __future__ import annotations

import json
import math
from typing import Any

from .scoring import refinement_issues, usable_text


def _response_rows(call: dict[str, Any]) -> list[dict[str, Any]] | None:
    if call.get("status") != "done":
        return None
    choices = (call.get("response") or {}).get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    text = message.get("content") or message.get("reasoning_content")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return None
    return rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None


def _id_key(value: Any) -> tuple[str, str] | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return type(value).__name__, str(value)


def batch_diagnostics(batch: dict[str, Any], cases: list[dict[str, Any]],
                      calls: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    selected = [row for row in cases if row.get("id") in batch.get("input_ids", [])
                or row.get("record_id") in batch.get("input_ids", [])]
    original_ids = batch.get("input_record_ids") or [row["record_id"] for row in selected if row.get("record_id")]
    call_ids = batch.get("model_call_ids") or []
    related = [call for call in calls if call.get("operation") == "chat" and
               (call.get("id") in call_ids or
                call.get("context", {}).get("batch_id") == batch.get("id"))]
    # Only inspect the final response; do not add retries together or alter ID types for the core.
    raw = _response_rows(related[-1]) if related else None
    returned_ids = [row.get("id") for row in raw] if raw is not None else []
    original = {_id_key(value) for value in original_ids} - {None}
    returned = {_id_key(value) for value in returned_ids} - {None}
    exact = len(original & returned) if raw is not None and original else None
    as_text = len({key[1] for key in original} & {key[1] for key in returned}) if exact is not None else None
    valid = (sum(not refinement_issues(row.get("refined") or {}) for row in selected)
             if len(selected) == len(batch.get("input_ids", [])) and selected else None)
    return {"id": batch.get("id"), "scope": scope, "input_count": len(batch.get("input_ids", [])),
            "execution_status": batch.get("status"), "execution_stage": batch.get("stage"), "error": batch.get("error"),
            "model_call_ids": [call["id"] for call in related],
            "model_response_available": raw is not None,
            "model_response_count": len(raw) if raw is not None else None,
            "model_valid_fields_count": sum(not refinement_issues(row) for row in raw) if raw is not None else None,
            "original_id_types": sorted({key[0] for key in original}),
            "model_id_types": sorted({key[0] for key in returned}),
            "exact_id_matches": exact, "text_id_matches": as_text,
            "core_returned_count": len(batch["returned_ids"]) if "returned_ids" in batch else None,
            "core_valid_result_count": batch.get("core_valid_result_count"),
            "database_update_count": batch.get("written"), "persisted_valid_count": valid,
            "interpretation": "本体返回记录数和数据库更新操作数不等于有效整理数；编号转成文本仅用于诊断对照，不改变本体输入或回复"}


def diagnose(details: dict[str, Any], vectors: dict[str, Any],
             desktop_vectors: dict[str, Any] | None = None) -> dict[str, Any]:
    calls = details.get("model_calls", [])
    findings: list[dict[str, Any]] = []
    batches = []

    def add(code: str, title: str, description: str, *, scope: str = "合成实验",
            severity: str = "error", evidence: Any = None) -> None:
        findings.append({"code": code, "title": title, "description": description,
                         "scope": scope, "severity": severity, "evidence": evidence})

    for scope, rows, saved_batches in (
        ("合成实验", details.get("cases", []), details.get("batches", {}).get("batches", [])),
        ("真实桌面", details.get("live_cases", []), details.get("desktop_batches", [])),
    ):
        for index, saved in enumerate(saved_batches, 1):
            batch = {**saved, "id": saved.get("id", f"LIVE-B{index:03d}")}
            item = batch_diagnostics(batch, rows, calls, scope)
            batches.append(item)
            complete = item["persisted_valid_count"]
            if saved.get("status") == "failed":
                add("retag_batch_error", "整理批次执行报错，已保存结果单独统计",
                    f"批次{item['id']}在{saved.get('stage', '整理')}阶段报错：{saved.get('error', '未保存错误详情')}。"
                    "此前已保存的整理结果继续按实际字段评分，报错批次的输入与进度保留供核对。",
                    scope=scope, evidence=item)
            if item["exact_id_matches"] is not None and item["text_id_matches"] > item["exact_id_matches"]:
                labels = {"str": "文本", "float": "小数数字", "int": "整数数字"}
                add("retag_id_types", "整理回复中的记录编号类型不同",
                    f"原编号为{'、'.join(labels.get(kind, kind) for kind in item['original_id_types'])}，"
                    f"模型回复为{'、'.join(labels.get(kind, kind) for kind in item['model_id_types'])}；"
                    f"按原类型对照匹配{item['exact_id_matches']}条，转成文本对照匹配{item['text_id_matches']}条。"
                    "若有效整理仍缺失，可优先核查本体的编号匹配；此对照不会修改回复或补写整理结果。",
                    scope=scope, severity="warning" if complete != item["input_count"] else "info", evidence=item)
            if complete is not None and complete < item["input_count"]:
                returned = item["model_response_count"]
                updates = item["database_update_count"]
                add("retag_incomplete", "有效整理结果未全部保存",
                    f"最终有效整理{complete}/{item['input_count']}条；模型原始结果数：{returned if returned is not None else '未保存或无法解析'}，"
                    f"数据库更新操作数：{updates if updates is not None else '未保存'}。接口请求完成或更新了记录，不代表整理字段完整有效。",
                    scope=scope, evidence=item)
        placeholders = [row["id"] for row in rows if
                        (value := (row.get("refined") or {}).get("refined_summary")) and not usable_text(value)]
        if placeholders:
            add("invalid_refined_summary", "整理摘要中出现无效占位值",
                f"{len(placeholders)}条整理摘要为无效占位值。新版新跑的关键词对照会回退到有效的原始摘要；"
                "历史查询仍按当时保存的返回结果统计，复算不会补造新的检索结果。",
                scope=scope, severity="warning", evidence={"case_ids": placeholders})

    for scope, check, phase in (("合成实验", vectors, "合成记录"),
                                ("真实桌面", desktop_vectors or {}, "真实桌面记录")):
        if check.get("status") != "failed":
            continue
        valid_requests = 0
        for call in calls:
            if call.get("operation") != "embedding" or call.get("status") != "done" or call.get("context", {}).get("phase") != phase:
                continue
            for row in (call.get("response") or {}).get("data", []):
                vector = row.get("embedding")
                if (isinstance(vector, list) and len(vector) == check.get("dimension", 768) and
                        all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector) and any(vector)):
                    valid_requests += 1
        changed = [row["id"] for row in check.get("issues", []) if
                   any("改变了原始检索向量" in reason for reason in row.get("reasons", []))]
        add("vector_integrity", "检索向量检查失败，语义质量未测",
            (f"记录阶段有{valid_requests}次接口返回有效向量；" if valid_requests else "") +
            f"整理后检查为{check.get('valid', 0)}/{check.get('total', 0)}条有效，{len(changed)}条原有向量发生改变。"
            "因此暂停语义效果评分，不把这项写成零分；具体故障需核查本体读写过程。",
            scope=scope, evidence={"vector_check": check, "valid_embedding_responses": valid_requests})
    fallback = details.get("fault_injection", {})
    if fallback.get("recorded") is False:
        add("fallback_not_recorded", "受控模型失联场景未保存原始记录",
            "这是平台主动注入的失联场景，其处理步骤结束但没有找到入库记录；不表示正常模型调用发生了真实断连。",
            scope="故障自检", evidence=fallback)
    return {"schema_version": 1, "findings": findings, "organization_batches": batches,
            "model_requests": {"saved": len(calls), "completed": sum(call.get("status") == "done" for call in calls),
                               "failed": sum(call.get("status") == "error" for call in calls)},
            "limits": "根据保存的原文和最终字段诊断；不修复本体、不改写模型输出、不重新查询历史实验"}
