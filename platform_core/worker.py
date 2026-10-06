from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import atomic_json, now, read_json
from .isolation import cleanup, preserve_database, runtime_paths, verify_import
from .manifest import run_path, section, update_run
from .model_trace import ModelTrace
from .paper import keyword_match, run_time_queries
from .preflight import check
from .privacy import image_fields, is_loopback
from .reporting import export
from .resources import ResourceSampler
from .scoring import complete_query_rows, query_completion, rank_query, refinement_issues, summarize_cases
from .validation import audit_vectors


def _cancelled(run_id: str) -> bool:
    return (run_path(run_id) / "cancel.flag").exists()


def _write_evidence(run_id: str, name: str, value: Any) -> None:
    atomic_json(run_path(run_id) / "evidence" / name, value)


def _records(daemon: Any) -> list[dict[str, Any]]:
    return daemon.db_store.store.get_table().to_arrow().to_pylist()


def _make_snapshot(case: dict[str, Any], index: int) -> Any:
    from TimeIndex.daemon.wmi_monitor import ProcessEvent, SystemSnapshot, WindowInfo

    timestamp = datetime.now()
    pid = 10000 + index
    return SystemSnapshot(
        timestamp=timestamp,
        process_events=[ProcessEvent(timestamp=timestamp, event_type=case.get("event_type", "created"),
                                     process_name=case["process"], pid=pid)],
        windows=[WindowInfo(hwnd=pid, title=case["title"], pid=pid,
                            process_name=case["process"])],
        hardware=None)


def _refresh_refinements(daemon: Any, cases: list[dict[str, Any]]) -> None:
    updated = {row["id"]: row for row in _records(daemon)}
    for item in cases:
        item["refined"] = {key: updated.get(item["record_id"], {}).get(key)
                           for key in ("refined_summary", "refined_tags", "cluster_id")}
        item["organization_status"] = "incomplete" if refinement_issues(item["refined"]) else "done"


def _organize_records(run_id: str, daemon: Any, dataset: dict[str, Any], cases: list[dict[str, Any]],
                      id_map: dict[str, str], trace: ModelTrace) -> None:
    batches: list[dict[str, Any]] = []
    batch_size = min(50, daemon._retag_batch_size)

    def save_batches() -> None:
        _write_evidence(run_id, "retag_batches.json",
                        {"batch_size": batch_size, "origin": "TimeIndex Daemon.retag_batch_size", "batches": batches})

    try:
        pending = [row for row in daemon.db_store.get_pending_retag(daemon.db_store.get_count())
                   if row["id"] in id_map]
        save_batches()
        for offset in range(0, len(pending), batch_size):
            if _cancelled(run_id):
                break
            batch = pending[offset:offset + batch_size]
            batch_id = f"B{len(batches) + 1:03d}"
            case_ids = [id_map.get(row["id"], row["id"]) for row in batch]
            evidence = {"id": batch_id, "input_ids": case_ids,
                        "input_record_ids": [row["id"] for row in batch], "status": "running",
                        "stage": "model", "model_call_ids": [],
                        "returned_ids_meaning": "本体函数返回的记录列表，可能包括未获有效整理的原记录；有效结果数单列",
                        "written_meaning": "数据库更新操作数，不代表整理字段完整有效；最终有效数见报告"}
            batches.append(evidence)
            save_batches()
            try:
                with trace.scope(phase="合成整理", batch_id=batch_id, case_ids=case_ids):
                    retagged = daemon.llm_processor.retag_cluster(batch)
                evidence.update(returned_ids=[id_map.get(row["id"], row["id"]) for row in retagged],
                                core_valid_result_count=sum(not refinement_issues(row) for row in retagged),
                                stage="database_update")
                save_batches()
                written = daemon.db_store.update_retag_records(retagged) if retagged else 0
                evidence.update(written=written, status="done", stage="finished")
            except Exception as error:
                evidence.update(status="failed", error=str(error))
                raise
            finally:
                evidence["model_call_ids"] = [call["id"] for call in trace.calls
                                              if call["context"].get("batch_id") == batch_id]
                save_batches()
            _refresh_refinements(daemon, cases)
            _write_evidence(run_id, "cases.json", cases)
        _refresh_refinements(daemon, cases)
        scored = summarize_cases(dataset, cases)
        complete, total = scored["retag_complete"], scored["retag_total"]
        section(run_id, "organization", "done" if complete == total and total else "partial" if complete else "failed",
                None if complete == total and total else
                f"整理完成 {complete}/{total}；缺失或无效记录：" + ", ".join(row["id"] for row in scored["issues"]))
    except Exception as error:
        reason = str(error)
        try:
            # Read the actual database even if an update raised after saving some rows.
            _refresh_refinements(daemon, cases)
        except Exception as refresh_error:
            reason += f"；最终整理字段读取失败：{refresh_error}；保留此前已核验的结果"
        section(run_id, "organization", "failed", reason)
    finally:
        _write_evidence(run_id, "cases.json", cases)


