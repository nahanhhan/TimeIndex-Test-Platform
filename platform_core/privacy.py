from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlparse, urlunparse


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


def normalize_endpoint(url: str) -> str:
    parsed = urlparse(url.strip())
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("模型接口须是 http(s) 地址，且不能包含账号、查询参数或片段")
    path = parsed.path.rstrip("/") or "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def is_private_lan(url: str) -> bool:
    try:
        address = ipaddress.ip_address(urlparse(url).hostname or "")
    except ValueError:
        return False
    return any(address in network for network in PRIVATE_NETWORKS)


def is_loopback(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def canaries(dataset: dict[str, Any]) -> list[str]:
    return sorted({case["canary"] for case in dataset["cases"] if case.get("canary")} | set(dataset.get("privacy_markers", [])),
                  key=lambda marker: (-len(marker), marker))


def titles(dataset: dict[str, Any]) -> list[str]:
    return sorted({case["title"] for case in dataset["cases"] if case.get("title")},
                  key=len, reverse=True)


def redact(value: str, dataset: dict[str, Any], *, keep_test_titles: bool = False,
           marker_replacements: dict[str, str] | None = None) -> str:
    if not keep_test_titles:
        for number, title in enumerate(titles(dataset), 1):
            value = value.replace(title, f"[窗口标题-{number:03d}]")
    for number, secret in enumerate(canaries(dataset), 1):
        replacement = (marker_replacements or {}).get(secret, f"[测试敏感串-{number:03d}]")
        value = value.replace(secret, replacement)
    return value


def exposed_fields(value: Any, marker: str, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            found.extend(exposed_fields(item, marker, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(exposed_fields(item, marker, f"{prefix}[{index}]"))
        return found
    if marker in str(value):
        return [prefix or "$root"]
    return []


def audit(dataset: dict[str, Any], stages: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for stage, value in stages.items():
        occurrences = []
        for marker in canaries(dataset):
            fields = exposed_fields(value, marker)
            if fields:
                occurrences.append({"marker_id": marker.rsplit("-", 2)[-2:], "fields": fields})
        result[stage] = {"exposed_markers": len(occurrences),
                         "total_markers": len(canaries(dataset)), "occurrences": occurrences}
    return result


def blacklist_exposure(snapshots: list[dict[str, Any]], blacklisted: list[str]) -> dict[str, Any]:
    blocked = {name.casefold() for name in blacklisted}
    events = []
    windows = []
    for snapshot in snapshots:
        events.extend(event for event in snapshot.get("process_events", [])
                      if event.get("process_name", event.get("process", "")).casefold() in blocked)
        windows.extend(window for window in snapshot.get("windows", [])
                       if window.get("process_name", window.get("process", "")).casefold() in blocked)
    return {"blocked_process_events_observed": len(events),
            "blocked_window_titles_observed": len(windows),
            "window_title_exposure": bool(windows)}


def image_fields(value: Any) -> list[str]:
    keys = {"screenshot", "screen_image", "image", "image_data", "png", "jpeg", "webp"}
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in keys:
                found.append(str(key))
            found.extend(image_fields(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(image_fields(item))
    return found


def assert_redacted(text: str, dataset: dict[str, Any], *, keep_test_titles: bool = False) -> None:
    leaks = [marker for marker in canaries(dataset) + ([] if keep_test_titles else titles(dataset)) if marker in text]
    if leaks:
        raise ValueError(f"导出内容仍含 {len(leaks)} 个原始标题或审计串")


def as_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

