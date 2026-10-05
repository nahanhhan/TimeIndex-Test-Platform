from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from platform_core.common import ROOT, read_json
from tests.test_project import make_project


@unittest.skipUnless(os.name == "nt", "Windows startup and packaging scripts")
class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT, prefix=".launcher-test-")
        self.folder = Path(self.temporary.name).resolve()
        self.assertTrue(self.folder.is_relative_to(ROOT))
        self.platform = self.folder / "平台代码 (独立)"
        self.platform.mkdir()
        self.project = make_project(self.folder / "桌面目录" / "TimeIndex")
        for name in ("uv.lock", ".python-version", "README.md", "README_zh.md", "LICENSE"):
            (self.project / name).write_text("Controlled fixture\n", encoding="utf-8")
        for name in ("launch.ps1", "start.cmd", "package.ps1", "pyproject.toml", "uv.lock",
                     "app.py", "README.md", "paper_experiment.md", "plan.md", ".gitignore", "AGENTS.md"):
            shutil.copy2(ROOT / name, self.platform / name)
        module = self.platform / "platform_core"
        module.mkdir()
        for name in ("__init__.py", "common.py", "project.py"):
            shutil.copy2(ROOT / "platform_core" / name, module / name)
        for name in ("datasets", "tests", "openspec"):
            (self.platform / name).mkdir()
        tools = self.folder / "tools"
        tools.mkdir()
        fake = tools / "fake_uv.py"
        fake.write_text(
            "import subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "if args[0] == 'sync':\n"
            "    print('Controlled locked-install fixture')\n"
            "    sys.exit(0)\n"
            "if args[0] == 'run':\n"
            "    args = args[args.index('python') + 1:]\n"
            "    sys.exit(subprocess.call([sys.executable, '-B', *args]))\n"
            "sys.exit(2)\n", encoding="utf-8")
        # Keep the launcher integration independent of network/install availability.
        (tools / "uv.cmd").write_text(f'@"{sys.executable}" "{fake}" %*\n', encoding="utf-8")
        self.env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
                    "TIMEINDEX_PROJECT_DIR": "", "PYTHONPATH": str(self.platform)}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(self.platform / name), *args], cwd=self.platform,
                              env=self.env, capture_output=True, encoding="utf-8", errors="replace", timeout=45)

    def test_start_cmd_accepts_a_separate_unicode_project_and_remembers_it(self) -> None:
        result = subprocess.run(["cmd.exe", "/d", "/c", str(self.platform / "start.cmd"),
                                 "-CheckOnly", "-TimeIndexPath", str(self.project)], cwd=self.platform,
                                env=self.env, capture_output=True, encoding="utf-8", errors="replace", timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_json(self.platform / ".timeindex-project.json")["path"], str(self.project))
        self.assertIn("Environment check passed", result.stdout)
        self.assertFalse((self.project / "src/TimeIndex/.lancedb").exists())

    def test_explicit_missing_source_stops_check_only_startup(self) -> None:
        result = self.run_script("launch.ps1", "-CheckOnly", "-TimeIndexPath", str(self.folder / "missing"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TimeIndex project lookup failed", result.stdout)
        self.assertFalse((self.platform / ".timeindex-project.json").exists())

    def test_packaging_separate_projects_uses_stable_archive_paths(self) -> None:
        result = self.run_script("package.ps1", "-TimeIndexPath", str(self.project))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        files = list((self.platform / "dist").glob("*.zip"))
        self.assertEqual(len(files), 1)
        with zipfile.ZipFile(files[0]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("TimeIndex/src/TimeIndex/config.yaml", archive.namelist())
            self.assertIn("TimeIndex/test_platform/platform_core/project.py", archive.namelist())
            self.assertIn("TimeIndex/test_platform/start.cmd", archive.namelist())
            self.assertFalse(any(".timeindex-project.json" in name for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
