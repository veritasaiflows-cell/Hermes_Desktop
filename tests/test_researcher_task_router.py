"""Tests for admission and staging of bounded Researcher tasks."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import researcher_task_router  # noqa: E402


class ResearcherTaskRouterTests(unittest.TestCase):
    def _request(self, **overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "schema": researcher_task_router.REQUEST_SCHEMA,
            "task_id": "research-042",
            "task_class": "test_discovery",
            "phase": "pre-implementation",
            "mode": "read-only",
            "objective": "Identify the focused test command and its source evidence.",
            "source_files": ["docs/testing.md", "tests/test_widget.py"],
            "output_schema": researcher_task_router.ARTIFACT_SCHEMA,
        }
        request.update(overrides)
        return request

    def _source_tree(self, root: Path) -> Path:
        source_root = root / "source-input"
        (source_root / "docs").mkdir(parents=True)
        (source_root / "tests").mkdir()
        (source_root / "docs" / "testing.md").write_text(
            "# Test guide\nRun: python -m unittest tests.test_widget -v\n",
            encoding="utf-8",
        )
        (source_root / "tests" / "test_widget.py").write_text(
            "class WidgetTests: pass\n",
            encoding="utf-8",
        )
        return source_root

    def test_declared_read_only_task_is_admitted(self) -> None:
        with TemporaryDirectory() as directory:
            source_root = self._source_tree(Path(directory))
            result = researcher_task_router.admit_request(self._request(), source_root)

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["task_class"], "test_discovery")
        self.assertEqual(result["reasons"], [])

    def test_write_mode_is_rejected_before_staging(self) -> None:
        with TemporaryDirectory() as directory:
            source_root = self._source_tree(Path(directory))
            result = researcher_task_router.admit_request(
                self._request(mode="write"), source_root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertIn("mode must be read-only", result["reasons"])

    def test_non_pre_implementation_phase_is_rejected_before_staging(self) -> None:
        with TemporaryDirectory() as directory:
            source_root = self._source_tree(Path(directory))
            result = researcher_task_router.admit_request(
                self._request(phase="implementation"), source_root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase must be pre-implementation", result["reasons"])

    def test_undeclared_task_class_is_rejected_before_staging(self) -> None:
        with TemporaryDirectory() as directory:
            source_root = self._source_tree(Path(directory))
            result = researcher_task_router.admit_request(
                self._request(task_class="code_implementation"), source_root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("task_class is not allowlisted" in reason for reason in result["reasons"])
        )

    def test_source_path_escape_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            source_root = self._source_tree(Path(directory))
            result = researcher_task_router.admit_request(
                self._request(source_files=["../outside.txt"]), source_root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("escapes source root" in reason for reason in result["reasons"]))

    def test_stage_copies_only_allowlisted_sources_and_freezes_hashes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = self._source_tree(root)
            active_root = root / "active-pack"
            staged = researcher_task_router.stage_request(
                self._request(), source_root, active_root
            )
            manifest = json.loads((active_root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(manifest["schema"], researcher_task_router.PACK_SCHEMA)
        self.assertEqual(manifest["task_class"], "test_discovery")
        self.assertEqual(
            [entry["path"] for entry in manifest["source_files"]],
            ["docs/testing.md", "tests/test_widget.py"],
        )
        self.assertTrue(all(len(entry["sha256"]) == 64 for entry in manifest["source_files"]))
        self.assertFalse((active_root / "sources" / "unlisted.txt").exists())

    def test_tampered_active_source_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = self._source_tree(root)
            active_root = root / "active-pack"
            researcher_task_router.stage_request(self._request(), source_root, active_root)
            (active_root / "sources" / "docs" / "testing.md").write_text(
                "tampered\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(researcher_task_router.ResearcherTaskError, "hash"):
                researcher_task_router.load_active_pack(active_root)


if __name__ == "__main__":
    unittest.main()
