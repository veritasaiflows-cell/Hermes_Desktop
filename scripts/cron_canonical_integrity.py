#!/usr/bin/env python3
"""A13: Verify canonical SQLite integrity without mutating domain records."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB, DEFAULT_DATABASE_PATH


def main() -> int:
    """Run a read-only integrity check; exit 1 on any failure."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with CanonicalDB(DEFAULT_DATABASE_PATH, read_only=True) as db:
            result = db.integrity_check()
    except Exception as exc:
        print(f"CANONICAL INTEGRITY FAIL {now} error={type(exc).__name__}")
        return 1
    if result != "ok":
        print(f"CANONICAL INTEGRITY FAIL {now} result={result}")
        return 1
    print(f"CANONICAL INTEGRITY OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
