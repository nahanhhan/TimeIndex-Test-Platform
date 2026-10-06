from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .common import atomic_json, digest_platform, now, read_json
from .manifest import run_path
from .paper import overview
from .privacy import assert_redacted, audit, redact
from .scoring import SCORING_VERSION, aggregate_queries, complete_query_rows, query_completion, ratio, summarize_cases
from .validation import audit_saved_vectors
from .details import batch_cluster_score, build_details, hide_uncontrolled_windows, random_reference
from .report_html import render_report
from .diagnostics import diagnose


def calculate(run_id: str) -> dict[str, Any]:
    directory = run_path(run_id)
    dataset = read_json(directory / "dataset.json")
    manifest = read_json(directory / "manifest.json")
    cases = read_json(directory / "evidence" / "cases.json", [])
    queries = read_json(directory / "evidence" / "queries.json", {})
    stages = read_json(directory / "evidence" / "privacy_stages.json", {})
    privacy_checks = read_json(directory / "evidence" / "privacy_checks.json", {})
    live = read_json(directory / "evidence" / "live.json")
    resources = read_json(directory / "evidence" / "resources.json")
    organization = summarize_cases(dataset, cases)
    details = build_details(directory, dataset, cases, queries)
    organization["cluster_global_diagnostic"] = organization["cluster"]
    if details["batches"].get("batches"):
        organization["cluster"] = batch_cluster_score(dataset, cases, details["batches"])
    vectors = read_json(directory / "evidence" / "vector_integrity.json")
    if vectors is None:
        vectors = audit_saved_vectors(directory) if cases else {"status": "not_measured"}
    sections = {name: dict(value) for name, value in manifest.get("sections", {}).items()}
    fallback = details["fault_injection"]
    if isinstance(fallback.get("recorded"), bool):
        sections["fallback"] = {"status": "done" if fallback["recorded"] else "failed",
                                "reason": None if fallback["recorded"] else "受控失联场景执行后没有入库记录"}
    if cases:
        recorded, selected = organization["cases_done"], organization["cases_total"]
        sections["recording"] = {"status": "done" if recorded == selected else "partial" if recorded else "failed",
                                 "reason": None if recorded == selected else f"成功入库 {recorded}/{selected}"}
    if organization["retag_total"]:
        complete, total = organization["retag_complete"], organization["retag_total"]
        execution = sections.get("organization", {})
        failed = execution.get("status") == "failed"
        reason = None if complete == total and not failed else f"整理完成 {complete}/{total}，详见逐例记录"
        if execution.get("status") in {"failed", "partial"} and execution.get("reason"):
            reason = (reason or f"整理完成 {complete}/{total}") + "；执行说明：" + execution["reason"]
        sections["organization"] = {"status": "failed" if failed else "done" if complete == total else "partial" if complete else "failed",
                                    "reason": reason}
    sections["retrieval_vectors"] = {"status": vectors["status"],
                                    "reason": "检索向量无效，语义分数不可用" if vectors["status"] == "failed"
                                    else vectors.get("reason")}
    expected_methods = [] if manifest["mode"] == "desktop" else ["semantic", "keyword", "tags"]
    if expected_methods and dataset.get("evaluation_profile") == "paper":
        expected_methods.append("title_keyword")
    query_rows = {name: complete_query_rows(dataset["queries"], queries.get(name, [])) for name in expected_methods}
    query_rows.update({name: list(rows) for name, rows in queries.items() if name not in query_rows})
    if query_rows.get("semantic") and vectors["status"] != "done":
        query_rows["semantic"] = [{**row, "status": "not_measured",
                                   "reason": "检索向量无效或未核验，不计算语义效果分数"}
                                  for row in query_rows["semantic"]]
        sections["retrieval_semantic"] = {"status": "not_measured", "reason": "检索向量无效或未核验"}
    elif "semantic" in query_rows:
        sections["retrieval_semantic"] = query_completion(query_rows["semantic"])
    if expected_methods:
        sections["retrieval_baselines"] = query_completion(
            [row for name in expected_methods if name != "semantic" for row in query_rows[name]])
    result: dict[str, Any] = {
        "run_id": run_id, "generated_at": now(), "mode": manifest["mode"],
        "scoring_version": SCORING_VERSION, "analysis_platform_sha256": digest_platform(),
        "conditions": {key: manifest.get(key) for key in (
            "source_sha256", "platform_sha256", "timeindex_project", "dataset_sha256", "dataset_version", "seed", "host", "endpoint",
            "model", "embedding_model", "dedicated_vm", "allow_remote_model", "allow_no_model", "resources")},
        "original_scoring_version": manifest.get("scoring_version"),
        "sections": sections, "organization": organization, "vector_integrity": vectors,
        "diagnostics": diagnose(details, vectors, live.get("vector_integrity") if isinstance(live, dict) else None),
        "desktop": live if isinstance(live, dict) else {"status": "not_measured"},
        "applications": {"synthetic": details["synthetic_software"], "desktop": details["desktop_software"]},
        "methodology": {
            "synthetic_input": "构造 TimeIndex SystemSnapshot，调用真实推理、记录写入、整理与检索；不启动其中列出的业务软件",
            "desktop_input": "专用测试桌面上自动打开记事本、PowerShell和已安装的Edge/Chrome；单独数据库、实际WMI窗口/事件采集",
            "organization_trigger": "合成和真实实验均在采集后调用TimeIndex核心整理函数；自动闲时触发未测试",
            "desktop_settings": details["desktop_settings"],
            "desktop_requested": bool(manifest.get("dedicated_vm")),
            "actual_software_launches": sum(row.get("actually_launched", False) for row in details["desktop_software"]),
            "observable_scope": "进程事件、可见窗口标题与时间；不把文档正文、用户编辑操作当成已观察事实",
            "tag_scoring": "标签线索覆盖是主要指标；coding等精确词表F1仅为辅助诊断，不强迫TimeIndex自由标签使用固定词表",
            "clustering_scope": "同一次真实TimeIndex整理调用内的记录关系；批次大小来自Daemon配置",
            "model_calls_saved": len(details["model_calls"]), "raw_model_trace_available": details["model_trace_available"],
            "evidence_limits": details["evidence_limits"],
            "platform_fixture": read_json(directory / "evidence" / "fixture.json"),
            "keyword_baseline": "新跑实验优先使用有效整理摘要，否则回退有效原始摘要；历史复算只统计保存的查询返回，不重新查询",
        },
        "retrieval_reference": random_reference(dataset, cases, dataset["queries"]),
        "retrieval": {name: aggregate_queries(rows) for name, rows in query_rows.items()},
        "paper": overview(dataset, cases, query_rows, details["model_calls"]),
        "privacy": {"stages": audit(dataset, stages) if stages else {"status": "not_measured"},
                    "checks": privacy_checks},
        "recording": live.get("recording") if isinstance(live, dict) else {"status": "not_measured"},
        "resources": resources if resources is not None else {"status": "not_selected"},
        "failures": [],
    }
    for item in organization["issues"]:
        result["failures"].append({"kind": "organization", "id": item["id"], "reason": "；".join(item["reasons"])})
    for batch in details["batches"].get("batches", []):
        if batch.get("status") == "failed":
            result["failures"].append({"kind": "organization_batch", "id": batch["id"],
                                       "reason": batch.get("error", "整理批次执行失败")})
    for item in organization["primary_app"]["issues"]:
        result["failures"].append({"kind": "primary_app", **item})
    for item in vectors.get("issues", []):
        result["failures"].append({"kind": "vector", "id": item["id"], "reason": "；".join(item["reasons"])})
    for item in cases:
        if item.get("status") != "done" or item.get("error"):
            result["failures"].append({"kind": "case", "id": item.get("id"),
                                       "reason": item.get("error", item.get("status"))})
    for method, rows in query_rows.items():
        for row in rows:
            if row.get("status") == "not_measured":
                continue
            if row.get("status") == "error" or row.get("hit_at_5") == 0:
                result["failures"].append({"kind": f"query:{method}", "id": row.get("id"),
                                           "reason": row.get("error", "前五条未命中")})
            if method == "time" and row.get("status") == "done" and not row.get("exact_set_match"):
                result["failures"].append({"kind": "query:time_set", "id": row.get("id"),
                                           "reason": "时间范围返回集合与预期不一致，详见逐题记录"})
    return result


