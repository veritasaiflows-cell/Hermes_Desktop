"""Regression coverage for deterministic workspace-status gate edges."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import graphify_gate_edges


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "graphify_gate_edges.py"


class GraphifyGateEdgesTests(unittest.TestCase):
    def _project(self) -> tuple[TemporaryDirectory, Path]:
        directory = TemporaryDirectory()
        root = Path(directory.name)
        (root / "canonical").mkdir()
        (root / "scripts").mkdir()
        (root / "tests").mkdir()
        (root / "graphify-out").mkdir()
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
        (root / "graphify-out" / "graph.json").write_text(
            json.dumps(
                {
                    "directed": False,
                    "nodes": [
                        {
                            "id": "scripts_workspace_status",
                            "label": "workspace_status.py",
                            "file_type": "code",
                            "source_file": "scripts/workspace_status.py",
                            "source_location": "L1",
                        },
                        {
                            "id": "scripts_graphify_freshness",
                            "label": "graphify_freshness.py",
                            "file_type": "code",
                            "source_file": "scripts/graphify_freshness.py",
                            "source_location": "L1",
                        },
                    ],
                    "links": [],
                }
            ),
            encoding="utf-8",
        )
        return directory, root

    def test_reconcile_adds_a_cited_edge_for_a_static_gate(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)

        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--project-root", str(root)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "updated")
        graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(
            graph["links"],
            [
                {
                    "_origin": "workspace_gate_contract",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "context": "workspace_status.DEFAULT_GATES:graphify_freshness",
                    "relation": "runs_gate",
                    "source": "scripts_workspace_status",
                    "source_file": "scripts/workspace_status.py",
                    "source_location": "L2",
                    "target": "scripts_graphify_freshness",
                    "weight": 1.0,
                }
            ],
        )

    def test_check_reports_a_declared_gate_missing_from_the_graph(self) -> None:
        directory, root = self._project()
        self.addCleanup(directory.cleanup)

        issues = graphify_gate_edges.gate_edge_issues(root)

        self.assertEqual(
            issues,
            [
                {
                    "code": "declared_gate_edge_missing",
                    "label": "graphify_freshness",
                    "required_refresh_command": "python scripts/graphify_gate_edges.py",
                    "source": "scripts/workspace_status.py",
                    "source_location": "L2",
                    "target": "scripts/graphify_freshness.py",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
