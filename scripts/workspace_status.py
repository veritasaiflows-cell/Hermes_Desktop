#!/usr/bin/env python3
"""Return a compact, single-command operating brief for the workspace.

This script consolidates the multi-gate status checks that previously required
many separate tool calls into one deterministic JSON output. It is intended for
agent startup use ("what is the current status?") and for cron/heartbeat runs.

It reads authoritative local sources only and does not mutate state.
"""
from __future__ import annotations

import argparse
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

from scripts.workspace_fingerprint import correctness_snapshot, correctness_sources, git_commit
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

# Fast startup tier: the minimum gates for trustworthy session orientation.
# Heavier or heartbeat-covered gates (alias, cron_registration,
# feedback_evaluation, claim_drift, graph_*, graphify, vector_memory,
# workspace_index, archive_stale) stay in the full brief and the A11 cron
# heartbeat. The fast routing gate allows cache reads (no --no-cache) since
# A5 refreshes the index hourly.
FAST_GATE_LABELS = ("organization", "routing", "wiki", "lane_register")


def fast_gates() -> list[tuple[str, list[str], int]]:
    """Return the startup-tier subset of DEFAULT_GATES in declared order."""
    selected: list[tuple[str, list[str], int]] = []
    for label, args, timeout in DEFAULT_GATES:
        if label not in FAST_GATE_LABELS:
            continue
        if label == "routing":
            args = [arg for arg in args if arg != "--no-cache"]
        selected.append((label, args, timeout))
    return selected


def _is_stale_index_signal(stdout: Any) -> bool:
    """Return whether an index gate payload reports staleness, not corruption.

    Stale indexes have a source-direct fallback, so they warn; missing or
    corrupt indexes still fail hard.
    """
    if not isinstance(stdout, dict):
        return False
    if stdout.get("status") in {"stale", "degraded"}:
        return True
    stale_count = stdout.get("stale_source_count")
    return isinstance(stale_count, int) and stale_count > 0


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
    """Collect branch, short commit, and worktree status in two git forks."""
    try:
        status_proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        rev_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        status_lines = status_proc.stdout.strip().splitlines()
        branch = "unknown"
        if status_lines and status_lines[0].startswith("##"):
            branch = status_lines[0][2:].split("...")[0].strip()
            prefix = "No commits yet on "
            if branch.startswith(prefix):
                branch = branch[len(prefix):]
            if not branch:
                branch = "unknown"
        short = rev_proc.stdout.strip() if rev_proc.returncode == 0 else ""
        return {"branch": branch, "commit_short": short, "status_lines": status_lines}
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


_FINGERPRINT_CACHE_NAME = "workspace-fingerprint-cache.json"


def _fingerprint_cache_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "tmp" / _FINGERPRINT_CACHE_NAME


def _cached_correctness_snapshot(project_root: Path) -> dict[str, Any]:
    """Return correctness_snapshot(), reusing a cache when sources are untouched.

    The cache key is the per-file (mtime_ns, size) map over the fingerprinted
    sources. Content is re-hashed only when the file set or any mtime/size
    changes; a missing or corrupt cache fails open to a full recompute. The
    tested commit is always read fresh since it never affects the fingerprint.
    """
    root = Path(project_root).resolve()
    try:
        stats = {}
        for path in correctness_sources(root):
            file_stat = path.stat()
            stats[path.relative_to(root).as_posix()] = [file_stat.st_mtime_ns, file_stat.st_size]
    except (OSError, ValueError):
        return correctness_snapshot(project_root)
    cache_path = _fingerprint_cache_path(root)
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("files") == stats
            and isinstance(cached.get("source_fingerprint"), str)
            and cached.get("source_file_count") == len(stats)
        ):
            return {
                "source_fingerprint": cached["source_fingerprint"],
                "source_file_count": len(stats),
                "tested_commit": git_commit(root),
            }
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    snapshot = correctness_snapshot(project_root)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "source_fingerprint": snapshot["source_fingerprint"],
                    "source_file_count": snapshot["source_file_count"],
                    "files": stats,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return snapshot


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
        current = _cached_correctness_snapshot(project_root)
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
    # vector_memory and workspace_index are judged separately below: stale
    # indexes warn (source-direct fallback exists), missing/corrupt ones fail.
    hard_labels = {
        "organization",
        "routing",
        "alias",
        "cron_registration",
        "graph_integrity",
        "graph_freshness",
        "archive_stale",
    }
    hard_failures: list[str] = []
    warnings: list[str] = []

    for label in hard_labels:
        if label not in gates:
            continue  # tier skipped this gate; the A11 heartbeat still covers it
        gate = gates[label]
        if gate.get("exit", 1) != 0:
            hard_failures.append(label)

    for label in ("vector_memory", "workspace_index"):
        gate = gates.get(label)
        if gate is None:
            continue  # tier skipped this gate; the A11 heartbeat still covers it
        if gate.get("exit", 1) != 0:
            if _is_stale_index_signal(gate.get("stdout")):
                warnings.append(f"{label}_stale")
            else:
                hard_failures.append(label)
        else:
            stdout = gate.get("stdout")
            if isinstance(stdout, dict) and stdout.get("status") in {"stale", "degraded"}:
                warnings.append(f"{label}_stale")

    wiki_gate = gates.get("wiki")
    if wiki_gate is None:
        pass  # tier skipped this gate; the A11 heartbeat still covers it
    elif wiki_gate.get("exit", 1) == 0:
        wiki_status = None
        stdout = wiki_gate.get("stdout")
        if isinstance(stdout, dict):
            wiki_status = stdout.get("status")
        if wiki_status == "stale":
            warnings.append("wiki_stale")
    else:
        hard_failures.append("wiki")

    claim_gate = gates.get("claim_drift")
    if claim_gate is not None and claim_gate.get("exit", 1) != 0:
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


def _write_compact_full_brief(project_root: Path, brief: dict, now: str) -> str:
    """Persist the full brief for compact mode; returns the path (or a note)."""
    stamp = now.replace(":", "").replace("-", "")
    path = project_root / "tmp" / f"workspace-status-{stamp}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brief, indent=2, default=str), encoding="utf-8")
        return str(path)
    except OSError:
        return "<full brief unwritable>"


def main(project_root: Path = PROJECT_ROOT, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="run the startup tier only (organization, routing, wiki, "
        "lane_register); heavier gates stay on the A11 heartbeat",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="print a small summary (health, gate exits, next action) instead "
        "of the full brief; keeps scheduler error stores small",
    )
    args = parser.parse_args([] if argv is None else argv)
    # An explicit test gate list always wins over tier selection.
    use_fast = bool(args.fast) and not _GATES_ENV
    selected = fast_gates() if use_fast else GATES
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gates = _run_gates(selected, project_root=project_root)
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
        "mode": "fast" if use_fast else "full",
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

    if args.compact:
        full_path = _write_compact_full_brief(project_root, brief, now)
        compact = {
            "schema": "workspace-status-compact.v1",
            "mode": brief["mode"],
            "generated_at": now,
            "health": brief["health"],
            "gate_exits": {
                label: gate.get("exit", 1) for label, gate in gates.items()
            },
            "correctness": {
                key: correctness.get(key)
                for key in ("status", "stale", "test_count", "completed_at")
            },
            "recommended_next_action": recommended_next_action,
            "full_brief_path": full_path,
        }
        print(json.dumps(compact, indent=2, default=str))
    else:
        print(json.dumps(brief, indent=2, default=str))
    return 0 if not hard else 1


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