def _metric_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    organization = result["organization"]
    for key, label, unit in [
        ("facts_before", "摘要事实覆盖率", "比例"), ("facts_after", "整理后事实覆盖率", "比例"),
        ("tags_before", "固定词表标签 F1（辅助诊断）", "比例"), ("tags_after", "整理后固定词表标签 F1（辅助诊断）", "比例"),
    ]:
        item = organization[key]
        rows.append({"项目": label, "值": item.get("coverage", item.get("f1")),
                     "样本量": item.get("total", item.get("cases", organization["cases_scored"])), "单位": unit,
                     "条件": result["mode"] + ("；整理结果缺失计为未命中" if key.endswith("after") else "")})
    for label, value, total in [
        ("合成场景入库完成率", ratio(organization["cases_done"], organization["cases_total"]), organization["cases_total"]),
        ("整理完成率", ratio(organization["retag_complete"], organization["retag_total"]), organization["retag_total"]),
        ("主要应用匹配率", organization["primary_app"]["accuracy"], organization["primary_app"]["total"]),
        ("标签线索覆盖率", organization["tag_cues_before"]["coverage"], organization["tag_cues_before"]["total"]),
        ("整理后标签线索覆盖率", organization["tag_cues_after"]["coverage"], organization["tag_cues_after"]["total"]),
        ("原始窗口信息保留率", ratio(organization["raw_recording"]["windows_preserved"], organization["raw_recording"]["windows_expected"]), organization["raw_recording"]["windows_expected"]),
        ("原始进程事件保留率", ratio(organization["raw_recording"]["events_preserved"], organization["raw_recording"]["events_expected"]), organization["raw_recording"]["events_expected"]),
        ("摘要编号保留率", organization["details_before"]["coverage"], organization["details_before"]["total"]),
        ("整理后编号保留率", organization["details_after"]["coverage"], organization["details_after"]["total"]),
    ]:
        rows.append({"项目": label, "值": value, "样本量": total, "单位": "比例", "条件": result["mode"]})
    for phase, label in [("before", "共同完成样本整理前标签 F1"), ("after", "共同完成样本整理后标签 F1")]:
        item = organization["paired"][f"tags_{phase}"]
        rows.append({"项目": label, "值": item["f1"], "样本量": item["cases"], "单位": "比例",
                     "条件": "仅比较同一批整理字段全部有效的记录"})
    for method, item in result["retrieval"].items():
        if method == "time":
            continue
        for metric in ("hit_at_1", "hit_at_5", "mrr_at_5", "mean_latency_ms"):
            rows.append({"项目": f"{method} {metric}", "值": item[metric], "样本量": item["scored"],
                         "单位": "毫秒" if metric == "mean_latency_ms" else "比例", "条件": result["mode"]})
    recording = result["recording"]
    if recording and recording.get("status") != "not_measured":
        rows.append({"项目": "活动记录检出率", "值": recording.get("recall"),
                     "样本量": recording.get("expected"), "单位": "比例", "条件": "专用测试桌面中的实际软件"})
        if "mean_write_latency_s" in recording:
            rows.append({"项目": "实际窗口出现至首条记录写入的平均延迟", "值": recording["mean_write_latency_s"],
                         "样本量": recording.get("matched"), "单位": "秒", "条件": "实际完成入库的时间，包含模型处理等待"})
    desktop = result.get("desktop", {})
    quality = desktop.get("quality", {})
    for key, label in [("facts_before", "真实软件摘要主题覆盖率"), ("facts_after", "真实软件整理后主题覆盖率"),
                       ("details_before", "真实软件摘要章节保留率"), ("details_after", "真实软件整理后章节保留率")]:
        item = quality.get(key, {})
        if item:
            rows.append({"项目": label, "值": item.get("coverage"), "样本量": item.get("total"), "单位": "比例",
                         "条件": "仅实际模型请求中出现的受控窗口；不评估正文理解"})
    for method, item in desktop.get("retrieval", {}).items():
        for metric in ("hit_at_1", "hit_at_5", "mrr_at_5", "mean_latency_ms"):
            rows.append({"项目": f"真实软件 {method} {metric}", "值": item[metric], "样本量": item["scored"],
                         "单位": "毫秒" if metric == "mean_latency_ms" else "比例", "条件": "真实软件的独立数据库"})
    return rows


