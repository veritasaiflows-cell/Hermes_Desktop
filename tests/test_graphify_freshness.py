"""Tests for the workspace-owned Graphify freshness gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import graphify_freshness

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GraphifyFreshnessTests(unittest.TestCase):
    def _project(self) -> tuple[TemporaryDirectory, Path]:
        directory = TemporaryDirectory()
        root = Path(directory.name)
        (root / "scripts").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "canonical").mkdir()
        graph_dir = root / "graphify-out"
        graph_dir.mkdir()

        source = root / "scripts" / "example.py"
        source.write_text("def example():\n    return 1\n", encoding="utf-8")
        (graph_dir / "manifest.json").write_text(
            json.dumps({"scripts/example.py": {"mtime": source.stat().st_mtime}}),
            encoding="utf-8",
        )
        (graph_dir / "graph.json").write_text(
            json.dumps(
                {
                    "built_at_commit": "abc123",
                    "nodes": [
                        {
                            "id": "scripts_example",
                            "source_file": "scripts/example.py",
                        }
                    ],
                    "links": [],
                }
            ),
            encoding="utf-8",
        )
        return directory, root

    def test_written_baseline_reports_fresh(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)

        written = graphify_freshness.write_baseline(root)
        report = graphify_freshness.check_freshness(root)

        self.assertEqual(written["status"], "baseline_written")
        self.assertEqual(report["status"], "fresh")
        self.assertEqual(report["issues"], [])

    def test_changed_baseline_source_reports_stale(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        (root / "scripts" / "example.py").write_text(
            "def example():\n    return 2\n",
            encoding="utf-8",
        )

        report = graphify_freshness.check_freshness(root)

        self.assertEqual(report["status"], "stale")
        matching = [
            issue
            for issue in report["issues"]
            if issue["code"] == "source_changed"
        ]
        self.assertEqual(matching[0]["path"], "scripts/example.py")

    def test_new_code_source_missing_from_graph_reports_stale(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        (root / "scripts" / "new_module.py").write_text(
            "def new_feature():\n    return True\n",
            encoding="utf-8",
        )

        report = graphify_freshness.check_freshness(root)

        self.assertEqual(report["status"], "stale")
        matching = [
            issue
            for issue in report["issues"]
            if issue["code"] == "source_missing_from_graph"
        ]
        self.assertEqual(matching[0]["path"], "scripts/new_module.py")

    def test_cli_returns_one_and_json_when_stale(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        (root / "scripts" / "example.py").write_text(
            "def example():\n    return 3\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "graphify_freshness.py"),
                "--project-root",
                str(root),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["status"], "stale")

    def test_pending_semantic_update_reports_stale(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        (root / "graphify-out" / "needs_update").write_text("pending\n", encoding="utf-8")

        report = graphify_freshness.check_freshness(root)

        self.assertEqual(report["status"], "stale")
        self.assertIn(
            "semantic_update_pending",
            {issue["code"] for issue in report["issues"]},
        )

    def test_graph_changed_after_baseline_reports_stale(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        graph_path = root / "graphify-out" / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["nodes"].append(
            {"id": "unexpected", "source_file": "scripts/example.py"}
        )
        graph_path.write_text(json.dumps(graph), encoding="utf-8")

        report = graphify_freshness.check_freshness(root)

        self.assertEqual(report["status"], "stale")
        self.assertIn(
            "graph_artifact_changed",
            {issue["code"] for issue in report["issues"]},
        )

    def test_baseline_rejects_manifest_path_outside_workspace(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        outside = root.parent / f"{root.name}-outside.py"
        outside.write_text("SECRET = 'not workspace data'\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        (root / "graphify-out" / "manifest.json").write_text(
            json.dumps({f"../{outside.name}": {"mtime": outside.stat().st_mtime}}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "outside workspace"):
            graphify_freshness.write_baseline(root)

    def test_baseline_rejects_linked_graph_directory(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graph_dir = root / "graphify-out"
        outside = root.parent / f"{root.name}-graphify-outside"
        shutil.copytree(graph_dir, outside)
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        shutil.rmtree(graph_dir)
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(graph_dir), str(outside)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode != 0:
                    self.skipTest(f"junction unavailable: {completed.stderr}")
                self.addCleanup(
                    lambda: subprocess.run(
                        ["cmd.exe", "/c", "rmdir", str(graph_dir)],
                        capture_output=True,
                        timeout=30,
                    )
                )
            else:
                graph_dir.symlink_to(outside, target_is_directory=True)
                self.addCleanup(graph_dir.unlink)
        except OSError as exc:
            self.skipTest(f"directory link unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "linked or reparse"):
            graphify_freshness.write_baseline(root)

    def test_cli_returns_unavailable_for_non_object_graph(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        (root / "graphify-out" / "graph.json").write_text("[]\n", encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "graphify_freshness.py"),
                "--project-root",
                str(root),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["status"], "unavailable")

    def test_baseline_rejects_linked_code_root(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        scripts = root / "scripts"
        outside = root.parent / f"{root.name}-outside-scripts"
        shutil.copytree(scripts, outside)
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        shutil.rmtree(scripts)
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(scripts), str(outside)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode != 0:
                    self.skipTest(f"junction unavailable: {completed.stderr}")
                self.addCleanup(
                    lambda: subprocess.run(
                        ["cmd.exe", "/c", "rmdir", str(scripts)],
                        capture_output=True,
                        timeout=30,
                    )
                )
            else:
                scripts.symlink_to(outside, target_is_directory=True)
                self.addCleanup(scripts.unlink)
        except OSError as exc:
            self.skipTest(f"directory link unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "linked or reparse"):
            graphify_freshness.write_baseline(root)

    def test_baseline_rejects_missing_code_source_coverage(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        graphify_freshness.write_baseline(root)
        baseline_path = root / "graphify-out" / "freshness-baseline.json"
        before = baseline_path.read_bytes()
        (root / "scripts" / "missing.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "missing code source coverage"):
            graphify_freshness.write_baseline(root)

        self.assertEqual(baseline_path.read_bytes(), before)

    def test_baseline_rejects_missing_declared_workspace_gate_edge(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        (root / "scripts" / "workspace_status.py").write_text(
            "DEFAULT_GATES: list[tuple[str, list[str], int]] = [\n"
            "    (\"graphify_freshness\", [\"scripts/graphify_freshness.py\"], 120),\n"
            "]\n",
            encoding="utf-8",
        )
        (root / "scripts" / "graphify_freshness.py").write_text(
            "print('fresh')\n",
            encoding="utf-8",
        )
        graph_path = root / "graphify-out" / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["nodes"].extend(
            [
                {
                    "id": "scripts_workspace_status",
                    "source_file": "scripts/workspace_status.py",
                    "source_location": "L1",
                    "file_type": "code",
                },
                {
                    "id": "scripts_graphify_freshness",
                    "source_file": "scripts/graphify_freshness.py",
                    "source_location": "L1",
                    "file_type": "code",
                },
            ]
        )
        graph_path.write_text(json.dumps(graph), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "missing declared workspace gate edges"):
            graphify_freshness.write_baseline(root)

    def test_baseline_rejects_missing_required_code_root(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        shutil.rmtree(root / "tests")

        with self.assertRaisesRegex(ValueError, "required source root is missing"):
            graphify_freshness.write_baseline(root)

    def test_baseline_rejects_broken_linked_code_root(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)
        scripts = root / "scripts"
        outside = root.parent / f"{root.name}-broken-outside-scripts"
        shutil.copytree(scripts, outside)
        shutil.rmtree(scripts)
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(scripts), str(outside)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode != 0:
                    self.skipTest(f"junction unavailable: {completed.stderr}")
                self.addCleanup(
                    lambda: subprocess.run(
                        ["cmd.exe", "/c", "rmdir", str(scripts)],
                        capture_output=True,
                        timeout=30,
                    )
                )
            else:
                scripts.symlink_to(outside, target_is_directory=True)
                self.addCleanup(scripts.unlink)
        except OSError as exc:
            self.skipTest(f"directory link unavailable: {exc}")
        shutil.rmtree(outside)

        with self.assertRaisesRegex(ValueError, "linked or reparse"):
            graphify_freshness.write_baseline(root)


if __name__ == "__main__":
    unittest.main()
