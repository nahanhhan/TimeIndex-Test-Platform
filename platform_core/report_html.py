from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from .details import METHOD_LABELS, compact_response


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "未提供"))


def block(value: Any) -> str:
    return '<pre>' + esc(json.dumps(value, ensure_ascii=False, indent=2, default=str)) + '</pre>'


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return '<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{esc(value)}</th>' for value in headers) + \
        '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def clock_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return "未记录"


def metric_value(value: Any, unit: str = "比例") -> str:
    if value is None:
        return "未测 / 无适用样本"
    if isinstance(value, (int, float)):
        return f"{value:.1%}" if unit == "比例" else f"{value:.2f}"
    return str(value)


def status_name(value: Any) -> str:
    return {"done": "完成", "partial": "部分完成", "not_measured": "未测", "failed": "失败",
            "cancelled": "已取消", "error": "错误", "started": "已启动", "not_started": "未启动",
            "not_available": "未安装", "running": "运行中", "not_selected": "未选择"}.get(value, str(value))


def tag_text(value: Any) -> str:
    return ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value or "")


def diagnostic_section(result: dict[str, Any]) -> str:
    diagnostics = result.get("diagnostics", {})
    if not diagnostics:
        return ""
    requests = diagnostics["model_requests"]
    findings = ''.join(f'<article><h3>{esc(row["title"])} · {esc(row["scope"])}</h3>'
                       f'<p>{esc(row["description"])}</p><details><summary>查看判断依据</summary>{block(row["evidence"])}</details></article>'
                       for row in diagnostics["findings"])
    batches = table(["批次 / 阶段", "模型原始结果数", "本体返回记录数", "数据库更新操作数", "最终有效整理数", "编号直接匹配 / 文本对照"],
                    [[str(row["id"]) + " / " + row["scope"], row["model_response_count"], row["core_returned_count"],
                      row["database_update_count"], f'{row["persisted_valid_count"] if row["persisted_valid_count"] is not None else "未保存"} / {row["input_count"]}',
                      f'{row["exact_id_matches"] if row["exact_id_matches"] is not None else "未测"} / {row["text_id_matches"] if row["text_id_matches"] is not None else "未测"}'] for row in diagnostics["organization_batches"]])
    return '<h2 id="diagnostics">本轮诊断：请求完成与结果有效分开看</h2>' + \
        f'<p>保存接口请求 {requests["saved"]} 次，其中请求完成 {requests["completed"]} 次、调用出错 {requests["failed"]} 次。' + \
        '这些数量不代表摘要准确或整理成功；整理是否有效以最终保存的字段为准。</p>' + batches + \
        (findings or '<p>现有证据未触发这些故障诊断；内容质量仍需查看逐例结果。</p>') + \
        f'<p>{esc(diagnostics["limits"])}</p>'


def retrieval_table(values: dict[str, Any]) -> str:
    labels = METHOD_LABELS
    return table(["检索方式", "已评分 / 全部题目", "首条命中率（已评分）", "前五条命中率（已评分）", "排名得分", "平均耗时（毫秒）", "错误 / 未测"],
                 [[labels.get(name, name), f"{item['scored']} / {item['total']}", metric_value(item.get("hit_at_1")),
                   metric_value(item.get("hit_at_5")), metric_value(item.get("mrr_at_5")), metric_value(item.get("mean_latency_ms"), "毫秒"),
                   f"{item.get('errors', 0)} / {item.get('not_measured', 0)}"] for name, item in values.items() if name != "time"])


