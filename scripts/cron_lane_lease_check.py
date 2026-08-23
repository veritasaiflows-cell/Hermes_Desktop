#!/usr/bin/env python3
"""Lane lease-expiry watchdog.

Scans the concurrent-lane register for:
1. Leased/running lanes whose lease expires within the warning window.
2. Leased/running lanes whose lease has already expired (operating dead).

Alerts on stdout when drift is detected; silent on green.
Follows the cron_claim_drift_check.py A-series pattern: no_agent=True,
exit 0 when clean, exit 1 on degradation, stderr summary always.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = PROJECT_ROOT / "state" / "concurrent-lane-register.sqlite"
WARNING_MINUTES = 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scan(register: Path, now: datetime, warning_minutes: int) -> tuple[list[dict], list[dict]]:
    """Return (expiring_soon, already_expired) lanes."""
    expiring: list[dict] = []
    expired: list[dict] = []
    if not register.is_file():
        return expiring, expired

    connection = sqlite3.connect(f"file:{register}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT lane_id, owner, status, lease_expires_at FROM lanes "
            "WHERE status IN ('leased', 'running')"
        ).fetchall()
    finally:
        connection.close()

    warning_at = now + timedelta(minutes=warning_minutes)
    for row in rows:
        expiry = _parse_utc(row["lease_expires_at"])
        if expiry is None:
            expired.append(
                {
                    "lane_id": row["lane_id"],
                    "owner": row["owner"],
                    "status": row["status"],
                    "reason": "missing_lease_expiry",
                }
            )
            continue
        entry = {
            "lane_id": row["lane_id"],
            "owner": row["owner"],
            "status": row["status"],
            "lease_expires_at": row["lease_expires_at"],
        }
        if expiry <= now:
            expired.append({**entry, "reason": "lease_expired_while_active"})
        elif expiry <= warning_at:
            expiring.append(entry)
    return expiring, expired


def main() -> int:
    now = _utc_now()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        expiring, expired = _scan(DEFAULT_REGISTER, now, WARNING_MINUTES)
    except sqlite3.Error as exc:
        print(f"LANE LEASE FAIL {now_iso} error={exc}")
        return 1

    if expiring or expired:
        print(f"LANE LEASE DEGRADED {now_iso}")
        print(
            json.dumps(
                {
                    "expiring_soon": expiring,
                    "expired_active": expired,
                    "warning_minutes": WARNING_MINUTES,
                },
                indent=2,
            )
        )
        return 1

    print(f"LANE LEASE OK {now_iso}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
