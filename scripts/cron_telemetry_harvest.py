#!/usr/bin/env python3
"""A8: Feedback/evaluation refresh.

A2-full already records canonical correctness telemetry at 06:00. A8 runs after
it at 06:30, consumes both canonical and profile-local metadata-only telemetry,
creates baseline-only review candidates for repeated signals, and writes the
compact derived report. It never applies a harness change or changes runtime
model/provider settings.

Silent on ready; emits a compact candidate identifier when human review is
required; alerts on a failed or malformed refresh.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 360
PROOF_MAX_AGE_SECONDS = 48 * 3600


def _proof_note(project_root: Path) -> str | None:
    """Check the latest A2-full run_checks proof; return a note if unusable.

    Returns None when a fresh accepted proof exists, otherwise a short
    machine-readable reason. A8 aggregates the proof either way -- the note
    just makes staleness explicit instead of silent.
    """
    db_path = project_root / "canonical" / "efficiens.db"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT completed_at, verification_result, acceptance_status "
                "FROM run_metrics WHERE request_type = 'run_checks' "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return "proof_unavailable"
    if row is None:
        return "no_run_checks_proof"
    completed_at, verification_result, acceptance_status = row
    try:
        completed = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - completed).total_seconds()
    except (ValueError, TypeError):
        return "proof_timestamp_unparseable"
    if acceptance_status != "accepted" or verification_result != "pass":
        return f"proof_not_accepted status={acceptance_status} result={verification_result}"
    if age_seconds > PROOF_MAX_AGE_SECONDS:
        return f"proof_stale age_hours={age_seconds / 3600:.1f}"
    return None


def main() -> int:
    """Run the feedback/evaluation refresh; exit 1 on failure, 0 on ready or review-required."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    proof_note = _proof_note(PROJECT_ROOT)
    if proof_note is not None:
        # Log-only: A8 still aggregates, but a timed-out/deferred A2-full no
        # longer flows through silently.
        print(f"TELEMETRY PROOF NOTE {now} {proof_note}", file=sys.stderr)
    retention_command = [PYTHON, "scripts/telemetry_retention.py", "maintain"]
    if os.environ.get("HERMES_TELEMETRY_MONTHLY_ARCHIVES") == "1":
        retention_command.append("--archive-months")
    try:
        retention_completed = subprocess.run(
            retention_command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"TELEMETRY RETENTION FAIL {now} reason=timeout_after_{TIMEOUT_SECONDS}s")
        return 1
    except Exception as exc:  # pragma: no cover - defensive subprocess boundary
        print(f"TELEMETRY RETENTION FAIL {now} reason=spawn_error")
        print(type(exc).__name__)
        return 1

    if retention_completed.returncode != 0:
        print(f"TELEMETRY RETENTION FAIL {now} exit={retention_completed.returncode}")
        if retention_completed.stdout:
            print(retention_completed.stdout[-2000:])
        if retention_completed.stderr:
            print(retention_completed.stderr[-1000:])
        return 1
    try:
        retention_report = json.loads(retention_completed.stdout)
    except json.JSONDecodeError:
        print(f"TELEMETRY RETENTION FAIL {now} reason=invalid_report")
        return 1
    if (
        retention_report.get("schema") != "telemetry-retention-report.v1"
        or retention_report.get("status") != "ok"
    ):
        print(f"TELEMETRY RETENTION FAIL {now} reason=invalid_status")
        return 1
    print(
        f"TELEMETRY RETENTION OK {now} "
        f"rollups={retention_report.get('rollups_created', 0)} "
        f"deleted={retention_report.get('raw_rows_deleted', 0)} "
        f"size_deleted={retention_report.get('size_rows_deleted', 0)}",
        file=sys.stderr,
    )

    try:
        completed = subprocess.run(
            [PYTHON, "scripts/feedback_evaluation_loop.py", "refresh"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"FEEDBACK EVALUATION FAIL {now} reason=timeout_after_{TIMEOUT_SECONDS}s")
        return 1
    except Exception as exc:  # pragma: no cover - defensive subprocess boundary
        print(f"FEEDBACK EVALUATION FAIL {now} reason=spawn_error")
        print(type(exc).__name__)
        return 1

    if completed.returncode != 0:
        print(f"FEEDBACK EVALUATION FAIL {now} exit={completed.returncode}")
        if completed.stdout:
            print(completed.stdout[-2000:])
        if completed.stderr:
            print(completed.stderr[-1000:])
        return 1
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(f"FEEDBACK EVALUATION FAIL {now} reason=invalid_report")
        return 1

    if report.get("schema") != "feedback-evaluation-report.v1":
        print(f"FEEDBACK EVALUATION FAIL {now} reason=invalid_schema")
        return 1

    status = report.get("status")
    if status == "review_required":
        candidate_ids = [
            str(candidate.get("candidate_id"))
            for candidate in report.get("candidates", [])
            if isinstance(candidate, dict) and candidate.get("candidate_id")
        ][:10]
        if not candidate_ids:
            print(f"FEEDBACK EVALUATION FAIL {now} reason=missing_candidates")
            return 1
        print(
            f"FEEDBACK REVIEW REQUIRED {now} candidates={','.join(candidate_ids) or 'unknown'}"
        )
        return 0
    if status == "failed":
        print(
            f"FEEDBACK EVALUATION FAIL {now} "
            f"reason=baseline_failed count={report.get('failed_candidate_count', 0)}"
        )
        return 1
    if status == "ready":
        if report.get("candidates"):
            print(f"FEEDBACK EVALUATION FAIL {now} reason=inconsistent_ready_report")
            return 1
        print(f"FEEDBACK EVALUATION OK {now}", file=sys.stderr)
        return 0

    print(f"FEEDBACK EVALUATION FAIL {now} reason=unexpected_status")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