def case_card(row: dict[str, Any], real: bool = False) -> str:
    original = row.get("record") or row.get("intent") or {}
    refined = row.get("refined") or {}
    windows = row.get("input", {}).get("windows", [])
    inputs = table(["输入进程", "窗口标题", "PID"], [[w.get("process_name"), w.get("title"), w.get("pid")] for w in windows])
    comparison = table(["阶段", "摘要", "标签", "主要应用 / 分组"], [
        ["TimeIndex 初始结果", original.get("summary"), tag_text(original.get("tags")), original.get("primary_app")],
        ["TimeIndex 整理结果", refined.get("refined_summary"), tag_text(refined.get("refined_tags")), refined.get("cluster_id")],
    ])
    links = ' '.join(f'<a href="#{esc(key)}">{esc(key)}</a>' for key in row.get("model_call_ids", []))
    evidence = {key: row.get(key) for key in ("expected", "facts_before", "facts_after", "details_before", "details_after", "recording_check", "quality_note", "input", "intent", "record", "refined", "error") if key in row}
    note = "真实桌面采集" if real else "合成输入，没有因此打开应用"
    missing = '' if row.get("raw_response_available", real) else '<p class="notice">旧版本没有保存原始回复，下方为解析后的结果。</p>'
    return f'<article id="case-{esc(row["id"])}"><h3>{esc(row["id"])} · {esc(note)}</h3><p>状态：{esc(status_name(row.get("status")))}；处理耗时：{esc(metric_value(row.get("elapsed_ms"), "毫秒"))} ms</p>' + \
        inputs + comparison + missing + f'<p>关联模型调用：{links or "未保存"}</p><details><summary>完整输入、标准答案、解析结果与入库结果</summary>{block(evidence)}</details></article>'


def query_cards(values: dict[str, Any]) -> str:
    cards = []
    for method, rows in values.items():
        for row in rows:
            requested = {key: row.get(key) for key in ("query", "task", "keyword", "keyword_terms", "tags_requested", "relevant_ids", "corpus_size", "returned_ids", "hit_at_1", "hit_at_5", "time_start", "time_end", "set_precision", "set_recall", "exact_set_match", "latency_ms", "status", "error", "reason") if key in row}
            records = row.get("returned_records", [])
            results = table(["排名", "记录 / 场景", "初始摘要", "整理摘要", "标签"],
                            [[r.get("rank", index + 1), r.get("case_id") or r.get("id"), r.get("summary"), r.get("refined_summary"),
                              tag_text(r.get("tags"))] for index, r in enumerate(records)])
            links = ' '.join(f'<a href="#{esc(key)}">{esc(key)}</a>' for key in row.get("model_call_ids", []))
            cards.append(f'<details><summary>{esc(METHOD_LABELS.get(method, method))} · {esc(row["id"])} · {esc(row.get("query"))} · 前五命中 {esc(row.get("hit_at_5"))}</summary>' +
                          f'<p>模型请求：{links or "无关联请求"}。{esc(row.get("model_call_note", "本轮未保存关联说明"))}</p>' +
                          block(requested) + (results if records else '<p>没有返回记录，或旧版本未保存返回记录正文。</p>') + '</details>')
    return ''.join(cards)


def paper_section(result: dict[str, Any]) -> str:
    paper = result.get("paper", {})
    if not paper.get("enabled"):
        return ""
    rows = []
    labels = METHOD_LABELS
    for task, item in paper["tasks"].items():
        for method, values in item["methods"].items():
            rows.append(["按主题找活动（主实验）" if task == "topic" else "找指定记录（诊断）",
                         labels[method], f"{values['scored']} / {values['total']}",
                         metric_value(values["hit_at_1"]), metric_value(values["hit_at_5"]),
                         metric_value(values["hit_at_5_all_questions"]),
                         f"{values['errors']} / {values['not_measured']}",
                         metric_value(item["random_reference"]["expected_random_hit_at_5"])])
    timed = paper["time_retrieval"]
    protocol = paper["protocol"]
    return '<h2 id="paper">论文实验：方法、数据和结果</h2>' + \
        f'<p>固定的{protocol["cases"]}个虚构窗口场景，覆盖{protocol["categories"]}类活动、{protocol["topics"]}个主题；' + \
        f'{protocol["topic_queries"]}个主题查找问题、{protocol["specific_queries"]}个指定记录问题，另验证{protocol["time_ranges"]}个时间范围。' + \
        '这些合成输入调用真实TimeIndex核心；真实桌面结果在下方单列。正文、截图内容和自动闲时触发未测。</p>' + \
        '<p>主结果关注原始记录、整理前后主题线索、主题回忆、时间查询和隐私。章节编号、精确英文标签与分组保留为诊断；不设统一及格分。关键词对照使用同一套预先给定的查询关键词；指定记录查询同时要求主题和章节。</p>' + \
        table(["任务", "方式", "完成 / 预定", "首条命中（完成题）", "前五命中（完成题）", "前五命中（全部预定题）", "错误 / 未测", "随机前五参考"], rows) + \
        f'<p>时间查询完成 {timed["scored"]}/{timed["planned"]}；完整返回全部正确记录的比例：{metric_value(timed["exact_set_accuracy_all_ranges"])}。' + \
        '此项核对返回集合，不以“前五碰巧包含一条”代替时间查询正确性。</p>' + \
        '<p>单轮结果可从论文主指标表、检索分任务表、时间查询表和隐私阶段表导出。建议同一配置重复三轮，分别保留每轮结果；重复运行不增加独立样本数量。模拟模型结果只用于平台验收。</p>' + \
        '<details><summary>预先固定的实验方案与全部时间查询</summary>' + block(paper) + '</details>'


