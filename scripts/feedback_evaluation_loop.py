#!/usr/bin/env python3
"""Metadata-only feedback-to-evaluation loop for existing workspace surfaces.

This module deliberately reuses the canonical ``tasks`` and
``validation_results`` tables. It detects repeated, category-only telemetry
signals, records a human-review candidate, and captures a fixed baseline before
any human-approved candidate change is evaluated. It never edits production
harness code or changes model/provider configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB
from scripts.run_checks import _failure_ids_sha256, _pytest_failure_ids, _pytest_outcome_counts
from scripts.workspace_fingerprint import correctness_snapshot

DEFAULT_CANONICAL_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
DEFAULT_TURN_DATABASE = (
    Path.home() / "AppData" / "Local" / "hermes" / "telemetry" / "turn-metrics.sqlite"
)
DEFAULT_HISTORY_PATH = (
    PROJECT_ROOT / "derived" / "feedback-evaluation" / "history.jsonl"
)
DEFAULT_REPORT_PATH = PROJECT_ROOT / "derived" / "feedback-evaluation" / "latest.json"
MIN_REPEATED_SIGNAL_COUNT = 3
WINDOW_DAYS = 7
COHORT_ID = "feedback-harness-v2"
COHORT_TARGETS = (
    "tests/test_run_checks.py",
    "tests/test_workspace_status.py",
    "tests/test_cron_wrappers.py",
    "tests/test_cron_registration_validator.py",
    "tests/test_feedback_evaluation_loop.py",
)
COHORT_TIMEOUT_SECONDS = 300
REPORT_MAX_AGE_HOURS = 30
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

CohortRunner = Callable[[], dict[str, object]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_token(value: object) -> str | None:
    text = str(value or "").strip()
    return text if _TOKEN_RE.fullmatch(text) else None


def _candidate_id(signal_key: str) -> str:
    digest = hashlib.sha256(signal_key.encode("utf-8")).hexdigest()[:16]
    return f"feedback-candidate-{digest}"


def _days_between(earlier: str, later: str) -> float:
    """Signed whole-fractional days between two ISO-8601 timestamps."""
    return (
        _parse_timestamp(later) - _parse_timestamp(earlier)
    ).total_seconds() / 86400.0


def run_fixed_cohort(*, project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Run the stable, small feedback/harness cohort without persisting raw output."""
    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *COHORT_TARGETS],
            cwd=str(Path(project_root)),
            capture_output=True,
            text=True,
            timeout=COHORT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        snapshot = correctness_snapshot(Path(project_root))
        return {
            "cohort_id": COHORT_ID,
            "execution_category": (
                "timeout" if isinstance(error, subprocess.TimeoutExpired) else "launch_error"
            ),
            "exit_code": None,
            "python_version": list(sys.version_info[:3]),
            "result": "fail",
            "test_count": 0,
            "test_failure_count": 0,
            "duration_ms": (time.perf_counter_ns() - started) // 1_000_000,
            "source_fingerprint": snapshot["source_fingerprint"],
            "tested_commit": snapshot["tested_commit"],
            "failure_ids_sha256": _failure_ids_sha256([]),
        }
    duration_ms = (time.perf_counter_ns() - started) // 1_000_000
    outcomes = _pytest_outcome_counts(completed.stdout)
    failures = _pytest_failure_ids(completed.stdout)
    snapshot = correctness_snapshot(Path(project_root))
    cohort_passed = (
        completed.returncode == 0
        and outcomes["test_count"] > 0
        and outcomes["test_failure_count"] == 0
    )
    if cohort_passed:
        execution_category = "none"
    elif completed.returncode == 1 and "No module named pytest" in completed.stderr:
        execution_category = "pytest_unavailable"
    elif completed.returncode == 2:
        execution_category = "collection_or_interruption"
    elif completed.returncode in (0, 5) and outcomes["test_count"] == 0:
        execution_category = "no_tests"
    elif outcomes["test_failure_count"] > 0:
        execution_category = "tests_failed"
    else:
        execution_category = "runner_error"
    return {
        "cohort_id": COHORT_ID,
        "execution_category": execution_category,
        "exit_code": completed.returncode,
        "python_version": list(sys.version_info[:3]),
        "result": "pass" if cohort_passed else "fail",
        "test_count": outcomes["test_count"],
        "test_failure_count": outcomes["test_failure_count"],
        "duration_ms": duration_ms,
        "source_fingerprint": snapshot["source_fingerprint"],
        "tested_commit": snapshot["tested_commit"],
        "failure_ids_sha256": _failure_ids_sha256(failures),
    }


