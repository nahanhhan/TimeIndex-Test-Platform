from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platform_core.common import ROOT, atomic_json, digest_source, read_json
from platform_core.isolation import cleanup, prepare
from platform_core.manifest import create_run
from platform_core.project import (PROJECT_ENV, REQUIRED_FILES, SETTINGS_FILE,
                                   discover_projects, resolve_project_dir,
                                   save_project_dir, validate_project)


def make_project(directory: Path) -> Path:
    for name in REQUIRED_FILES:
        file = directory / name
        file.parent.mkdir(parents=True, exist_ok=True)
        text = '[project]\nname = "timeindex"\nversion = "0.1.0"\n' if name == "pyproject.toml" else \
               "USER_DEBUG: false\n" if name.endswith("config.yaml") else "# Controlled test source\n"
        file.write_text(text, encoding="utf-8")
    return directory.resolve()


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT, prefix=".project-test-")
        self.folder = Path(self.temporary.name).resolve()
        self.assertTrue(self.folder.is_relative_to(ROOT))
        self.platform = self.folder / "test platform"
        self.platform.mkdir()
        self.environment = patch.dict(os.environ, {PROJECT_ENV: ""})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_finds_separate_nested_project_without_importing_or_writing_it(self) -> None:
        project = make_project(self.folder / "本体 (Tony)" / "TimeIndex")
        before = {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()}
        found = discover_projects(platform_root=self.platform, search_roots=[self.folder])
        self.assertEqual(found, [project])
        self.assertEqual(before, {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()})
        self.assertFalse((project / "src/TimeIndex/.lancedb").exists())

    def test_existing_parent_layout_still_works(self) -> None:
        project = make_project(self.folder / "TimeIndex")
        platform = project / "test_platform"
        platform.mkdir()
        self.assertEqual(resolve_project_dir(platform_root=platform), project)

    def test_explicit_path_overrides_environment_and_saved_choice(self) -> None:
        chosen = make_project(self.folder / "桌面" / "TimeIndex")
        other = make_project(self.folder / "other")
        save_project_dir(other, platform_root=self.platform)
        with patch.dict(os.environ, {PROJECT_ENV: str(other)}):
            self.assertEqual(resolve_project_dir(f'"{chosen}"', platform_root=self.platform), chosen)

    def test_environment_and_saved_path_work_outside_search_area(self) -> None:
        project = make_project(self.folder / "a/b/c/TimeIndex")
        with patch.dict(os.environ, {PROJECT_ENV: str(project)}):
            self.assertEqual(resolve_project_dir(platform_root=self.platform), project)
        save_project_dir(project, platform_root=self.platform)
        self.assertEqual(resolve_project_dir(platform_root=self.platform), project)

    def test_invalid_explicit_path_does_not_silently_pick_another_project(self) -> None:
        make_project(self.folder / "other")
        with self.assertRaisesRegex(ValueError, "缺少"):
            resolve_project_dir(self.folder / "missing", platform_root=self.platform)
        with patch.dict(os.environ, {PROJECT_ENV: str(self.folder / "missing")}):
            with self.assertRaisesRegex(ValueError, "缺少"):
                resolve_project_dir(platform_root=self.platform)

    def test_multiple_candidates_require_a_choice_and_stale_save_can_be_replaced(self) -> None:
        projects = [make_project(self.folder / name) for name in ("one", "two")]
        atomic_json(self.platform / SETTINGS_FILE, {"path": str(self.folder / "old-computer")})
        with patch("platform_core.project.discover_projects", return_value=projects):
            with self.assertRaisesRegex(ValueError, "多个"):
                resolve_project_dir(platform_root=self.platform)
        with patch("platform_core.project.discover_projects", return_value=[projects[0]]):
            self.assertEqual(resolve_project_dir(platform_root=self.platform), projects[0])

    def test_missing_project_and_wrong_metadata_have_actionable_errors(self) -> None:
        with patch("platform_core.project.discover_projects", return_value=[]):
            with self.assertRaisesRegex(ValueError, "TIMEINDEX_PROJECT_DIR"):
                resolve_project_dir(platform_root=self.platform)
        project = make_project(self.folder / "incorrect")
        (project / "pyproject.toml").write_text('[project]\nname = "different"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "项目名称不是"):
            validate_project(project)
        (project / "pyproject.toml").write_text("invalid TOML {", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "配置无法读取"):
            validate_project(project)

    def test_search_is_bounded_and_ignores_runtime_copies(self) -> None:
        make_project(self.folder / "a/b/c/too-deep")
        make_project(self.folder / "runs/runtime")
        make_project(self.folder / ".venv/ignored")
        self.assertEqual(discover_projects(platform_root=self.platform, search_roots=[self.folder]), [])

    def test_relative_path_and_saved_path_are_based_on_platform_location(self) -> None:
        project = make_project(self.folder / "TimeIndex")
        self.assertEqual(save_project_dir("../TimeIndex", platform_root=self.platform), project)
        self.assertEqual(read_json(self.platform / SETTINGS_FILE)["path"], str(project))

    def test_manifest_pins_the_source_used_for_the_isolated_copy(self) -> None:
        project = make_project(self.folder / "本体与平台分开")
        before = digest_source(project / "src/TimeIndex")
        with patch("platform_core.manifest.RUNS", self.platform / "runs"), \
                patch("platform_core.manifest.host_info", return_value={}):
            manifest = create_run("quick", timeindex_project=project)
            other = make_project(self.folder / "another")
            with patch.dict(os.environ, {PROJECT_ENV: str(other)}):
                paths = prepare(manifest["run_id"], manifest)
            try:
                self.assertEqual(manifest["timeindex_project"], str(project))
                self.assertEqual(digest_source(paths["source"]), before)
                self.assertTrue(paths["profile"].is_relative_to(self.platform))
                self.assertFalse(paths["database"].exists())
                self.assertEqual(digest_source(project / "src/TimeIndex"), before)
            finally:
                cleanup(manifest["run_id"])

    def test_bad_project_is_rejected_before_creating_a_run(self) -> None:
        with patch("platform_core.manifest.RUNS", self.platform / "runs"):
            with self.assertRaises(ValueError):
                create_run("quick", timeindex_project=self.folder / "missing")
        self.assertFalse((self.platform / "runs").exists())

    def test_dashboard_accepts_and_remembers_a_separate_project(self) -> None:
        from streamlit.testing.v1 import AppTest

        project = make_project(self.folder / "桌面本体")
        save = lambda path: save_project_dir(path, platform_root=self.platform)
        with patch("platform_core.project.save_project_dir", side_effect=save):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            field = next(item for item in app.text_input if item.label == "TimeIndex 本体项目文件夹")
            field.set_value(str(project)).run()
            button = next(item for item in app.button if item.label == "检查并记住本体位置")
            button.click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertTrue(any(str(project) in item.value for item in app.success))
        self.assertEqual(read_json(self.platform / SETTINGS_FILE)["path"], str(project))


if __name__ == "__main__":
    unittest.main()
