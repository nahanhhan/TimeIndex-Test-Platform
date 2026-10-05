"""Disposable visible window controlled by a JSON command file in the test VM."""
from __future__ import annotations

import argparse
import json
import os
import tkinter as tk
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    root.geometry("650x240+150+150")
    root.title(args.title)
    tk.Label(root, text="TimeIndex 自动采集实验窗口", font=("Segoe UI", 20)).pack(pady=40)
    tk.Label(root, text=args.title).pack()
    root.update()
    args.ready.write_text(json.dumps({"pid": os.getpid(), "hwnd": root.winfo_id()}), encoding="utf-8")
    last = None

    def poll() -> None:
        nonlocal last
        if args.control.exists():
            content = args.control.read_text(encoding="utf-8")
            if content != last:
                last = content
                command = json.loads(content)
                if command.get("action") == "close":
                    root.destroy()
                    return
                if command.get("action") == "title":
                    root.title(str(command["value"]))
                if command.get("action") == "focus":
                    root.lift()
                    root.focus_force()
        root.after(200, poll)

    root.after(200, poll)
    root.mainloop()


if __name__ == "__main__":
    main()

