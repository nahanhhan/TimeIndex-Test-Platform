from __future__ import annotations

import importlib.util
import json
import platform
import sys
import urllib.request
from typing import Any
from urllib.parse import urlparse

import psutil

from .privacy import is_loopback, is_private_lan, normalize_endpoint
from .project import project_status


def discover_model_pid(endpoint: str) -> int | None:
    if not is_loopback(endpoint):
        return None
    port = urlparse(endpoint).port or (443 if endpoint.startswith("https:") else 80)
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port and connection.pid:
                return connection.pid
    except (psutil.AccessDenied, OSError):
        return None
    return None


def check(endpoint: str, *, dedicated_vm: bool = False, allow_remote_model: bool = False,
          api_key: str | None = None, timeindex_project: str | None = None) -> dict[str, Any]:
    endpoint = normalize_endpoint(endpoint)
    modules = {name: importlib.util.find_spec(name) is not None for name in
               ("lancedb", "openai", "psutil", "yaml", "wmi", "win32gui", "streamlit")}
    local = is_loopback(endpoint)
    private_lan = is_private_lan(endpoint)
    allowed = local or (allow_remote_model and private_lan and not dedicated_vm)
    reachable = False
    detail = None
    model_ids: list[str] = []
    if allowed:
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            request = urllib.request.Request(endpoint + "/models", headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=3) as reply:
                response = json.load(reply)
                if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                    raise ValueError("模型列表响应格式无效")
                model_ids = [item["id"] for item in response["data"]
                             if isinstance(item, dict) and isinstance(item.get("id"), str)][:50]
                reachable = 200 <= reply.status < 300
        except Exception as error:
            detail = str(error)
    else:
        detail = ("专用测试桌面的真实采集只允许本机模型地址" if dedicated_vm else
                  "非本机地址须勾选局域网模型选项，并填写 10.x、172.16–31.x、192.168.x 或 fc00:: 地址")
    windows = platform.system() == "Windows"
    return {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 12),
            "timeindex_project": project_status(timeindex_project),
            "windows": windows, "dependencies": modules,
            "model_endpoint": endpoint, "loopback": local, "private_lan": private_lan,
            "endpoint_allowed": allowed, "model_reachable": reachable,
            "model_ids": model_ids, "model_detail": detail,
            "wmi_ready": windows and modules["wmi"] and modules["win32gui"],
            "desktop_ready": dedicated_vm and windows and modules["wmi"] and modules["win32gui"],
            "model_pid_candidate": discover_model_pid(endpoint) if reachable else None}

