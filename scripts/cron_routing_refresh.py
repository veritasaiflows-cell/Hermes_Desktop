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


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed = subprocess.run(
        [
            PYTHON,
            "scripts/workflow_router.py",
            "--all",
            "--answer",
            "summary",
            "--validate",
            "--write-index",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        print(f"ROUTING REFRESH FAIL {now}")
        print("--- stdout ---")
        print(completed.stdout)
        print("--- stderr ---")
        print(completed.stderr)
        return 1

    try:
        report = json.loads(completed.stdout)
        stale = report.get("routing_index_stale", True)
    except json.JSONDecodeError:
        print(f"ROUTING REFRESH OK (unparsed) {now}")
        return 0

    if stale:
        print(f"ROUTING REFRESH DEGRADED {now}")
        print(json.dumps({"routing_index_stale": True}, indent=2))
        return 1

    print(f"ROUTING REFRESH OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