def _fallback_case(run_id: str, daemon: Any, case: dict[str, Any]) -> dict[str, Any]:
    """Fault injection makes client calls fail immediately; TimeIndex handles the errors."""
    from TimeIndex.db.embedding_provider import embedding_provider

    old_chat, old_embedding = daemon.llm_processor.client, embedding_provider.client
    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("受控故障注入：本地模型不可用")

    class Endpoint:
        def __init__(self, **items: Any):
            self.__dict__.update(items)

    daemon.llm_processor.client = Endpoint(chat=Endpoint(completions=Endpoint(create=unavailable)))
    embedding_provider.client = Endpoint(embeddings=Endpoint(create=unavailable))
    snapshot = _make_snapshot(case, 9000)
    record_id = str(snapshot.timestamp.timestamp())
    try:
        started = time.perf_counter()
        daemon._process_snapshot(snapshot)
        found = next((row for row in _records(daemon) if row["id"] == record_id), None)
        result = {"status": "done", "recorded": found is not None,
                  "record_id": record_id, "record": {key: value for key, value in (found or {}).items()
                                               if key != "vector"},
                  "elapsed_ms": (time.perf_counter() - started) * 1000,
                  "expected": "模型不可用时检查是否仍有可用记录"}
        _write_evidence(run_id, "fallback.json", result)
        return result
    finally:
        daemon.llm_processor.client = old_chat
        embedding_provider.client = old_embedding


