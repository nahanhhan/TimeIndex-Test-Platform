from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil


def browser_executable() -> Path | None:
    candidates = []
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates.extend([Path(root) / "Microsoft/Edge/Application/msedge.exe",
                               Path(root) / "Google/Chrome/Application/chrome.exe"])
    return next((path for path in candidates if path.is_file()), None)


def app_plan(run_id: str, round_number: int, folder: Path) -> list[dict[str, Any]]:
    folder.mkdir(parents=True, exist_ok=True)
    system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    token = run_id[-8:] + f"-R{round_number}"
    secret = " TEST-SECRET-DESKTOP" if round_number == 1 else ""
    note = folder / f"Python 函数 第{round_number}节{secret} {token}-N.txt"
    note.write_text("Python 函数练习\n本文件仅用于打开记事本观察窗口标题。TimeIndex 不读取正文。\n", encoding="utf-8-sig")
    script = folder / f"terminal-{round_number}.ps1"
    terminal_title = f"实验报告 第{round_number}节 {token}-T"
    quoted = terminal_title.replace("'", "''")
    script.write_text("$Host.UI.RawUI.WindowTitle = '" + quoted + "'\nWrite-Host 'TimeIndex controlled desktop test'\n", encoding="utf-8-sig")
    page = folder / f"database-{round_number}.html"
    browser_title = f"数据库索引 第{round_number}节 {token}-B"
    page.write_text(f'<!doctype html><meta charset="utf-8"><title>{browser_title}</title><h1>数据库索引笔记</h1><p>仅本地测试页面，无外部资源。</p>', encoding="utf-8")
    browser = browser_executable()
    return [
        {"id": f"R{round_number}-N", "application": "Windows 记事本", "topic": "Python 函数",
         "executable": str(system / "notepad.exe"), "arguments": [str(note)],
         "marker": token + "-N", "document": note.name, "purpose": "打开编程笔记，观察真实窗口标题"},
        {"id": f"R{round_number}-T", "application": "Windows PowerShell", "topic": "实验报告",
         "executable": str(system / "WindowsPowerShell/v1.0/powershell.exe"),
         "arguments": ["-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", str(script)],
         "marker": token + "-T", "document": script.name, "new_console": True,
         "purpose": "显示报告任务标题；不执行或修改私人文件"},
        {"id": f"R{round_number}-B", "application": "Microsoft Edge" if browser and browser.name == "msedge.exe" else "Google Chrome",
         "topic": "数据库索引", "executable": str(browser) if browser else None,
         "arguments": ["--no-first-run", "--disable-background-mode", "--disable-extensions",
                       "--user-data-dir=" + str(folder / f"browser-profile-{round_number}"), "--app=" + page.as_uri()],
         "marker": token + "-B", "document": page.name, "purpose": "打开本地技术资料页面；使用独立浏览器配置"},
    ]


def matching_windows(marker: str) -> list[dict[str, Any]]:
    import win32gui
    import win32process

    rows = []
    def visit(hwnd: int, _extra: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if marker not in title:
            return
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = psutil.Process(pid).name()
        except psutil.Error:
            process = "unknown"
        rows.append({"hwnd": hwnd, "title": title, "pid": pid, "process": process})
    win32gui.EnumWindows(visit, None)
    return rows


class AppSession:
    def __init__(self, plan: dict[str, Any]):
        self.plan = plan
        self.child: subprocess.Popen | None = None
        self.closed = False
        self.evidence = {key: value for key, value in plan.items() if key not in {"arguments", "executable"}}
        self.evidence.update(executable_name=Path(plan["executable"]).name if plan.get("executable") else None,
                             actually_launched=False, status="not_started", windows=[])

    def open(self) -> dict[str, Any]:
        path = self.plan.get("executable")
        if not path or not Path(path).is_file():
            self.evidence.update(status="not_available", reason="测试软件未安装，未以模拟窗口替代")
            return self.evidence
        try:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if self.plan.get("new_console") else 0
            self.child = subprocess.Popen([path, *self.plan["arguments"]], creationflags=flags)
            self.evidence.update(actually_launched=True, root_pid=self.child.pid, opened_at=time.time())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                windows = matching_windows(self.plan["marker"])
                if windows:
                    self.evidence.update(status="started", windows=windows, visible_at=time.time())
                    return self.evidence
                time.sleep(0.2)
            self.evidence.update(status="failed", reason="进程已启动，但未发现带测试文档标题的可见窗口")
        except Exception as error:
            self.evidence.update(status="failed", reason=str(error))
        return self.evidence

    def focus(self) -> dict[str, Any]:
        import win32con
        import win32gui

        windows = matching_windows(self.plan["marker"])
        if not windows:
            return {"status": "failed", "reason": "测试窗口不可见"}
        try:
            win32gui.ShowWindow(windows[0]["hwnd"], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(windows[0]["hwnd"])
            return {"status": "done", "windows": windows}
        except Exception as error:
            return {"status": "failed", "reason": str(error), "windows": windows}

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        import win32con
        import win32gui

        owned = []
        if self.child and self.child.poll() is None:
            try:
                owned = psutil.Process(self.child.pid).children(recursive=True)
            except psutil.Error:
                pass
        for window in matching_windows(self.plan["marker"]):
            try:
                win32gui.PostMessage(window["hwnd"], win32con.WM_CLOSE, 0, 0)
            except Exception as error:
                self.evidence.setdefault("close_errors", []).append(str(error))
        if self.child and self.child.poll() is None:
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.terminate()
        for process in owned:
            try:
                if process.is_running():
                    process.terminate()
            except psutil.Error:
                pass
        self.evidence["closed_at"] = time.time()
