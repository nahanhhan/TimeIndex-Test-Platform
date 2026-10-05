from __future__ import annotations

from typing import Any

from .dataset import APPLICATIONS, TAG_ALIASES


PAPER_TOPICS = (
    ("Python 列表推导式", "coding", "Code", ["列表推导式", "列表推导", "list comprehension"]),
    ("Git 分支合并", "coding", "Code", ["分支合并", "合并分支", "merge"]),
    ("事务隔离级别", "reading", "Browser", ["事务隔离", "隔离级别", "transaction isolation"]),
    ("HTTP 缓存机制", "reading", "Browser", ["HTTP缓存", "缓存机制", "http cache"]),
    ("实验方法说明", "writing", "Writer", ["实验方法", "方法说明"]),
    ("文献综述草稿", "writing", "Writer", ["文献综述", "相关研究", "literature review"]),
    ("开发进度例会", "meeting", "Calendar", ["开发进度", "进度例会"]),
    ("课程项目讨论", "meeting", "Calendar", ["课程项目", "项目讨论"]),
    ("项目预算分析", "analysis", "Spreadsheet", ["项目预算", "预算分析"]),
    ("实验数据统计", "analysis", "Spreadsheet", ["实验数据", "数据统计"]),
    ("移动界面原型", "design", "Design", ["界面原型", "移动界面", "prototype"]),
    ("活动海报排版", "design", "Design", ["活动海报", "海报排版", "poster"]),
)


def build_paper() -> dict[str, Any]:
    cases, queries = [], []
    for index, (topic, tag, app, alternatives) in enumerate(PAPER_TOPICS, 1):
        process, application, app_aliases = APPLICATIONS[app]
        related = []
        for number in range(1, 6):
            case_id = f"P{index:02d}-{number:02d}"
            marker = f"TEST-PAPER-SECRET-{case_id}" if number == 1 else None
            document = (f"{topic} 第{number}节", f"{topic}笔记 第{number}节",
                        f"第{number}节 {topic}资料", f"{topic} 第{number}节记录",
                        f"{topic}复习 第{number}节")[number - 1]
            title = f"{document}{' ' + marker if marker else ''} - {application}"
            cases.append({"id": case_id, "title": title, "process": process, "application": application,
                          "input_kind": "synthetic_snapshot", "actually_launched": False,
                          "observable_scope": "只依据窗口标题、进程事件与时间，不评估文档正文或实际编辑动作",
                          "event_type": "created", "facts": [[topic, *alternatives]],
                          "required_details": [f"第{number}节"], "app_aliases": [*app_aliases, process],
                          "tags": [tag], "tag_aliases": [*TAG_ALIASES[tag], topic, *alternatives],
                          "group": f"PG{index:02d}", "canary": marker})
            related.append(case_id)
        queries.append({"id": f"PT{index:02d}", "task": "topic",
                        "text": f"找出以前涉及{topic}的电脑活动", "keyword": topic,
                        "keyword_terms": [topic], "tags": [*TAG_ALIASES[tag], topic],
                        "relevant_ids": related})
        # Three specific-record questions per activity category, with no overlapping target IDs.
        for number in ((1, 4) if index % 2 else (3,)):
            queries.append({"id": f"PS{index:02d}-{number:02d}", "task": "specific",
                            "text": f"查找{topic}第{number}节对应的记录", "keyword": topic,
                            "keyword_terms": [topic, f"第{number}节"], "tags": [*TAG_ALIASES[tag], topic],
                            "relevant_ids": [f"P{index:02d}-{number:02d}"]})
    return {"version": "paper-1.0", "synthetic": True, "seed": 20261004,
            "evaluation_profile": "paper", "cases": cases, "queries": queries,
            "protocol": {"id": "timeindex-paper-1.0", "development_dataset": "default.json@2.0",
                         "categories": 6, "topics": 12, "cases": 60, "topic_queries": 12,
                         "specific_queries": 18, "time_ranges": 6, "recommended_repetitions": 3,
                         "primary": ["recording", "organization", "topic_retrieval", "time_retrieval", "privacy"],
                         "diagnostic": ["specific_retrieval", "chapter_retention", "tag_f1", "clustering"],
                         "scope": "受控合成输入的真实核心处理；真实采集另在专用桌面验证，自动闲时触发未测"}}
