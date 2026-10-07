from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from platform_core.bundling import build_report_zip, bundle_signature
from platform_core.common import ROOT, read_json
from platform_core.controller import cancel, start, status
from platform_core.dataset import validate_dataset
from platform_core.manifest import list_runs
from platform_core.preflight import check
from platform_core.project import project_status, save_project_dir


@st.cache_data(show_spinner=False, max_entries=3)
def _report_zip(run_id: str, signature: tuple[tuple[str, int, int], ...]) -> bytes:
    return build_report_zip(run_id)


st.set_page_config(page_title="TimeIndex 测试平台", page_icon="🧪", layout="wide")
st.title("TimeIndex 自动化测试平台")
st.caption("批量测试记录、整理、回忆和隐私暴露；硬件资源测量可选。所有结果保存在本机。")

detected_project = project_status()
timeindex_project = st.text_input(
    "TimeIndex 本体项目文件夹", value=detected_project["path"] or "",
    help="选择包含 pyproject.toml 和 src/TimeIndex 的项目根目录。支持与测试平台放在不同文件夹；留空自动查找。")
selected_project = project_status(timeindex_project or None)
if selected_project["ready"]:
    st.caption(f"本轮使用的 TimeIndex 项目：{selected_project['path']}")
else:
    st.warning(selected_project["reason"])
if st.button("检查并记住本体位置"):
    try:
        saved_project = save_project_dir(selected_project["path"] or timeindex_project)
        st.success(f"已记住 TimeIndex 本体位置：{saved_project}")
    except (OSError, ValueError) as error:
        st.error(str(error))

left, right = st.columns([2, 1])
with left:
    mode_label = st.radio("测试方式", ["快速流程检查（合成）", "论文实验", "真实软件实验", "完整实验", "自定义样本"], horizontal=True)
    mode = {"快速流程检查（合成）": "quick", "论文实验": "paper", "真实软件实验": "desktop", "完整实验": "full", "自定义样本": "custom"}[mode_label]
    uploaded = st.file_uploader("自定义 JSON 数据集", type=["json"]) if mode == "custom" else None
    endpoint = st.text_input("模型接口地址", value="http://127.0.0.1:1234/v1",
                             help="LM Studio 在另一台电脑时，填写那台电脑的局域网 IP，例如 http://192.168.1.20:1234/v1。")
    model = st.text_input("摘要模型", value="gemma-4-e4b")
    embedding_model = st.text_input("向量模型", value="text-embedding-embeddinggemma-300m")
    api_key = st.text_input("模型服务访问令牌（未启用认证可留空）", type="password", value="")
with right:
    resources = st.checkbox("测量硬件资源", value=False)
    desktop_choice = st.checkbox("真实软件测试：我正在专用测试电脑或虚拟机的桌面中运行", value=False,
                                disabled=mode not in {"full", "desktop", "paper"},
                                help="自动打开记事本、PowerShell和可用的Edge/Chrome。会采集所有可见窗口，请在没有私人窗口的专用桌面启用。")
    dedicated_vm = bool(desktop_choice and mode in {"full", "desktop", "paper"})
    allow_remote_model = st.checkbox("允许连接另一台电脑的局域网模型（仅虚构样本）", value=False,
                                     disabled=dedicated_vm,
                                     help="只接受私有局域网 IP；真实桌面采集不能使用此选项。")
    allow_remote_model = bool(allow_remote_model and not dedicated_vm)
    allow_no_model = st.checkbox("仅检查平台，不运行模型质量测试", value=False,
                                 disabled=mode != "quick",
                                 help="无模型自检会生成诊断记录，结果不能作为模型测试成绩。")
    allow_no_model = bool(allow_no_model and mode == "quick")
    model_pid_text = st.text_input("模型进程 PID（可选）", value="", disabled=allow_remote_model,
                                   help="模型在另一台电脑时，平台只能测量测试电脑的资源。")
    model_timeout_s = st.number_input("每次模型请求最多等待（秒）", min_value=1, value=120,
                                     help="正式摘要、整理及向量调用均使用此上限。模型列表探测3秒，推理连接探测最多60秒；超时会保留部分报告。")
    verify_core_repairs = st.checkbox("附加本体修复专项验证（模拟接口）", value=False,
                                      help="在本轮隔离副本测试数字编号、代码块和写入失败保护，不计入实际模型质量成绩；黑名单需另启用专用桌面。")
    st.info("快速检查：8 条多样化合成输入，不实际打开业务软件。完整实验：60 条合成输入、30 个问题；可加测真实软件，逐次记录实际窗口、模型原文和检索结果。")

