#!/usr/bin/env python3
"""A17: Read-only Graphify release advisory; never run an updater."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GRAPHIFY_PIN = "0.9.45"
PYPI_URL = "https://pypi.org/pypi/graphifyy/json"
RELEASE_NOTES_URL = "https://github.com/Graphify-Labs/graphify/releases"
_STABLE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_latest_graphify() -> str:
    request = Request(PYPI_URL, headers={"User-Agent": "efficiens-graphify-advisory/1"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    version = payload.get("info", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("PyPI response did not contain a version")
    return version


def _release_tuple(value: str) -> tuple[int, ...]:
    if not _STABLE_VERSION.fullmatch(value):
        raise ValueError("Only stable dotted-numeric versions are supported")
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _is_newer_version(candidate: str, configured: str) -> bool:
    return _release_tuple(candidate) > _release_tuple(configured)


def main(*, fetch_latest: Callable[[], str] = _fetch_latest_graphify) -> int:
    """Report a newer stable Graphify release without changing local state."""
    now = _utc_now()
    try:
        latest = fetch_latest()
        update_available = _is_newer_version(latest, GRAPHIFY_PIN)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            "GRAPHIFY VERSION CHECK UNAVAILABLE "
            f"{now} error={type(exc).__name__}"
        )
        return 1

    if update_available:
        print(f"GRAPHIFY VERSION ADVISORY {now}")
        print(
            json.dumps(
                {
                    "schema": "graphify-version-advisory.v1",
                    "configured_graphify": GRAPHIFY_PIN,
                    "latest_graphify": latest,
                    "graphify_update_available": True,
                    "release_notes": RELEASE_NOTES_URL,
                    "automatic_updates_applied": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(
        f"GRAPHIFY VERSION CURRENT {now} configured={GRAPHIFY_PIN} latest={latest}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
