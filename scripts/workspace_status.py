#!/usr/bin/env python3
"""Return a compact, single-command operating brief for the workspace.

This script consolidates the multi-gate status checks that previously required
many separate tool calls into one deterministic JSON output. It is intended for
agent startup use ("what is the current status?") and for cron/heartbeat runs.

It reads authoritative local sources only and does not mutate state.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.workspace_fingerprint import correctness_snapshot
from canonical.db import read_only_database_uri

DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
PYTHON = sys.executable

DEFAULT_GATES: list[tuple[str, list[str], int]] = [
    ("organization", ["scripts/workspace_organization_validator.py"], 60),
    (
        "routing",
        [
            "scripts/workflow_router.py",
            "--all",
            "--answer",
            "summary",
            "--validate",
            "--no-cache",
            "--skip-recall-context",
        ],
        120,
    ),
    ("wiki", ["scripts/wiki_bootstrap.py", "validate"], 120),
    ("alias", ["scripts/cron_alias_sweep.py"], 60),
    ("cron_registration", ["scripts/cron_registration_validator.py"], 60),
    ("feedback_evaluation", ["scripts/feedback_evaluation_loop.py", "status"], 60),
    (
        "lane_register",
        [
            "scripts/concurrent_lane_manager.py",
            "validate",
        ],
        60,
    ),
    ("claim_drift", ["scripts/cron_claim_drift_check.py"], 60),
    ("graph_integrity", ["scripts/graph_memory.py", "validate"], 120),
    ("graph_freshness", ["scripts/cron_graph_freshness.py"], 120),
    ("graphify_freshness", ["scripts/graphify_freshness.py"], 120),
    ("vector_memory", ["scripts/vector_memory_index.py", "status"], 120),
    ("workspace_index", ["scripts/workspace_index.py", "status"], 120),
    ("archive_stale", ["scripts/cron_archive_stale_workflows.py", "--check-only"], 120),
]
# Allow tests to inject a different gate list via the environment.
_GATES_ENV = os.environ.get("WORKSPACE_STATUS_GATES")
GATES: list[tuple[str, list[str], int]] = (
    json.loads(_GATES_ENV) if _GATES_ENV else DEFAULT_GATES
)


def _run(label: str, args: list[str], timeout: int, project_root: Path = PROJECT_ROOT) -> dict:
    start = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            [PYTHON, *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        stdout = completed.stdout.strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = stdout
        return {
            "label": label,
            "exit": completed.returncode,
            "elapsed_ms": elapsed_ms,
            "stdout": parsed,
            "stderr_tail": completed.stderr.strip().splitlines()[-3:] if completed.stderr.strip() else [],
        }
    except subprocess.TimeoutExpired:
        return {
            "label": label,
            "exit": 124,
            "elapsed_ms": timeout * 1000,
            "stdout": None,
            "stderr_tail": [f"timeout after {timeout}s"],
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "label": label,
            "exit": 125,
            "elapsed_ms": 0,
            "stdout": None,
            "stderr_tail": [str(exc)],
        }


def _run_gates(
    gates: list[tuple[str, list[str], int]],
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, dict]:
    """Run gates serially to avoid shared SQLite and artifact races."""
    results: dict[str, dict] = {}
    for label, args, timeout in gates:
        results[label] = _run(
            label,
            args,
            timeout,
            project_root=project_root,
        )
    return results


def _git_head() -> dict:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        short = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return {"branch": branch, "commit_short": short, "status_lines": status.splitlines()}
    except Exception as exc:
        return {"error": str(exc)}


def _load_workflows() -> list[dict]:
    path = DEFAULT_STATE_DIR / "active_workflows.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        workflows = data.get("workflows", [])
        if isinstance(workflows, dict):
            workflows = list(workflows.values())
        return workflows
    except Exception:
        return []


def _routing_brief(routing_gate: dict) -> dict:
    stdout = routing_gate.get("stdout") or {}
    workflows = stdout.get("workflows", [])
    return {
        "index_path": stdout.get("index_path"),
        "routing_index_stale": stdout.get("routing_index_stale", True),
        "unsafe_to_trust": stdout.get("unsafe_to_trust", True),
        "routing_freshness": stdout.get("routing_freshness"),
        "workflows": [
            {
                "workflow_id": w.get("workflow_id"),
                "display_name": w.get("display_name"),
                "effective_status": w.get("effective_status"),
                "blocker_count": w.get("blocker_count", 0),
                "owner_action_required": w.get("owner_action_required"),
                "next_action": w.get("next_action"),
            }
            for w in workflows
        ],
    }


def _correctness_status(project_root: Path) -> dict[str, Any]:
    """Return the latest full-test outcome and whether it matches current sources."""
    database_path = Path(project_root).resolve() / "canonical" / "efficiens.db"
    if not database_path.is_file():
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": "canonical_database_missing",
        }
    try:
        uri = read_only_database_uri(database_path)
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT started_at, completed_at, duration_ms, resource_usage_json, "
                "verification_result, acceptance_status "
                "FROM run_metrics WHERE request_type = 'run_checks' "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": f"telemetry_read_failed:{type(exc).__name__}",
        }
    if row is None:
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": "no_run_checks_telemetry",
        }
    try:
        resource = json.loads(row["resource_usage_json"] or "{}")
    except json.JSONDecodeError:
        resource = None
    if not isinstance(resource, dict):
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": "correctness_telemetry_invalid",
        }
    recorded_fingerprint = resource.get("source_fingerprint")
    test_count = resource.get("test_count")
    test_failure_count = resource.get("test_failure_count")
    source_file_count = resource.get("source_file_count")
    tested_commit = resource.get("tested_commit")
    telemetry_complete = (
        isinstance(recorded_fingerprint, str)
        and len(recorded_fingerprint) == 64
        and all(character in "0123456789abcdef" for character in recorded_fingerprint)
        and type(test_count) is int
        and test_count >= 0
        and type(test_failure_count) is int
        and 0 <= test_failure_count <= test_count
        and type(source_file_count) is int
        and source_file_count >= 0
        and "tested_commit" in resource
        and (tested_commit is None or isinstance(tested_commit, str))
        and isinstance(row["completed_at"], str)
        and bool(row["completed_at"])
    )
    if not telemetry_complete:
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": "correctness_telemetry_incomplete",
        }
    try:
        current = correctness_snapshot(project_root)
    except (OSError, ValueError) as exc:
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": f"correctness_fingerprint_failed:{type(exc).__name__}",
        }
    stale = not recorded_fingerprint or recorded_fingerprint != current["source_fingerprint"]
    accepted = (
        row["verification_result"] == "pass"
        and row["acceptance_status"] == "accepted"
    )
    accepted_proof_is_valid = test_count > 0 and test_failure_count == 0
    current_proof_metadata_is_consistent = (
        source_file_count > 0
        and (stale or source_file_count == current["source_file_count"])
    )
    if accepted and not (
        accepted_proof_is_valid and current_proof_metadata_is_consistent
    ):
        return {
            "available": False,
            "status": "unavailable",
            "stale": True,
            "reason": "correctness_telemetry_inconsistent",
        }
    status = "failed" if not accepted else "stale" if stale else "current"
    return {
        "available": True,
        "status": status,
        "stale": stale,
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "duration_ms": row["duration_ms"],
        "verification_result": row["verification_result"],
        "acceptance_status": row["acceptance_status"],
        "test_count": resource.get("test_count"),
        "test_failure_count": resource.get("test_failure_count"),
        "tested_commit": resource.get("tested_commit"),
        "tested_source_fingerprint": recorded_fingerprint,
        "current_commit": current.get("tested_commit"),
        "current_source_fingerprint": current["source_fingerprint"],
        "source_file_count": current["source_file_count"],
    }


def _health_decision(gates: dict[str, dict]) -> tuple[str, list[str], list[str]]:
    hard_labels = {
        "organization",
        "routing",
        "alias",
        "cron_registration",
        "graph_integrity",
        "graph_freshness",
        "vector_memory",
        "workspace_index",
        "archive_stale",
    }
    hard_failures: list[str] = []
    warnings: list[str] = []

    for label in hard_labels:
        gate = gates.get(label, {})
        if gate.get("exit", 1) != 0:
            hard_failures.append(label)

    wiki_gate = gates.get("wiki", {})
    if wiki_gate.get("exit", 1) == 0:
        wiki_status = None
        stdout = wiki_gate.get("stdout")
        if isinstance(stdout, dict):
            wiki_status = stdout.get("status")
        if wiki_status == "stale":
            warnings.append("wiki_stale")
    else:
        hard_failures.append("wiki")

    claim_gate = gates.get("claim_drift", {})
    if claim_gate.get("exit", 1) != 0:
        warnings.append("claim_drift")

    lane_gate = gates.get("lane_register", {})
    if lane_gate:
        if lane_gate.get("exit", 1) != 0:
            warnings.append("lane_register_degraded")
        else:
            lane_stdout = lane_gate.get("stdout")
            if isinstance(lane_stdout, dict):
                expired = lane_stdout.get("expired_leases") or []
                collisions = lane_stdout.get("collisions") or []
                lane_hard_failures = lane_stdout.get("hard_failures") or []
                if expired:
                    warnings.append("lane_lease_expired")
                if collisions:
                    warnings.append("lane_collision")
                if lane_hard_failures:
                    warnings.append("lane_register_hard_failures")

    feedback_gate = gates.get("feedback_evaluation", {})
    if feedback_gate:
        if feedback_gate.get("exit", 1) != 0:
            warnings.append("feedback_evaluation_unavailable")
        else:
            feedback = feedback_gate.get("stdout")
            feedback_status = feedback.get("status") if isinstance(feedback, dict) else None
            report_status = feedback.get("report_status") if isinstance(feedback, dict) else None
            if feedback_status == "review_required":
                warnings.append("feedback_review_required")
            elif feedback_status == "failed":
                warnings.append("feedback_evaluation_failed")
            if report_status == "stale" or (
                report_status is None and feedback_status == "stale"
            ):
                warnings.append("feedback_evaluation_stale")
            elif report_status == "unavailable" or (
                report_status is None and feedback_status not in {"ready", "review_required"}
            ):
                warnings.append("feedback_evaluation_unavailable")

    if "graphify_freshness" in gates:
        graphify_gate = gates["graphify_freshness"]
        if graphify_gate.get("exit", 1) != 0:
            stdout = graphify_gate.get("stdout")
            graphify_status = stdout.get("status") if isinstance(stdout, dict) else None
            warnings.append(
                "graphify_stale"
                if graphify_status == "stale"
                else "graphify_unavailable"
            )

    if hard_failures:
        return "degraded", hard_failures, warnings
    if warnings:
        return "healthy_with_warnings", [], warnings
    return "healthy", [], []


def _recent_commit(n: int = 3) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "log", "--oneline", "-n", str(n)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.stdout.strip().splitlines()
    except Exception:
        return []


def main(project_root: Path = PROJECT_ROOT) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gates = _run_gates(GATES, project_root=project_root)
    decision, hard, warnings = _health_decision(gates)
    routing_brief = _routing_brief(gates.get("routing", {}))
    correctness = _correctness_status(Path(project_root))
    feedback_evaluation = gates.get("feedback_evaluation", {}).get("stdout")
    if correctness.get("status") != "current":
        correctness_warning = (
            "correctness_failed"
            if correctness.get("status") == "failed"
            else (
                "correctness_stale"
                if correctness.get("status") == "stale"
                else "correctness_unavailable"
            )
        )
        warnings.append(correctness_warning)
        if not hard:
            decision = "healthy_with_warnings"

    # Use the project_root for git and workflows in tests; production still
    # resolves through the default PROJECT_ROOT.
    git_head = _git_head() if project_root == PROJECT_ROOT else {"branch": "unknown", "commit_short": "unknown", "status_lines": []}
    workflows = _load_workflows() if project_root == PROJECT_ROOT else []
    recent = _recent_commit() if project_root == PROJECT_ROOT else []

    if hard:
        recommended_next_action = "Investigate hard failures before any workspace mutation."
    elif correctness.get("status") in {"unavailable", "stale", "failed"}:
        recommended_next_action = (
            "Run python scripts/cron_test_gate.py to refresh the full correctness proof."
        )
    elif "graphify_stale" in warnings or "graphify_unavailable" in warnings:
        recommended_next_action = (
            "Build and validate an isolated candidate before promoting Graphify; "
            "do not write or baseline the MCP-served artifact in place."
        )
    elif "feedback_review_required" in warnings:
        recommended_next_action = (
            "Review the feedback-evaluation candidate before any promotion decision."
        )
    elif "feedback_evaluation_stale" in warnings or "feedback_evaluation_unavailable" in warnings:
        recommended_next_action = (
            "Run python scripts/cron_telemetry_harvest.py to refresh the feedback-evaluation report."
        )
    elif warnings:
        recommended_next_action = "Review workspace warnings before the next mutation."
    else:
        recommended_next_action = (
            "Workspace is healthy; next action depends on the active workflow routing brief."
        )

    brief = {
        "schema": "workspace-status.v1",
        "generated_at": now,
        "git": git_head,
        "recent_commits": recent,
        "active_workflows": workflows,
        "routing_brief": routing_brief,
        "correctness": correctness,
        "feedback_evaluation": feedback_evaluation,
        "gates": gates,
        "health": {
            "status": decision,
            "hard_failures": hard,
            "warnings": warnings,
        },
        "recommended_next_action": recommended_next_action,
    }

    print(json.dumps(brief, indent=2, default=str))
    return 0 if not hard else 1


if __name__ == "__main__":
    raise SystemExit(main())
