#!/usr/bin/env python3
"""Tests for scripts/workspace_status.py.

These tests use a temporary project layout so the workspace_status script
operates on mocked gate commands rather than the real repository state.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from canonical.db import CanonicalDB
from scripts import workspace_status
from scripts.workspace_fingerprint import correctness_snapshot

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
    def test_pending_review_and_stale_report_are_both_warnings(self) -> None:
        gates = {
            label: {"exit": 0}
            for label in (
                "organization",
                "routing",
                "alias",
                "cron_registration",
                "graph_integrity",
                "graph_freshness",
                "vector_memory",
                "workspace_index",
                "archive_stale",
            )
        }
        gates["wiki"] = {"exit": 0, "stdout": {"status": "fresh"}}
        gates["claim_drift"] = {"exit": 0}
        gates["feedback_evaluation"] = {
            "exit": 0,
            "stdout": {
                "status": "review_required",
                "report_status": "stale",
                "pending_review_count": 1,
            },
        }

        status, hard, warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "healthy_with_warnings")
        self.assertEqual(hard, [])
        self.assertIn("feedback_review_required", warnings)
        self.assertIn("feedback_evaluation_stale", warnings)

    def test_failed_feedback_baseline_is_a_warning(self) -> None:
        gates = {
            label: {"exit": 0}
            for label in (
                "organization",
                "routing",
                "alias",
                "cron_registration",
                "graph_integrity",
                "graph_freshness",
                "vector_memory",
                "workspace_index",
                "archive_stale",
            )
        }
        gates["wiki"] = {"exit": 0, "stdout": {"status": "fresh"}}
        gates["claim_drift"] = {"exit": 0}
        gates["feedback_evaluation"] = {
            "exit": 0,
            "stdout": {
                "status": "failed",
                "report_status": "fresh",
                "failed_candidate_count": 1,
            },
        }

        status, hard, warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "healthy_with_warnings")
        self.assertEqual(hard, [])
        self.assertIn("feedback_evaluation_failed", warnings)

    def test_lane_gate_does_not_erase_earlier_hard_failures(self) -> None:
        """A healthy lane gate must not mask index/graph hard failures.

        Regression: the lane branch rebound the accumulated ``hard_failures``
        list to the lane payload's own (usually empty) list, so any hard
        failure detected before it was silently discarded and a degraded
        workspace reported as healthy.
        """
        gates = {
            label: {"exit": 0}
            for label in (
                "organization",
                "routing",
                "alias",
                "cron_registration",
                "graph_integrity",
                "graph_freshness",
                "archive_stale",
            )
        }
        gates["wiki"] = {"exit": 0, "stdout": {"status": "fresh"}}
        gates["claim_drift"] = {"exit": 0}
        gates["vector_memory"] = {"exit": 1, "stdout": {"status": "unavailable"}}
        gates["workspace_index"] = {"exit": 1, "stdout": None}
        gates["lane_register"] = {
            "exit": 0,
            "stdout": {"expired_leases": [], "collisions": [], "hard_failures": []},
        }

        status, hard, _warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "degraded")
        self.assertIn("vector_memory", hard)
        self.assertIn("workspace_index", hard)

    def test_stale_vector_indexes_are_warnings_not_failures(self) -> None:
        """Stale (not missing/corrupt) indexes warn; source-direct fallback exists."""
        gates = {
            label: {"exit": 0}
            for label in (
                "organization",
                "routing",
                "alias",
                "cron_registration",
                "graph_integrity",
                "graph_freshness",
                "archive_stale",
            )
        }
        gates["wiki"] = {"exit": 0, "stdout": {"status": "fresh"}}
        gates["claim_drift"] = {"exit": 0}
        gates["vector_memory"] = {
            "exit": 1,
            "stdout": {"status": "degraded", "stale_source_count": 14},
        }
        gates["workspace_index"] = {
            "exit": 1,
            "stdout": {"available": True, "stale_source_count": 1},
        }

        status, hard, warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "healthy_with_warnings")
        self.assertEqual(hard, [])
        self.assertIn("vector_memory_stale", warnings)
        self.assertIn("workspace_index_stale", warnings)

    def test_fast_tier_covers_startup_gates_with_cached_routing(self) -> None:
        fast = workspace_status.fast_gates()
        self.assertEqual(
            [label for label, _args, _timeout in fast],
            ["organization", "routing", "wiki", "lane_register", "note_drift"],
        )
        routing = next(args for label, args, _timeout in fast if label == "routing")
        self.assertIn("--validate", routing)
        self.assertIn("--skip-recall-context", routing)
        self.assertNotIn("--no-cache", routing)

    def test_note_drift_gate_runs_the_a19_checker_read_only_and_in_parallel(self) -> None:
        by_label = {label: (args, timeout) for label, args, timeout in workspace_status.DEFAULT_GATES}
        self.assertIn("note_drift", by_label)
        self.assertEqual(by_label["note_drift"][0], ["scripts/check_note_state_drift.py"])
        self.assertIn("note_drift", workspace_status.PARALLEL_SAFE_GATES)

    def test_note_drift_failure_is_a_warning_not_a_hard_failure(self) -> None:
        gates = {
            "organization": {"exit": 0},
            "routing": {"exit": 0, "stdout": {"workflows": []}},
            "wiki": {"exit": 0, "stdout": {"status": "fresh"}},
            "lane_register": {"exit": 0, "stdout": {}},
            "note_drift": {"exit": 1, "stdout": "NOTE DRIFT DEGRADED"},
        }
        status, hard, warnings = workspace_status._health_decision(gates)
        self.assertEqual(status, "healthy_with_warnings")
        self.assertEqual(hard, [])
        self.assertIn("note_drift", warnings)

    def test_note_drift_pass_keeps_health_clean(self) -> None:
        gates = {
            "organization": {"exit": 0},
            "routing": {"exit": 0, "stdout": {"workflows": []}},
            "wiki": {"exit": 0, "stdout": {"status": "fresh"}},
            "lane_register": {"exit": 0, "stdout": {}},
            "note_drift": {"exit": 0},
        }
        status, hard, warnings = workspace_status._health_decision(gates)
        self.assertEqual((status, hard, warnings), ("healthy", [], []))

    def test_health_decision_ignores_gates_skipped_by_fast_tier(self) -> None:
        gates = {
            "organization": {"exit": 0},
            "routing": {"exit": 0, "stdout": {"workflows": []}},
            "wiki": {"exit": 0, "stdout": {"status": "fresh"}},
            "lane_register": {"exit": 0, "stdout": {}},
        }

        status, hard, warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "healthy")
        self.assertEqual(hard, [])
        self.assertEqual(warnings, [])

    def test_fingerprint_cache_reuses_hash_until_sources_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")

            first = workspace_status._cached_correctness_snapshot(root)
            with patch.object(
                workspace_status,
                "correctness_snapshot",
                side_effect=AssertionError("must use cache"),
            ):
                second = workspace_status._cached_correctness_snapshot(root)
            self.assertEqual(first["source_fingerprint"], second["source_fingerprint"])

            (root / "scripts" / "example.py").write_text("VALUE = 22\n", encoding="utf-8")
            third = workspace_status._cached_correctness_snapshot(root)
            self.assertNotEqual(first["source_fingerprint"], third["source_fingerprint"])

    def test_lane_register_hard_failures_surface_as_a_warning(self) -> None:
        gates = {
            label: {"exit": 0}
            for label in (
                "organization",
                "routing",
                "alias",
                "cron_registration",
                "graph_integrity",
                "graph_freshness",
                "vector_memory",
                "workspace_index",
                "archive_stale",
            )
        }
        gates["wiki"] = {"exit": 0, "stdout": {"status": "fresh"}}
        gates["claim_drift"] = {"exit": 0}
        gates["lane_register"] = {
            "exit": 0,
            "stdout": {
                "expired_leases": [],
                "collisions": [],
                "hard_failures": ["lane_scope_violation"],
            },
        }

        status, hard, warnings = workspace_status._health_decision(gates)

        self.assertEqual(status, "healthy_with_warnings")
        self.assertEqual(hard, [])
        self.assertIn("lane_register_hard_failures", warnings)

    def test_default_feedback_evaluation_gate_is_read_only_status(self) -> None:
        feedback = next(
            gate for gate in workspace_status.DEFAULT_GATES if gate[0] == "feedback_evaluation"
        )
        self.assertEqual(
            feedback[1],
            ["scripts/feedback_evaluation_loop.py", "status"],
        )

    def test_default_routing_gate_requires_freshness_validation(self) -> None:
        routing = next(gate for gate in workspace_status.DEFAULT_GATES if gate[0] == "routing")
        self.assertIn("--validate", routing[1])
        self.assertIn("--no-cache", routing[1])
        self.assertIn("--skip-recall-context", routing[1])

    def test_default_archive_gate_is_check_only(self) -> None:
        archive = next(
            gate for gate in workspace_status.DEFAULT_GATES if gate[0] == "archive_stale"
        )
        self.assertIn("--check-only", archive[1])

    def test_gate_runner_preserves_declared_order_for_unlisted_gates(self) -> None:
        calls = []
        def fake_run(label, args, timeout, project_root):
            calls.append(label)
            return {"label": label, "exit": 0, "elapsed_ms": 1}

        gates = [
            ("first", ["scripts/first.py"], 30),
            ("second", ["scripts/second.py"], 30),
        ]
        with patch.object(workspace_status, "_run", side_effect=fake_run):
            result = workspace_status._run_gates(gates, project_root=PROJECT_ROOT)

        self.assertEqual(list(result), ["first", "second"])
        self.assertEqual(calls, ["first", "second"])

    def test_parallel_safe_gates_overlap_and_results_keep_declared_order(self) -> None:
        import threading
        import time as _time

        safe = sorted(workspace_status.PARALLEL_SAFE_GATES)[:3]
        barrier = threading.Barrier(len(safe), timeout=5)

        def fake_run(label, args, timeout, project_root):
            # Deadlocks (BrokenBarrierError) unless all safe gates run concurrently.
            barrier.wait()
            return {"label": label, "exit": 0, "elapsed_ms": 1}

        gates = [(label, ["scripts/x.py"], 30) for label in safe]
        with patch.object(workspace_status, "_run", side_effect=fake_run):
            result = workspace_status._run_gates(gates, project_root=PROJECT_ROOT)

        self.assertEqual(list(result), safe)
        self.assertTrue(all(entry["exit"] == 0 for entry in result.values()))

    def test_writer_gates_never_overlap_each_other(self) -> None:
        import threading
        import time as _time

        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def fake_run(label, args, timeout, project_root):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            _time.sleep(0.05)
            with lock:
                active["n"] -= 1
            return {"label": label, "exit": 0, "elapsed_ms": 1}

        writers = ["lane_register", "vector_memory", "workspace_index", "unknown_gate"]
        for label in writers:
            self.assertNotIn(label, workspace_status.PARALLEL_SAFE_GATES)
        gates = [(label, ["scripts/x.py"], 30) for label in writers]
        with patch.object(workspace_status, "_run", side_effect=fake_run):
            result = workspace_status._run_gates(gates, project_root=PROJECT_ROOT)

        self.assertEqual(list(result), writers)
        self.assertEqual(active["max"], 1)

    def test_serial_env_override_disables_parallelism(self) -> None:
        calls = []

        def fake_run(label, args, timeout, project_root):
            calls.append(label)
            return {"label": label, "exit": 0, "elapsed_ms": 1}

        safe = sorted(workspace_status.PARALLEL_SAFE_GATES)[:3]
        gates = [(label, ["scripts/x.py"], 30) for label in safe]
        with (
            patch.dict(os.environ, {"WORKSPACE_STATUS_SERIAL": "1"}),
            patch.object(workspace_status, "_run", side_effect=fake_run),
        ):
            result = workspace_status._run_gates(gates, project_root=PROJECT_ROOT)

        self.assertEqual(calls, safe)
        self.assertEqual(list(result), safe)

    def test_parallel_safe_set_excludes_proven_writers(self) -> None:
        # Measured 2026-09-26: these gates touched files (SQLite WAL/SHM or schema).
        for writer in ("lane_register", "vector_memory", "workspace_index"):
            self.assertNotIn(writer, workspace_status.PARALLEL_SAFE_GATES)
        declared = {label for label, _args, _timeout in workspace_status.DEFAULT_GATES}
        self.assertTrue(workspace_status.PARALLEL_SAFE_GATES <= declared)

    def test_direct_script_entrypoint_resolves_project_imports(self) -> None:
        env = os.environ.copy()
        env["WORKSPACE_STATUS_GATES"] = "[]"

        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "workspace_status.py")],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

        self.assertNotIn("ModuleNotFoundError", completed.stderr)
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["schema"], "workspace-status.v1")

    def test_compact_mode_emits_summary_and_persists_full_brief(self) -> None:
        gate_scripts = {"organization": "print('{\"status\": \"ok\"}')"}
        project_root = self._make_minimal_project(gate_scripts)

        returncode, parsed = self._run_workspace_status_argv(
            project_root, gate_scripts, ["--compact"]
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(parsed["schema"], "workspace-status-compact.v1")
        self.assertNotIn("gates", parsed)
        self.assertIn("organization", parsed["gate_exits"])
        full_path = Path(parsed["full_brief_path"])
        self.assertTrue(full_path.is_file())
        self.assertEqual(
            json.loads(full_path.read_text(encoding="utf-8"))["schema"],
            "workspace-status.v1",
        )

    @staticmethod
    def _execute_mock_gate(label: str, body: str) -> dict:
        """Execute a tiny gate fixture in-process with the real gate-result shape."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        returncode = 0
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exec(compile(body, f"<mock-gate:{label}>", "exec"), {"__name__": "__main__"})
        except SystemExit as exc:
            returncode = exc.code if isinstance(exc.code, int) else 1

        raw_stdout = stdout.getvalue().strip()
        parsed = None
        if raw_stdout:
            try:
                parsed = json.loads(raw_stdout)
            except json.JSONDecodeError:
                parsed = raw_stdout
        return {
            "label": label,
            "exit": returncode,
            "elapsed_ms": 0,
            "stdout": parsed,
            "stderr_tail": stderr.getvalue().strip().splitlines()[-3:],
        }

    def _run_workspace_status(self, project_root: Path, gate_scripts: dict[str, str]) -> tuple[int, dict]:
        gates = [
            (label, [f"scripts/{label}.py"], 30)
            for label in gate_scripts
        ]
        return self._run_workspace_status_argv(project_root, gate_scripts, None)

    def _run_workspace_status_argv(
        self,
        project_root: Path,
        gate_scripts: dict[str, str],
        argv: list[str] | None,
    ) -> tuple[int, dict]:
        gates = [
            (label, [f"scripts/{label}.py"], 30)
            for label in gate_scripts
        ]

        def fake_run(label, _args, _timeout, project_root):
            return self._execute_mock_gate(label, gate_scripts[label])

        stdout = io.StringIO()
        with (
            patch.object(workspace_status, "GATES", gates),
            patch.object(workspace_status, "_run", side_effect=fake_run),
            redirect_stdout(stdout),
        ):
            returncode = workspace_status.main(project_root, argv=argv)

        raw_stdout = stdout.getvalue().strip()
        if not raw_stdout:
            return returncode, {"parse_error": True, "stderr": ""}
        try:
            return returncode, json.loads(raw_stdout)
        except json.JSONDecodeError:
            return returncode, {"parse_error": True, "stdout": raw_stdout, "stderr": ""}

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

    def test_missing_correctness_evidence_returns_warning(self) -> None:
        gate_scripts = {
            "routing": 'import json,sys\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json,sys\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'import sys\nprint("OK", file=sys.stderr)',
            "cron_registration": 'print("OK")',
            "claim_drift": 'import sys\nprint("OK", file=sys.stderr)',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok", "count": 0, "issues": []}))',
            "graph_freshness": 'import sys\nprint("OK", file=sys.stderr)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "organization": 'import json\nprint(json.dumps({"ok": True, "issues": []}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertEqual(brief["health"]["hard_failures"], [])
        self.assertIn("correctness_unavailable", brief["health"]["warnings"])
        self.assertIn("cron_test_gate.py", brief["recommended_next_action"])
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
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "organization": 'import json\nprint(json.dumps({"ok": True, "issues": []}))',
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
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "organization": 'import json\nprint(json.dumps({"ok": True, "issues": []}))',
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
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "document_count": 5, "status": "ok"}))',
            "organization": 'import json\nprint(json.dumps({"ok": True, "issues": []}))',
            "archive_stale": 'import sys\nprint("OK", file=sys.stderr)',
        }
        root = self._make_minimal_project(gate_scripts)
        code, brief = self._run_workspace_status(root, gate_scripts)
        self.assertEqual(code, 0)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("claim_drift", brief["health"]["warnings"])

    def test_organization_failure_returns_degraded(self) -> None:
        gate_scripts = {
            "routing": 'import json\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'print("OK")',
            "cron_registration": 'print("OK")',
            "claim_drift": 'print("OK")',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok"}))',
            "graph_freshness": 'print("OK")',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "stale_source_count": 0}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "stale_source_count": 0}))',
            "organization": 'import sys\nsys.exit(1)',
            "archive_stale": 'print("OK")',
        }
        root = self._make_minimal_project(gate_scripts)

        code, brief = self._run_workspace_status(root, gate_scripts)

        self.assertEqual(code, 1)
        self.assertIn("organization", brief["health"]["hard_failures"])

    def test_graphify_stale_returns_warning_not_failure(self) -> None:
        gate_scripts = {
            "routing": 'import json\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'print("OK")',
            "cron_registration": 'print("OK")',
            "claim_drift": 'print("OK")',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok"}))',
            "graph_freshness": 'print("OK")',
            "graphify_freshness": 'import json,sys\nprint(json.dumps({"status": "stale"})); sys.exit(1)',
            "vector_memory": 'import json\nprint(json.dumps({"available": True, "stale_source_count": 0}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True, "stale_source_count": 0}))',
            "organization": 'import json\nprint(json.dumps({"ok": True, "issues": []}))',
            "archive_stale": 'print("OK")',
        }
        root = self._make_minimal_project(gate_scripts)
        snapshot = correctness_snapshot(root)
        with CanonicalDB(root / "canonical" / "efficiens.db") as db:
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                resource_usage_json={"test_count": 1, "test_failure_count": 0, **snapshot},
                verification_result="pass",
                final_outcome="accepted",
                acceptance_status="accepted",
                completed_at="2026-08-20T04:00:02Z",
            )

        code, brief = self._run_workspace_status(root, gate_scripts)

        self.assertEqual(code, 0)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("graphify_stale", brief["health"]["warnings"])
        self.assertIn("isolated candidate", brief["recommended_next_action"])
        self.assertNotIn("--write-baseline", brief["recommended_next_action"])

    def test_current_correctness_run_is_surfaced(self) -> None:
        gate_scripts = {
            "routing": 'import json\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'print("OK")',
            "cron_registration": 'print("OK")',
            "claim_drift": 'print("OK")',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok"}))',
            "graph_freshness": 'print("OK")',
            "vector_memory": 'import json\nprint(json.dumps({"available": True}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True}))',
            "organization": 'import json\nprint(json.dumps({"ok": True}))',
            "archive_stale": 'print("OK")',
        }
        root = self._make_minimal_project(gate_scripts)
        snapshot = correctness_snapshot(root)
        with CanonicalDB(root / "canonical" / "efficiens.db") as db:
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                duration_ms=1234,
                resource_usage_json={
                    "test_count": 176,
                    "test_failure_count": 0,
                    **snapshot,
                },
                verification_result="pass",
                final_outcome="accepted",
                acceptance_status="accepted",
                started_at="2026-08-20T04:00:00Z",
                completed_at="2026-08-20T04:00:02Z",
            )

        code, brief = self._run_workspace_status(root, gate_scripts)

        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["correctness"]["status"], "current")
        self.assertEqual(brief["correctness"]["test_count"], 176)
        self.assertEqual(brief["correctness"]["completed_at"], "2026-08-20T04:00:02Z")
        self.assertFalse(brief["correctness"]["stale"])
        self.assertEqual(brief["health"]["status"], "healthy")

    def test_non_object_correctness_telemetry_returns_unavailable_warning(self) -> None:
        gate_scripts = {
            "routing": 'import json\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'print("OK")',
            "cron_registration": 'print("OK")',
            "claim_drift": 'print("OK")',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok"}))',
            "graph_freshness": 'print("OK")',
            "vector_memory": 'import json\nprint(json.dumps({"available": True}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True}))',
            "organization": 'import json\nprint(json.dumps({"ok": True}))',
            "archive_stale": 'print("OK")',
        }
        root = self._make_minimal_project(gate_scripts)
        with CanonicalDB(root / "canonical" / "efficiens.db") as db:
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                resource_usage_json=[],
                verification_result="pass",
                final_outcome="accepted",
                acceptance_status="accepted",
                started_at="2026-08-20T04:00:00Z",
                completed_at="2026-08-20T04:00:02Z",
            )

        code, brief = self._run_workspace_status(root, gate_scripts)

        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["correctness"]["status"], "unavailable")
        self.assertIn("correctness_unavailable", brief["health"]["warnings"])

    def test_incomplete_correctness_telemetry_returns_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            snapshot = correctness_snapshot(root)
            with CanonicalDB(root / "canonical" / "efficiens.db") as db:
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    resource_usage_json={
                        "source_fingerprint": snapshot["source_fingerprint"],
                    },
                    verification_result="pass",
                    final_outcome="accepted",
                    acceptance_status="accepted",
                    completed_at="2026-08-20T04:00:02Z",
                )

            status = workspace_status._correctness_status(root)

        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["reason"], "correctness_telemetry_incomplete")

    def test_inconsistent_accepted_correctness_telemetry_is_not_current(self) -> None:
        invalid_overrides = [
            {"test_count": 0},
            {"test_failure_count": 1},
            {"source_file_count": 999},
        ]
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "canonical").mkdir()
                (root / "scripts").mkdir()
                (root / "scripts" / "example.py").write_text(
                    "VALUE = 1\n",
                    encoding="utf-8",
                )
                snapshot = correctness_snapshot(root)
                resource = {
                    "source_fingerprint": snapshot["source_fingerprint"],
                    "test_count": 1,
                    "test_failure_count": 0,
                    "source_file_count": snapshot["source_file_count"],
                    "tested_commit": snapshot["tested_commit"],
                    **overrides,
                }
                with CanonicalDB(root / "canonical" / "efficiens.db") as db:
                    db.record_run(
                        request_type="run_checks",
                        route_selected="deterministic",
                        resource_usage_json=resource,
                        verification_result="pass",
                        final_outcome="accepted",
                        acceptance_status="accepted",
                        completed_at="2026-08-20T04:00:02Z",
                    )

                status = workspace_status._correctness_status(root)

            self.assertEqual(status["status"], "unavailable")
            self.assertEqual(status["reason"], "correctness_telemetry_inconsistent")

    def test_fingerprint_error_returns_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            with CanonicalDB(root / "canonical" / "efficiens.db") as db:
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    resource_usage_json={
                        "source_fingerprint": "0" * 64,
                        "test_count": 1,
                        "test_failure_count": 0,
                        "source_file_count": 0,
                        "tested_commit": None,
                    },
                    verification_result="pass",
                    final_outcome="accepted",
                    acceptance_status="accepted",
                    completed_at="2026-08-20T04:00:02Z",
                )
            with patch.object(
                workspace_status,
                "correctness_snapshot",
                side_effect=OSError("locked source"),
            ):
                status = workspace_status._correctness_status(root)

        self.assertEqual(status["status"], "unavailable")
        self.assertIn("fingerprint", status["reason"])

    def test_added_source_file_marks_correctness_stale_not_inconsistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            (root / "scripts").mkdir()
            (root / "scripts" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = correctness_snapshot(root)
            with CanonicalDB(root / "canonical" / "efficiens.db") as db:
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    resource_usage_json={
                        "source_fingerprint": snapshot["source_fingerprint"],
                        "test_count": 1,
                        "test_failure_count": 0,
                        "source_file_count": snapshot["source_file_count"],
                        "tested_commit": snapshot["tested_commit"],
                    },
                    verification_result="pass",
                    final_outcome="accepted",
                    acceptance_status="accepted",
                    completed_at="2026-08-20T04:00:02Z",
                )
            (root / "scripts" / "added.py").write_text("VALUE = 2\n", encoding="utf-8")

            status = workspace_status._correctness_status(root)

        self.assertTrue(status["available"])
        self.assertEqual(status["status"], "stale")
        self.assertTrue(status["stale"])
        self.assertNotIn("reason", status)

    def test_stale_correctness_run_with_missing_source_count_is_inconsistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            (root / "scripts").mkdir()
            (root / "scripts" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = correctness_snapshot(root)
            with CanonicalDB(root / "canonical" / "efficiens.db") as db:
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    resource_usage_json={
                        "source_fingerprint": "0" * 64,
                        "test_count": 1,
                        "test_failure_count": 0,
                        "source_file_count": 0,
                        "tested_commit": snapshot["tested_commit"],
                    },
                    verification_result="pass",
                    final_outcome="accepted",
                    acceptance_status="accepted",
                    completed_at="2026-08-20T04:00:02Z",
                )

            status = workspace_status._correctness_status(root)

        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("inconsistent", status["reason"])

    def test_source_change_marks_correctness_stale_and_warns(self) -> None:
        gate_scripts = {
            "routing": 'import json\nprint(json.dumps({"workflows": [], "routing_index_stale": False, "unsafe_to_trust": False}))',
            "wiki": 'import json\nprint(json.dumps({"status": "fresh"}))',
            "alias": 'print("OK")',
            "cron_registration": 'print("OK")',
            "claim_drift": 'print("OK")',
            "graph_integrity": 'import json\nprint(json.dumps({"status": "ok"}))',
            "graph_freshness": 'print("OK")',
            "vector_memory": 'import json\nprint(json.dumps({"available": True}))',
            "workspace_index": 'import json\nprint(json.dumps({"available": True}))',
            "organization": 'import json\nprint(json.dumps({"ok": True}))',
            "archive_stale": 'print("OK")',
        }
        root = self._make_minimal_project(gate_scripts)
        snapshot = correctness_snapshot(root)
        with CanonicalDB(root / "canonical" / "efficiens.db") as db:
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                resource_usage_json={
                    "test_count": 176,
                    "test_failure_count": 0,
                    **snapshot,
                },
                verification_result="pass",
                final_outcome="accepted",
                acceptance_status="accepted",
                started_at="2026-08-20T04:00:00Z",
                completed_at="2026-08-20T04:00:02Z",
            )
        routing_script = root / "scripts" / "routing.py"
        routing_script.write_text(
            routing_script.read_text(encoding="utf-8") + "\n# changed after tests\n",
            encoding="utf-8",
        )

        code, brief = self._run_workspace_status(root, gate_scripts)

        self.assertEqual(code, 0, brief)
        self.assertEqual(brief["correctness"]["status"], "stale")
        self.assertTrue(brief["correctness"]["stale"])
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("correctness_stale", brief["health"]["warnings"])
        self.assertIn(
            "python scripts/cron_test_gate.py",
            brief["recommended_next_action"],
        )


