#!/usr/bin/env python3
"""A7: Authoritative queue hygiene sweep.

Scans active_workflows.json for workflows whose lifecycle is in a terminal
state and whose updated_at is older than a threshold. Archives the removed
entries into state/archive/active_workflows/YYYY-MM-DD.json (rotating the
prior backup if one already exists for the same day), then regenerates
active_workflows.json without those entries.

This prevents the authoritative queue from growing unbounded while keeping
an audit trail of removed entries.

Terminal states are defined in TERMINAL_STATES and default threshold is 30
days. The authoritative queue is mutated, so this job should run weekly
and alert on any failure.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "state" / "archive" / "active_workflows"
DEFAULT_STALE_DAYS = 30
TERMINAL_STATES = {"closed", "closed_with_follow_up", "blocked", "gated", "on_hold"}


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with optional ``Z``) into a UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _archive_removed(archive_dir: Path, removed: list[dict], now: datetime) -> Path:
    """Write (and rotate) the daily archive of removed workflow entries."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    day_label = now.strftime("%Y-%m-%d")
    archive_path = archive_dir / f"{day_label}.json"

    # Rotate if a same-day archive already exists (e.g., multiple runs).
    if archive_path.exists():
        prior = json.loads(archive_path.read_text(encoding="utf-8"))
        removed.extend(prior.get("removed_workflows", []))

    payload = {
        "archived_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archived_by": "cron_queue_hygiene",
        "threshold_days": DEFAULT_STALE_DAYS,
        "terminal_states": sorted(TERMINAL_STATES),
        "removed_workflows": removed,
    }
    archive_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return archive_path


def main() -> int:
    """Remove terminal workflows older than the threshold and archive them."""
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    active_path = DEFAULT_STATE_DIR / "active_workflows.json"
    if not active_path.exists():
        print(f"QUEUE HYGIENE OK {now_iso} no_active_queue", file=sys.stderr)
        return 0

    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"QUEUE HYGIENE FAIL {now_iso} parse_error={exc}")
        return 1

    workflows = active.get("workflows", [])
    if isinstance(workflows, dict):
        workflow_list = list(workflows.values())
        list_format = False
    else:
        workflow_list = list(workflows)
        list_format = True

    kept: list[dict] = []
    removed: list[dict] = []

    for wf in workflow_list:
        state = wf.get("lifecycle", wf.get("state", "unknown"))
        updated_at = _parse_timestamp(wf.get("updated_at") or wf.get("created_at"))

        if state in TERMINAL_STATES and updated_at is not None:
            age_days = (now - updated_at).total_seconds() / 86400
            if age_days >= DEFAULT_STALE_DAYS:
                removed.append(
                    {
                        "workflow_id": wf.get("workflow_id"),
                        "state": state,
                        "age_days": round(age_days, 1),
                        "updated_at": wf.get("updated_at") or wf.get("created_at"),
                    }
                )
                continue

        kept.append(wf)

    if not removed:
        print(
            f"QUEUE HYGIENE OK {now_iso} checked={len(workflow_list)} stale_threshold_days={DEFAULT_STALE_DAYS}",
            file=sys.stderr,
        )
        return 0

    archive_path = _archive_removed(DEFAULT_ARCHIVE_DIR, removed, now)

    new_active = dict(active)
    if list_format:
        new_active["workflows"] = kept
    else:
        new_active["workflows"] = {wf.get("workflow_id"): wf for wf in kept if wf.get("workflow_id")}

    new_active["generated_at"] = now_iso
    new_active["generated_by"] = active.get("generated_by", "control-plane-bootstrap")

    # Atomic-ish write: write to temp then replace.
    tmp_path = active_path.with_suffix(".json.new")
    tmp_path.write_text(json.dumps(new_active, indent=2), encoding="utf-8")
    shutil.move(str(tmp_path), str(active_path))

    print(f"QUEUE HYGIENE DEGRADED {now_iso}")
    print(
        json.dumps(
            {
                "removed_count": len(removed),
                "remaining_count": len(kept),
                "archive_path": str(archive_path),
                "removed_workflows": removed,
            },
            indent=2,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
