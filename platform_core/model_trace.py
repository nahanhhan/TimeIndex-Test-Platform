from __future__ import annotations

import contextvars
import json
import threading
import time
from contextlib import contextmanager
from typing import Any

from .common import atomic_json, now


class ModelTrace:
    """Observe SDK calls without modifying the request, response or TimeIndex prompts."""

    def __init__(self, path: Any):
        self.path = path
        self.calls: list[dict[str, Any]] = []
        self._context: contextvars.ContextVar = contextvars.ContextVar("timeindex_trace_context", default={})
        self._lock = threading.RLock()
        self._secrets: set[str] = set()
        self.capture_errors: list[str] = []
        self._save()

    @contextmanager
    def scope(self, **context: Any):
        token = self._context.set({**self._context.get(), **context})
        try:
            yield
        finally:
            self._context.reset(token)

    def _save(self) -> None:
        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, str):
                for secret in self._secrets:
                    value = value.replace(secret, "[访问令牌已隐藏]")
            return value
        try:
            atomic_json(self.path, scrub({"schema_version": 1, "calls": self.calls, "capture_errors": self.capture_errors}))
        except OSError as error:
            self.capture_errors.append(str(error))

    def invoke(self, operation: str, create: Any, *args: Any, **kwargs: Any) -> Any:
        allowed = {"model", "messages", "input", "temperature", "max_tokens", "max_completion_tokens",
                   "response_format", "seed", "top_p", "frequency_penalty", "presence_penalty"}
        request = json.loads(json.dumps({key: value for key, value in kwargs.items() if key in allowed},
                                       ensure_ascii=False, default=str))
        with self._lock:
            row = {"id": f"M{len(self.calls) + 1:04d}", "operation": operation,
                   "context": dict(self._context.get()), "started_at": now(), "status": "running",
                   "request": request, "response": None, "error": None}
            self.calls.append(row)
            self._save()
        started = time.perf_counter()
        try:
            response = create(*args, **kwargs)
            try:
                dumped = response.model_dump(mode="json") if hasattr(response, "model_dump") else {"capture_status": "response_object_not_serializable"}
            except Exception as error:
                self.capture_errors.append(str(error))
                dumped = {"capture_status": "response_capture_failed"}
            with self._lock:
                row.update(status="done", response=dumped)
            return response
        except Exception as error:
            with self._lock:
                row.update(status="error", error=str(error))
            raise
        finally:
            with self._lock:
                row.update(finished_at=now(), elapsed_ms=(time.perf_counter() - started) * 1000)
                self._save()

    def wrap(self, client: Any) -> Any:
        key = getattr(client, "api_key", None)
        if isinstance(key, str) and key:
            self._secrets.add(key)
        return _Client(client, self)


class _Node:
    def __init__(self, original: Any, trace: ModelTrace, operation: str, path: tuple[str, ...]):
        self.original, self.trace, self.operation, self.path = original, trace, operation, path

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.original, name)
        if name == "create" and not self.path:
            return lambda *args, **kwargs: self.trace.invoke(self.operation, value, *args, **kwargs)
        if self.path and name == self.path[0]:
            return _Node(value, self.trace, self.operation, self.path[1:])
        return value


class _Client:
    def __init__(self, original: Any, trace: ModelTrace):
        self.original, self.trace = original, trace

    def __getattr__(self, name: str) -> Any:
        if name == "chat":
            return _Node(self.original.chat, self.trace, "chat", ("completions",))
        if name == "embeddings":
            return _Node(self.original.embeddings, self.trace, "embedding", ())
        return getattr(self.original, name)
