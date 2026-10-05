"""Runs against a copied TimeIndex tree, with its own profile and database."""
from __future__ import annotations

import json
import sys

from platform_core.isolation import runtime_paths, verify_import


def main(run_id: str) -> None:
    verify_import(run_id)
    from TimeIndex.db.vector_store import VectorStore
    from TimeIndex.daemon.llm_processor import LLMProcessor

    store = VectorStore(db_path=str(runtime_paths(run_id)["database"]))
    records = [{"id": f"record-{index}", "summary": f"Python函数第{index}节", "tags": ["coding"],
                "primary_app": "Code", "hardware": {"cpu": index},
                "active_windows": [{"title": f"Python 函数 第{index}节 - Code"}],
                "vector": [0.25 + index / 100] * 768} for index in range(12)]
    store.add_batch(records)
    pending = store.get_pending_retag_records(12)
    assert len(pending) == 12, "pending query must not use the default ten-row limit"
    assert store.get_record_count() == 12
    assert all(row["refined_summary"] is None and row["cluster_id"] is None for row in pending)
    assert all(isinstance(row["tags"], list) and len(row["vector"]) == 768 for row in pending)
    before = {row["id"]: row for row in store.get_table().to_arrow().to_pylist()}
    parser = LLMProcessor.__new__(LLMProcessor)
    answer = [{"id": pending[0]["id"], "refined_summary": "Python函数第0节", "refined_tags": ["coding"],
               "cluster_id": "group"},
              {"id": pending[1]["id"], "refined_summary": "NaN", "refined_tags": None, "cluster_id": "NaN"}]
    updates = parser._parse_retag_response(json.dumps(answer), pending)
    assert len(updates) == 1
    assert all(row["refined_tags"] is None for row in pending), "parser must not alter input evidence"
    store.update_batch(updates)
    assert store.update({"id": pending[2]["id"], "refined_summary": "Python函数", "refined_tags": ["coding"],
                         "cluster_id": "group"})
    after = {row["id"]: row for row in store.get_table().to_arrow().to_pylist()}
    for record_id in before:
        assert before[record_id]["vector"] == after[record_id]["vector"]
        assert before[record_id]["hardware"] == after[record_id]["hardware"]
        assert before[record_id]["active_windows"] == after[record_id]["active_windows"]
    try:
        store.update({"id": pending[0]["id"], "vector": [1.0]})
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("bad vector dimension must be rejected before changing stored data")
    current = {row["id"]: row for row in store.get_table().to_arrow().to_pylist()}
    assert current == after, "failed update must preserve the original row"
    assert not store.update({"id": "missing", "summary": "unused"})
    assert parser._parse_retag_response(json.dumps(answer[:1] * 2), pending) == []
    assert parser._parse_retag_response("{}", pending) == []
    assert len(store.get_pending_retag_records(12)) == 10
    store.add({"id": "without-embedding", "summary": "原始活动已保存", "tags": ["unknown"], "vector": []})
    saved = {row["id"]: row for row in store.get_table().to_arrow().to_pylist()}
    assert saved["without-embedding"]["vector"] is None
    found = store.semantic_search("test", query_vector=[0.25] * 768, limit=20)
    assert len(found) == 12 and all(row["id"] != "without-embedding" for row in found)
    store.close()
    print("Retag parser, nulls, vectors, metadata, query count and atomic update verified.")


if __name__ == "__main__":
    main(sys.argv[1])
