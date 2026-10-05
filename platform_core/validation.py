from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .common import read_json


def audit_vectors(before: list[dict[str, Any]], after: list[dict[str, Any]],
                  dimension: int = 768) -> dict[str, Any]:
    originals = {row["id"]: row.get("vector") for row in before}
    current = {row["id"]: row for row in after}
    issues = []
    valid = preserved = 0
    for record_id, original in originals.items():
        vector = current.get(record_id, {}).get("vector")
        reasons = []
        if not isinstance(vector, list) or len(vector) != dimension:
            reasons.append("检索向量缺失或维度错误")
        elif not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            reasons.append("检索向量包含无效数值")
        elif not any(vector):
            reasons.append("检索向量全部为零")
        else:
            valid += 1
        if original is not None:
            if vector == original:
                preserved += 1
            else:
                reasons.append("整理写回改变了原始检索向量")
        if reasons:
            issues.append({"id": record_id, "reasons": reasons})
    return {"status": "failed" if issues else "done" if originals else "not_measured",
            "total": len(originals), "valid": valid, "preserved": preserved,
            "dimension": dimension, "issues": issues}


def audit_saved_vectors(directory: Path) -> dict[str, Any]:
    """Read saved databases for older runs without changing their query evidence."""
    database = directory / "evidence" / "timeindex_db"
    if not (database / "timeindex.lance").exists():
        return {"status": "not_measured", "reason": "未保存可核验的检索向量"}
    try:
        import lancedb

        rows = lancedb.connect(str(database)).open_table("timeindex").to_arrow().to_pylist()
        cases = read_json(directory / "evidence" / "cases.json", [])
        labels = {row["record_id"]: row["id"] for row in cases if row.get("record_id")}
        if labels:
            rows = [row for row in rows if row["id"] in labels]
        result = audit_vectors([{"id": row["id"], "vector": None} for row in rows], rows)
        for issue in result["issues"]:
            issue["id"] = labels.get(issue["id"], issue["id"])
        result["origin"] = "saved_database"
        return result
    except Exception as error:
        return {"status": "not_measured", "reason": f"保存的检索向量无法核验：{error}"}