def _run_synthetic(run_id: str, dataset: dict[str, Any], ready: bool,
                   embeddings_ready: bool, trace: ModelTrace) -> tuple[list[dict[str, Any]],
                                                                               dict[str, list[dict[str, Any]]],
                                                                               dict[str, Any]]:
    from TimeIndex.daemon.daemon import Daemon
    from TimeIndex.db.embedding_provider import embedding_provider

    daemon = Daemon()
    daemon.llm_processor.client = trace.wrap(daemon.llm_processor.client)
    original_embedding_client = embedding_provider.client
    embedding_provider.client = trace.wrap(original_embedding_client)
    captures: dict[str, Any] = {"prompts": [], "embeddings": [], "embedding_checks": [], "intents": [], "snapshots": []}
    original_prompt = daemon.llm_processor._build_intent_prompt
    original_retag_prompt = daemon.llm_processor._build_retag_prompt
    original_infer = daemon.llm_processor.infer_intent
    original_embedding = embedding_provider.get_embedding

    def prompt(snapshot: Any) -> str:
        value = original_prompt(snapshot)
        captures["prompts"].append({"kind": "infer", "text": value})
        return value

    def retag_prompt(records: Any) -> str:
        value = original_retag_prompt(records)
        captures["prompts"].append({"kind": "retag", "text": value})
        return value

    def infer(snapshot: Any) -> dict[str, Any]:
        value = original_infer(snapshot)
        captures["intents"].append(value)
        return value

    def embedding(text: str) -> list[float]:
        captures["embeddings"].append(text)
        value = original_embedding(text) if embeddings_ready else []
        checked = audit_vectors([{"id": "request", "vector": None}], [{"id": "request", "vector": value}])
        captures["embedding_checks"].append({"dimension": len(value), "valid": checked["status"] == "done"})
        return value

    daemon.llm_processor._build_intent_prompt = prompt
    daemon.llm_processor._build_retag_prompt = retag_prompt
    daemon.llm_processor.infer_intent = infer
    embedding_provider.get_embedding = embedding
    cases: list[dict[str, Any]] = []
    queries: dict[str, list[dict[str, Any]]] = {name: [] for name in ("semantic", "keyword", "tags")}
    if dataset.get("evaluation_profile") == "paper":
        queries["title_keyword"] = []
    baseline_store: Any = None
    try:
        fallback = _fallback_case(run_id, daemon, dataset["cases"][0])
        section(run_id, "fallback", "done" if fallback["recorded"] else "failed",
                None if fallback["recorded"] else "实际管线在模型失联后未能成功入库")
        if not ready:
            section(run_id, "recording", "not_measured", "本地模型服务未就绪；仅运行故障注入场景")
            section(run_id, "organization", "not_measured", "本地模型服务未就绪")
            section(run_id, "retrieval_semantic", "not_measured", "本地嵌入模型服务未就绪")
            section(run_id, "retrieval_baselines", "not_measured", "无正常模型生成的记录")
            return cases, queries, captures

        id_map: dict[str, str] = {}
        for index, case in enumerate(dataset["cases"]):
            if _cancelled(run_id):
                break
            snapshot = _make_snapshot(case, index)
            captures["snapshots"].append({"case_id": case["id"], "timestamp": snapshot.timestamp.isoformat(),
                                          "windows": [{"title": item.title, "process_name": item.process_name,
                                                       "pid": item.pid} for item in snapshot.windows],
                                          "process_events": [{"event_type": item.event_type,
                                                              "process_name": item.process_name, "pid": item.pid}
                                                             for item in snapshot.process_events]})
            record_id = str(snapshot.timestamp.timestamp())
            before_intents = len(captures["intents"])
            started = time.perf_counter()
            with trace.scope(phase="合成记录", case_id=case["id"], record_id=record_id):
                daemon._process_snapshot(snapshot)
            elapsed_ms = (time.perf_counter() - started) * 1000
            record = next((row for row in _records(daemon) if row["id"] == record_id), None)
            intent = captures["intents"][-1] if len(captures["intents"]) > before_intents else {}
            row = {"id": case["id"], "record_id": record_id,
                   "status": "done" if record else "record_error" if intent else "error",
                   "elapsed_ms": elapsed_ms,
                   "input": captures["snapshots"][-1], "intent": intent,
                   "fallback": bool(intent.get("fallback")),
                   "input_kind": "synthetic_snapshot", "actually_launched": False,
                   "model_call_ids": [call["id"] for call in trace.calls if call["context"].get("case_id") == case["id"]],
                   "record": {key: value for key, value in (record or {}).items() if key != "vector"},
                   "error": None if record else "TimeIndex 处理后未找到入库记录"}
            if record:
                id_map[record_id] = case["id"]
            cases.append(row)
            _write_evidence(run_id, "cases.json", cases)
            update_run(run_id, completed_cases=index + 1)
            if index == 0 and row["fallback"]:
                raise RuntimeError("摘要模型首次推理返回了降级结果；请检查模型名称、服务日志和模型输出格式")

        saved = [row for row in _records(daemon) if row["id"] in id_map]
        before_retag = saved
        if saved:
            _organize_records(run_id, daemon, dataset, cases, id_map, trace)
        else:
            section(run_id, "organization", "partial", "仅评价推理摘要；无入库记录可供重整理")
            for item in cases:
                item["refined"] = {}
        _write_evidence(run_id, "cases.json", cases)
        recorded = sum(row["status"] == "done" for row in cases)
        section(run_id, "recording", "done" if recorded == len(dataset["cases"]) else
                "partial" if recorded else "failed",
                None if recorded == len(dataset["cases"]) else f"成功入库 {recorded}/{len(dataset['cases'])}")

        saved = [row for row in _records(daemon) if row["id"] in id_map]
        vectors = audit_vectors(before_retag, saved) if embeddings_ready else {
            "status": "not_measured", "reason": "嵌入模型不可用，原始记录已保存但向量未测", "issues": [],
            "total": len(saved), "valid": 0, "preserved": 0}
        for issue in vectors["issues"]:
            issue["id"] = id_map.get(issue["id"], issue["id"])
        _write_evidence(run_id, "vector_integrity.json", vectors)
        section(run_id, "retrieval_vectors", vectors["status"],
                "整理后检索向量无效，语义评分暂停" if vectors["status"] == "failed" else None)

        query_store = daemon.db_store
        corpus = saved
        if not embeddings_ready and not saved:
            # Keep retrieval baselines separate from the production write outcome.
            # Zero vectors are a test fixture; these records never count as captured activities.
            from TimeIndex.db.vector_store import TimeIndexStore
            fixture_records = []
            for item in cases:
                intent = item.get("intent") or {}
                if not intent.get("summary") or item.get("fallback"):
                    continue
                fixture_id = f"fixture-{item['id']}"
                fixture_records.append({"id": fixture_id, "timestamp": item["input"]["timestamp"],
                                        "summary": intent["summary"], "tags": intent.get("tags") or [],
                                        "primary_app": intent.get("primary_app") or "unknown",
                                        "vector": [0.0] * 768})
                id_map[fixture_id] = item["id"]
            if fixture_records:
                baseline_store = TimeIndexStore(db_path=str(run_path(run_id) / "evidence" / "baseline_db"))
                baseline_store.add_activity_batch(fixture_records)
                query_store = baseline_store
                corpus = baseline_store.store.get_table().to_arrow().to_pylist()
            _write_evidence(run_id, "retrieval_corpus.json",
                            {"kind": "seeded_from_inference", "records": len(fixture_records),
                             "reason": "嵌入服务不可用；零向量仅用于关键词和标签对照，不计入记录效果"})

        for query in dataset["queries"]:
            if _cancelled(run_id):
                break
            for method in queries:
                found = []
                started = time.perf_counter()
                try:
                    if not corpus:
                        scored = {"status": "not_measured", "reason": "没有可用的对照记录"}
                        queries[method].append({"id": query["id"], "query": query["text"],
                                                "relevant_ids": query["relevant_ids"], **scored})
                        continue
                    if method == "semantic":
                        if not embeddings_ready:
                            scored = {"status": "not_measured", "reason": "嵌入模型不可用"}
                            queries[method].append({"id": query["id"], "query": query["text"],
                                                    "relevant_ids": query["relevant_ids"], **scored})
                            continue
                        if vectors["status"] != "done":
                            queries[method].append({"id": query["id"], "query": query["text"],
                                                    "relevant_ids": query["relevant_ids"], "status": "not_measured",
                                                    "reason": "整理后向量完整性检查未通过，未执行语义查询"})
                            continue
                        previous_checks = len(captures["embedding_checks"])
                        with trace.scope(phase="合成回忆", query_id=query["id"], method=method):
                            found = query_store.search_activities(query["text"], limit=5)
                        if (len(captures["embedding_checks"]) == previous_checks or
                                not captures["embedding_checks"][-1]["valid"]):
                            raise ValueError("查询嵌入无效；关键词降级结果不计为语义命中")
                    elif method == "tags":
                        found = query_store.get_activities_by_tags(query["tags"], limit=5)
                    else:
                        found = [row for row in reversed(corpus)
                                 if keyword_match(row, query, raw_title=method == "title_keyword")][:5]
                    result_ids = [id_map.get(str(item.get("id")), "unknown") for item in found]
                    scored = rank_query(query["relevant_ids"], result_ids,
                                        (time.perf_counter() - started) * 1000)
                except Exception as error:
                    scored = rank_query(query["relevant_ids"], [],
                                        (time.perf_counter() - started) * 1000, str(error))
                returned = [{"rank": rank, "case_id": id_map.get(str(item.get("id")), "unknown"),
                             **{key: item.get(key) for key in ("id", "summary", "refined_summary", "tags", "primary_app", "active_windows")}}
                            for rank, item in enumerate(found, 1)] if scored.get("status") == "done" else []
                queries[method].append({"id": query["id"], "query": query["text"],
                                        "task": query.get("task", "unspecified"),
                                        "keyword": query["keyword"], "keyword_terms": query.get("keyword_terms", [query["keyword"]]),
                                        "tags_requested": query["tags"],
                                        "corpus_size": len(corpus), "returned_records": returned,
                                        "model_call_ids": [call["id"] for call in trace.calls if call["context"].get("query_id") == query["id"]],
                                        "relevant_ids": query["relevant_ids"], **scored})
            _write_evidence(run_id, "queries.json", queries)
        if dataset.get("evaluation_profile") == "paper":
            queries["time"] = run_time_queries(dataset, cases, daemon.db_store, id_map,
                                                 lambda: _cancelled(run_id))
            _write_evidence(run_id, "time_queries.json", queries["time"])
            _write_evidence(run_id, "queries.json", queries)
            complete = sum(row.get("status") == "done" for row in queries["time"])
            attempted = sum(row.get("status") in {"done", "error"} for row in queries["time"])
            expected = dataset["protocol"]["time_ranges"]
            section(run_id, "retrieval_time", "done" if complete == expected else "partial" if complete else "failed" if attempted else "not_measured",
                    None if complete == expected else f"时间范围查询完成 {complete}/{expected}")
        semantic = query_completion(complete_query_rows(dataset["queries"], queries["semantic"]))
        section(run_id, "retrieval_semantic", "not_measured" if not embeddings_ready or vectors["status"] != "done" else semantic["status"],
                "嵌入模型不可用" if not embeddings_ready else "检索向量无效或未核验，未执行语义查询" if vectors["status"] != "done" else semantic["reason"])
        baseline_rows = [row for method in queries if method not in {"semantic", "time"}
                         for row in complete_query_rows(dataset["queries"], queries[method])]
        baselines = query_completion(baseline_rows)
        section(run_id, "retrieval_baselines", baselines["status"], baselines["reason"])
        return cases, queries, captures
    finally:
        embedding_provider.get_embedding = original_embedding
        embedding_provider.client = original_embedding_client
        if baseline_store is not None:
            baseline_store.close()
        daemon.db_store.close()


