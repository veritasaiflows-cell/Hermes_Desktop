#!/usr/bin/env python3
"""A15: Monitor the legacy Graphify artifact without mutating it."""
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
    """Exit zero and stay silent on stdout only when the artifact is fresh."""
    now = _utc_now()
    try:
        report = check(Path(project_root).resolve())
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            "GRAPHIFY ARTIFACT UNAVAILABLE "
            f"{now} error={type(exc).__name__}"
        )
        return 1
    if report.get("status") != "fresh":
        print(f"GRAPHIFY ARTIFACT STALE {now}")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    print(f"GRAPHIFY ARTIFACT CURRENT {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
