#!/usr/bin/env python3
"""A18: Fail-closed hold for Graphify writes until atomic publication is activated."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.graphify_freshness import check_freshness

FreshnessCheck = Callable[[Path], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(
    project_root: Path = PROJECT_ROOT,
    *,
    check: FreshnessCheck = check_freshness,
) -> int:
    """Remain read-only; code refresh is blocked until pointer publication exists."""
    now = _utc_now()
    root = Path(project_root).resolve()
    try:
        report = check(root)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            "GRAPHIFY CODE REFRESH BLOCKED "
            f"{now} reason=freshness_check_failed error={type(exc).__name__}"
        )
        return 1

    if report.get("status") == "fresh":
        print(f"GRAPHIFY CODE REFRESH SKIP {now} reason=artifact_current", file=sys.stderr)
        return 0

    print(f"GRAPHIFY CODE REFRESH BLOCKED {now}")
    print(
        json.dumps(
            {
                "schema": "graphify-code-refresh-hold.v1",
                "status": "blocked",
                "reason": "atomic_publication_not_activated",
                "writer_executed": False,
                "freshness": report,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
