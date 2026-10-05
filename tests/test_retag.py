from __future__ import annotations

import os
import subprocess
import sys
import unittest

from platform_core.common import ROOT
from platform_core.isolation import cleanup, prepare
from platform_core.manifest import create_run
from tests.test_isolation import database_fingerprint


class RetagRegressionTests(unittest.TestCase):
    def test_storage_and_parser_in_isolated_source(self) -> None:
        before = database_fingerprint()
        manifest = create_run("quick")
        paths = prepare(manifest["run_id"], manifest)
        env = os.environ.copy()
        env.update({"USERPROFILE": str(paths["profile"]),
                    "PYTHONPATH": os.pathsep.join([str(paths["runtime"] / "src"), str(ROOT)])})
        try:
            result = subprocess.run([sys.executable, "-m", "tests.retag_regression", manifest["run_id"]],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            cleanup(manifest["run_id"])
        self.assertEqual(database_fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
