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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 360


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