if mode == "paper":
    st.info("论文实验：60个独立编制的虚构场景，覆盖6类活动、12个主题；12题按主题找活动、18题找具体记录，另测6个时间范围。主结果与章节、标签词表等诊断结果分开统计。")
    st.caption("建议先用快速检查确认模型可用，再用相同配置重复论文实验三轮。确认专用桌面后可加测真实软件和黑名单；资源测量可选。下载诊断ZIP即可保留全部论文表格与逐例证据。")

with st.expander("环境检查", expanded=False):
    if st.button("检查当前环境"):
        try:
            diagnosis = check(endpoint, dedicated_vm=dedicated_vm,
                              allow_remote_model=allow_remote_model, api_key=api_key or None,
                              timeindex_project=timeindex_project or None)
            if diagnosis["timeindex_project"]["ready"]:
                st.success(f"已找到 TimeIndex 本体：{diagnosis['timeindex_project']['path']}")
            else:
                st.error(diagnosis["timeindex_project"]["reason"])
            if diagnosis["model_reachable"]:
                st.success(f"已连接模型接口：{diagnosis['model_endpoint']}")
                if diagnosis["model_ids"]:
                    st.write("服务列出的模型：" + "、".join(diagnosis["model_ids"]))
                    for label, chosen in (("摘要模型", model), ("向量模型", embedding_model)):
                        if chosen not in diagnosis["model_ids"]:
                            st.warning(f"{label}“{chosen}”不在服务列出的模型中；请核对完整名称。")
            else:
                st.error(f"模型接口未就绪：{diagnosis['model_detail']}")
            st.json(diagnosis, expanded=False)
        except ValueError as error:
            st.error(str(error))

if st.button("开始批量实验", type="primary"):
    dataset_path: Path | None = None
    try:
        if mode == "custom":
            if uploaded is None:
                raise ValueError("请先选择自定义数据集")
            data = json.loads(uploaded.getvalue())
            validate_dataset(data)
            folder = ROOT / ".uploads"
            folder.mkdir(exist_ok=True)
            dataset_path = folder / f"{secrets.token_hex(8)}.json"
            dataset_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        model_pid = int(model_pid_text) if model_pid_text.strip() and not allow_remote_model else None
        manifest = start(mode, dataset_path, resources=resources, dedicated_vm=dedicated_vm,
                         endpoint=endpoint, model=model, embedding_model=embedding_model,
                         model_pid=model_pid, allow_remote_model=allow_remote_model,
                         allow_no_model=allow_no_model, api_key=api_key or None,
                          timeindex_project=timeindex_project or None, model_timeout_s=model_timeout_s,
                          verify_core_repairs=verify_core_repairs)
        st.success(f"已启动实验：{manifest['run_id']}")
    except Exception as error:
        st.error(str(error))
    finally:
        if dataset_path and dataset_path.exists():
            dataset_path.unlink()

st.divider()
st.subheader("实验记录")
runs = list_runs()
if not runs:
    st.info("尚无实验记录。")
