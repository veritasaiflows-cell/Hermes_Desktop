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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
PYTHON = sys.executable

DEFAULT_GATES: list[tuple[str, list[str], int]] = [
    ("routing", ["scripts/workflow_router.py", "--all", "--answer", "summary"], 120),
    ("wiki", ["scripts/wiki_bootstrap.py", "validate"], 120),
    ("alias", ["scripts/cron_alias_sweep.py"], 60),
    ("cron_registration", ["scripts/cron_registration_validator.py"], 60),
    ("claim_drift", ["scripts/cron_claim_drift_check.py"], 60),
    ("graph_integrity", ["scripts/graph_memory.py", "validate"], 120),
    ("graph_freshness", ["scripts/cron_graph_freshness.py"], 120),
    ("vector_memory", ["scripts/vector_memory_index.py", "status"], 120),
    ("archive_stale", ["scripts/cron_archive_stale_workflows.py"], 120),
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


def _health_decision(gates: dict[str, dict]) -> tuple[str, list[str], list[str]]:
    hard_labels = {
        "routing",
        "alias",
        "cron_registration",
        "graph_integrity",
        "graph_freshness",
        "vector_memory",
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
    gates = {
        label: _run(label, args, timeout, project_root=project_root)
        for label, args, timeout in GATES
    }
    decision, hard, warnings = _health_decision(gates)
    routing_brief = _routing_brief(gates.get("routing", {}))

    # Use the project_root for git and workflows in tests; production still
    # resolves through the default PROJECT_ROOT.
    git_head = _git_head() if project_root == PROJECT_ROOT else {"branch": "unknown", "commit_short": "unknown", "status_lines": []}
    workflows = _load_workflows() if project_root == PROJECT_ROOT else []
    recent = _recent_commit() if project_root == PROJECT_ROOT else []

    brief = {
        "schema": "workspace-status.v1",
        "generated_at": now,
        "git": git_head,
        "recent_commits": recent,
        "active_workflows": workflows,
        "routing_brief": routing_brief,
        "gates": gates,
        "health": {
            "status": decision,
            "hard_failures": hard,
            "warnings": warnings,
        },
        "recommended_next_action": (
            "Investigate hard failures before any workspace mutation."
            if hard
            else "Workspace is healthy; next action depends on the active workflow routing brief."
        ),
    }

    print(json.dumps(brief, indent=2, default=str))
    return 0 if not hard else 1


if __name__ == "__main__":
    raise SystemExit(main())
