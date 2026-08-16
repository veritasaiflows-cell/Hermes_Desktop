#!/usr/bin/env python3
"""A3: Routing-cache hygiene sweep.

Evicts expired entries from canonical/efficiens.db routing_cache and prunes
rows whose cached source_signatures no longer match the live control plane.

Silent on green. Alerts on failure.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB

DEFAULT_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"


def _current_source_signatures() -> dict[str, object]:
    """Mirror the router's source fingerprinting for the live state files."""
    import hashlib
    from datetime import datetime, timezone

    state_dir = PROJECT_ROOT / "state"
    paths = [
        state_dir / "active_workflows.json",
        state_dir / "workflow_alias_index.json",
        state_dir / "workflow-control-overrides.json",
    ]
    sigs: dict[str, object] = {}
    for path in paths:
        if not path.exists():
            sigs[str(path.as_posix())] = {
                "sha256": "",
                "size": -1,
                "modified_at": "",
                "exists": False,
            }
            continue
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        sigs[str(path.as_posix())] = {
            "sha256": digest.hexdigest(),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "exists": True,
        }
    return sigs


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evicted = 0
    signature_evicted = 0
    remaining = 0

    if DEFAULT_DATABASE.exists():
        try:
            with CanonicalDB(DEFAULT_DATABASE) as db:
                evicted = db.clear_expired_routing_cache()
                signature_evicted = db.delete_routing_cache_with_mismatched_signatures(
                    current_signatures=_current_source_signatures()
                )
                remaining = db.connection.execute(
                    "SELECT COUNT(*) FROM routing_cache"
                ).fetchone()[0]
        except Exception as exc:
            print(f"ROUTING CACHE SWEEP FAIL {now} error={exc}")
            return 1

    print(
        f"ROUTING CACHE SWEEP OK {now} evicted={evicted} signature_evicted={signature_evicted} remaining={remaining}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
