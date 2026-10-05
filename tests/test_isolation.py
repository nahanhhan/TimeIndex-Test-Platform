from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from platform_core.common import ROOT, digest_source
from platform_core.project import resolve_project_dir
from platform_core.dataset import DEFAULT_DATASET
from platform_core.isolation import cleanup, prepare, preserve_database
from platform_core.manifest import create_run


def database_fingerprint() -> list[tuple[str, int, int]]:
    database = resolve_project_dir() / "src" / "TimeIndex" / ".lancedb"
    if not database.exists():
        return []
    return [(str(path.relative_to(database)), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(database.rglob("*")) if path.is_file()]


class IsolationTests(unittest.TestCase):
    def test_modes_and_isolated_import(self) -> None:
        source_before, database_before = digest_source(), database_fingerprint()
        full = create_run("full")
        self.assertEqual((full["selected_cases"], full["selected_queries"]), (60, 30))
        custom = create_run("custom", DEFAULT_DATASET)
        self.assertEqual(custom["selected_cases"], 60)
        manifest = create_run("quick")
        self.assertFalse(manifest["resources"])
        self.assertEqual((manifest["selected_cases"], manifest["selected_queries"]), (8, 4))
        paths = prepare(manifest["run_id"], manifest)
        self.assertFalse(paths["database"].exists())
        env = os.environ.copy()
        env.update({"USERPROFILE": str(paths["profile"]), "HOME": str(paths["profile"]),
                    "PYTHONPATH": os.pathsep.join([str(paths["runtime"] / "src"), str(ROOT)])})
        result = subprocess.run([sys.executable, "-c",
                                 "import json; from platform_core.isolation import verify_import; "
                                 f"print(json.dumps(verify_import('{manifest['run_id']}')))"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        imported = json.loads(result.stdout)
        self.assertTrue(Path(imported["database"]).is_relative_to(paths["run"]))
        paths["database"].mkdir(exist_ok=True)
        (paths["database"] / "sample.txt").write_text("evidence", encoding="utf-8")
        copied = preserve_database(manifest["run_id"])
        self.assertEqual(preserve_database(manifest["run_id"]), copied)
        self.assertEqual((copied / "sample.txt").read_text(encoding="utf-8"), "evidence")
        self.assertTrue((paths["run"] / "evidence" / "timeindex_db.complete.json").exists())
        (paths["run"] / "evidence" / "sentinel.txt").parent.mkdir(exist_ok=True)
        (paths["run"] / "evidence" / "sentinel.txt").write_text("keep", encoding="utf-8")
        cleanup(manifest["run_id"])
        self.assertFalse(paths["runtime"].exists())
        self.assertTrue((paths["run"] / "evidence" / "sentinel.txt").exists())
        self.assertEqual(digest_source(), source_before)
        self.assertEqual(database_fingerprint(), database_before)


if __name__ == "__main__":
    unittest.main()

