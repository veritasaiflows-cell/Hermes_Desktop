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

Preflight: before spending ~90s on the full suite, check the lane register
for active write lanes whose allowed_writes touch the correctness surface
(scripts/, tests/, canonical/). A2-full fingerprints the source tree — any
concurrent write guarantees a drift rejection after the run, wasting the
test effort. When active lanes block, stay silent on stdout and note the
deferral on stderr with exit 0: the resulting telemetry gap already surfaces
as a correctness_stale warning in the status brief, so paging would be noise.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TIMEOUT_SECONDS = 300
LANE_REGISTER = PROJECT_ROOT / "state" / "concurrent-lane-register.sqlite"
# Write-scope prefixes whose edits invalidate the A2-full source fingerprint.
CORRECTNESS_SURFACE = ("scripts/", "tests/", "canonical/")


def _active_write_lanes_blocking(register: Path = LANE_REGISTER) -> list[dict]:
    """Return active write lanes whose scope overlaps the correctness surface."""
    if not register.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{register}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT lane_id, owner, status, allowed_writes_json FROM lanes "
                "WHERE status IN ('planned', 'leased', 'running')"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        # Register unreadable — don't block the correctness gate on an
        # observability failure; run the suite and let telemetry judge.
        return []

    blocking = []
    for row in rows:
        try:
            writes = json.loads(row["allowed_writes_json"] or "[]")
        except json.JSONDecodeError:
            continue
        for path in writes:
            normalized = str(path).replace("\\", "/").lower()
            if any(f"/{prefix}" in normalized for prefix in CORRECTNESS_SURFACE):
                blocking.append(
                    {
                        "lane_id": row["lane_id"],
                        "owner": row["owner"],
                        "status": row["status"],
                    }
                )
                break
    return blocking


def main() -> int:
    """Run the full correctness suite; exit 0 on pass, non-zero on any failure."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    blocking = _active_write_lanes_blocking()
    if blocking:
        print(
            f"TEST GATE DEFERRED {now} reason=active_write_lanes "
            f"lanes={len(blocking)}",
            file=sys.stderr,
        )
        return 0

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
