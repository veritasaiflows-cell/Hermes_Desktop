#!/usr/bin/env python3
"""A12: Refresh exact and semantic retrieval indexes from approved sources."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.retrieval_refresh import refresh_indexes
from scripts.cron_routing_refresh import main as refresh_routing


def _synchronize_routing(now: str) -> bool:
    """Run A5 and return whether routing synchronization succeeded."""
    try:
        routing_status = refresh_routing()
    except Exception as exc:
        print(
            f"RETRIEVAL REFRESH FAIL {now} "
            f"reason=routing_sync_exception error={type(exc).__name__}"
        )
        return False
    if routing_status != 0:
        print(f"RETRIEVAL REFRESH FAIL {now} reason=routing_sync_failed")
        return False
    return True


def main() -> int:
    """Refresh retrieval indexes, then synchronize routing; exit 1 on any failure."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        summary = refresh_indexes()
    except Exception as exc:
        print(f"RETRIEVAL REFRESH FAIL {now} error={type(exc).__name__}")
        _synchronize_routing(now)
        return 1
    retrieval_degraded = bool(
        summary["workspace_index"]["skipped_files"]
        or summary["vector_index"]["skipped_files"]
        or summary["vector_index"].get("embedding_errors", 0)
    )
    if retrieval_degraded:
        print(f"RETRIEVAL REFRESH DEGRADED {now}")
        print(json.dumps(summary, indent=2, sort_keys=True))
    if not _synchronize_routing(now):
        return 1
    if retrieval_degraded:
        return 1
    print(
        f"RETRIEVAL REFRESH OK {now} sources={summary['source_count']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
