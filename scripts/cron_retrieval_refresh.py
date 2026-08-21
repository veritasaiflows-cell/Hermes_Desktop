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


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        summary = refresh_indexes()
    except Exception as exc:
        print(f"RETRIEVAL REFRESH FAIL {now} error={type(exc).__name__}")
        return 1
    if (
        summary["workspace_index"]["skipped_files"]
        or summary["vector_index"]["skipped_files"]
        or summary["vector_index"].get("embedding_errors", 0)
    ):
        print(f"RETRIEVAL REFRESH DEGRADED {now}")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1
    print(
        f"RETRIEVAL REFRESH OK {now} sources={summary['source_count']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
