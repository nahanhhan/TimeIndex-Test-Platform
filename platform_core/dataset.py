from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import ROOT, digest_file


DEFAULT_DATASET = ROOT / "datasets" / "default.json"
PAPER_DATASET = ROOT / "datasets" / "paper.json"
TOPICS = [
    ("Python 函数", "coding", "Code", "代码练习"),
    ("数据库索引", "reading", "Browser", "技术阅读"),
    ("实验报告", "writing", "Writer", "论文写作"),
    ("项目会议", "meeting", "Calendar", "会议安排"),
    ("数据图表", "analysis", "Spreadsheet", "数据分析"),
    ("界面设计", "design", "Design", "界面设计"),
]

APPLICATIONS = {
    "Code": ("code.exe", "Visual Studio Code", ["Code", "VSCode", "VS Code", "Visual Studio Code"]),
    "Browser": ("msedge.exe", "Microsoft Edge", ["Edge", "Microsoft Edge", "msedge.exe"]),
    "Writer": ("winword.exe", "Microsoft Word", ["Word", "Microsoft Word", "winword.exe"]),
    "Calendar": ("outlook.exe", "Outlook", ["Outlook", "outlook.exe"]),
    "Spreadsheet": ("excel.exe", "Microsoft Excel", ["Excel", "Microsoft Excel", "excel.exe"]),
    "Design": ("chrome.exe", "Chrome - Figma", ["Chrome", "Figma", "chrome.exe"]),
}

TAG_ALIASES = {
    "coding": ["coding", "code", "programming", "编程", "代码", "Python", "python", "code.exe"],
    "reading": ["reading", "browse", "browsing", "阅读", "数据库", "索引", "database", "技术资料"],
    "writing": ["writing", "文稿", "报告", "写作", "Word", "word", "document"],
    "meeting": ["meeting", "会议", "calendar", "日历", "Outlook", "outlook"],
    "analysis": ["analysis", "分析", "数据", "图表", "Excel", "excel", "spreadsheet"],
    "design": ["design", "设计", "界面", "Figma", "figma", "UI", "ui"],
}


def build_default() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    for group, (topic, tag, app, activity) in enumerate(TOPICS):
        for number in range(10):
            case_id = f"S{group + 1:02d}-{number + 1:02d}"
            process, app_name, app_aliases = APPLICATIONS[app]
            secret = f"TEST-SECRET-{case_id}" if number == 0 else None
            title = f"{topic} 第{number + 1}节 {secret or ''} - {app_name}".replace("  ", " ")
            cases.append({
                "id": case_id,
                "title": title,
                "process": process,
                "application": app_name,
                "input_kind": "synthetic_snapshot",
                "actually_launched": False,
                "observable_scope": "仅进程事件与窗口标题；不包含文档正文、键盘操作或网页内容",
                "event_type": "created",
                "facts": [[topic, topic.replace(" ", "")]],
                "required_details": [f"第{number + 1}节"],
                "app_aliases": app_aliases + [process],
                "tag_aliases": TAG_ALIASES[tag],
                "tags": [tag],
                "group": f"G{group + 1:02d}",
                "canary": secret,
            })
        for question in range(5):
            target = cases[group * 10 + question * 2]
            queries.append({
                "id": f"Q{group + 1:02d}-{question + 1:02d}",
                "text": [f"查找{topic}第{question * 2 + 1}节的活动", f"我什么时候处理过{topic}第{question * 2 + 1}节？",
                         f"回忆{topic}第{question * 2 + 1}节对应的窗口", f"{topic}第{question * 2 + 1}节用过什么应用？",
                         f"找出关于{topic}第{question * 2 + 1}节的记录"][question],
                "keyword": topic,
                "tags": TAG_ALIASES[tag],
                "relevant_ids": [target["id"]],
            })
    return {"version": "2.0", "synthetic": True, "seed": 20260927, "cases": cases, "queries": queries,
            "quick_case_ids": ["S01-01", "S01-02", "S02-01", "S02-02", "S03-01", "S04-01", "S05-01", "S06-01"],
            "quick_query_ids": ["Q01-01", "Q02-01", "Q03-01", "Q05-01"],
            "measurement_scope": "合成快照用于检查真实 TimeIndex 推理、入库、整理与检索；不证明实际软件采集效果"}


