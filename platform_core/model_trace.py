from __future__ import annotations

import contextvars
import json
import threading
import time
from contextlib import contextmanager
from typing import Any

from .common import atomic_json, now


class ModelWaitStopped(BaseException):
    """Escape core fallback handlers when the platform stops an entire experiment."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class ModelTrace:
    """Observe SDK calls without modifying the request, response or TimeIndex prompts."""

    def __init__(self, path: Any, *, timeout_s: float | None = None,
                 cancelled: Any = None, on_call: Any = None):
        self.path = path
        self.calls: list[dict[str, Any]] = []
        self._context: contextvars.ContextVar = contextvars.ContextVar("timeindex_trace_context", default={})
        self._lock = threading.RLock()
        self._secrets: set[str] = set()
        self.capture_errors: list[str] = []
        self.timeout_s, self.cancelled, self.on_call = timeout_s, cancelled, on_call
        self.stop_error: ModelWaitStopped | None = None
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

    def _notify(self, row: dict[str, Any] | None) -> None:
        if self.on_call:
            try:
                self.on_call(row)
            except Exception as error:
                self.capture_errors.append(str(error))

    def _request(self, create: Any, args: Any, kwargs: Any, limit: float | None) -> Any:
        if self.stop_error:
            raise self.stop_error
        if self.cancelled and self.cancelled():
            raise ModelWaitStopped("cancelled", "已取消本轮；未继续发送模型请求")
        if limit is None:
            return create(*args, **kwargs)
        # Only the transport runs in this daemon thread. Late replies cannot write
        # records, replace evidence or resume TimeIndex processing after we stop.
        finished = threading.Event()
        result: list[Any] = []

        def request() -> None:
            try:
                result.append((True, create(*args, **kwargs)))
            except BaseException as error:
                result.append((False, error))
            finally:
                finished.set()

        deadline = time.monotonic() + limit
        threading.Thread(target=request, daemon=True, name="timeindex-model-request").start()
        while not finished.wait(0.1):
            if self.cancelled and self.cancelled():
                raise ModelWaitStopped("cancelled", "已取消模型等待；保留已完成的记录和请求证据")
            if time.monotonic() >= deadline:
                raise ModelWaitStopped("timeout", f"模型请求等待超过 {limit:g} 秒；停止本轮并保留部分结果")
        success, value = result[0]
        if not success:
            if isinstance(value, TimeoutError) or type(value).__name__ == "APITimeoutError":
                raise ModelWaitStopped("timeout", f"模型请求在 {limit:g} 秒等待上限内发生超时：{value}")
            raise value
        return value

    def invoke(self, operation: str, create: Any, *args: Any,
               _wait_limit: float | None = None, **kwargs: Any) -> Any:
        _wait_limit = self.timeout_s if _wait_limit is None else _wait_limit
        allowed = {"model", "messages", "input", "temperature", "max_tokens", "max_completion_tokens",
                   "response_format", "seed", "top_p", "frequency_penalty", "presence_penalty"}
        request = json.loads(json.dumps({key: value for key, value in kwargs.items() if key in allowed},
                                       ensure_ascii=False, default=str))
        with self._lock:
            row = {"id": f"M{len(self.calls) + 1:04d}", "operation": operation,
                   "context": dict(self._context.get()), "started_at": now(), "status": "running",
                   "request": request, "response": None, "error": None}
            row["wait_limit_s"] = _wait_limit
            self.calls.append(row)
            self._save()
        self._notify(row)
        started = time.perf_counter()
        try:
            response = self._request(create, args, kwargs, _wait_limit)
            try:
                dumped = response.model_dump(mode="json") if hasattr(response, "model_dump") else {"capture_status": "response_object_not_serializable"}
            except Exception as error:
                self.capture_errors.append(str(error))
                dumped = {"capture_status": "response_capture_failed"}
            with self._lock:
                row.update(status="done", response=dumped)
            return response
        except BaseException as error:
            with self._lock:
                row.update(status="error", error=str(error))
                if isinstance(error, ModelWaitStopped):
                    self.stop_error = error
                    row["error_kind"] = error.kind
            raise
        finally:
            with self._lock:
                row.update(finished_at=now(), elapsed_ms=(time.perf_counter() - started) * 1000)
                self._save()
            self._notify(None)

    def wrap(self, client: Any, *, timeout_s: float | None = None) -> Any:
        key = getattr(client, "api_key", None)
        if isinstance(key, str) and key:
            self._secrets.add(key)
        limit = self.timeout_s if timeout_s is None else timeout_s
        if limit is not None and callable(getattr(client, "with_options", None)):
            client = client.with_options(timeout=limit, max_retries=0)
        return _Client(client, self, limit)


class _Node:
    def __init__(self, original: Any, trace: ModelTrace, operation: str, path: tuple[str, ...], limit: float | None):
        self.original, self.trace, self.operation, self.path = original, trace, operation, path
        self.limit = limit

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.original, name)
        if name == "create" and not self.path:
            return lambda *args, **kwargs: self.trace.invoke(self.operation, value, *args, _wait_limit=self.limit, **kwargs)
        if self.path and name == self.path[0]:
            return _Node(value, self.trace, self.operation, self.path[1:], self.limit)
        return value


class _Client:
    def __init__(self, original: Any, trace: ModelTrace, limit: float | None):
        self.original, self.trace = original, trace
        self.limit = limit

    def __getattr__(self, name: str) -> Any:
        if name == "chat":
            return _Node(self.original.chat, self.trace, "chat", ("completions",), self.limit)
        if name == "embeddings":
            return _Node(self.original.embeddings, self.trace, "embedding", (), self.limit)
        return getattr(self.original, name)
