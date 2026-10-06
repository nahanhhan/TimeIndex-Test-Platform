from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from platform_core import manifest
from platform_core.common import ROOT, atomic_json, read_json


class ManifestUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(dir=ROOT, prefix=".manifest-test-")
        self.directory = Path(folder.name).resolve()
        self.assertTrue(self.directory.is_relative_to(ROOT.resolve()))
        self.addCleanup(folder.cleanup)
        self.path = self.directory / "manifest.json"
        atomic_json(self.path, {"status": "running", "current_phase": "generating_report", "sections": {}})
        path_patch = patch.object(manifest, "run_path", return_value=self.directory)
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def test_late_metadata_cannot_reopen_or_reclassify_a_finished_run(self) -> None:
        manifest.section("fixture", "fallback", "failed", "not recorded")
        manifest.update_run("fixture", status="partial", finished_at="finished")
        result = manifest.update_run("fixture", worker_pid=2448, status="running", current_phase="late progress")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["current_phase"])
        self.assertEqual(result["worker_pid"], 2448)
        self.assertEqual(result["sections"]["fallback"]["status"], "failed")
        self.assertEqual(manifest.update_run("fixture", status="interrupted")["status"], "partial")

    def test_stale_interruption_check_does_not_add_a_false_error(self) -> None:
        manifest.update_run("fixture", status="done")
        before = self.path.read_bytes()
        result = manifest.update_run("fixture", expected_status="running", status="interrupted", error="stale check")
        self.assertEqual(result["status"], "done")
        self.assertNotIn("error", result)
        self.assertEqual(self.path.read_bytes(), before)

    def test_overlapping_reads_preserve_both_metadata_and_terminal_status(self) -> None:
        first_read, release_first, second_started, second_read = (threading.Event() for _ in range(4))
        calls = 0
        counter_lock = threading.Lock()

        def paused_read(path, default=None):
            nonlocal calls
            value = read_json(path, default)
            with counter_lock:
                calls += 1
                first = calls == 1
            if first:
                first_read.set()
                if not release_first.wait(5):
                    raise TimeoutError("controlled first read was not released")
            else:
                second_read.set()
            return value

        def finish():
            second_started.set()
            return manifest.update_run("fixture", status="partial", finished_at="finished")

        with patch.object(manifest, "read_json", side_effect=paused_read), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(manifest.update_run, "fixture", worker_pid=2448)
            second = None
            try:
                self.assertTrue(first_read.wait(5))
                second = pool.submit(finish)
                self.assertTrue(second_started.wait(5))
                self.assertFalse(second_read.wait(0.15), "a second writer read before the first writer finished")
            finally:
                release_first.set()
            first.result(timeout=5)
            if second:
                second.result(timeout=5)
        saved = read_json(self.path)
        self.assertEqual(saved["worker_pid"], 2448)
        self.assertEqual(saved["status"], "partial")
        self.assertIsNone(saved["current_phase"])

    def test_simultaneous_section_updates_preserve_every_step(self) -> None:
        def write(index):
            manifest.section("fixture", f"step-{index}", "failed", f"reason-{index}")

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(24)))
        saved = read_json(self.path)["sections"]
        self.assertEqual(len(saved), 24)
        self.assertTrue(all(saved[f"step-{i}"]["reason"] == f"reason-{i}" for i in range(24)))

    def test_worker_in_another_process_cannot_be_overwritten(self) -> None:
        # This child only writes a platform manifest; it never imports TimeIndex.
        script = """
import sys
from pathlib import Path
from platform_core.common import atomic_json, file_lock, read_json
directory = Path(sys.argv[1])
path = directory / 'manifest.json'
with file_lock(path.with_name(path.name + '.guard')):
    value = read_json(path)
    atomic_json(directory / 'ready.json', {'locked': True})
    sys.stdin.readline()
    value.update(status='partial', current_phase=None, sections={'fallback': {'status': 'failed'}})
    atomic_json(path, value)
"""
        with subprocess.Popen([sys.executable, "-B", "-c", script, str(self.directory)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child, \
                ThreadPoolExecutor(max_workers=1) as pool:
            future = None
            started = threading.Event()

            def late_parent():
                started.set()
                return manifest.update_run("fixture", worker_pid=2448, status="running", current_phase="late progress")

            try:
                deadline = time.monotonic() + 10
                while not (self.directory / "ready.json").exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((self.directory / "ready.json").exists())
                future = pool.submit(late_parent)
                self.assertTrue(started.wait(5))
                time.sleep(0.15)
                self.assertFalse(future.done(), "the parent did not respect the child process lock")
            finally:
                _, error = child.communicate("finish\n", timeout=15)
            self.assertEqual(child.returncode, 0, error)
            if future:
                future.result(timeout=5)
        saved = read_json(self.path)
        self.assertEqual(saved["status"], "partial")
        self.assertIsNone(saved["current_phase"])
        self.assertEqual(saved["worker_pid"], 2448)
        self.assertEqual(saved["sections"]["fallback"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
