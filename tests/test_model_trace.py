from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from platform_core.common import ROOT
from platform_core.model_trace import ModelTrace


class TraceTests(unittest.TestCase):
    def test_exact_request_response_and_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".trace-test-") as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(ROOT))
            seen = {}
            response = SimpleNamespace(model_dump=lambda **kwargs: {"model": "actual-model", "choices": [
                {"finish_reason": "stop", "message": {"content": '```json\n{"summary":"模型原文"}\n```'}}]})
            def create(**kwargs):
                seen.update(kwargs)
                return response
            secret = 'secret-"never-log'
            client = SimpleNamespace(api_key=secret, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            trace = ModelTrace(Path(folder) / "calls.json")
            messages = [{"role": "system", "content": "original system prompt"}, {"role": "user", "content": "actual input"}]
            with trace.scope(case_id="a", phase="记录"):
                actual = trace.wrap(client).chat.completions.create(model="requested-model", messages=messages,
                                                                   extra_headers={"Authorization": secret})
            self.assertIs(actual, response)
            self.assertIs(seen["messages"], messages)
            self.assertEqual(seen["extra_headers"]["Authorization"], secret)
            text = (Path(folder) / "calls.json").read_text(encoding="utf-8")
            stored = json.loads(text)["calls"][0]
            self.assertEqual(stored["request"]["messages"], messages)
            self.assertEqual(stored["context"]["case_id"], "a")
            self.assertEqual(stored["response"]["choices"][0]["message"]["content"], '```json\n{"summary":"模型原文"}\n```')
            self.assertNotIn("Authorization", text)
            def fail(**kwargs):
                raise RuntimeError("bad credentials: " + secret)
            client.chat.completions.create = fail
            with self.assertRaises(RuntimeError):
                trace.wrap(client).chat.completions.create(model="requested-model", messages=messages)
            text = (Path(folder) / "calls.json").read_text(encoding="utf-8")
            self.assertNotIn(secret, text)
            self.assertEqual(json.loads(text)["calls"][-1]["status"], "error")

    def test_capture_write_failure_does_not_change_model_result(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".trace-test-") as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(ROOT))
            response = SimpleNamespace(model_dump=lambda **kwargs: {"value": "original"})
            with patch("platform_core.model_trace.atomic_json", side_effect=OSError("disk unavailable")):
                trace = ModelTrace(Path(folder) / "calls.json")
                result = trace.invoke("chat", lambda **kwargs: response, model="m", messages=[])
            self.assertIs(result, response)
            self.assertTrue(trace.capture_errors)
            self.assertEqual(trace.calls[-1]["status"], "done")


if __name__ == "__main__":
    unittest.main()