def render_report(result: dict[str, Any], details: dict[str, Any], metrics: list[dict[str, Any]]) -> str:
    organization = result["organization"]
    real_started = sum(row.get("actually_launched", False) for row in details["desktop_software"] if row.get("kind") != "blacklist_probe")
    synthetic_summary = f'合成入库 {organization["cases_done"]}/{organization["cases_total"]}；整理完成 {organization["retag_complete"]}/{organization["retag_total"]}' if organization["cases_total"] else "合成阶段未执行"
    if organization["cases_total"] and result["sections"].get("organization", {}).get("status") == "not_measured":
        synthetic_summary = f'合成入库 {organization["cases_done"]}/{organization["cases_total"]}；整理未测'
    summary = f'{synthetic_summary}；实际启动业务软件 {real_started} 次'
    original_version = result.get("original_scoring_version")
    replay_note = (f'<p class="notice">本轮原评分版本：{esc(original_version)}；当前分析版本：{esc(result["scoring_version"])}。'
                   '查询按原来保存的返回结果统计，没有重新执行；新版关键词回退只影响新跑实验。</p>'
                   if original_version and original_version != result["scoring_version"] else '')
    software = table(["应用名称", "进程", "涉及场景", "是否真的启动"],
                     [[row["application"], row["process"], ", ".join(row["case_ids"]), "否：仅构造窗口快照"] for row in details["synthetic_software"]])
    real = table(["软件 / 用途", "任务 / 文件", "状态", "启动 / 关闭（北京时间）", "启动 PID", "观察到的真实窗口"],
                 [[str(row.get("application")) + " / " + str(row.get("purpose", "真实桌面测试")), row.get("document"), row.get("status"),
                   clock_time(row.get("opened_at")) + " / " + clock_time(row.get("closed_at")), row.get("root_pid"),
                   "；".join(w["title"] for w in row.get("windows", [])) or row.get("reason")] for row in details["desktop_software"]])
    timeline = table(["时间（北京时间）", "场景", "软件", "操作", "状态 / 原因"],
                     [[clock_time(row.get("time")), row.get("scene_id"), row.get("application"),
                       {"open": "打开", "focus": "切换到前台", "observe": "观察", "close": "关闭"}.get(row.get("action"), row.get("action")),
                       str(row.get("status", "已请求；关闭结果见软件证据")) + " " + str(row.get("reason") or "")] for row in details["live_actions"]])
    method_rows = [["合成场景", "构造窗口与进程输入，调用真实TimeIndex推理、入库、整理和检索；不会据此打开软件"],
                   ["真实软件场景", "自动打开记事本、PowerShell、本地网页（Edge/Chrome），实际采集窗口和进程；使用独立数据库"],
                   ["输入范围", "窗口标题、进程事件与时间；没有提供文档正文或键盘操作"],
                   ["整理方式", "采集后调用TimeIndex核心整理函数；自动闲时触发未测"],
                   ["记录完整性", "核对窗口标题、进程、PID、时间是否原样入库"],
                   ["线索质量", "核对线索是否大致可信、能否缩小范围；错误应用或冲突内容需排查。需要细节时按需使用本体raw接口"],
                   ["辅助诊断", "词语、章节、固定标签及分组分数只辅助观察；省略函数或章节、摘要/向量相同和粗分组不单独判失败"],
                   ["标签与分组", "接受不同的合理标签用词；固定词表分数仅供诊断；只比较同一整理批次的分组"],
                   ["摘要关键词对照", "新跑实验使用有效整理摘要，否则回退有效原始摘要；历史复算只统计保存的查询结果"],
                   ["模型调用证据", f"保存 {len(details['model_calls'])} 次接口调用；完整输入、原始回复和解析结果分别展示"]]
    calls = []
    for call in details["model_calls"]:
        response = call.get("response") or {}
        choices = response.get("choices", [])
        outputs = ''.join('<h4>模型原文</h4><pre>' + esc(choice.get("message", {}).get("content")) + '</pre>' +
                          ('<h4>reasoning_content 原文</h4><pre>' + esc(choice["message"]["reasoning_content"]) + '</pre>'
                           if choice.get("message", {}).get("reasoning_content") else '') for choice in choices)
        caption = f'{call["id"]} · {call["context"].get("phase", "未注明阶段")} · {"文本模型" if call["operation"] == "chat" else "向量模型"} · {"请求已完成" if call["status"] == "done" else status_name(call["status"])}'
        metadata = {"context": call["context"], "started_at": call.get("started_at"), "finished_at": call.get("finished_at"),
                    "elapsed_ms": call.get("elapsed_ms"), "requested_model": call["request"].get("model"),
                    "returned_model": response.get("model"), "usage": response.get("usage"),
                    "finish_reasons": [choice.get("finish_reason") for choice in choices], "error": call.get("error")}
        calls.append(f'<details id="{esc(call["id"])}"><summary>{esc(caption)}</summary><h4>调用信息</h4>{block(metadata)}' +
                     f'<h4>实际发出的请求（含系统提示词与用户输入）</h4>{block(call["request"])}' + outputs +
                     f'<h4>接口回复</h4>{block(compact_response(call))}' +
                     (f'<details><summary>完整向量接口原始回复</summary>{block(response)}</details>' if call["operation"] == "embedding" else '') + '</details>')
    status_rows = [[name, status_name(value.get("status")), value.get("reason") or ""] for name, value in result["sections"].items()]
    metric_rows = [[row["项目"], metric_value(row["值"], row["单位"]), row["样本量"], row["单位"], row["条件"]] for row in metrics]
    failures = ''.join(f'<li>{esc(row["kind"])} / {esc(row["id"])}：{esc(row["reason"])}</li>' for row in result["failures"])
    observations = ''.join(f'<li>{esc(row["kind"])} / {esc(row["id"])}：{esc(row["reason"])}</li>' for row in result.get("quality_observations", []))
    assessment = table(["类别", "如何理解"], [
        ["流程完成", "步骤是否执行、记录是否保存、整理字段是否有效；不代表线索正确"],
        ["线索质量", "能否提供大致可信的历史线索、帮助缩小范围；结合原始模型回复核对冲突内容"],
        ["辅助诊断", "词语、章节、标签词表、指定记录和分组分数不设本体及格要求"],
        ["未测 / 未验证", "没有执行证据的项目保持未知；普通快速实验不能代替本体修复专项验证"]])
    no_real = '' if real_started else '<p class="notice">本轮没有实际打开业务软件。合成场景中的应用名称仅作为模型输入，不能据此宣称真实桌面采集已通过。</p>'
    no_calls = '' if details["model_trace_available"] else '<p class="notice">这轮旧证据未保存原始模型请求和回复。解析结果不能当成模型原始输出；请用新版重跑以取得完整证据。</p>'
    style = 'body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f3f6fa;color:#172b3a}main{max-width:1240px;margin:auto;padding:28px}h1,h2{color:#104f6a}h2{margin-top:36px}nav{display:flex;gap:18px;flex-wrap:wrap;padding:14px;background:#fff;border-radius:8px}a{color:#126286}article,details{background:#fff;border:1px solid #d7e1e9;border-radius:8px;padding:14px;margin:12px 0}details details{background:#f8fafc}summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;padding:14px;border-radius:6px;font-size:13px;line-height:1.6}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0}th,td{border-bottom:1px solid #d7e1e9;text-align:left;vertical-align:top;padding:11px;min-width:85px;overflow-wrap:anywhere}th{background:#e9f1f7}.notice{border-left:4px solid #c98925;padding:12px;background:#fff7e8}p{line-height:1.7}.lead{font-size:19px}code{overflow-wrap:anywhere}@media print{details{break-inside:avoid}nav{display:none}}'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TimeIndex 逐例实验报告</title><style>{style}</style><main>
