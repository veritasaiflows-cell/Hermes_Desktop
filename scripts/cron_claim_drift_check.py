#!/usr/bin/env python3
"""A9: Claim drift and replay-time integrity monitor.

Scans the canonical database for:
1. Active claims that expire within the configured warning window.
2. Workflow-run claims whose stored result hash no longer matches the bundle.
3. Replay records marked as stale due to missing active claims.

Alerts on stdout when drift is detected; silent on green.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB

DEFAULT_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
CLAIM_WARNING_HOURS = 24


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with optional ``Z``) into a UTC datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _bundle_result_hash(result: dict) -> str:
    """Hash a workflow result bundle excluding its own ``bundle_sha256`` field."""
    payload = dict(result)
    payload.pop("bundle_sha256", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _check_expiring_claims(db: CanonicalDB, now: datetime, warning_hours: int) -> list[dict]:
    """Return active claims expiring within ``warning_hours`` of ``now``."""
    warning_until = now + timedelta(hours=warning_hours)
    rows = db.connection.execute(
        "SELECT claim_id, title, subject_type, subject_id, valid_until "
        "FROM claims "
        "WHERE status = 'active' AND valid_until IS NOT NULL AND valid_until <= ? "
        "ORDER BY valid_until",
        (warning_until.isoformat().replace("+00:00", "Z"),),
    ).fetchall()
    return [
        {
            "claim_id": row["claim_id"],
            "title": row["title"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "valid_until": row["valid_until"],
        }
        for row in rows
    ]


def _check_tampered_workflow_runs(db: CanonicalDB) -> list[dict]:
    """Return workflow runs whose stored bundle hash no longer matches their result."""
    rows = db.connection.execute(
        "SELECT run_id, workflow_id, run_key, result_json FROM workflow_runs"
    ).fetchall()
    tampered = []
    for row in rows:
        result = json.loads(row["result_json"] or "{}")
        stored_hash = result.get("bundle_sha256")
        if stored_hash is None:
            continue
        if _bundle_result_hash(result) != stored_hash:
            tampered.append(
                {
                    "run_id": row["run_id"],
                    "workflow_id": row["workflow_id"],
                    "run_key": row["run_key"],
                    "reason": "bundle_sha256 mismatch",
                }
            )
    return tampered


def _check_stale_replays(db: CanonicalDB) -> list[dict]:
    """Return workflow runs recorded as ``replayed_stale`` due to missing active claims."""
    rows = db.connection.execute(
        "SELECT run_id, workflow_id, run_key, result_json FROM workflow_runs"
    ).fetchall()
    stale = []
    for row in rows:
        result = json.loads(row["result_json"] or "{}")
        if result.get("mode") == "replayed_stale":
            stale.append(
                {
                    "run_id": row["run_id"],
                    "workflow_id": row["workflow_id"],
                    "run_key": row["run_key"],
                    "reason": result.get("freshness", {}).get(
                        "reason", "No active provenance claim"
                    ),
                }
            )
    return stale


def _claim_coverage(db: CanonicalDB) -> dict[str, int]:
    """Return claim counts by status so a green gate cannot hide an empty corpus.

    The drift checks below only inspect ``status='active'`` claims. When no
    active claim exists they scan an empty set and report OK — a green light
    over an unmeasured road. Surfacing the coverage counts alongside the result
    makes that distinction visible without changing exit semantics: an empty
    claims table is legitimate (workflows may be gated), but it must never be
    mistaken for verified coverage.
    """
    coverage: dict[str, int] = {}
    try:
        rows = db.connection.execute(
            "SELECT status, COUNT(*) AS n FROM claims GROUP BY status"
        ).fetchall()
    except sqlite3.Error:
        return {}
    for row in rows:
        coverage[str(row["status"])] = int(row["n"])
    coverage["total"] = sum(coverage.values())
    coverage["active"] = coverage.get("active", 0)
    return coverage


def main() -> int:
    """Scan for expiring claims, tampered runs, and stale replays; exit 1 on drift."""
    now = _utc_now()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    database_path = DEFAULT_DATABASE

    if not database_path.exists():
        print(f"CLAIM DRIFT OK {now_iso} no_database", file=sys.stderr)
        return 0

    try:
        with CanonicalDB(database_path, read_only=True) as db:
            expiring = _check_expiring_claims(db, now, CLAIM_WARNING_HOURS)
            tampered = _check_tampered_workflow_runs(db)
            stale = _check_stale_replays(db)
            coverage = _claim_coverage(db)
    except sqlite3.Error as exc:
        print(f"CLAIM DRIFT FAIL {now_iso} error={exc}")
        return 1

    if expiring or tampered or stale:
        print(f"CLAIM DRIFT DEGRADED {now_iso}")
        print(
            json.dumps(
                {
                    "expiring_claims": expiring,
                    "tampered_workflow_runs": tampered,
                    "stale_replays": stale,
                    "warning_hours": CLAIM_WARNING_HOURS,
                    "claim_coverage": coverage,
                },
                indent=2,
            )
        )
        return 1

    active = coverage.get("active", 0)
    total = coverage.get("total", 0)
    # Qualify the green result: OK over zero active claims is "nothing to check",
    # not "everything verified". Exit code is unchanged either way.
    scope = "no_active_claims" if active == 0 else f"active={active}"
    print(
        f"CLAIM DRIFT OK {now_iso} {scope} total_claims={total}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
