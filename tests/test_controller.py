from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from platform_core import controller
from platform_core.common import ROOT, atomic_json, read_json


class ControllerLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(dir=ROOT, prefix=".controller-test-")
        self.addCleanup(self.folder.cleanup)
        self.runs = Path(self.folder.name)
        self.active = self.runs / ".active"
        self.run_patch = patch.object(controller, "RUNS", self.runs)
        self.active_patch = patch.object(controller, "ACTIVE", self.active)
        self.run_patch.start()
        self.active_patch.start()
        self.addCleanup(self.active_patch.stop)
        self.addCleanup(self.run_patch.stop)

    def test_preparing_run_blocks_a_second_start_and_foreign_release(self) -> None:
        controller._lock("first")
        owner = read_json(self.active)
        self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(owner["process_created_at"], psutil.Process().create_time())
        with self.assertRaisesRegex(RuntimeError, "正在准备或运行"):
            controller._lock("second")
        controller.release_lock("second")
        self.assertEqual(read_json(self.active), owner)
        controller.release_lock("first")
        self.assertFalse(self.active.exists())

    def test_pid_reuse_and_abandoned_legacy_lock_can_be_recovered(self) -> None:
        for owner in ({"run_id": "old", "pid": os.getpid(), "process_created_at": 0},
                      {"run_id": "old", "pid": None}):
            with self.subTest(owner=owner):
                atomic_json(self.active, owner)
                controller._lock("new")
                self.assertEqual(read_json(self.active)["run_id"], "new")
                controller.release_lock("new")

    def test_simultaneous_threads_only_allow_one_run(self) -> None:
        barrier = threading.Barrier(2)

        def attempt(run_id):
            barrier.wait(timeout=5)
            try:
                controller._lock(run_id)
                return True
            except RuntimeError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ("one", "two")))
        self.assertEqual(sum(outcomes), 1)

    def test_worker_finishing_during_start_is_not_reset_to_running(self) -> None:
        run = self.runs / "fixture"
        run.mkdir()
        manifest = {"run_id": "fixture", "status": "created", "resources": False, "dedicated_vm": False,
                    "allow_no_model": True, "allow_remote_model": False,
                    "endpoint": "http://127.0.0.1:1234/v1", "timeindex_project": "fixture"}
        atomic_json(run / "manifest.json", manifest)
        paths = {"run": run, "profile": run / "profile", "runtime": run / "runtime"}

        def finish_immediately(*args, **kwargs):
            controller.update_run("fixture", status="partial")
            controller.release_lock("fixture")
            return SimpleNamespace(pid=os.getpid())

        with patch.object(controller, "create_run", return_value=manifest), \
                patch.object(controller, "prepare", return_value=paths), \
                patch.object(controller, "check", return_value={"desktop_ready": False}), \
                patch.object(controller, "run_path", return_value=run), \
                patch("platform_core.manifest.run_path", return_value=run), \
                patch.object(controller.subprocess, "Popen", side_effect=finish_immediately):
            result = controller.start("quick")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["current_phase"])
        self.assertFalse(self.active.exists())

    def test_preparing_run_is_visible_to_another_process(self) -> None:
        # The child only acquires a platform lock; it never starts TimeIndex.
        script = (
            "import sys; from pathlib import Path; from platform_core import controller; "
            "controller.RUNS=Path(sys.argv[1]); controller.ACTIVE=controller.RUNS/'.active'; "
            "controller._lock('child'); print('ready',flush=True); sys.stdin.readline(); controller.release_lock('child')"
        )
        with subprocess.Popen([sys.executable, "-B", "-c", script, str(self.runs)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
            try:
                deadline = time.monotonic() + 10
                while not self.active.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(self.active.exists(), "准备锁未能在限定时间内生成")
                with self.assertRaises(RuntimeError):
                    controller._lock("parent")
            finally:
                _, error = child.communicate("exit\n", timeout=15)
            self.assertEqual(child.returncode, 0, error)
        self.assertFalse(self.active.exists())


if __name__ == "__main__":
    unittest.main()
