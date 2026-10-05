from __future__ import annotations

import unittest

from platform_core.dataset import build_default
from platform_core.preflight import check
from platform_core.privacy import (assert_redacted, audit, blacklist_exposure, is_loopback,
                                   is_private_lan, normalize_endpoint, redact)


class PrivacyTests(unittest.TestCase):
    def test_loopback_and_redaction(self) -> None:
        self.assertTrue(is_loopback("http://127.0.0.1:1234/v1"))
        self.assertTrue(is_loopback("http://[::1]:1234/v1"))
        self.assertFalse(is_loopback("http://example.com/v1"))
        self.assertEqual(normalize_endpoint("http://192.168.1.20:1234"),
                         "http://192.168.1.20:1234/v1")
        self.assertTrue(is_private_lan("http://192.168.1.20:1234/v1"))
        self.assertFalse(is_private_lan("http://8.8.8.8:1234/v1"))
        denied = check("http://192.168.1.20:1234", allow_remote_model=False)
        self.assertFalse(denied["endpoint_allowed"])
        self.assertFalse(denied["model_reachable"])
        self.assertFalse(check("http://192.168.1.20:1234", dedicated_vm=True,
                               allow_remote_model=True)["endpoint_allowed"])
        dataset = build_default()
        original = dataset["cases"][0]["title"]
        safe = redact(original, dataset)
        assert_redacted(safe, dataset)
        self.assertNotIn("TEST-SECRET", safe)

    def test_exposure_and_blacklist_window(self) -> None:
        data = build_default()
        marker = data["cases"][0]["canary"]
        result = audit(data, {"capture": {"title": marker}, "summary": {"text": "无"}})
        self.assertEqual(result["capture"]["exposed_markers"], 1)
        self.assertEqual(result["summary"]["exposed_markers"], 0)
        exposure = blacklist_exposure([{"windows": [{"process_name": "blocked.exe", "title": "test"}]}],
                                      ["blocked.exe"])
        self.assertTrue(exposure["window_title_exposure"])


if __name__ == "__main__":
    unittest.main()