def _paper_main_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    paper = result["paper"]
    if not paper["enabled"]:
        return []
    organization = result["organization"]
    raw = organization["raw_recording"]
    values = [
        ("合成输入入库完成率", ratio(organization["cases_done"], organization["cases_total"]), organization["cases_total"], "比例"),
        ("原始窗口信息保留率", ratio(raw["windows_preserved"], raw["windows_expected"]), raw["windows_expected"], "比例"),
        ("原始进程事件保留率", ratio(raw["events_preserved"], raw["events_expected"]), raw["events_expected"], "比例"),
        ("整理完成率", ratio(organization["retag_complete"], organization["retag_total"]), organization["retag_total"], "比例"),
        ("整理前主题线索保留率", organization["facts_before"]["coverage"], organization["facts_before"]["total"], "比例"),
        ("整理后主题线索保留率", organization["facts_after"]["coverage"], organization["facts_after"]["total"], "比例"),
        ("整理前自由标签线索覆盖率", organization["tag_cues_before"]["coverage"], organization["tag_cues_before"]["total"], "比例"),
        ("整理后自由标签线索覆盖率", organization["tag_cues_after"]["coverage"], organization["tag_cues_after"]["total"], "比例"),
        ("时间查询完整返回率", paper["time_retrieval"]["exact_set_accuracy_all_ranges"], paper["time_retrieval"]["planned"], "比例"),
        ("平均单条处理及证据保存时间", paper["processing"]["mean_ms"], paper["processing"]["cases"], "毫秒"),
    ]
    rows = [{"项目": label, "值": value, "样本量": total, "单位": unit,
             "条件": "合成输入，真实TimeIndex核心；不能代替真实桌面采集成绩"} for label, value, total, unit in values]
    for name, label in (("summary", "摘要接口平均响应时间"), ("embedding", "向量接口平均响应时间"),
                        ("organization", "每批整理接口平均响应时间")):
        item = paper["processing"]["model_apis"][name]
        rows.append({"项目": label, "值": item["mean_ms"], "样本量": item["calls"], "单位": "毫秒",
                     "条件": "实际成功接口调用；包含服务和通信等待，不是纯模型计算时间"})
    for method, item in paper["tasks"]["topic"]["methods"].items():
        for metric, label in (("completion_rate", "查询完成率"), ("hit_at_1", "首条命中率（已完成查询）"),
                              ("hit_at_5_all_questions", "前五命中率（全部预定问题）"), ("mrr_at_5", "前五排名得分（已完成查询）")):
            rows.append({"项目": f"主题查找 {method} {label}", "值": item[metric],
                         "样本量": item["scored"] if metric in {"hit_at_1", "mrr_at_5"} else item["total"],
                         "单位": "比例", "条件": "同主题的多条记录预先列为相关；未执行数和错误数见检索分任务表"})
    if result["recording"] and result["recording"].get("status") != "not_measured":
        rows.append({"项目": "真实桌面入库检出率", "值": result["recording"].get("recall"),
                     "样本量": result["recording"].get("expected"), "单位": "比例", "条件": "实际WMI采集，独立桌面数据库"})
    for stage, item in result["privacy"]["stages"].items():
        if isinstance(item, dict) and "exposed_markers" in item:
            rows.append({"项目": f"隐私测试标记出现数 {stage}", "值": item["exposed_markers"],
                         "样本量": item["total_markers"], "单位": "个",
                         "条件": "虚构测试标记；呈现各阶段暴露范围，不是隐私通过率"})
    return rows


