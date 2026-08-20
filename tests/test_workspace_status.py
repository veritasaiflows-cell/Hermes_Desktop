#!/usr/bin/env python3
"""Tests for scripts/workspace_status.py.

These tests use a temporary project layout so the workspace_status script
operates on mocked gate commands rather than the real repository state.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _gate_env(gate_scripts: dict[str, str]) -> str:
    """Return a WORKSPACE_STATUS_GATES value that points at mocked scripts."""
    return json.dumps(
        [
            [label, [f"scripts/{label}.py"], 30]
            for label in gate_scripts
        ]
    )


class WorkspaceStatusTests(unittest.TestCase):
    def _run_workspace_status(self, project_root: Path, gate_scripts: dict[str, str]) -> tuple[int, dict]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["WORKSPACE_STATUS_GATES"] = _gate_env(gate_scripts)

        # Run via Python import so we can pass project_root to main().
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
                f"from scripts.workspace_status import main; "
                f"raise SystemExit(main({str(project_root)!r}))",
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if not completed.stdout.strip():
            return completed.returncode, {"parse_error": True, "stderr": completed.stderr}
        try:
            return completed.returncode, json.loads(completed.stdout)
        except json.JSONDecodeError:
            return completed.returncode, {"parse_error": True, "stdout": completed.stdout, "stderr": completed.stderr}

    def _make_minimal_project(self, gate_scripts: dict[str, str]) -> Path:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        self.addCleanup(directory.cleanup)

        (root / "scripts").mkdir(parents=True)
        (root / "state").mkdir(parents=True)
        (root / "state" / "workflows").mkdir(parents=True)
        (root / "canonical").mkdir(parents=True)
        (root / "wiki").mkdir(parents=True)

        active = {
            "schema": "active-workflows.v1",
            "workflows": [
                {
                    "workflow_id": "WF-1000",
                    "display_name": "Workflow A",
                    "lifecycle": "active",
                    "effective_status": "active",
                    "blocker_count": 0,
                    "owner_action_required": False,
                    "next_action": "next",
                }
            ],
        }
        (root / "state" / "active_workflows.json").write_text(json.dumps(active), encoding="utf-8")
        (root / "state" / "workflow_alias_index.json").write_text(
            json.dumps({"schema": "workflow-alias-index.v1", "aliases": {}}), encoding="utf-8"
        )
        (root / "state" / "workflow-control-overrides.json").write_text("[]", encoding="utf-8")
        (root / "state" / "workflow-routing-index.json").write_text(
            json.dumps({
                "schema": "workflow-routing-index.v1",
                "generated_at": "2026-01-01T00:00:00Z",
                "source_signatures": {},
                "workflows": [],
            }),
            encoding="utf-8",
        )

        for label, body in gate_scripts.items():
            (root / "scripts" / f"{label}.py").write_text(body, encoding="utf-8")

        return root

    def test_healthy_project_returns_healthy(self) -> None:
        gate_scripts = {
            "routing": 'import json,sys\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json,sys\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'import sys\nprint("OK", file=sys.stderr)',
            "cron_registration": 'print("OK")',
            "claim_drift": 'import sys\nprint("OK", file=sys.stderr)',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok", "count": 0, "issues": []}))',
            "graph_freshness": 'import sys\nprint("OK", file=sys.stderr)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["health"]["status"], "healthy")
        self.assertEqual(brief["health"]["hard_failures"], [])
        self.assertEqual(brief["health"]["warnings"], [])
        self.assertEqual(brief["schema"], "workspace-status.v1")

    def test_routing_failure_returns_degraded(self) -> None:
        gate_scripts = {
            "routing": 'import sys\nsys.exit(1)',
            "wiki": 'import json,sys\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'import sys\nprint("OK", file=sys.stderr)',
            "cron_registration": 'print("OK")',
            "claim_drift": 'import sys\nprint("OK", file=sys.stderr)',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok", "count": 0, "issues": []}))',
            "graph_freshness": 'import sys\nprint("OK", file=sys.stderr)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 1)
        self.assertEqual(brief["health"]["status"], "degraded")
        self.assertIn("routing", brief["health"]["hard_failures"])

    def test_wiki_stale_returns_warning_not_failure(self) -> None:
        gate_scripts = {
            "routing": 'import json,sys\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json,sys\nprint(json.dumps({"status": "stale"}))',
            "alias": 'import sys\nprint("OK", file=sys.stderr)',
            "cron_registration": 'print("OK")',
            "claim_drift": 'import sys\nprint("OK", file=sys.stderr)',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok", "count": 0, "issues": []}))',
            "graph_freshness": 'import sys\nprint("OK", file=sys.stderr)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 0)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("wiki_stale", brief["health"]["warnings"])

    def test_claim_drift_returns_warning(self) -> None:
        gate_scripts = {
            "routing": 'import json,sys\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json,sys\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'import sys\nprint("OK", file=sys.stderr)',
            "cron_registration": 'print("OK")',
            "claim_drift": 'import sys\nprint("DEGRADED", file=sys.stderr); sys.exit(1)',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok", "count": 0, "issues": []}))',
            "graph_freshness": 'import sys\nprint("OK", file=sys.stderr)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 0)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("claim_drift", brief["health"]["warnings"])


if __name__ == "__main__":
    unittest.main()
