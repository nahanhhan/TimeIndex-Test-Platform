"""Optional protocol/storage checks in the existing isolated experiment only."""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .common import atomic_json, read_json
from .isolation import verify_import
from .manifest import run_path
from .model_trace import ModelTrace, ModelWaitStopped


def verify(run_id: str, parent_trace: ModelTrace) -> dict[str, Any]:
    verify_import(run_id)
    from openai import OpenAI
    from TimeIndex.daemon.llm_processor import LLMProcessor
    from TimeIndex.db.vector_store import VectorStore

    directory = run_path(run_id)
    trace = ModelTrace(directory / "evidence" / "core_repairs_calls.json",
                       timeout_s=parent_trace.timeout_s, cancelled=parent_trace.cancelled,
                       on_call=parent_trace.on_call)
    result = {"status": "running", "source_sha256": read_json(directory / "manifest.json")["source_sha256"],
              "scope": "当前选定源码副本的模拟协议及隔离数据库测试，不是实际模型质量成绩，也不代表其他版本通过",
              "checks": {"numeric_ids": {"status": "not_measured"}, "code_block": {"status": "not_measured"},
                         "failed_update": {"status": "not_measured"},
                         "blacklist": {"status": "not_measured", "reason": "需另在专用测试桌面验证进程事件和窗口"}}}
    destination = directory / "evidence" / "core_repairs.json"
    atomic_json(destination, result)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            response = {"id": "fixture", "object": "chat.completion", "created": 1, "model": "repair-fixture",
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": self.server.answer}}]}
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = OpenAI(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="repair-fixture")
    store = VectorStore(db_path=str(directory / "evidence" / "core_repair_db"))
    processor = LLMProcessor.__new__(LLMProcessor)
    processor.client, processor.model = trace.wrap(client), "repair-fixture"
    records = [{"id": value, "summary": "查看粗粒度活动线索", "tags": ["free-tag"],
                "primary_app": "test-app", "timestamp": "2026-10-07T09:00:00",
                "active_windows": [{"title": "受控测试窗口", "pid": 100}],
                "process_events": [{"type": "created", "process": "test.exe", "pid": 100}],
                "hardware": {"cpu": 1}, "vector": [0.25] * 768}
               for value in ("1791202814.105918", "9007199254740993")]
    try:
        store.add_batch(records)
        originals = store.get_table().to_arrow().to_pylist()
        for scenario in ("numeric_ids", "code_block"):
            answers = [{"id": row["id"], "refined_summary": "粗粒度测试线索", "refined_tags": ["free-cue"],
                        "cluster_id": "coarse"} for row in originals]
            content = json.dumps(answers, ensure_ascii=False)
            if scenario == "numeric_ids":
                # Keep numeric JSON exact; a float conversion could corrupt a large ID.
                for row in answers:
                    content = content.replace(json.dumps(row["id"]), row["id"])
            else:
                content = "```json\n" + content + "\n```"
            server.answer = content
            before = copy.deepcopy(originals)
            with trace.scope(phase="本体修复专项验证（模拟接口）", scenario=scenario, simulated=True):
                updates = processor.retag_cluster(originals)
            written = store.update_batch(updates) if updates else 0
            persisted = store.get_table().to_arrow().to_pylist()
            expected_ids = {row["id"] for row in originals}
            ok = (len(updates) == len(originals) and {row["id"] for row in updates} == expected_ids
                  and written == len(originals) and originals == before
                  and all(row.get("refined_summary") == "粗粒度测试线索" for row in persisted))
            result["checks"][scenario] = {"status": "passed" if ok else "failed", "returned": len(updates),
                                          "written": written, "input_unchanged": originals == before,
                                          "model_call_ids": [call["id"] for call in trace.calls if call["context"].get("scenario") == scenario]}
            atomic_json(destination, result)

        before = store.get_table().to_arrow().to_pylist()
        table, get_table = store.get_table(), store.get_table
        injected = []

        class FailingWrites:
            def __getattr__(self, name):
                if name in {"update", "add", "merge_insert"}:
                    def fail(*_args, **_kwargs):
                        injected.append(name)
                        raise RuntimeError("受控数据库写入失败")
                    return fail
                return getattr(table, name)

        # Replace only this disposable database's dependency in memory, never source.
        store.get_table = lambda *_args, **_kwargs: FailingWrites()
        error = None
        try:
            store.update({"id": records[0]["id"], "refined_summary": "不可写入的更新"})
        except Exception as caught:
            error = str(caught)
        finally:
            store.get_table = get_table
        after = store.get_table().to_arrow().to_pylist()
        result["checks"]["failed_update"] = {
            "status": "passed" if injected and before == after else "failed" if injected else "not_measured",
            "write_failure_injected": bool(injected), "old_records_unchanged": before == after, "error": error,
            "reason": "比较失败更新前后全部数据库字段（含向量、窗口、事件及硬件），未执行的写入路径不算通过"}
        statuses = [value["status"] for key, value in result["checks"].items() if key != "blacklist"]
        result["status"] = "done" if all(value == "passed" for value in statuses) else "partial"
        atomic_json(destination, result)
        return result
    except (Exception, ModelWaitStopped) as error:
        result.update(status="partial", reason=str(error))
        atomic_json(destination, result)
        raise
    finally:
        store.close()
        client.close()
        server.shutdown()
        server.server_close()
