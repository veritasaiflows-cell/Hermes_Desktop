#!/usr/bin/env python3
"""Code-correctness gate (new A2-full / call it test-gate).

Runs the full unit-test and isolated-smoke suite. This is a correctness
regression guard, not an operational liveness probe. It is intentionally
slower than cron_health_check.py and should be scheduled less frequently
(e.g., daily or post-merge), not as a sub-minute heartbeat.

Exits 0 if the full suite passes, non-zero on any failure. Prints the
standard test runner output and a final summary line.

This script exists so that cron_health_check.py can remain a fast,
operational gate (routing + wiki + alias) while the correctness gate
keeps its own schedule.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 300


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        completed = subprocess.run(
            [PYTHON, "scripts/run_checks.py", "--skip-smoke", "--record-telemetry"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"TEST GATE FAIL {now} reason=timeout_after_{TIMEOUT_SECONDS}s")
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"TEST GATE FAIL {now} reason=spawn_error")
        print(str(exc))
        return 1

    if completed.returncode != 0:
        print(f"TEST GATE FAIL {now} exit={completed.returncode}")
        if completed.stdout:
            print("--- stdout ---")
            print(completed.stdout[-4000:])
        if completed.stderr:
            print("--- stderr ---")
            print(completed.stderr[-2000:])
        return 1

    # Green: silent on stdout; log to stderr only.
    print(f"TEST GATE OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