def validate_dataset(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise ValueError("数据集缺少 version")
    cases, queries = data.get("cases"), data.get("queries")
    if not isinstance(cases, list) or not cases or not isinstance(queries, list):
        raise ValueError("数据集需要非空 cases 和 queries 列表")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or any(key not in case for key in
                                             ("id", "title", "process", "facts", "tags", "group")):
            raise ValueError("场景缺少必需字段")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError(f"场景 ID 为空或重复: {case_id}")
        ids.add(case_id)
        if not isinstance(case["facts"], list) or not case["facts"] or any(
            not isinstance(item, list) or not item or not all(isinstance(word, str) and word for word in item)
            for item in case["facts"]
        ):
            raise ValueError(f"场景 {case_id} 的 facts 必须是非空同义表达列表")
        if not isinstance(case["tags"], list) or not all(isinstance(tag, str) for tag in case["tags"]):
            raise ValueError(f"场景 {case_id} 的 tags 无效")
    query_ids: set[str] = set()
    for query in queries:
        if not isinstance(query, dict) or any(key not in query for key in
                                              ("id", "text", "keyword", "tags", "relevant_ids")):
            raise ValueError("检索问题缺少必需字段")
        if query["id"] in query_ids:
            raise ValueError(f"检索问题 ID 重复: {query['id']}")
        query_ids.add(query["id"])
        if not query["relevant_ids"] or not set(query["relevant_ids"]).issubset(ids):
            raise ValueError(f"检索问题 {query['id']} 引用了不存在的场景")
        if query.get("task", "unspecified") not in {"topic", "specific", "unspecified"}:
            raise ValueError(f"检索问题 {query['id']} 的 task 无效")
        if "keyword_terms" in query and (not isinstance(query["keyword_terms"], list) or
                not query["keyword_terms"] or not all(isinstance(term, str) and term.strip() for term in query["keyword_terms"])):
            raise ValueError(f"检索问题 {query['id']} 的 keyword_terms 无效")
    if not set(data.get("quick_case_ids", [])).issubset(ids):
        raise ValueError("快速场景 ID 无效")
    if not set(data.get("quick_query_ids", [])).issubset(query_ids):
        raise ValueError("快速检索问题 ID 无效")
    quick_ids = set(data.get("quick_case_ids", []))
    for query in queries:
        if query["id"] in data.get("quick_query_ids", []) and not set(query["relevant_ids"]).issubset(quick_ids):
            raise ValueError("快速检索问题的目标必须包含在快速场景中")
    if data.get("evaluation_profile") == "paper":
        protocol = data.get("protocol")
        counts = {"cases": len(cases), "topic_queries": sum(q.get("task") == "topic" for q in queries),
                  "specific_queries": sum(q.get("task") == "specific" for q in queries),
                  "time_ranges": (len(cases) + 9) // 10,
                  "categories": len({tag for case in cases for tag in case["tags"]}),
                  "topics": len({case["group"] for case in cases})}
        if not isinstance(protocol, dict) or any(protocol.get(key) != value for key, value in counts.items()):
            raise ValueError("论文数据集 protocol 的样本、任务或时间范围数量不一致")
        if counts["topic_queries"] + counts["specific_queries"] != len(queries):
            raise ValueError("论文查询必须明确划分 topic 和 specific 任务")


def load_dataset(path: Path = DEFAULT_DATASET) -> tuple[dict[str, Any], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_dataset(data)
    return data, digest_file(path)


def select(data: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in {"quick", "full", "custom", "desktop", "paper"}:
        raise ValueError(f"未知模式: {mode}")
    if mode == "desktop":
        return {**data, "cases": [], "queries": [], "privacy_markers": ["TEST-SECRET-DESKTOP"]}
    if mode != "quick":
        return data
    ids = set(data["quick_case_ids"])
    query_ids = set(data["quick_query_ids"])
    return {**data, "cases": [c for c in data["cases"] if c["id"] in ids],
            "queries": [q for q in data["queries"] if q["id"] in query_ids]}