def _turn_error_signals(
    turn_database: Path,
    *,
    now: str,
    min_count: int = MIN_REPEATED_SIGNAL_COUNT,
) -> list[dict[str, object]]:
    """Return repeated, safe category signals from profile-local turn telemetry."""
    if not turn_database.is_file():
        return []
    cutoff = (_parse_timestamp(now) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    connection = sqlite3.connect(turn_database)
    try:
        rows = connection.execute(
            "SELECT error_category, api_error_count FROM turn_metrics "
            "WHERE completed_at >= ? AND api_error_count > 0",
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()

    counts: dict[str, int] = {}
    affected_turns: dict[str, int] = {}
    for error_category, api_error_count in rows:
        for category in str(error_category or "").split(","):
            token = _safe_token(category)
            if token is None:
                continue
            counts[token] = counts.get(token, 0) + max(1, int(api_error_count or 0))
            affected_turns[token] = affected_turns.get(token, 0) + 1

    return [
        {
            "signal_key": f"turn_api_error:{category}",
            "source": "turn_telemetry",
            "category": category,
            "occurrences": count,
            "affected_turns": affected_turns[category],
            "recommendation": "review_provider_recovery",
        }
        for category, count in sorted(counts.items())
        if count >= min_count
    ]


def _turn_tool_error_signals(
    turn_database: Path,
    *,
    now: str,
    min_count: int = MIN_REPEATED_SIGNAL_COUNT,
) -> list[dict[str, object]]:
    """Return repeated normalized tool failures across at least two turns."""
    if not turn_database.is_file():
        return []
    cutoff = (_parse_timestamp(now) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    connection = sqlite3.connect(turn_database)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(turn_metrics)").fetchall()
        }
        if "tool_error_categories_json" not in columns:
            return []
        rows = connection.execute(
            "SELECT tool_error_categories_json FROM turn_metrics "
            "WHERE completed_at >= ? AND tool_error_count > 0",
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()

    counts: dict[str, int] = {}
    affected_turns: dict[str, int] = {}
    for (value,) in rows:
        try:
            categories = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(categories, dict):
            continue
        seen_in_turn: set[str] = set()
        for raw_category, raw_count in categories.items():
            category = _safe_token(raw_category)
            if (
                category is None
                or category.endswith("_blocked")
                or not isinstance(raw_count, int)
                or isinstance(raw_count, bool)
                or raw_count <= 0
            ):
                continue
            counts[category] = counts.get(category, 0) + raw_count
            seen_in_turn.add(category)
        for category in seen_in_turn:
            affected_turns[category] = affected_turns.get(category, 0) + 1

    return [
        {
            "signal_key": f"turn_tool_error:{category}",
            "source": "turn_telemetry",
            "category": category,
            "occurrences": count,
            "affected_turns": affected_turns[category],
            "recommendation": "review_tool_reliability",
        }
        for category, count in sorted(counts.items())
        if count >= min_count and affected_turns.get(category, 0) >= 2
    ]


def _turn_dropped_diagnostics_signals(
    turn_database: Path,
    *,
    now: str,
    min_count: int = MIN_REPEATED_SIGNAL_COUNT,
) -> list[dict[str, object]]:
    """Return a signal when the loop is silently dropping error diagnostics.

    Dropped diagnostics are an observability blind spot: the loop can look
    healthy while losing the evidence it needs to classify failures. Only
    turns with actual errors are counted, so success-diagnostic drops alone
    do not create review candidates.
    """
    if not turn_database.is_file():
        return []
    cutoff = (_parse_timestamp(now) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    connection = sqlite3.connect(turn_database)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(turn_metrics)").fetchall()
        }
        if "tool_error_diagnostics_dropped_count" not in columns:
            return []
        rows = connection.execute(
            "SELECT tool_error_diagnostics_dropped_count FROM turn_metrics "
            "WHERE completed_at >= ? AND tool_error_count > 0 "
            "AND tool_error_diagnostics_dropped_count > 0",
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()

    occurrences = sum(max(0, int(row[0] or 0)) for row in rows)
    affected_turns = len(rows)
    if occurrences < min_count or affected_turns < 2:
        return []
    return [
        {
            "signal_key": "turn_telemetry_dropped:dropped_error_diagnostics",
            "source": "turn_telemetry",
            "category": "dropped_error_diagnostics",
            "occurrences": occurrences,
            "affected_turns": affected_turns,
            "recommendation": "review_telemetry_observability",
        }
    ]


def _canonical_error_signals(
    canonical_database: Path,
    *,
    now: str,
    min_count: int = MIN_REPEATED_SIGNAL_COUNT,
) -> list[dict[str, object]]:
    """Return repeated, category-only failure signals from canonical run metrics."""
    cutoff = (_parse_timestamp(now) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with CanonicalDB(Path(canonical_database), read_only=True) as db:
        rows = db.connection.execute(
            "SELECT errors_json FROM run_metrics WHERE started_at >= ?",
            (cutoff,),
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            errors = json.loads(row[0] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(errors, list):
            continue
        for error in errors:
            token = _safe_token(error)
            if token is not None:
                counts[token] = counts.get(token, 0) + 1
    recommendation_by_category = {
        "unit_tests_failed": "review_regression_failure",
        "wiki_not_fresh": "review_wiki_freshness",
    }
    return [
        {
            "signal_key": f"run_error:{category}",
            "source": "run_metrics",
            "category": category,
            "occurrences": count,
            "affected_turns": count,
            "recommendation": recommendation_by_category.get(
                category, "review_repeated_run_error"
            ),
        }
        for category, count in sorted(counts.items())
        if count >= min_count
    ]


RECOMMENDATION_SURFACE_BY_CATEGORY: dict[str, str] = {
    "APIConnectionError": "provider_config",
    "RateLimitError": "provider_config",
    "NotFoundError": "provider_config",
    "skill_manage_error": "skill_procedure",
    "skill_view_error": "skill_procedure",
    "read_file_missing_path": "skill_procedure",
    "search_files_invalid_regex": "skill_procedure",
    "terminal_error": "skill_procedure",
    "terminal_timeout": "skill_procedure",
    "patch_error": "tool_wrapper",
    "web_extract_error": "tool_wrapper",
    "web_search_error": "tool_wrapper",
    "execute_code_error": "tool_wrapper",
    "unit_tests_failed": "run_pipeline",
    "wiki_not_fresh": "run_pipeline",
    "dropped_error_diagnostics": "telemetry_observability",
}

_SURFACE_FALLBACK_BY_PREFIX: dict[str, str] = {
    "turn_tool_error:": "tool_wrapper",
    "turn_api_error:": "provider_config",
    "run_error:": "run_pipeline",
    "turn_telemetry_dropped:": "telemetry_observability",
}

_SURFACE_SUMMARY: dict[str, str] = {
    "provider_config": (
        "Provider-level failure; review retry/backoff/provider policy. "
        "Human-gated: no autonomous model/provider changes."
    ),
    "skill_procedure": (
        "Behavioral failure class; capture the working procedure and its "
        "pitfalls into the governing skill so future sessions pre-check it."
    ),
    "tool_wrapper": (
        "Structural tool failure; fix the wrapper (pre-checks, retry, "
        "fallback) rather than individual call sites."
    ),
    "run_pipeline": (
        "Pipeline-level failure; review the failing check and its upstream "
        "producer before changing harness code."
    ),
    "telemetry_observability": (
        "The loop is losing diagnostics; raise retention limits or fix "
        "collection before trusting silence as success."
    ),
}


def _signal_recommendations(signals: list[dict[str, object]]) -> list[dict[str, object]]:
    """Route repeated signals to improvement surfaces with a review priority.

    Metadata-only: this never edits skills, harness code, or provider
    configuration; it labels where a human-reviewed change should happen.
    """
    recommendations: list[dict[str, object]] = []
    for signal in signals:
        signal_key = str(signal.get("signal_key") or "")
        category = str(signal.get("category") or "")
        surface = RECOMMENDATION_SURFACE_BY_CATEGORY.get(category)
        if surface is None:
            for prefix, fallback in _SURFACE_FALLBACK_BY_PREFIX.items():
                if signal_key.startswith(prefix):
                    surface = fallback
                    break
        if surface is None:
            surface = "tool_wrapper"
        occurrences = int(signal.get("occurrences") or 0)
        affected_turns = int(signal.get("affected_turns") or 0)
        recommendations.append(
            {
                "signal_key": signal_key,
                "category": category,
                "surface": surface,
                "summary": f"'{category}': {_SURFACE_SUMMARY[surface]}",
                "occurrences": occurrences,
                "affected_turns": affected_turns,
                "priority_score": occurrences * affected_turns,
                "recommendation": str(signal.get("recommendation") or ""),
            }
        )
    recommendations.sort(key=lambda item: -int(item["priority_score"]))
    rank = 0
    previous_score: int | None = None
    for index, item in enumerate(recommendations):
        score = int(item["priority_score"])
        if score != previous_score:
            rank = index + 1
            previous_score = score
        item["rank"] = rank
    return recommendations


def _existing_task(db: CanonicalDB, task_id: str) -> dict[str, object] | None:
    row = db.connection.execute(
        "SELECT task_id, task_type, status, scope, updated_at FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _run_cohort_evidence(cohort_runner: CohortRunner) -> dict[str, object]:
    evidence = dict(cohort_runner())
    required = {
        "cohort_id",
        "result",
        "test_count",
        "test_failure_count",
        "duration_ms",
        "source_fingerprint",
        "tested_commit",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"Baseline cohort result missing fields: {', '.join(missing)}")
    result = _safe_token(evidence["result"])
    if result not in {"pass", "fail"}:
        raise ValueError("Baseline cohort result must be 'pass' or 'fail'")
    evidence["result"] = result
    evidence["phase"] = "baseline"
    return evidence


def _write_baseline(
    db: CanonicalDB,
    *,
    candidate_id: str,
    cohort_runner: CohortRunner,
    now: str,
) -> dict[str, object]:
    evidence = _run_cohort_evidence(cohort_runner)
    _record_baseline_evidence(db, candidate_id=candidate_id, evidence=evidence, now=now)
    return evidence


def _record_baseline_evidence(
    db: CanonicalDB,
    *,
    candidate_id: str,
    evidence: dict[str, object],
    now: str,
) -> None:
    db.insert(
        "validation_results",
        {
            "subject_type": "improvement_candidate",
            "subject_id": candidate_id,
            "validator": "feedback_evaluation.baseline.v1",
            "result": evidence["result"],
            "evidence_json": json.dumps(evidence, sort_keys=True),
            "validated_at": now,
        },
    )
    db.update(
        "tasks",
        candidate_id,
        {"status": "baseline_recorded" if evidence["result"] == "pass" else "baseline_failed"},
    )


def rebaseline_candidate(
    *,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    candidate_id: str,
    reviewer: str,
    reason: str,
    cohort_runner: CohortRunner,
    now: str | None = None,
) -> dict[str, object]:
    """Explicitly capture a fresh baseline for a pending candidate.

    Rebaselining is human-invoked before a candidate change. It is unavailable
    once candidate evidence has been recorded, and preserves the replaced
    baseline ID in an auditable event.
    """
    reviewer_token = _safe_token(reviewer)
    reason_token = _safe_token(reason)
    if reviewer_token is None:
        raise ValueError("rebaseline requires a valid reviewer")
    if reason_token is None:
        raise ValueError("rebaseline requires a valid reason")
    recorded_at = now or _utc_now()
    with CanonicalDB(Path(canonical_database)) as db:
        task = _existing_task(db, candidate_id)
        if task is None:
            raise ValueError("candidate does not exist")
        if task["task_type"] != "improvement_candidate":
            raise ValueError("rebaseline requires a feedback improvement candidate")
        if task["scope"] != "feedback_evaluation":
            raise ValueError("rebaseline requires a feedback_evaluation candidate")
        if task["status"] not in {"candidate", "baseline_recorded", "baseline_failed"}:
            raise ValueError("rebaseline requires a pending candidate")
        previous_baseline = _latest_validation(
            db,
            candidate_id=candidate_id,
            validator="feedback_evaluation.baseline.v1",
        )
        # Run the cohort first (it can take minutes), then re-check the task
        # is still pending before any write, so a concurrent decision or
        # evaluation cannot be silently overwritten by this rebaseline.
        evidence = _run_cohort_evidence(cohort_runner)
        current = _existing_task(db, candidate_id)
        if current is None or current["status"] not in {
            "candidate",
            "baseline_recorded",
            "baseline_failed",
        }:
            raise ValueError("candidate is no longer pending; rebaseline aborted")
        _record_baseline_evidence(db, candidate_id=candidate_id, evidence=evidence, now=recorded_at)
        db.insert(
            "events",
            {
                "event_type": "feedback_candidate_rebaselined",
                "subject_type": "improvement_candidate",
                "subject_id": candidate_id,
                "payload_json": json.dumps(
                    {
                        "previous_baseline_validation_id": (
                            previous_baseline["validation_id"]
                            if previous_baseline is not None
                            else None
                        ),
                        "reviewer": reviewer_token,
                        "reason": reason_token,
                    },
                    sort_keys=True,
                ),
                "occurred_at": recorded_at,
                "recorded_at": recorded_at,
            },
        )
    return evidence


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _latest_validation(
    db: CanonicalDB,
    *,
    candidate_id: str,
    validator: str,
) -> dict[str, object] | None:
    row = db.connection.execute(
        "SELECT validation_id, result, evidence_json, validated_at FROM validation_results "
        "WHERE subject_type = ? AND subject_id = ? AND validator = ? "
        "ORDER BY validated_at DESC, rowid DESC LIMIT 1",
        ("improvement_candidate", candidate_id, validator),
    ).fetchone()
    return dict(row) if row is not None else None


def _validation_by_id(
    db: CanonicalDB,
    *,
    candidate_id: str,
    validator: str,
    validation_id: str,
) -> dict[str, object] | None:
    row = db.connection.execute(
        "SELECT validation_id, result, evidence_json, validated_at FROM validation_results "
        "WHERE validation_id = ? AND subject_type = ? AND subject_id = ? AND validator = ?",
        (validation_id, "improvement_candidate", candidate_id, validator),
    ).fetchone()
    return dict(row) if row is not None else None


def _comparison_is_acceptable(
    baseline_validation: dict[str, object], candidate_validation: dict[str, object]
) -> bool:
    try:
        baseline = json.loads(str(baseline_validation["evidence_json"]))
        candidate = json.loads(str(candidate_validation["evidence_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("evaluation evidence is invalid") from exc
    required = {"cohort_id", "test_count", "test_failure_count", "duration_ms"}
    if not all(isinstance(value, dict) and required.issubset(value) for value in (baseline, candidate)):
        raise ValueError("evaluation evidence is incomplete")
    if baseline["cohort_id"] != candidate["cohort_id"]:
        return False
    if candidate["test_count"] != baseline["test_count"]:
        return False
    if candidate["test_failure_count"] > baseline["test_failure_count"]:
        return False
    if baseline["duration_ms"] == 0:
        return candidate["duration_ms"] == 0
    return candidate["duration_ms"] <= baseline["duration_ms"] * 1.20


def evaluate_candidate(
    *,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    candidate_id: str,
    cohort_runner: CohortRunner,
    now: str | None = None,
) -> dict[str, object]:
    """Run the fixed cohort after a human has prepared a candidate change."""
    evidence = dict(cohort_runner())
    required = {
        "cohort_id",
        "result",
        "test_count",
        "test_failure_count",
        "duration_ms",
        "source_fingerprint",
        "tested_commit",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"Candidate cohort result missing fields: {', '.join(missing)}")
    result = _safe_token(evidence["result"])
    if result not in {"pass", "fail"}:
        raise ValueError("Candidate cohort result must be 'pass' or 'fail'")
    evidence["result"] = result
    evidence["phase"] = "candidate"
    validated_at = now or _utc_now()
    with CanonicalDB(Path(canonical_database)) as db:
        if _existing_task(db, candidate_id) is None:
            raise ValueError("candidate does not exist")
        baseline = _latest_validation(
            db,
            candidate_id=candidate_id,
            validator="feedback_evaluation.baseline.v1",
        )
        if baseline is None:
            raise ValueError("candidate evaluation requires baseline evidence")
        if baseline["result"] != "pass":
            raise ValueError("candidate evaluation requires a passing baseline")
        evidence["baseline_validation_id"] = baseline["validation_id"]
        db.insert(
            "validation_results",
            {
                "subject_type": "improvement_candidate",
                "subject_id": candidate_id,
                "validator": "feedback_evaluation.candidate.v1",
                "result": result,
                "evidence_json": json.dumps(evidence, sort_keys=True),
                "validated_at": validated_at,
            },
        )
        db.update("tasks", candidate_id, {"status": "evaluated"})
        db.insert(
            "events",
            {
                "event_type": "feedback_candidate_evaluated",
                "subject_type": "improvement_candidate",
                "subject_id": candidate_id,
                "payload_json": json.dumps(
                    {"cohort_id": evidence["cohort_id"], "result": result},
                    sort_keys=True,
                ),
                "occurred_at": validated_at,
                "recorded_at": validated_at,
            },
        )
    return evidence


def feedback_status(
    *,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    report_path: Path = DEFAULT_REPORT_PATH,
    now: str | None = None,
) -> dict[str, object]:
    """Return a compact, read-only freshness and review-status packet."""
    try:
        with CanonicalDB(Path(canonical_database), read_only=True) as db:
            rows = db.connection.execute(
                "SELECT task_id, status FROM tasks WHERE scope = ? "
                "AND status IN ('candidate', 'baseline_recorded', 'baseline_failed', 'evaluated') "
                "ORDER BY task_id",
                ("feedback_evaluation",),
            ).fetchall()
    except Exception:
        return {
            "schema": "feedback-evaluation-status.v1",
            "available": False,
            "status": "unavailable",
            "report_status": "unknown",
            "reason": "canonical_read_failed",
            "pending_review_count": 0,
            "candidate_ids": [],
            "failed_candidate_count": 0,
            "failed_candidate_ids": [],
        }
    candidate_ids = [str(row[0]) for row in rows]
    failed_candidate_ids = [str(row[0]) for row in rows if row[1] == "baseline_failed"]

    def combined_status(default: str) -> str:
        if failed_candidate_ids:
            return "failed"
        if candidate_ids:
            return "review_required"
        return default

    def report_problem(reason: str) -> dict[str, object]:
        return {
            "schema": "feedback-evaluation-status.v1",
            "available": False,
            "status": combined_status("unavailable"),
            "report_status": "unavailable",
            "reason": reason,
            "pending_review_count": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "failed_candidate_count": len(failed_candidate_ids),
            "failed_candidate_ids": failed_candidate_ids,
        }

    report = Path(report_path)
    if not report.is_file():
        return report_problem("report_missing")
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
        generated_at = payload["generated_at"]
        if payload.get("schema") != "feedback-evaluation-report.v1":
            raise ValueError("unexpected report schema")
        age_seconds = max(
            0,
            int((_parse_timestamp(now or _utc_now()) - _parse_timestamp(generated_at)).total_seconds()),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return report_problem("report_invalid")
    if age_seconds > REPORT_MAX_AGE_HOURS * 3600:
        return {
            "schema": "feedback-evaluation-status.v1",
            "available": True,
            "status": combined_status("stale"),
            "report_status": "stale",
            "generated_at": generated_at,
            "age_seconds": age_seconds,
            "pending_review_count": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "failed_candidate_count": len(failed_candidate_ids),
            "failed_candidate_ids": failed_candidate_ids,
        }
    return {
        "schema": "feedback-evaluation-status.v1",
        "available": True,
        "status": combined_status("ready"),
        "report_status": "fresh",
        "generated_at": generated_at,
        "age_seconds": age_seconds,
        "pending_review_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "failed_candidate_count": len(failed_candidate_ids),
        "failed_candidate_ids": failed_candidate_ids,
    }


def record_decision(
    *,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    candidate_id: str,
    decision: str,
    reviewer: str,
    now: str | None = None,
) -> str:
    """Record an explicit human review decision without applying any code change."""
    decision_token = _safe_token(decision)
    reviewer_token = _safe_token(reviewer)
    if decision_token not in {"accepted", "rejected"}:
        raise ValueError("decision must be 'accepted' or 'rejected'")
    if reviewer_token is None:
        raise ValueError("reviewer must be a short category token")
    recorded_at = now or _utc_now()

    with CanonicalDB(Path(canonical_database)) as db:
        task = _existing_task(db, candidate_id)
        if task is None:
            raise ValueError("candidate does not exist")
        if decision_token == "accepted":
            candidate = _latest_validation(
                db,
                candidate_id=candidate_id,
                validator="feedback_evaluation.candidate.v1",
            )
            if candidate is None:
                raise ValueError("accepted decision requires baseline and candidate evaluation")
            try:
                candidate_evidence = json.loads(str(candidate["evidence_json"]))
                baseline_validation_id = str(candidate_evidence["baseline_validation_id"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("candidate evaluation does not pin baseline evidence") from exc
            baseline = _validation_by_id(
                db,
                candidate_id=candidate_id,
                validator="feedback_evaluation.baseline.v1",
                validation_id=baseline_validation_id,
            )
            if baseline is None:
                raise ValueError("candidate evaluation references missing baseline evidence")
            if baseline["result"] != "pass" or candidate["result"] != "pass":
                raise ValueError("accepted decision requires passing evaluation evidence")
            if not _comparison_is_acceptable(baseline, candidate):
                raise ValueError("accepted decision requires an acceptable cohort comparison")

        decision_id = db.insert(
            "decisions",
            {
                "title": f"Feedback candidate decision: {candidate_id}",
                "decision": decision_token,
                "rationale": f"human_review:{reviewer_token}",
                "status": "recorded",
                "scope": "feedback_evaluation",
            },
        )
        db.update("tasks", candidate_id, {"status": decision_token})
        db.insert(
            "events",
            {
                "event_type": "feedback_candidate_decided",
                "subject_type": "improvement_candidate",
                "subject_id": candidate_id,
                "payload_json": json.dumps(
                    {"decision": decision_token, "reviewer": reviewer_token},
                    sort_keys=True,
                ),
                "occurred_at": recorded_at,
                "recorded_at": recorded_at,
            },
        )
    return decision_id


def _read_latest_history_counts(
    history_path: Path,
) -> tuple[dict[str, int] | None, str | None]:
    """Return (signal_counts, generated_at) of the newest valid history line."""
    if not history_path.is_file():
        return None, None
    try:
        lines = [
            line
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return None, None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            counts = payload.get("signal_counts")
            generated_at = payload.get("generated_at")
            if isinstance(counts, dict) and generated_at:
                return {
                    str(key): int(value) for key, value in counts.items()
                }, str(generated_at)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None, None


def _append_history_line(history_path: Path, entry: dict[str, object]) -> None:
    """Append one refresh snapshot to the history log (never rewritten)."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def refresh_loop(
    *,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    turn_database: Path = DEFAULT_TURN_DATABASE,
    report_path: Path = DEFAULT_REPORT_PATH,
    history_path: Path | None = None,
    cohort_runner: CohortRunner,
    now: str | None = None,
) -> dict[str, object]:
    """Aggregate repeated safe signals and record baseline-only review candidates.

    ``history_path`` is deliberately ``None``-defaulted: only the CLI (an
    explicit operator action) writes history, so library and test calls can
    never touch the production history file.
    """
    generated_at = now or _utc_now()
    history_target = Path(history_path) if history_path is not None else None
    signals = [
        *_canonical_error_signals(Path(canonical_database), now=generated_at),
        *_turn_error_signals(Path(turn_database), now=generated_at),
        *_turn_tool_error_signals(Path(turn_database), now=generated_at),
        *_turn_dropped_diagnostics_signals(Path(turn_database), now=generated_at),
    ]
    previous_counts = (
        _read_latest_history_counts(history_target)[0]
        if history_target is not None
        else None
    )
    candidates: list[dict[str, object]] = []
    created_count = 0
    baseline_cache: dict[str, object] | None = None

    def shared_baseline_runner() -> dict[str, object]:
        nonlocal baseline_cache
        if baseline_cache is None:
            baseline_cache = dict(cohort_runner())
        return dict(baseline_cache)

    with CanonicalDB(Path(canonical_database)) as db:
        for signal in signals:
            candidate_id = _candidate_id(str(signal["signal_key"]))
            task = _existing_task(db, candidate_id)
            if task is None:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": f"Feedback candidate: {signal['signal_key']}",
                        "description": "Repeated metadata-only signal; human review required.",
                        "task_type": "improvement_candidate",
                        "status": "candidate",
                        "scope": "feedback_evaluation",
                    },
                )
                db.insert(
                    "events",
                    {
                        "event_type": "feedback_candidate_created",
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "payload_json": json.dumps(signal, sort_keys=True),
                        "occurred_at": generated_at,
                        "recorded_at": generated_at,
                    },
                )
                created_count += 1
                task = {"task_id": candidate_id, "status": "candidate"}

            reopened = False
            if task["status"] == "accepted":
                continue

            if task["status"] == "rejected":
                # Rejections are terminal for the *decision*, not for the signal.
                # If the same category recurs beyond the rolling signal window,
                # reopen the candidate so a post-fix review can be measured
                # against fresh evidence. Within the window, treat rejection as
                # authoritative and skip.
                decided_at = str(task.get("updated_at") or "")
                if not decided_at or _days_between(decided_at, generated_at) < WINDOW_DAYS:
                    continue
                db.update(
                    "tasks",
                    candidate_id,
                    {"status": "candidate", "updated_at": generated_at},
                )
                db.insert(
                    "events",
                    {
                        "event_type": "feedback_candidate_reopened",
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "payload_json": json.dumps(
                            {
                                "previous_status": "rejected",
                                "rejected_at": decided_at,
                                "reopened_reason": "signal_recurred_after_window",
                                "signal": signal,
                            },
                            sort_keys=True,
                        ),
                        "occurred_at": generated_at,
                        "recorded_at": generated_at,
                    },
                )
                task = {"task_id": candidate_id, "status": "candidate"}
                reopened = True

            baseline = None
            latest_baseline = _latest_validation(
                db,
                candidate_id=candidate_id,
                validator="feedback_evaluation.baseline.v1",
            )
            baseline_cohort_id = None
            if latest_baseline is not None:
                try:
                    baseline_cohort_id = json.loads(
                        str(latest_baseline["evidence_json"])
                    ).get("cohort_id")
                except (TypeError, json.JSONDecodeError):
                    baseline_cohort_id = None
            baseline_is_current = bool(
                latest_baseline is not None
                and latest_baseline["result"] == "pass"
                and baseline_cohort_id == COHORT_ID
            )
            # A rejected candidate that recurs after the window begins a new
            # review cycle. Its prior cohort evidence belongs to the rejected
            # cycle, so the reopened candidate must receive a fresh baseline.
            if task["status"] != "evaluated" and (reopened or not baseline_is_current):
                baseline = _write_baseline(
                    db,
                    candidate_id=candidate_id,
                    cohort_runner=shared_baseline_runner,
                    now=generated_at,
                )
                task = {
                    "task_id": candidate_id,
                    "status": (
                        "baseline_recorded"
                        if baseline["result"] == "pass"
                        else "baseline_failed"
                    ),
                }
                latest_baseline = _latest_validation(
                    db,
                    candidate_id=candidate_id,
                    validator="feedback_evaluation.baseline.v1",
                )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "status": task["status"],
                    "signal": signal,
                    "baseline_recorded": bool(
                        latest_baseline is not None and latest_baseline["result"] == "pass"
                    ),
                }
            )

    failed_candidate_count = sum(
        1 for candidate in candidates if candidate["status"] == "baseline_failed"
    )
    signal_counts = (
        {str(signal_key): 0 for signal_key in previous_counts}
        if previous_counts is not None
        else {}
    )
    signal_counts.update(
        {
            str(signal["signal_key"]): int(signal["occurrences"])
            for signal in signals
        }
    )
    signal_trends: dict[str, dict[str, int | None]] = {}
    for signal_key, occurrences in signal_counts.items():
        previous = (
            None
            if previous_counts is None
            else int(previous_counts.get(signal_key, 0))
        )
        signal_trends[signal_key] = {
            "previous_occurrences": previous,
            "occurrences": occurrences,
            "delta": None if previous is None else occurrences - previous,
        }
    report: dict[str, object] = {
        "schema": "feedback-evaluation-report.v1",
        "generated_at": generated_at,
        "window_days": WINDOW_DAYS,
        "minimum_repeated_signal_count": MIN_REPEATED_SIGNAL_COUNT,
        "status": (
            "failed"
            if failed_candidate_count
            else "review_required" if candidates else "ready"
        ),
        "signals": signals,
        "signal_trends": signal_trends,
        "recommendations": _signal_recommendations(signals),
        "candidates": candidates,
        "created_candidate_count": created_count,
        "failed_candidate_count": failed_candidate_count,
        "source_paths": {
            "canonical_database": str(Path(canonical_database).resolve()),
            "turn_database": str(Path(turn_database).resolve()),
            "history": (
                str(history_target.resolve()) if history_target is not None else None
            ),
        },
    }
    if history_target is not None:
        _append_history_line(
            history_target,
            {
                "schema": "feedback-evaluation-history.v1",
                "generated_at": generated_at,
                "signal_counts": signal_counts,
            },
        )
    _atomic_write_json(Path(report_path), report)
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def add_storage_arguments(command: argparse.ArgumentParser, *, turn: bool = False) -> None:
        command.add_argument("--database", type=Path, default=DEFAULT_CANONICAL_DATABASE)
        command.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
        command.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
        if turn:
            command.add_argument("--turn-database", type=Path, default=DEFAULT_TURN_DATABASE)

    refresh = commands.add_parser("refresh", help="aggregate signals and record baseline-only candidates")
    add_storage_arguments(refresh, turn=True)

    status = commands.add_parser("status", help="read feedback/evaluation freshness and review state")
    add_storage_arguments(status)

    rebaseline = commands.add_parser(
        "rebaseline",
        help="explicitly renew a pending candidate baseline before candidate changes",
    )
    rebaseline.add_argument("candidate_id")
    rebaseline.add_argument("--reviewer", required=True)
    rebaseline.add_argument("--reason", required=True)
    rebaseline.add_argument("--database", type=Path, default=DEFAULT_CANONICAL_DATABASE)

    evaluate = commands.add_parser("evaluate", help="run the fixed cohort for one prepared candidate")
    evaluate.add_argument("candidate_id")
    evaluate.add_argument("--database", type=Path, default=DEFAULT_CANONICAL_DATABASE)

    decide = commands.add_parser("decide", help="record an explicit human decision after review")
    decide.add_argument("candidate_id")
    decide.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--database", type=Path, default=DEFAULT_CANONICAL_DATABASE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        if arguments.command == "refresh":
            payload = refresh_loop(
                canonical_database=arguments.database,
                turn_database=arguments.turn_database,
                report_path=arguments.report,
                history_path=arguments.history,
                cohort_runner=run_fixed_cohort,
            )
        elif arguments.command == "status":
            payload = feedback_status(
                canonical_database=arguments.database,
                report_path=arguments.report,
            )
        elif arguments.command == "rebaseline":
            payload = rebaseline_candidate(
                canonical_database=arguments.database,
                candidate_id=arguments.candidate_id,
                reviewer=arguments.reviewer,
                reason=arguments.reason,
                cohort_runner=run_fixed_cohort,
            )
        elif arguments.command == "evaluate":
            payload = evaluate_candidate(
                canonical_database=arguments.database,
                candidate_id=arguments.candidate_id,
                cohort_runner=run_fixed_cohort,
            )
        else:
            decision_id = record_decision(
                canonical_database=arguments.database,
                candidate_id=arguments.candidate_id,
                decision=arguments.decision,
                reviewer=arguments.reviewer,
            )
            payload = {
                "schema": "feedback-evaluation-decision.v1",
                "candidate_id": arguments.candidate_id,
                "decision": arguments.decision,
                "decision_id": decision_id,
            }
    except Exception as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}, sort_keys=True))
        return 1
    if (
        arguments.command == "rebaseline"
        and str(payload.get("result")) != "pass"
    ):
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
