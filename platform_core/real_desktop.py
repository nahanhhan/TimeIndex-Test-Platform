from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .common import atomic_json, read_json
from .desktop_apps import AppSession, app_plan
from .desktop_quality import evaluate
from .manifest import run_path, update_run
from .model_trace import ModelTrace
from .scoring import aggregate_queries, rank_query, recording, refinement_issues
from .paper import keyword_match
from .validation import audit_vectors


def run_desktop(run_id: str, rounds: int, dwell_s: float, trace: ModelTrace | None) -> dict[str, Any]:
    from TimeIndex.daemon.daemon import Daemon
    from TimeIndex.db.embedding_provider import embedding_provider
    from TimeIndex.db.vector_store import TimeIndexStore
    from TimeIndex.utils.config import config

    run = run_path(run_id)
    if not read_json(run / "manifest.json").get("dedicated_vm"):
        return {"status": "not_measured", "reason": "未声明专用测试桌面"}
    trace = trace or ModelTrace(run / "evidence" / "desktop_model_calls.json")
    scene = run / "desktop_control"
    scene.mkdir(exist_ok=True)
    snapshots, events, actions, examples, inventory = [], [], [], [], []
    expected_windows, expected_events, sessions = [], [], []
    cancelled = False
    daemon = Daemon(idle_threshold=999999, global_blacklist=config.global_blacklist)
    settings = {"wmi_interval_s": daemon.wmi_collector.interval, "retag_batch_size": daemon._retag_batch_size,
                "global_blacklist": config.global_blacklist, "organization_trigger": "采集结束后调用核心整理函数",
                "automatic_idle_trigger": "not_measured", "reason": "自动闲时触发未测；实验期间禁用自动整理以保留前后对照"}
    atomic_json(run / "evidence" / "desktop_settings.json", settings)
    daemon.db_store.close()
    daemon.db_store = TimeIndexStore(db_path=str(run / "evidence" / "desktop_db"))
    daemon.llm_processor.client = trace.wrap(daemon.llm_processor.client)
    old_embedding_client = embedding_provider.client
    embedding_provider.client = trace.wrap(old_embedding_client)
    original_process, original_infer = daemon._process_snapshot, daemon.llm_processor.infer_intent
    intents: dict[str, Any] = {}

    def infer(snapshot: Any) -> Any:
        value = original_infer(snapshot)
        intents[str(snapshot.timestamp.timestamp())] = value
        return value

    def process(snapshot: Any) -> None:
        record_id = str(snapshot.timestamp.timestamp())
        started = time.perf_counter()
        with trace.scope(phase="真实桌面记录", record_id=record_id, snapshot_time=snapshot.timestamp.isoformat()):
            original_process(snapshot)
        rows = daemon.db_store.store.get_table().to_arrow().to_pylist()
        record = next((row for row in rows if row["id"] == record_id), {})
        examples.append({"id": "LIVE-" + record_id, "record_id": record_id, "input_kind": "real_desktop",
                         "input": asdict(snapshot), "intent": {k: v for k, v in intents.get(record_id, {}).items() if k != "vector"},
                         "record": {k: v for k, v in record.items() if k != "vector"},
                         "elapsed_ms": (time.perf_counter() - started) * 1000, "recorded_at": time.time(),
                         "status": "done" if record else "record_error"})
        atomic_json(run / "evidence" / "live_cases.json", examples)

    def on_snapshot(snapshot: Any) -> None:
        snapshots.append({"timestamp": snapshot.timestamp.isoformat(), "time": snapshot.timestamp.timestamp(),
                          "windows": [asdict(row) for row in snapshot.windows],
                          "process_events": [asdict(row) for row in snapshot.process_events]})

    daemon._process_snapshot = process
    daemon.llm_processor.infer_intent = infer
    daemon.wmi_collector.add_callback(on_snapshot)
    daemon.wmi_collector.add_event_callback(lambda event: events.append({"kind": "process", "event_type": event.event_type,
                                                                       "pid": event.pid, "process": event.process_name,
                                                                       "time": event.timestamp.timestamp()}))
    try:
        daemon.start()
        for round_number in range(1, rounds + 1):
            for plan in app_plan(run_id, round_number, scene):
                if (run / "cancel.flag").exists():
                    cancelled = True
                    break
                session = AppSession(plan)
                sessions.append(session)
                started = time.time()
                item = session.open()
                inventory.append(item)
                if read_json(run / "manifest.json")["mode"] == "desktop":
                    update_run(run_id, completed_cases=len(inventory), current_phase=f"真实软件 {item['id']} · {item['application']}")
                atomic_json(run / "evidence" / "desktop_apps.json", {"applications": inventory,
                            "observable_scope": "真实软件的进程事件和可见窗口标题；TimeIndex 不读取文档正文或键盘内容"})
                actions.append({"action": "open", "application": item["application"], "scene_id": item["id"],
                                "time": started, "status": item["status"], "document": item["document"], "reason": item.get("reason")})
                if item["status"] != "started":
                    session.close() if item.get("actually_launched") else None
                    continue
                expected_events.append({"kind": "process", "event_type": "created", "pid": item["root_pid"], "time": started})
                for window in item["windows"]:
                    expected_windows.append({"kind": "window", "title": window["title"], "pid": window["pid"], "time": item["visible_at"]})
                time.sleep(dwell_s)
                focus = session.focus()
                actions.append({"action": "focus", "application": item["application"], "scene_id": item["id"],
                                "time": time.time(), **focus})
                time.sleep(dwell_s)
                actions.append({"action": "observe", "application": item["application"], "scene_id": item["id"],
                                "time": time.time(), "status": "done"})
                deadline = time.monotonic() + max(60, dwell_s)
                while time.monotonic() < deadline and not (run / "cancel.flag").exists():
                    if any(any(plan["marker"] in window["title"] for window in row["input"]["windows"]) for row in examples):
                        break
                    time.sleep(0.2)
                actions.append({"action": "close", "application": item["application"], "scene_id": item["id"], "time": time.time()})
                if session.child and session.child.poll() is None:
                    expected_events.append({"kind": "process", "event_type": "exited", "pid": item["root_pid"], "time": time.time()})
                session.close()
                time.sleep(dwell_s)
            if cancelled:
                break
    finally:
        for session in sessions:
            if session.evidence.get("actually_launched"):
                session.close()
        daemon.stop()
        if daemon._process_thread and daemon._process_thread.is_alive():
            daemon._process_thread.join(timeout=60)
        atomic_json(run / "evidence" / "desktop_apps.json", {"applications": inventory})
        atomic_json(run / "evidence" / "live_raw.json", {"actions": actions, "snapshots": snapshots, "events": events,
                                                        "expected_windows": expected_windows, "expected_events": expected_events})
        embedding_provider.client = old_embedding_client

    if daemon._process_thread and daemon._process_thread.is_alive():
        return {"status": "partial", "reason": "采集已停止，但模型处理线程未在限定时间内结束；输出证据不完整"}
    rows = daemon.db_store.store.get_table().to_arrow().to_pylist()
    before = rows
    batches = []
    if not cancelled:
        pending = daemon.db_store.get_pending_retag(daemon.db_store.get_count())
        for offset in range(0, len(pending), daemon._retag_batch_size):
            batch = pending[offset:offset + daemon._retag_batch_size]
            batch_id = f"LIVE-B{len(batches) + 1:03d}"
            with trace.scope(phase="真实桌面整理", batch_id=batch_id, record_ids=[r["id"] for r in batch]):
                updated = daemon.llm_processor.retag_cluster(batch)
            core_valid_results = sum(not refinement_issues(row) for row in updated)
            written = daemon.db_store.update_retag_records(updated)
            batches.append({"id": batch_id, "input_ids": [r["id"] for r in batch],
                            "input_record_ids": [r["id"] for r in batch], "returned_ids": [r["id"] for r in updated],
                            "core_valid_result_count": core_valid_results, "written": written,
                            "model_call_ids": [call["id"] for call in trace.calls if call["context"].get("batch_id") == batch_id],
                            "returned_ids_meaning": "本体返回列表，不代表全部得到有效整理", "written_meaning": "数据库更新操作数，不等于有效整理数"})
    rows = daemon.db_store.store.get_table().to_arrow().to_pylist()
    indexed = {row["id"]: row for row in rows}
    for example in examples:
        example["refined"] = {key: indexed.get(example["record_id"], {}).get(key) for key in ("refined_summary", "refined_tags", "cluster_id")}
    quality = evaluate(examples, inventory, trace.calls)
    atomic_json(run / "evidence" / "live_cases.json", examples)
    vectors = audit_vectors(before, rows)
    methods: dict[str, list[dict[str, Any]]] = {name: [] for name in ("semantic", "keyword", "tags")}
    for item in inventory:
        if cancelled or item["status"] != "started":
            continue
        relevant = [row["id"] for row in rows if any(item["marker"] in str(window) for window in row.get("active_windows") or [])]
        for method in methods:
            started = time.perf_counter()
            found = []
            query = f"查找{item['topic']}第{item['id'].split('-')[0][1:]}节的活动"
            if not relevant:
                methods[method].append({"id": item["id"], "query": query, "status": "not_measured",
                                        "relevant_ids": [], "corpus_size": len(rows), "returned_records": [],
                                        "reason": "受控窗口未入库，没有可评分的目标记录；漏记计入记录指标"})
                continue
            try:
                if method == "semantic":
                    if vectors["status"] != "done":
                        methods[method].append({"id": item["id"], "query": query, "status": "not_measured",
                                                "relevant_ids": relevant, "corpus_size": len(rows), "returned_records": [],
                                                "reason": "桌面记录向量无效或未核验，未执行语义查询"})
                        continue
                    embedding_provider.client = trace.wrap(old_embedding_client)
                    with trace.scope(phase="真实桌面回忆", query_id=item["id"], method=method):
                        found = daemon.db_store.search_activities(query, limit=5)
                    query_calls = [call for call in trace.calls if call["operation"] == "embedding" and call["context"].get("query_id") == item["id"]]
                    returned_vector = ((query_calls[-1].get("response") or {}).get("data") or [{}])[0].get("embedding") if query_calls else None
                    if audit_vectors([{"id": "query", "vector": None}], [{"id": "query", "vector": returned_vector}])["status"] != "done":
                        raise ValueError("查询向量无效；关键词降级结果不计为语义结果")
                elif method == "keyword":
                    found = [row for row in rows if keyword_match(row, {"keyword": item["topic"]})][:5]
                else:
                    tags = [item["topic"], item["application"], item["executable_name"], "coding" if item["topic"] == "Python 函数" else
                            "reading" if item["topic"] == "数据库索引" else "writing"]
                    found = daemon.db_store.get_activities_by_tags(tags, limit=5)
                score = rank_query(relevant, [row["id"] for row in found], (time.perf_counter() - started) * 1000)
            except Exception as error:
                score = rank_query(relevant, [], (time.perf_counter() - started) * 1000, str(error))
            methods[method].append({"id": item["id"], "query": query,
                                    "keyword": item["topic"] if method == "keyword" else None,
                                    "tags_requested": tags if method == "tags" else None,
                                    "relevant_ids": relevant, "corpus_size": len(rows),
                                    "returned_records": [{k: v for k, v in row.items() if k != "vector"} for row in found], **score})
    embedding_provider.client = old_embedding_client
    observed = [{"kind": "window", "title": window["title"], "pid": window["pid"], "time": snapshot["time"]}
                for snapshot in snapshots for window in snapshot["windows"] if any(item["marker"] in window["title"] for item in inventory)]
    persisted = []
    written_at = {example["record_id"]: example["recorded_at"] for example in examples}
    for row in rows:
        for raw in row.get("active_windows") or []:
            window = json.loads(raw)
            if any(item["marker"] in window["title"] for item in inventory):
                persisted.append({"kind": "window", "title": window["title"], "pid": window["pid"],
                                  "time": datetime.fromisoformat(row["timestamp"]).timestamp(), "record_id": row["id"],
                                  "recorded_at": written_at.get(row["id"])})
    db_score = recording(expected_windows, persisted)
    write_latencies = [match["observed"]["recorded_at"] - match["expected"]["time"] for match in db_score["matches"]
                       if match["observed"].get("recorded_at")]
    db_score.update(matching_time="按原始快照时间匹配窗口；latency_s是采集延迟，写入延迟使用实际完成入库的时间单列",
                    mean_write_latency_s=sum(write_latencies) / len(write_latencies) if write_latencies else None,
                    max_write_latency_s=max(write_latencies) if write_latencies else None)
    controlled_pids = {item["pid"] for item in expected_events}
    event_score = recording(expected_events, [event for event in events if event["pid"] in controlled_pids])
    event_score["scope"] = "仅评分实际启动测试软件的预期PID；非测试进程事件留在本地证据，不算误记"
    from .live import _probe_blacklist
    blacklist = None if cancelled else _probe_blacklist(run_id, scene)
    if blacklist and blacklist.get("software"):
        inventory.append(blacklist["software"])
        atomic_json(run / "evidence" / "desktop_apps.json", {"applications": inventory})
    atomic_json(run / "evidence" / "live_raw.json", {"actions": actions, "snapshots": snapshots, "events": events,
                "expected_windows": expected_windows, "expected_events": expected_events, "persisted": persisted})
    atomic_json(run / "evidence" / "desktop_queries.json", methods)
    atomic_json(run / "evidence" / "desktop_batches.json", batches)
    daemon.db_store.close()
    skipped = any(item["status"] != "started" for item in inventory if item.get("kind") != "blacklist_probe")
    missing_capture = not snapshots or not examples
    organized = sum(not refinement_issues(row.get("refined") or {}) for row in examples)
    incomplete = skipped or organized < len(examples) or vectors["status"] != "done" or any(row.get("status") == "error" for row in methods["semantic"]) or any(action.get("status") == "failed" for action in actions)
    return {"status": "cancelled" if cancelled else "failed" if missing_capture else "partial" if incomplete else "done", "actions": len(actions), "rounds": rounds,
            "reason": "实际桌面未产生快照或入库记录" if missing_capture else "部分软件、整理或检索未完成，详见逐例证据" if incomplete else None,
            "organization": {"complete": organized, "total": len(examples)},
            "quality": quality, "settings": settings,
            "software_started": sum(item["status"] == "started" for item in inventory), "software_attempted": len(inventory),
            "raw_windows": recording(expected_windows, observed), "raw_events": event_score,
            "database": db_score, "blacklist_probe": blacklist, "vector_integrity": vectors,
            "retrieval": {name: aggregate_queries(values) for name, values in methods.items()},
            "recording": {"expected": db_score["expected"], "matched": db_score["matched"], "recall": db_score["recall"],
                          "mean_write_latency_s": db_score["mean_write_latency_s"], "max_write_latency_s": db_score["max_write_latency_s"],
                          "duplicate_count": db_score["duplicate_count"], "false_positive_count": db_score["false_positive_count"]}}
