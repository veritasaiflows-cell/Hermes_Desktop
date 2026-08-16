#!/usr/bin/env python3
"""A8: Telemetry harvest.

Runs run_checks.py with --record-telemetry so the canonical run_metrics table
accumulates metadata even when no manual check run occurs. This is the scheduled
writer for the telemetry layer.

Silent on green; alerts on failure. The underlying run_checks.py telemetry path
is fail-safe, so a telemetry write failure does not change the exit code.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 600


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
        print(f"TELEMETRY HARVEST FAIL {now} reason=timeout_after_{TIMEOUT_SECONDS}s")
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"TELEMETRY HARVEST FAIL {now} reason=spawn_error")
        print(str(exc))
        return 1

    if completed.returncode != 0:
        print(f"TELEMETRY HARVEST FAIL {now} exit={completed.returncode}")
        if completed.stdout:
            print("--- stdout ---")
            print(completed.stdout[-4000:])
        if completed.stderr:
            print("--- stderr ---")
            print(completed.stderr[-2000:])
        return 1

    print(f"TELEMETRY HARVEST OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
