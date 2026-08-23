#!/usr/bin/env python3
"""Daily wiki freshness regen (A1).

Regenerates the wiki bootstrap manifest and validates the result. Prints a
single short line on success or a diagnostic block on failure. Exits 0 on
fresh publish, non-zero on any failure.

Intended use: `no_agent` cron, daily schedule. Silent when green -- the
A2 watchdog will catch any failure. When non-zero, the cron delivers the
diagnostic block to the user.
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
    """Publish and validate the wiki; exit 0 on fresh, non-zero on failure."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed = subprocess.run(
        [PYTHON, "scripts/wiki_bootstrap.py", "publish"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        print(f"WIKI REGEN FAIL {now}")
        print("--- stdout ---")
        print(completed.stdout)
        print("--- stderr ---")
        print(completed.stderr)
        return 1

    # Parse the publish report to extract a single status line.
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(f"WIKI REGEN OK (unparsed) {now}")
        return 0

    status = report.get("status", "unknown")
    if status != "fresh":
        print(f"WIKI REGEN DEGRADED {now} status={status}")
        print(json.dumps({"issues": report.get("issues", [])[:5]}, indent=2))
        return 1

    changed = len(report.get("changed_sources", []))
    # Success -> STDOUT EMPTY so a no_agent cron stays silent ("alert only on
    # failure"). Log line goes to stderr only.
    print(f"WIKI REGEN OK {now} changed_sources={changed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
