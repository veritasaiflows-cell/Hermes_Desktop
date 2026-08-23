#!/usr/bin/env python3
"""A5: Routing index freshness refresh.

Lightweight cron that regenerates the workflow routing index and capsules
if any source is stale. This complements the on-demand router cache by
ensuring derived artifacts are ready before the next agent session.

Silent on green; alerts on failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 120


def _run_router(command: list[str], now: str):
    """Run one bounded router command and return ``None`` after a timeout."""
    try:
        return subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"ROUTING REFRESH FAIL {now} "
            f"reason=timeout_after_{TIMEOUT_SECONDS}s"
        )
        return None


def main() -> int:
    """Regenerate the routing index via the router; exit 1 on failure or stale output."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed = _run_router(
        [
            PYTHON,
            "scripts/workflow_router.py",
            "--all",
            "--answer",
            "summary",
            "--validate",
            "--write-index",
        ],
        now,
    )
    if completed is None:
        return 1
    if completed.returncode != 0:
        print(f"ROUTING REFRESH FAIL {now}")
        print("--- stdout ---")
        print(completed.stdout)
        print("--- stderr ---")
        print(completed.stderr)
        return 1

    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(f"ROUTING REFRESH FAIL {now} reason=invalid_report")
        return 1
    if not isinstance(report, dict):
        print(f"ROUTING REFRESH FAIL {now} reason=invalid_report")
        return 1
    stale = report.get("routing_index_stale", True)

    if stale:
        print(f"ROUTING REFRESH DEGRADED {now}")
        print(json.dumps({"routing_index_stale": True}, indent=2))
        return 1

    print(f"ROUTING REFRESH OK {now} action=regenerated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