class UncommittedWorkTests(unittest.TestCase):
    """Phase 0: aged uncommitted work warns (never fails); mtime age is a heuristic."""

    HOUR = 3600.0

    def _age(self, path: Path, hours: float, now: float) -> None:
        os.utime(path, (now - hours * self.HOUR, now - hours * self.HOUR))

    @staticmethod
    def _seed_repo(root: Path, env: dict[str, str], files: dict[str, str]) -> None:
        for name, body in files.items():
            (root / name).write_text(body, encoding="utf-8")
        for args in (
            ["init", "-q"],
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@x.invalid", "commit", "-q", "-m", "seed", "--no-verify"],
        ):
            subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root, env=env, check=True, capture_output=True)

    def test_scenarios_on_one_real_repository_under_hostile_git_environment(self) -> None:
        """Clean, staged A/M/D, MM partial staging, unstaged, aged, ignored, unicode/space, deletions.

        The whole scenario runs with inherited GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE pointing at a
        second (decoy) repository; results must describe only the requested repository.
        """
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        git = lambda root, *a: subprocess.run(  # noqa: E731
            ["git", "-c", "core.autocrlf=false", *a], cwd=root, env=env, check=True, capture_output=True
        )
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as decoy_tmp:
            root, decoy = Path(tmp), Path(decoy_tmp)
            self._seed_repo(decoy, env, {"decoy.py": "D = 1\n"})
            (decoy / "decoy-dirty.py").write_text("x", encoding="utf-8")
            self._seed_repo(
                root,
                env,
                {".gitignore": "ignored.log\n", "tracked.py": "A = 1\n", "doomed.py": "B = 1\n",
                 "staged_mod.py": "S = 1\n", "staged_del.py": "R = 1\n", "partial.py": "P = 1\n"},
            )
            hostile = {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
            }
            with patch.dict(os.environ, hostile):
                clean = workspace_status._uncommitted_work(root)
                self.assertEqual((clean["status"], clean["changed_count"]), ("clean", 0))

                now = 2_000_000_000.0
                (root / "tracked.py").write_text("A = 2\n", encoding="utf-8")
                (root / "staged_new.py").write_text("N = 1\n", encoding="utf-8")
                (root / "staged_mod.py").write_text("S = 2\n", encoding="utf-8")
                (root / "partial.py").write_text("P = 2\n", encoding="utf-8")
                git(root, "add", "staged_new.py", "staged_mod.py", "partial.py")
                git(root, "rm", "-q", "staged_del.py")
                (root / "partial.py").write_text("P = 3\n", encoding="utf-8")
                (root / "new dir").mkdir()
                (root / "new dir" / "notes \u00fc.md").write_text("x", encoding="utf-8")
                (root / "fresh.py").write_text("x", encoding="utf-8")
                (root / "ignored.log").write_text("x", encoding="utf-8")
                (root / "doomed.py").unlink()
                for name, hours in (("tracked.py", 5), ("new dir/notes \u00fc.md", 30), ("fresh.py", 1),
                                    ("ignored.log", 99), ("staged_new.py", 6), ("staged_mod.py", 2), ("partial.py", 8)):
                    self._age(root / name, hours, now)

                aged = workspace_status._uncommitted_work(root, now=now)

            codes = {item["path"]: item["code"] for item in aged["aged"]}
            self.assertEqual(aged["status"], "aged")
            self.assertEqual(
                [item["path"] for item in aged["aged"]],
                ["new dir/notes \u00fc.md", "partial.py", "staged_new.py", "tracked.py"],
            )
            self.assertEqual(codes, {"new dir/notes \u00fc.md": "??", "partial.py": "MM", "staged_new.py": "A", "tracked.py": "M"})
            # tracked M, doomed D, staged A, staged M, staged D, MM, two untracked; ignored excluded; decoy unseen.
            self.assertEqual(aged["changed_count"], 8)
            self.assertEqual((aged["examined_count"], aged["truncated"]), (8, False))
            self.assertEqual(aged["oldest_age_hours"], 30.0)
            self.assertEqual(sorted(aged["unknown_age"]), ["doomed.py", "staged_del.py"])
            self.assertIn("mtime", aged["heuristic"])
            self.assertFalse(any("decoy" in item["path"] for item in aged["aged"]))

            recent = workspace_status._uncommitted_work(root, now=now - 29 * self.HOUR)
            self.assertEqual((recent["status"], recent["aged_count"]), ("dirty", 0), "deletions/recent edits never warn alone")

            with patch.object(workspace_status, "UNCOMMITTED_WORK_STAT_LIMIT", 3):
                bounded = workspace_status._uncommitted_work(root, now=now)
            self.assertEqual((bounded["changed_count"], bounded["examined_count"], bounded["truncated"]), (8, 3, True))

    def test_invalid_nested_git_dir_never_falls_back_to_an_ancestor_repository(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            self._seed_repo(parent, env, {"parent.py": "X = 1\n"})
            (parent / "parent-dirty.py").write_text("x", encoding="utf-8")
            child = parent / "child"
            (child / ".git").mkdir(parents=True)
            result = workspace_status._uncommitted_work(child)
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("aged", result)

    def test_non_repository_roots_and_git_failures_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(workspace_status._uncommitted_work(Path(tmp))["status"], "unavailable")
            (Path(tmp) / ".git").mkdir()
            with patch.object(workspace_status.subprocess, "run", side_effect=OSError("no git")):
                self.assertEqual(workspace_status._uncommitted_work(Path(tmp))["status"], "unavailable")
            failed = subprocess.CompletedProcess([], 128, b"", b"fatal")
            with patch.object(workspace_status.subprocess, "run", return_value=failed):
                self.assertEqual(workspace_status._uncommitted_work(Path(tmp))["status"], "unavailable")

    def test_aged_work_warns_without_changing_exit_status(self) -> None:
        aged = {"status": "aged", "changed_count": 1, "aged_count": 1, "oldest_age_hours": 9.0,
                "aged": [{"path": "x.py", "code": "M", "age_hours": 9.0}]}
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, (
            patch.object(workspace_status, "GATES", [])
        ), patch.object(workspace_status, "_uncommitted_work", return_value=aged), patch.object(
            workspace_status, "_correctness_status", return_value={"status": "current"}
        ), redirect_stdout(stdout):
            code = workspace_status.main(Path(tmp), argv=[])
        brief = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(brief["health"]["status"], "healthy_with_warnings")
        self.assertIn("uncommitted_work_aged", brief["health"]["warnings"])
        self.assertEqual(brief["uncommitted_work"]["aged"][0]["path"], "x.py")
        self.assertIn("checkpoint_pending", brief["recommended_next_action"])

    def test_preflight_failures_are_unavailable_and_preserve_main_exit(self) -> None:
        import contextlib
        import io
        from unittest.mock import patch
        from scripts import workspace_status as ws

        cases = (("resolve", PermissionError("denied")),
                 ("resolve", RuntimeError("symlink loop")),
                 ("exists", OSError("probe failed")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for method, error in cases:
                for hard in ([], ["organization"]):
                    with self.subTest(method=method, error=type(error).__name__, hard=hard):
                        buf = io.StringIO()
                        with patch.object(ws.Path, method, side_effect=error), \
                             patch.object(ws, "_run_gates", return_value={}), \
                             patch.object(ws, "_health_decision", return_value=("degraded" if hard else "healthy", hard[:], [])), \
                             patch.object(ws, "_routing_brief", return_value={}), \
                             patch.object(ws, "_correctness_status", return_value={"status": "current"}), \
                             contextlib.redirect_stdout(buf):
                            result = ws._uncommitted_work(root)
                            code = ws.main(project_root=root, argv=["--fast"])
                        self.assertEqual(result, {"status": "unavailable", "reason": type(error).__name__})
                        brief = json.loads(buf.getvalue())
                        self.assertEqual(code, 1 if hard else 0)
                        self.assertEqual(brief["health"]["hard_failures"], hard)
                        self.assertEqual(brief["uncommitted_work"], result)
                        self.assertNotIn("uncommitted_work_aged", brief["health"]["warnings"])

    def test_compact_output_discloses_actual_capped_examination(self) -> None:
        import contextlib
        import io
        from unittest.mock import patch
        from scripts import workspace_status as ws

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("one.txt", "two.txt"):
                (root / name).write_text("new", encoding="utf-8")
            # Keep the real helper and parser; only the Git boundary is synthetic.
            (root / ".git").mkdir()
            response = subprocess.CompletedProcess([], 0, b"?? one.txt\0?? two.txt\0", b"")
            buf = io.StringIO()
            with patch.object(ws, "UNCOMMITTED_WORK_STAT_LIMIT", 1), \
                 patch.object(ws.subprocess, "run", return_value=response), \
                 patch.object(ws, "_run_gates", return_value={}), \
                 patch.object(ws, "_health_decision", return_value=("healthy", [], [])), \
                 patch.object(ws, "_routing_brief", return_value={}), \
                 patch.object(ws, "_correctness_status", return_value={"status": "current"}), \
                 contextlib.redirect_stdout(buf):
                code = ws.main(project_root=root, argv=["--fast", "--compact"])
            brief = json.loads(buf.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(brief["uncommitted_work"]["changed_count"], 2)
            self.assertEqual(brief["uncommitted_work"]["examined_count"], 1)
            self.assertTrue(brief["uncommitted_work"]["truncated"])

    def test_compact_brief_carries_the_uncommitted_summary(self) -> None:
        dirty = {"status": "dirty", "changed_count": 2, "aged_count": 0, "oldest_age_hours": 0.5, "aged": []}
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, (
            patch.object(workspace_status, "GATES", [])
        ), patch.object(workspace_status, "_uncommitted_work", return_value=dirty), patch.object(
            workspace_status, "_correctness_status", return_value={"status": "current"}
        ), redirect_stdout(stdout):
            code = workspace_status.main(Path(tmp), argv=["--compact"])
        compact = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(compact["uncommitted_work"]["changed_count"], 2)
        self.assertNotIn("uncommitted_work_aged", compact["health"]["warnings"])


if __name__ == "__main__":
    unittest.main()
