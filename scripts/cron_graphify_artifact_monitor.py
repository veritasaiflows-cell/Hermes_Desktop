#!/usr/bin/env python3
"""A15: Monitor the legacy Graphify artifact without mutating it."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.graphify_freshness import check_freshness

FreshnessCheck = Callable[[Path], dict[str, Any]]

STATE_NAME = "graphify-monitor-state.json"
MAX_LISTED_ISSUES = 50


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest_state_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "tmp" / STATE_NAME


def _alert_digest(report: dict[str, Any]) -> str:
    """Return a stable identity for one stale episode (generation + issues)."""
    issues = report.get("issues")
    if not isinstance(issues, list):
        issues = []
    canonical = json.dumps(
        {
            "baseline_path": report.get("baseline_path"),
            "graph_path": report.get("graph_path"),
            "issues": issues,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_digest(state_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_digest(state_path: Path, digest: str, now: str) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"digest": digest, "first_seen": now}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _compact_alert(report: dict[str, Any]) -> dict[str, Any]:
    """Trim a freshness report to counts plus paths (scheduler stores stdout)."""
    issues = report.get("issues")
    if not isinstance(issues, list):
        issues = []
    counts: dict[str, int] = {}
    paths: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code", "unknown"))
        counts[code] = counts.get(code, 0) + 1
        path = issue.get("path")
        if isinstance(path, str) and len(paths) < MAX_LISTED_ISSUES:
            paths.append(path)
    return {
        "schema": report.get("schema"),
        "status": report.get("status"),
        "generated_at": report.get("generated_at"),
        "baseline_path": report.get("baseline_path"),
        "issue_counts": counts,
        "issue_total": len(issues),
        "issue_paths": paths,
    }


def main(
    project_root: Path = PROJECT_ROOT,
    *,
    check: FreshnessCheck = check_freshness,
    state_path: Path | None = None,
) -> int:
    """Exit zero and stay silent on stdout when fresh or already reported.

    The first sighting of a stale episode pages (exit 1, compact alert on
    stdout). Repeats of the same episode heartbeat on stderr only (exit 0)
    so an unhealed drift does not page hourly; A18 promotion starts a new
    episode only if the report actually changes.
    """
    now = _utc_now()
    digest_path = Path(state_path) if state_path is not None else _digest_state_path(project_root)
    try:
        report = check(Path(project_root).resolve())
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            "GRAPHIFY ARTIFACT UNAVAILABLE "
            f"{now} error={type(exc).__name__}"
        )
        return 1
    if report.get("status") == "fresh":
        try:
            digest_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"GRAPHIFY ARTIFACT CURRENT {now}", file=sys.stderr)
        return 0
    digest = _alert_digest(report)
    previous = _read_digest(digest_path)
    if previous is not None and previous.get("digest") == digest:
        print(
            f"GRAPHIFY ARTIFACT STILL STALE {now} "
            f"first_seen={previous.get('first_seen', 'unknown')}",
            file=sys.stderr,
        )
        return 0
    print(f"GRAPHIFY ARTIFACT STALE {now}")
    print(json.dumps(_compact_alert(report), indent=2, sort_keys=True))
    _write_digest(digest_path, digest, now)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
