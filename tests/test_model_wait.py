from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from platform_core.common import ROOT
from platform_core.model_trace import ModelTrace, ModelWaitStopped


class ModelWaitTests(unittest.TestCase):
    def test_absolute_timeout_preserves_request_and_ignores_late_response(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".wait-test-") as folder:
            path = Path(folder) / "calls.json"
            release, finished = threading.Event(), threading.Event()
            response = SimpleNamespace(model_dump=lambda **_kwargs: {"unchanged": True})
            def delayed(**_kwargs):
                release.wait(3)
                finished.set()
                return response
            trace = ModelTrace(path, timeout_s=0.15)
            started = time.monotonic()
            with self.assertRaises(ModelWaitStopped) as stopped:
                trace.invoke("chat", delayed, model="m", messages=[{"role": "user", "content": "original"}])
            self.assertEqual(stopped.exception.kind, "timeout")
            self.assertLess(time.monotonic() - started, 1)
            before = path.read_bytes()
            row = json.loads(before)["calls"][0]
            self.assertEqual(row["error_kind"], "timeout")
            self.assertIsNone(row["response"])
            self.assertEqual(row["request"]["messages"][0]["content"], "original")
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertEqual(path.read_bytes(), before)

    def test_cancellation_interrupts_wait_and_clears_current_step(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".wait-test-") as folder:
            cancel, release = threading.Event(), threading.Event()
            notifications = []
            def delayed(**_kwargs):
                cancel.set()
                release.wait(2)
                return SimpleNamespace(model_dump=lambda **_kwargs: {})
            trace = ModelTrace(Path(folder) / "calls.json", timeout_s=5, cancelled=cancel.is_set,
                               on_call=lambda row: notifications.append(None if row is None else row["id"]))
            try:
                with self.assertRaises(ModelWaitStopped) as stopped:
                    trace.invoke("embedding", delayed, model="m", input="original")
                self.assertEqual(stopped.exception.kind, "cancelled")
                self.assertEqual(notifications, ["M0001", None])
                self.assertEqual(trace.calls[0]["status"], "error")
            finally:
                release.set()

    def test_wait_control_preserves_successful_response_and_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".wait-test-") as folder:
            response = SimpleNamespace(model_dump=lambda **_kwargs: {"original": True})
            options, requests = [], []
            class Client:
                api_key = "secret"
                def with_options(self, **kwargs):
                    options.append(kwargs)
                    return self
                chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: requests.append(kwargs) or response))
            trace = ModelTrace(Path(folder) / "calls.json", timeout_s=3)
            messages = [{"role": "user", "content": "unchanged"}]
            actual = trace.wrap(Client()).chat.completions.create(model="m", messages=messages)
            self.assertIs(actual, response)
            self.assertIs(requests[0]["messages"], messages)
            self.assertEqual(options, [{"timeout": 3, "max_retries": 0}])
            self.assertEqual(trace.calls[0]["response"], {"original": True})


if __name__ == "__main__":
    unittest.main()