def _write_table(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    path.write_text("\ufeff" + output.getvalue(), encoding="utf-8", newline="")


def export(run_id: str) -> dict[str, Path]:
    directory = run_path(run_id)
    dataset = read_json(directory / "dataset.json")
    result = calculate(run_id)
    cases = read_json(directory / "evidence" / "cases.json", [])
    queries = read_json(directory / "evidence" / "queries.json", {})
    details = build_details(directory, dataset, cases, queries)
    report_dir = directory / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    keep_titles = bool(dataset.get("synthetic"))
    def public(value: Any) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        text = hide_uncontrolled_windows(text, directory, details)
        text = redact(text, dataset, keep_test_titles=keep_titles)
        assert_redacted(text, dataset, keep_test_titles=keep_titles)
        return text
    safe = json.loads(public(result))
    details["diagnostics"] = result["diagnostics"]
    safe_details = json.loads(public(details))
    summary = report_dir / "summary.json"
    atomic_json(summary, safe)
    assert_redacted(summary.read_text(encoding="utf-8"), dataset, keep_test_titles=keep_titles)
    details_path = report_dir / "details.json"
    atomic_json(details_path, safe_details)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["项目", "值", "样本量", "单位", "条件"])
    writer.writeheader()
    writer.writerows(_metric_rows(safe))
    csv_text = redact(output.getvalue(), dataset)
    assert_redacted(csv_text, dataset)
    csv_path = report_dir / "paper_metrics.csv"
    csv_path.write_text("\ufeff" + csv_text, encoding="utf-8", newline="")

    html_text = render_report(safe, safe_details, _metric_rows(safe))
    html_text = redact(html_text, dataset, keep_test_titles=keep_titles)
    assert_redacted(html_text, dataset, keep_test_titles=keep_titles)
    html_path = report_dir / "report.html"
    html_path.write_text(html_text, encoding="utf-8")
    if safe["paper"]["enabled"]:
        _write_table(report_dir / "paper_main_metrics.csv", ["项目", "值", "样本量", "单位", "条件"], _paper_main_rows(safe))
        tasks = []
        for task, item in safe["paper"]["tasks"].items():
            for method, values in item["methods"].items():
                tasks.append({"任务": "主题查找（主实验）" if task == "topic" else "指定记录（诊断）", "方式": method,
                              "预定问题数": values["total"], "已完成数": values["scored"], "错误数": values["errors"],
                              "未测数": values["not_measured"], "首条命中率": values["hit_at_1"],
                              "前五命中率": values["hit_at_5"], "全部问题前五命中率": values["hit_at_5_all_questions"],
                              "排名得分": values["mrr_at_5"], "平均耗时毫秒": values["mean_latency_ms"],
                              "随机前五命中参考": item["random_reference"]["expected_random_hit_at_5"]})
        _write_table(report_dir / "retrieval_tasks.csv", list(tasks[0]), tasks)
        stages = [{"阶段": name, "出现敏感标记数": item["exposed_markers"], "测试标记总数": item["total_markers"]}
                  for name, item in safe["privacy"]["stages"].items() if isinstance(item, dict) and "exposed_markers" in item]
        _write_table(report_dir / "privacy_stages.csv", ["阶段", "出现敏感标记数", "测试标记总数"], stages)
        time_rows = [{"查询": row["id"], "开始": row.get("time_start"), "结束": row.get("time_end"),
                      "预期记录数": row["expected_count"], "返回记录数": row.get("returned_count"),
                      "集合准确率": row.get("set_precision"), "集合召回率": row.get("set_recall"),
                      "完整匹配": row.get("exact_set_match"), "状态": row["status"]}
                     for row in safe["paper"]["time_retrieval"]["queries"]]
        _write_table(report_dir / "time_queries.csv", ["查询", "开始", "结束", "预期记录数", "返回记录数", "集合准确率", "集合召回率", "完整匹配", "状态"], time_rows)
        for filename in ("paper_main_metrics.csv", "retrieval_tasks.csv", "privacy_stages.csv", "time_queries.csv"):
            assert_redacted((report_dir / filename).read_text(encoding="utf-8"), dataset, keep_test_titles=keep_titles)
    return {"summary": summary, "csv": csv_path, "html": html_path, "details": details_path}

