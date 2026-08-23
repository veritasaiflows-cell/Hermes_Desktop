#!/usr/bin/env python3
"""A6: Alias dead-target sweep.

Checks every alias in state/workflow_alias_index.json points to a workflow
that still exists in the active queue. Reports missing targets but does not
mutate authoritative sources.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"


def main() -> int:
    """Sweep aliases for dead workflow targets; exit 1 if any are found."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    alias_path = DEFAULT_STATE_DIR / "workflow_alias_index.json"
    active_path = DEFAULT_STATE_DIR / "active_workflows.json"

    if not alias_path.exists() or not active_path.exists():
        print(f"ALIAS SWEEP OK {now} missing_sources", file=sys.stderr)
        return 0

    aliases = json.loads(alias_path.read_text(encoding="utf-8")).get("aliases", {})
    active = json.loads(active_path.read_text(encoding="utf-8"))
    workflows = active.get("workflows", {})
    active_ids = {wf.get("workflow_id") for wf in workflows.values()} if isinstance(workflows, dict) else {wf.get("workflow_id") for wf in workflows}

    dead = [
        {"alias": alias, "workflow_id": wf_id}
        for alias, wf_id in aliases.items()
        if wf_id not in active_ids
    ]

    if dead:
        print(f"ALIAS SWEEP DEGRADED {now}")
        print(json.dumps({"dead_aliases": dead}, indent=2))
        return 1

    print(f"ALIAS SWEEP OK {now} aliases={len(aliases)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
