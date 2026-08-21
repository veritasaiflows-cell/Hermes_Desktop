#!/usr/bin/env python3
"""A4: Stale-workflow archive sweep.

Flags workflows that have been in terminal/stale states for longer than the
configured threshold, archives their derived capsules if desired, and emits a
single diagnostic report. The authoritative queue is never mutated here;
only generated artifacts and a report are touched.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_CAPSULE_DIR = DEFAULT_STATE_DIR / "workflows"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "state" / "archive" / "workflows"
DEFAULT_STALE_DAYS = 30
TERMINAL_STATES = {"closed", "closed_with_follow_up", "blocked", "gated", "on_hold"}


def main(*, check_only: bool = False) -> int:
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    active_path = DEFAULT_STATE_DIR / "active_workflows.json"
    if not active_path.exists():
        print(f"ARCHIVE SWEEP OK {now_iso} no_active_queue", file=sys.stderr)
        return 0

    active = json.loads(active_path.read_text(encoding="utf-8"))
    workflows = active.get("workflows", [])
    if isinstance(workflows, dict):
        workflows = list(workflows.values())

    flagged: list[dict] = []
    archived_files: list[str] = []

    for wf in workflows:
        state = wf.get("lifecycle", wf.get("state", "unknown"))
        updated_at = wf.get("updated_at") or wf.get("created_at")
        if state not in TERMINAL_STATES or not updated_at:
            continue
        try:
            updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            age_days = (now - updated).total_seconds() / 86400
        except Exception:
            continue

        if age_days >= DEFAULT_STALE_DAYS:
            record = {
                "workflow_id": wf.get("workflow_id"),
                "state": state,
                "age_days": round(age_days, 1),
                "updated_at": updated_at,
            }
            flagged.append(record)

            wf_id = wf.get("workflow_id")
            if wf_id:
                capsule = DEFAULT_CAPSULE_DIR / f"{wf_id}.json"
                compact = DEFAULT_CAPSULE_DIR / f"{wf_id}-capsule.json"
                if not check_only and (capsule.exists() or compact.exists()):
                    archive_dir = DEFAULT_ARCHIVE_DIR / now.strftime("%Y-%m-%d")
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    for src in (capsule, compact):
                        if src.exists():
                            dst = archive_dir / src.name
                            shutil.move(str(src), str(dst))
                            archived_files.append(str(dst))

    if flagged:
        report = {
            "flagged_workflows": flagged,
            "archived_capsules": archived_files,
            "threshold_days": DEFAULT_STALE_DAYS,
        }
        print(f"ARCHIVE SWEEP DEGRADED {now_iso}")
        print(json.dumps(report, indent=2))
        return 1

    print(f"ARCHIVE SWEEP OK {now_iso} checked={len(workflows)} stale_threshold_days={DEFAULT_STALE_DAYS}", file=sys.stderr)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="report stale workflows without creating directories or moving capsules",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    raise SystemExit(main(check_only=arguments.check_only))