else:
    ids = [item["run_id"] for item in runs]
    selected = st.selectbox("选择一轮实验", ids)

    @st.fragment(run_every="2s")
    def progress_panel(run_id: str) -> None:
        try:
            item = status(run_id)
        except Exception as error:
            st.error(str(error))
            return
        status_names = {"done": "完成", "running": "运行中", "failed": "失败", "partial": "部分完成",
                        "diagnostic": "无模型自检", "cancelled": "已取消", "interrupted": "意外中断"}
        progress_text = (f"已尝试 {item.get('completed_cases', 0)} / {item.get('total_cases', 0)} 次软件启动" if item.get("mode") == "desktop" else
                         f"已处理 {item.get('completed_cases', 0)} / {item.get('total_cases', 0)} 个合成场景")
        st.write(f"**状态：{status_names.get(item['status'], item['status'])}**　{progress_text}")
        if item["status"] == "done":
            st.caption("完成表示步骤已执行；摘要是否准确、是否找回目标记录，请查看详细报告。")
        if item["status"] == "diagnostic":
            st.warning("这轮只检查了无模型时的平台流程，没有运行模型质量测试。")
        elif item["status"] == "partial":
            st.warning("部分指标未完成。请展开各项目状态查看原因，勿将未测项目当作模型成绩。")
        elif item["status"] == "done" and item.get("completed_cases", 0) < item.get("total_cases", 0):
            st.error("这轮旧记录没有处理完预定场景，不能作为已完成的模型测试。")
        total = item.get("total_cases") or 1
        st.progress(min(item.get("completed_cases", 0) / total, 1.0))
        if item.get("error"):
            st.error(item["error"])
        if item["status"] == "running" and item.get("current_phase"):
            st.caption(item["current_phase"])
        waiting = item.get("current_model_call")
        if item["status"] == "running" and waiting:
            elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(waiting["started_at"])).total_seconds())
            st.caption(f"当前模型步骤：{waiting['phase']} · 请求 {waiting['id']} · 已等待 {elapsed:.0f} 秒 / 上限 {waiting['wait_limit_s']:g} 秒")
        if item["status"] == "running" and st.button("取消本轮实验", key=f"cancel-{run_id}"):
            cancel(run_id)
            st.rerun()
        st.json(item.get("sections", {}), expanded=False)
        report = ROOT / "runs" / run_id / "reports"
        saved_summary = read_json(report / "summary.json", {})
        findings = saved_summary.get("diagnostics", {}).get("findings", [])
        if findings:
            with st.expander("本轮诊断说明", expanded=item["status"] in {"partial", "failed"}):
                for finding in findings:
                    st.write(f"**{finding['title']}**")
                    st.write(finding["description"])
        if item["status"] not in {"created", "running"}:
            try:
                st.download_button("一键下载诊断报告（ZIP）",
                                   data=_report_zip(run_id, bundle_signature(run_id)),
                                   file_name=f"{run_id}-reports.zip", mime="application/zip",
                                   key=f"download-zip-{run_id}", type="primary")
                st.caption("一次下载报告、模型原文、逐例结果和实验信息；已遮罩测试敏感串与非测试窗口标题。")
            except (OSError, ValueError, RuntimeError) as error:
                st.error(f"诊断报告 ZIP 生成失败：{error}")
        for label, filename, mime in [
            ("下载论文主指标表", "paper_main_metrics.csv", "text/csv"),
            ("下载检索分任务对照表", "retrieval_tasks.csv", "text/csv"),
            ("下载时间查询结果表", "time_queries.csv", "text/csv"),
            ("下载隐私阶段表", "privacy_stages.csv", "text/csv"),
            ("下载详细实验报告（逐例输入与模型原文）", "report.html", "text/html"),
            ("下载论文指标表", "paper_metrics.csv", "text/csv"),
            ("下载汇总 JSON", "summary.json", "application/json"),
            ("下载逐例明细 JSON（含原始模型回复）", "details.json", "application/json"),
        ]:
            path = report / filename
            if path.exists():
                st.download_button(label, data=path.read_bytes(), file_name=f"{run_id}-{filename}",
                                   mime=mime, key=f"download-{filename}-{run_id}")
        html_report = report / "report.html"
        if html_report.exists() and st.checkbox("在页面查看详细实验报告", key=f"view-report-{run_id}"):
            import streamlit.components.v1 as components
            components.html(html_report.read_text(encoding="utf-8"), height=1000, scrolling=True)

    progress_panel(selected)