<h1>TimeIndex 逐例实验报告</h1><p>运行：{esc(result['run_id'])} · 模式：{esc(result['mode'])} · 评分规则：{esc(result['scoring_version'])}</p><p class="lead">{esc(summary)}</p>
{replay_note}
<nav><a href="#diagnostics">本轮诊断</a><a href="#method">测试方式</a><a href="#software">软件清单</a><a href="#cases">逐例结果</a><a href="#calls">模型原始输出</a><a href="#queries">回忆测试</a><a href="#privacy">隐私与资源</a><a href="#metrics">全部指标</a></nav>
<p>TimeIndex 提供粗粒度历史时间线线索，供信息残缺的LLM缩小范围；隐私优先，不追求保存所有细节。</p>{assessment}
{diagnostic_section(result)}
{paper_section(result)}
<h2 id="method">这轮到底测试了什么</h2>{no_real}{table(['项目','测试方式与边界'], method_rows)}<p>完成状态表示步骤完成，不表示内容正确；自动评分只检查列出的事实线索，完整质量请结合逐例原文复核。</p><details><summary>测试方式原始信息与证据缺失说明</summary>{block(result['methodology'])}</details>
<details><summary>完整实验条件与源码校验值</summary>{block(result['conditions'])}</details>
<h2 id="software">软件与窗口清单</h2><h3>合成输入中的软件（未实际启动）</h3>{software}<h3>实际软件启动情况</h3>{real if details['desktop_software'] else '<p>未执行真实软件场景。</p>'}
<details><summary>真实桌面操作时间线</summary>{timeline}{block(details['desktop_software'])}</details>
<h2 id="cases">每个场景的输入与输出</h2>{''.join(case_card(row) for row in details['cases'])}
<h3>真实桌面采集、摘要与整理输出</h3>{''.join(case_card(row, True) for row in details['live_cases']) or '<p>本轮未保存真实桌面逐例结果。</p>'}
<details><summary>每批整理的输入记录、返回记录与写回数量</summary>{block(details['batches'])}</details>
<h2 id="calls">逐次模型请求与原始回复</h2><p>以下请求和回复由接口调用旁路保存，不改写 TimeIndex 提示词或模型回答。请求头和访问令牌不导出，测试敏感串已遮罩，非测试窗口标题已隐藏。旧版本缺失的原始回复不会被拼造。</p>{no_calls}{''.join(calls)}
<details><summary>受控模型失联场景的实际结果（本地注入故障）</summary>{block(details['fault_injection'])}</details>
<h2 id="queries">具体问了什么、找回了什么</h2><p>语义和标签查询调用本体；关键词是平台实现的摘要对照，不能标作本体关键词接口成绩。没有调用模型的方法明确标注。TimeIndex 回忆返回记录列表；需要细节时按需使用本体raw接口。未保存执行结果的预定题目计入未测；命中率和排名得分只统计已完成题目。快速检查的记录很少，分数不能代表长期使用效果。</p>{retrieval_table(result['retrieval'])}<details><summary>记录库大小与随机命中的理论参考</summary>{block(result['retrieval_reference'])}</details>{query_cards(details['queries'])}
<h3>真实软件记录的回忆结果</h3>{retrieval_table(result.get('desktop', {}).get('retrieval', {}))}{query_cards(details['desktop_queries']) or '<p>未执行。</p>'}
<h2 id="privacy">隐私与可选资源</h2>{block(result['privacy'])}<details><summary>资源测量结果与测量边界</summary>{block(result['resources'])}</details><p>报告保留受控虚构输入以便复核；原始完整版证据仅保存在本地运行目录。未测的真实桌面、黑名单或硬件项目不能算作通过。</p>
<h2 id="metrics">指标与适用范围</h2>{table(['项目','值','样本量','单位','条件'], metric_rows)}<h3>流程状态与执行失败</h3>{table(['步骤','完成状态','说明'],status_rows)}<ul>{failures or '<li>无执行失败。</li>'}</ul><h3>线索质量与检索观察</h3><p>以下是待复核的线索或题目表现；应用别名未匹配不自动证明内容冲突，细节省略不直接判为本体缺陷。</p><ul>{observations or '<li>未触发上述观察；仍需结合逐例原文判断线索质量。</li>'}</ul><h3>本体修复专项验证</h3>{block(result.get('core_repair_checks', {'status': 'not_measured'}))}<p>未执行的数字编号、代码块、写入失败保护和专用桌面黑名单项目均保持未验证。</p>
</main></html>'''
