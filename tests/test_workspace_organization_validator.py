#!/usr/bin/env python3
"""End-to-end tests for the workspace organization contract."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_LAYERS = (
    "source",
    "canonical",
    "graph",
    "vector",
    "derived",
    "telemetry",
    "state",
    "tmp",
)


class WorkspaceOrganizationValidatorTests(unittest.TestCase):
    def _workspace(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        for layer in REQUIRED_LAYERS:
            path = root / layer
            path.mkdir(parents=True)
            (path / "README.md").write_text(f"# {layer}\n", encoding="utf-8")
        return directory, root

    def _run(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "workspace_organization_validator.py"),
                "--project-root",
                str(root),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_clean_workspace_passes(self) -> None:
        directory, root = self._workspace()
        self.addCleanup(directory.cleanup)

        completed = self._run(root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["issues"], [])

    def test_live_index_in_tmp_fails(self) -> None:
        directory, root = self._workspace()
        self.addCleanup(directory.cleanup)
        (root / "tmp" / "vector-memory.sqlite").write_bytes(b"not-a-live-db")

        completed = self._run(root)

        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        self.assertIn("forbidden_tmp_artifact", {issue["code"] for issue in report["issues"]})

    def test_canonical_database_in_state_fails(self) -> None:
        directory, root = self._workspace()
        self.addCleanup(directory.cleanup)
        (root / "state" / "canonical.db").write_bytes(b"orphan")

        completed = self._run(root)

        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        self.assertIn("canonical_database_misplaced", {issue["code"] for issue in report["issues"]})

    def test_bootstrap_routes_sessions_through_governed_organization_cadence(self) -> None:
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        governance = (PROJECT_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
        procedures = (PROJECT_ROOT / "references" / "operating-procedures.md").read_text(
            encoding="utf-8"
        )
        status_contract = (
            PROJECT_ROOT / "references" / "workspace-status-maintenance.md"
        ).read_text(encoding="utf-8")

        self.assertIn("python scripts/workspace_status.py", agents)
        self.assertIn("## Workspace placement map", governance)
        self.assertIn("## Workspace organization cadence", procedures)
        self.assertIn("workspace_organization_validator.py", status_contract)
        self.assertIn("workspace_index.py status", status_contract)


if __name__ == "__main__":
    unittest.main()