def _final_status(manifest: dict[str, Any], dataset: dict[str, Any], cases: list[dict[str, Any]],
                  sections: dict[str, Any], embeddings_ready: bool, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    if manifest.get("allow_no_model"):
        return "diagnostic"
    required = {"organization", "retrieval_vectors", "retrieval_semantic", "retrieval_baselines",
                "retrieval_time", "live_recording", "model_evidence", "fallback"}
    if ((manifest.get("desktop_mode") == "real_applications" and
         sections.get("live_recording", {}).get("status") != "done") or not embeddings_ready or
            (manifest.get("resources") and sections.get("resources", {}).get("status") != "done") or
            len(cases) < len(dataset["cases"]) or any(item["status"] != "done" for item in cases) or
            any(value.get("status") in {"failed", "partial"} for name, value in sections.items() if name in required)):
        return "partial"
    return "done"


def run(run_id: str) -> None:
    directory = run_path(run_id)
    manifest = read_json(directory / "manifest.json")
    sampler: ResourceSampler | None = None
    cancelled = False
    database_preserved = False
    final_status = "failed"
    trace = ModelTrace(directory / "evidence" / "model_calls.json")
    try:
        imported = verify_import(run_id)
        _write_evidence(run_id, "import_paths.json", imported)
        preflight = read_json(directory / "evidence" / "preflight.json") or check(
            manifest["endpoint"], timeindex_project=manifest.get("timeindex_project"))
        dataset = read_json(directory / "dataset.json")
        if manifest["resources"]:
            sampler = ResourceSampler(os.getpid(), manifest.get("model_pid"), runtime_paths(run_id)["database"],
                                      extra_databases=(directory / "evidence" / "desktop_db",))
            sampler.start()
            time.sleep(1)
            sampler.phase = "active"
        else:
            section(run_id, "resources", "not_selected")
        ready = bool(preflight["model_reachable"] and preflight["endpoint_allowed"] and
                     not manifest.get("allow_no_model"))
        embeddings_ready = False
        if ready:
            from openai import OpenAI
            from TimeIndex.utils.config import config

            try:
                client = trace.wrap(OpenAI(base_url=config.llm_base_url, api_key=config.llm_api_key,
                                          timeout=60, max_retries=0))
                with trace.scope(phase="模型连接检查"):
                    response = client.chat.completions.create(
                        model=manifest["model"], messages=[{"role": "user", "content": "请简短回复 OK"}],
                        max_tokens=32)
                message = response.choices[0].message
                if not (message.content or getattr(message, "reasoning_content", None)):
                    raise ValueError("模型返回空文本")
                _write_evidence(run_id, "chat_probe.json",
                                {"status": "done", "requested_model": manifest["model"],
                                 "returned_model": response.model})
                section(run_id, "model", "done")
            except Exception as error:
                section(run_id, "model", "failed", str(error))
                raise RuntimeError(f"摘要模型调用失败：{error}") from error
            embedding_error = None
            try:
                with trace.scope(phase="向量连接检查"):
                    embedding_response = client.embeddings.create(
                        model=manifest["embedding_model"], input="TimeIndex embedding probe")
                probe_vector = embedding_response.data[0].embedding
                dimension = len(probe_vector)
                embeddings_ready = audit_vectors([{"id": "probe", "vector": None}],
                                                  [{"id": "probe", "vector": probe_vector}])["status"] == "done"
                if not embeddings_ready:
                    embedding_error = f"返回向量无效（{dimension} 维）；需要 768 维有限非零数值"
            except Exception as error:
                dimension = 0
                embedding_error = str(error)
            _write_evidence(run_id, "embedding_probe.json",
                            {"ready": embeddings_ready, "dimension": dimension,
                             "expected_dimension": 768, "error": embedding_error})
            section(run_id, "embedding_model", "done" if embeddings_ready else "failed", embedding_error)
        else:
            section(run_id, "model", "not_measured", "已选择无模型自检；不会运行模型质量场景")
            section(run_id, "embedding_model", "not_measured", "未运行模型调用")
        if manifest["mode"] == "desktop":
            cases, queries = [], {}
            captures = {"prompts": [], "embeddings": [], "embedding_checks": [], "intents": [], "snapshots": []}
            for name in ("recording", "organization", "retrieval_semantic", "retrieval_baselines", "fallback"):
                section(run_id, name, "not_measured", "本轮只执行真实软件实验，不构造合成窗口")
        else:
            cases, queries, captures = _run_synthetic(run_id, dataset, ready, embeddings_ready, trace)
        _write_evidence(run_id, "prompts.json", captures["prompts"])
        _write_evidence(run_id, "embeddings.json", captures["embeddings"])
        _write_evidence(run_id, "embedding_checks.json", captures["embedding_checks"])
        _write_evidence(run_id, "snapshots.json", captures["snapshots"])
        _write_evidence(run_id, "queries.json", queries)
        privacy_stages = {
            "capture": captures["snapshots"],
            "model_input": {"successful_requests": [call["request"] for call in trace.calls if call["status"] == "done"]},
            "model_input_attempts_failed": {"requests": [call["request"] for call in trace.calls if call["status"] == "error"]},
            "summary": captures["intents"],
            "database": [{key: value for key, value in item.get("record", {}).items() if key != "vector"}
                         for item in cases],
        }
        _write_evidence(run_id, "privacy_stages.json", privacy_stages)
        from TimeIndex.db.vector_store import get_schema
        schema_fields = get_schema().names
        privacy_checks = {"model_endpoint_loopback": is_loopback(manifest["endpoint"]),
                          "remote_model_enabled": manifest.get("allow_remote_model", False),
                          "image_fields_in_schema": image_fields({name: True for name in schema_fields}),
                          "image_fields_in_records": image_fields(privacy_stages["database"]),
                          "blacklist": {"status": "not_measured", "reason": "需要专用测试桌面中的实际窗口探针"}}
        _write_evidence(run_id, "privacy_checks.json", privacy_checks)
        section(run_id, "privacy", "done" if captures["snapshots"] else "not_measured",
                None if captures["snapshots"] else "未处理含虚构敏感串的正常采集场景")

        if (manifest["mode"] in {"full", "desktop", "paper"} and manifest["dedicated_vm"] and
                preflight["desktop_ready"] and not _cancelled(run_id)):
            from .live import run_live
            update_run(run_id, current_phase="正在专用测试桌面打开真实软件并采集活动")
            live_result = run_live(run_id, trace=trace)
            _write_evidence(run_id, "live.json", live_result)
            live_raw = read_json(directory / "evidence" / "live_raw.json", {})
            if live_raw:
                privacy_stages["live_capture"] = live_raw.get("snapshots", [])
                privacy_stages["live_database"] = live_raw.get("persisted", [])
                _write_evidence(run_id, "privacy_stages.json", privacy_stages)
            if live_result.get("blacklist_probe"):
                privacy_checks["blacklist"] = live_result["blacklist_probe"]
                _write_evidence(run_id, "privacy_checks.json", privacy_checks)
            live_cases = read_json(directory / "evidence" / "live_cases.json", [])
            privacy_stages["live_summary"] = [row.get("intent") for row in live_cases]
            privacy_stages["live_organization"] = [row.get("refined") for row in live_cases]
            privacy_stages["model_input"] = {"successful_requests": [call["request"] for call in trace.calls if call["status"] == "done"]}
            _write_evidence(run_id, "privacy_stages.json", privacy_stages)
            if live_raw.get("snapshots"):
                section(run_id, "privacy", "done", "审计已执行，暴露与屏蔽结果见报告，不代表隐私全部通过")
            section(run_id, "live_recording", live_result["status"], live_result.get("reason"))
        else:
            section(run_id, "live_recording", "not_measured",
                    "未启用真实软件实验，或专用测试桌面/WMI条件不满足")
        if sampler:
            resource_result = sampler.stop()
            resource_result["phases"] = "包括本轮已执行的合成和真实桌面阶段"
            _write_evidence(run_id, "resources.json", resource_result)
            sampler = None
            section(run_id, "resources", resource_result["status"], resource_result.get("reason"))
        preserve_database(run_id)
        database_preserved = True
        update_run(run_id, current_phase="正在生成实验表格和逐例报告")
        section(run_id, "model_evidence", "partial" if trace.capture_errors else "done",
                "部分模型原文取证失败，原始模型返回未被改写" if trace.capture_errors else None)
        export(run_id)
        privacy_stages["export"] = {name: (directory / "reports" / name).read_text(encoding="utf-8")
                                   for name in ("report.html", "paper_metrics.csv", "details.json", "summary.json",
                                                "paper_main_metrics.csv", "retrieval_tasks.csv", "privacy_stages.csv", "time_queries.csv")
                                   if (directory / "reports" / name).exists()}
        _write_evidence(run_id, "privacy_stages.json", privacy_stages)
        export(run_id)
        cancelled = _cancelled(run_id)
        final_status = _final_status(manifest, dataset, cases, read_json(directory / "manifest.json")["sections"],
                                     embeddings_ready, cancelled)
    except Exception as error:
        (directory / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        update_run(run_id, error=str(error))
        try:
            export(run_id)
        except Exception:
            pass
    finally:
        if sampler:
            resource_result = sampler.stop()
            _write_evidence(run_id, "resources.json", resource_result)
            section(run_id, "resources", resource_result["status"], resource_result.get("reason"))
        if not database_preserved:
            try:
                preserve_database(run_id)
                database_preserved = True
            except Exception as error:
                update_run(run_id, preserve_error=str(error))
                final_status = "failed"
        try:
            if database_preserved:
                cleanup(run_id)
        except Exception as error:
            update_run(run_id, cleanup_error=str(error))
            final_status = "failed"
        update_run(run_id, status=final_status, finished_at=now())
        from .controller import release_lock
        release_lock(run_id)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: python -m platform_core.worker <run_id>")
    run(sys.argv[1])

