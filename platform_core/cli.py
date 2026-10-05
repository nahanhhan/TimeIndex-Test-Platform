from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import controller
from .bundling import build_report_zip
from .common import ROOT
from .dataset import build_default, validate_dataset
from .isolation import cleanup
from .manifest import list_runs
from .preflight import check
from .project import resolve_project_dir, save_project_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="TimeIndex 自动化测试平台")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="检查当前环境")
    doctor.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    doctor.add_argument("--allow-lan-model", action="store_true")
    doctor.add_argument("--timeindex-project", type=Path, help="TimeIndex 本体项目根目录；留空自动查找")
    locate = commands.add_parser("locate", help="查找或记住 TimeIndex 本体项目位置")
    locate.add_argument("--timeindex-project", type=Path)
    locate.add_argument("--save", action="store_true")
    run = commands.add_parser("run", help="启动实验")
    run.add_argument("--mode", choices=("quick", "full", "custom", "desktop", "paper"), default="quick")
    run.add_argument("--dataset", type=Path)
    run.add_argument("--timeindex-project", type=Path, help="TimeIndex 本体项目根目录；留空自动查找")
    run.add_argument("--resources", action="store_true")
    run.add_argument("--dedicated-vm", "--dedicated-desktop", dest="dedicated_vm", action="store_true",
                     help="专用测试电脑或虚拟机桌面；完整模式自动运行实际软件")
    run.add_argument("--allow-lan-model", action="store_true")
    run.add_argument("--no-model-self-check", action="store_true")
    run.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    run.add_argument("--model", default="gemma-4-e4b")
    run.add_argument("--embedding-model", default="text-embedding-embeddinggemma-300m")
    run.add_argument("--model-pid", type=int)
    one = commands.add_parser("status", help="查看进度")
    one.add_argument("run_id")
    stop = commands.add_parser("cancel", help="取消实验")
    stop.add_argument("run_id")
    clean = commands.add_parser("cleanup", help="清理临时副本")
    clean.add_argument("run_id")
    again = commands.add_parser("replay", help="从已保存结果重新计算报告")
    again.add_argument("run_id")
    bundle = commands.add_parser("bundle", help="将本轮报告和脱敏证据打包为 ZIP")
    bundle.add_argument("run_id")
    commands.add_parser("list", help="列出实验")
    commands.add_parser("generate-dataset", help="重建预置数据集")
    args = parser.parse_args()
    api_key = os.environ.get("TIMEINDEX_TEST_MODEL_API_KEY")
    if args.command == "doctor":
        result = check(args.endpoint, allow_remote_model=args.allow_lan_model, api_key=api_key,
                       timeindex_project=args.timeindex_project)
    elif args.command == "locate":
        project = resolve_project_dir(args.timeindex_project)
        if args.save:
            save_project_dir(project)
        result = {"timeindex_project": str(project), "saved": args.save}
    elif args.command == "run":
        result = controller.start(args.mode, args.dataset, resources=args.resources,
                                  dedicated_vm=args.dedicated_vm, endpoint=args.endpoint,
                                  model=args.model, embedding_model=args.embedding_model,
                                  model_pid=args.model_pid, allow_remote_model=args.allow_lan_model,
                                  allow_no_model=args.no_model_self_check, api_key=api_key,
                                  timeindex_project=args.timeindex_project)
    elif args.command == "status":
        result = controller.status(args.run_id)
    elif args.command == "cancel":
        result = controller.cancel(args.run_id)
    elif args.command == "cleanup":
        cleanup(args.run_id)
        result = {"cleaned": args.run_id}
    elif args.command == "replay":
        result = {key: str(path) for key, path in controller.replay(args.run_id).items()}
    elif args.command == "bundle":
        from .manifest import run_path
        destination = run_path(args.run_id) / "reports" / f"{args.run_id}-reports.zip"
        data = build_report_zip(args.run_id)
        destination.write_bytes(data)
        result = {"zip": str(destination)}
    elif args.command == "list":
        result = list_runs()
    else:
        result = build_default()
        validate_dataset(result)
        destination = ROOT / "datasets" / "default.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"path": str(destination), "cases": len(result["cases"]),
                  "queries": len(result["queries"])}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

