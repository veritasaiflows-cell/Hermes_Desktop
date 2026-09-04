#!/usr/bin/env python3
"""Deterministic admission gate for bounded helper-agent spawns.

Trusted parent-side utility. A helper agent may only be spawned after this
gate admits a ``helper-agent-request.v1`` describing the spawn. The gate is
a deterministic, no-LLM control surface in the same family as
``researcher_task_router.py``: it fails closed, grants no authority by
itself, and cannot make a child session safer than its configured tools.

What admission means:

- ``read-only``: the request declares no write surfaces, no lane, and no
  write-capable toolsets. Admission is a labeling contract for the parent
  that then spawns the helper; it is not a sandbox. Enforcement of the
  declared boundary remains a spawn-time and review-time concern.
- ``write``: the request must name a leased (or running) ``write``-mode lane
  from the concurrent lane register whose lease is unexpired, whose owner
  matches the request, and whose normalized allowed-write surfaces cover
  every requested write path (a requested path must be equal to or a child
  of a lane surface; requesting a parent surface is refused so scope cannot
  be widened beyond the leased lane).

``terminal`` and ``execute_code`` are never admissible in any mode: a shell
is not lane-bounded, so admitting one would make lane coverage
unenforceable. They are rejected in read-only mode as write-capable and in
write mode as outside the write-mode allowlist.

Every validation verdict (admitted or rejected) is appended as one JSON line
to the spawn audit log (default
``<project-root>/state/helper-agent-spawns.jsonl``; override with
``--audit-log``, disable with ``--no-audit-log``). An audit write failure is
fail-closed: the command exits 2 with a JSON rejection even when the request
itself was admissible, so no spawn proceeds without its audit event.

Exit codes mirror ``researcher_task_router.py``: 0 admitted, 2 rejected.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.concurrent_lane_manager import (  # noqa: E402
    DEFAULT_FORBIDDEN_SURFACES,
    LaneManagerError,
    _load_json_list,
    _normalize_path,
    _path_overlaps,
    _parse_utc,
)

REQUEST_SCHEMA = "helper-agent-request.v1"
ADMISSION_SCHEMA = "helper-agent-admission.v1"
SPAWN_AUDIT_SCHEMA = "helper-agent-spawn.v1"
DEFAULT_AUDIT_LOG_RELATIVE = Path("state") / "helper-agent-spawns.jsonl"

ALLOWED_TASK_CLASSES = frozenset(
    {
        "research",
        "analysis",
        "implementation",
        "test_authoring",
        "review",
        "documentation",
    }
)
WRITE_PHASE = "implementation"
READ_ONLY_PHASE = "pre-implementation"
ALLOWED_MODES = ("read-only", "write")

# Detection set for read-only labeling only — toolsets that can mutate state
# in the wild. This is NOT an allowlist: WRITE_MODE_TOOLSETS below is the
# only write-mode allowlist, and it deliberately excludes ``terminal`` and
# ``execute_code`` because a shell is not lane-bounded.
WRITE_CAPABLE_TOOLSETS = frozenset({"write_file", "patch", "execute_code", "terminal"})

# Toolset families never admissible for a bounded helper spawn. Matched by
# family (normalized, separators ignored) so naming variants cannot bypass
# the boundary: ``computer-use``, ``cron-job``, ``delegate_task``,
# ``memory_search``, ``skills_manage``, ``project_create``, and MCP tool
# names (``mcp__<server>__<tool>``) are all rejected.
FORBIDDEN_TOOLSET_FAMILIES = (
    "cronjob",
    "computer_use",
    "desktop_ui",
    "project",
    "memory",
    "skills",
    "skill",
    "delegate",
    "mcp",
    "setup_mcp",
    "install_mcp",
)

# The only toolsets a read-only admission may declare. Read-only requests
# fail closed on anything else: an undeclared toolset cannot be proven
# side-effect free, so it is rejected rather than labeled.
READ_ONLY_TOOLSETS = frozenset(
    {
        "read_file",
        "read_files",
        "search_files",
        "web_search",
        "web_extract",
    }
)

# The only toolsets a write-mode admission may declare on top of the
# read-only set: file writers whose effect is bounded by the lane's allowed
# write surfaces. ``terminal`` and ``execute_code`` are deliberately absent —
# a shell is not lane-bounded, so admitting one would make lane coverage
# unenforceable — and unknown names fail closed exactly as in read-only mode.
WRITE_MODE_TOOLSETS = READ_ONLY_TOOLSETS | {"write_file", "patch"}

REQUIRED_REQUEST_FIELDS = {
    "schema",
    "task_id",
    "task_class",
    "phase",
    "mode",
    "objective",
    "scope",
    "allowed_toolsets",
    "allowed_writes",
    "max_duration_minutes",
    "owner",
}
# lane_id is conditionally required for write mode, so it is not listed above.
_TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_DURATION_CAP_MINUTES = 480

LANE_ACCEPTED_STATUSES = ("leased", "running")


class HelperAgentRouterError(ValueError):
    """Raised when a spawn request crosses the helper-agent boundary."""


def _reject(reason: str, reasons: list[str]) -> None:
    reasons.append(reason)


def _validate_task_id(value: object, reasons: list[str]) -> None:
    if not isinstance(value, str) or not _TASK_ID_PATTERN.fullmatch(value):
        _reject("task_id must be a lowercase stable identifier", reasons)


def _validate_enum(
    value: object,
    allowed: tuple[str, ...] | frozenset,
    label: str,
    reasons: list[str],
    expected: str | None = None,
) -> None:
    if not isinstance(value, str) or value not in allowed:
        _reject(expected or f"{label} is not allowlisted", reasons)


def _validate_nonempty(value: object, label: str, reasons: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        _reject(f"{label} must be a non-empty string", reasons)


def _validate_max_duration(value: object, reasons: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject("max_duration_minutes must be an integer", reasons)
        return
    if value < 1 or value > _MAX_DURATION_CAP_MINUTES:
        _reject(
            f"max_duration_minutes must be between 1 and {_MAX_DURATION_CAP_MINUTES}",
            reasons,
        )


def _toolset_family(name: str) -> str:
    """Normalize a toolset name into its family key.

    Lowercase, hyphens/spaces folded to underscores, and a ``mcp__server__tool``
    name maps to the ``mcp`` family. Any prefix of the normalized name that
    matches a forbidden family (e.g. ``delegate_task`` -> ``delegate``,
    ``memory_search`` -> ``memory``) is caught downstream via startswith.
    """
    normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized.startswith("mcp__"):
        return "mcp"
    return normalized


def _validate_toolsets(value: object, mode: str, reasons: list[str]) -> None:
    if not isinstance(value, list) or not value:
        _reject("allowed_toolsets must be a non-empty list", reasons)
        return
    if any(not isinstance(item, str) for item in value):
        _reject("allowed_toolsets entries must be strings", reasons)
        return
    # Case-insensitive matching: casing must never bypass the tool boundary.
    families = [_toolset_family(item) for item in value]
    if "" in families:
        _reject("allowed_toolsets entries must be non-empty strings", reasons)
    forbidden = sorted(
        {
            banned
            for banned in FORBIDDEN_TOOLSET_FAMILIES
            for family in families
            if family == banned
            or family.startswith(banned + "_")
            or family.replace("_", "") == banned.replace("_", "")
            or family.replace("_", "").startswith(banned.replace("_", "") + "_")
        }
    )
    if forbidden:
        _reject("forbidden toolset(s): " + ", ".join(forbidden), reasons)
    write_capable = sorted(
        {
            item.strip().lower()
            for item in value
            if isinstance(item, str)
            and (
                item.strip().lower() in WRITE_CAPABLE_TOOLSETS
                or _toolset_family(item).replace("_", "")
                in {member.replace("_", "") for member in WRITE_CAPABLE_TOOLSETS}
            )
        }
    )
    if write_capable and mode != "write":
        _reject(
            "write-capable toolset(s) not allowed in read-only mode: "
            + ", ".join(write_capable),
            reasons,
        )
    if mode in ("read-only", "write"):
        allowed = READ_ONLY_TOOLSETS if mode == "read-only" else WRITE_MODE_TOOLSETS
        unknown = sorted({family for family in families if family not in allowed})
        if unknown:
            _reject(
                f"{mode} mode allows only these toolsets: "
                + ", ".join(sorted(allowed))
                + f"; not admissible: {', '.join(unknown)}",
                reasons,
            )


def _validate_relative_surface(
    value: object, project_root: Path, reasons: list[str]
) -> "str | None":
    """Validate one requested write surface; return its normalized absolute path."""
    if not isinstance(value, str) or not value.strip():
        _reject("allowed_writes entries must be non-empty strings", reasons)
        return None
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        _reject(f"allowed_writes entries must be workspace-relative, not absolute: {value!r}", reasons)
        return None
    stripped = value.strip()
    # Anything naming a machine-absolute location is absolute, whatever its
    # separators or leading whitespace: UNC paths (\\server\share, //server)
    # and drive-relative forms (C:foo) must be rejected, not reinterpreted.
    if stripped.startswith(("\\\\", "//", "\\")) or (
        len(stripped) >= 2 and stripped[1] == ":"
    ):
        _reject(f"allowed_writes entries must be workspace-relative, not absolute: {value!r}", reasons)
        return None
    if ".." in Path(stripped.replace("\\", "/")).parts:
        _reject(f"allowed_writes path traversal is not allowed: {value!r}", reasons)
        return None
    try:
        return _normalize_path(value, project_root)
    except LaneManagerError as exc:
        _reject(f"allowed_writes entry invalid: {exc}", reasons)
        return None


def _check_forbidden_surfaces(
    normalized: list[Path | None], originals: list[object], project_root: Path, reasons: list[str]
) -> None:
    """Compare against forbidden surfaces normalized at the request's own root."""
    for original, candidate in zip(originals, normalized):
        if candidate is None:
            continue
        for surface in DEFAULT_FORBIDDEN_SURFACES:
            try:
                surface_path = _normalize_path(surface, project_root)
            except LaneManagerError:
                continue
            if _path_overlaps(candidate, surface_path):
                _reject(
                    f"allowed_writes entry {original!r} overlaps a forbidden surface: {surface}",
                    reasons,
                )


def _load_lane(connection: sqlite3.Connection, lane_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM lanes WHERE lane_id = ?", (lane_id,)
    ).fetchone()


def _check_lane(
    request: dict[str, Any],
    project_root: Path,
    normalized: list[Path | None],
    reasons: list[str],
) -> None:
    register_path = project_root / "state" / "concurrent-lane-register.sqlite"
    if not register_path.is_file():
        _reject("lane register is unavailable; cannot verify write lane", reasons)
        return
    lane_id = request.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id.strip():
        _reject("write-mode requests must declare lane_id", reasons)
        return

    try:
        # Plain filesystem path (not a URI): --project-root values containing
        # SQLite URI metacharacters (#, ?, %) must be taken literally, and a
        # URI built by string interpolation would silently misbind the
        # connection. query_only keeps the register read-only.
        connection = sqlite3.connect(str(register_path))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            row = _load_lane(connection, lane_id)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        _reject(f"lane register is unreadable: {exc}", reasons)
        return

    if row is None:
        _reject(f"unknown lane: {lane_id!r}", reasons)
        return
    if row["lane_mode"] != "write":
        _reject(f"lane {lane_id!r} is not a write-mode lane", reasons)
        return
    if row["status"] not in LANE_ACCEPTED_STATUSES:
        _reject(
            f"lane {lane_id!r} is not leased or running (status={row['status']!r}); "
            "a lease must be granted before a write-mode spawn",
            reasons,
        )
        return

    owner = request.get("owner")
    if not isinstance(owner, str) or row["owner"] != owner:
        _reject(
            f"request owner {owner!r} does not match lane owner {row['owner']!r}",
            reasons,
        )
        return

    try:
        lease_expires = _parse_utc(row["lease_expires_at"]) if row["lease_expires_at"] else None
    except (TypeError, ValueError) as exc:
        _reject(
            f"lane {lane_id!r} has an unparseable lease expiry "
            f"({row['lease_expires_at']!r}): {exc}",
            reasons,
        )
        return
    now = datetime.now(timezone.utc)
    if lease_expires is None:
        _reject(f"lane {lane_id!r} has no lease expiry recorded", reasons)
        return
    if lease_expires.tzinfo is None:
        # The manager writes Z-suffixed UTC leases; a naive value is malformed.
        _reject(
            f"lane {lane_id!r} lease expiry {row['lease_expires_at']!r} is not timezone-aware; "
            "refresh the lease with concurrent_lane_manager.py",
            reasons,
        )
        return
    if lease_expires <= now:
        _reject(
            f"lane lease expired at {row['lease_expires_at']}; refresh the lease before spawning",
            reasons,
        )
        return

    try:
        stored = _load_json_list(row["allowed_writes_json"], field_name="allowed_writes")
    except LaneManagerError as exc:
        _reject(f"lane {lane_id!r} register row is corrupt: {exc}", reasons)
        return
    lane_surfaces: list[str] = []
    for entry in stored:
        if not isinstance(entry, str) or not entry:
            _reject(f"lane {lane_id!r} has malformed allowed_writes", reasons)
            return
        lane_surfaces.append(entry)

    for original, candidate in zip(request.get("allowed_writes", []), normalized):
        if candidate is None:
            continue
        key = str(candidate)
        # Directional containment: the requested path must be the lane surface
        # itself or live underneath it. A requested parent/ancestor of a lane
        # surface widens the leased scope and must be rejected. The
        # surface-itself case is compared with separators stripped, so a
        # not-yet-on-disk directory surface requested without its trailing
        # slash is still covered (QA F6).
        def _covers(surface: str) -> bool:
            base = surface.rstrip("\\/")
            surface_is_directory = surface.endswith("\\") or surface.endswith("/")
            if surface_is_directory:
                return (
                    key.rstrip("\\/") == base
                    or key.startswith(base + "\\")
                    or key.startswith(base + "/")
                )
            # A stored FILE surface covers only itself: it cannot contain
            # children, so a request beneath it is never covered.
            return key.rstrip("\\/") == base

        covered = any(_covers(surface) for surface in lane_surfaces)
        if not covered:
            _reject(
                f"allowed_writes entry {original!r} is not covered by the leased lane {lane_id!r}",
                reasons,
            )


def admit_request(
    request: object, *, project_root: Path | None = None
) -> dict[str, Any]:
    """Validate mode consistency, toolset policy, and lane coverage for a spawn."""
    root = Path(project_root).resolve() if project_root is not None else PROJECT_ROOT
    reasons: list[str] = []
    if not isinstance(request, dict):
        return {
            "schema": ADMISSION_SCHEMA,
            "status": "rejected",
            "task_id": None,
            "mode": None,
            "lane_id": None,
            "reasons": ["request must be a JSON object"],
        }
    unexpected = sorted(set(request) - REQUIRED_REQUEST_FIELDS - {"lane_id"})
    missing = sorted(REQUIRED_REQUEST_FIELDS - set(request))
    # lane_id is required only for write mode; read-only must not declare one.
    if request.get("mode") == "write" and "lane_id" not in request:
        missing.append("lane_id")
        missing = sorted(set(missing))
    if request.get("mode") != "write" and "lane_id" in request:
        _reject("lane_id is only valid for write-mode requests", reasons)
    if missing:
        _reject("missing required request fields: " + ", ".join(missing), reasons)
    if unexpected:
        _reject("unexpected request fields: " + ", ".join(unexpected), reasons)
    if request.get("schema") != REQUEST_SCHEMA:
        _reject("unsupported request schema", reasons)

    mode = request.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        _reject("mode must be read-only or write", reasons)
        mode = None

    task_class = request.get("task_class")
    if not isinstance(task_class, str) or task_class not in ALLOWED_TASK_CLASSES:
        _reject("task_class is not allowlisted", reasons)

    expected_phase_value = WRITE_PHASE if mode == "write" else READ_ONLY_PHASE
    if request.get("phase") != expected_phase_value:
        _reject(f"phase must be {expected_phase_value}", reasons)

    _validate_nonempty(request.get("objective"), "objective", reasons)
    _validate_nonempty(request.get("scope"), "scope", reasons)
    _validate_nonempty(request.get("owner"), "owner", reasons)
    task_id = request.get("task_id")
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        _reject("task_id must be a lowercase stable identifier", reasons)
    _validate_max_duration(request.get("max_duration_minutes"), reasons)

    toolsets = request.get("allowed_toolsets")
    _validate_toolsets(toolsets, mode if isinstance(mode, str) else "", reasons)

    writes = request.get("allowed_writes")
    if mode == "read-only":
        if writes != []:
            _reject("read-only request must not declare allowed_writes", reasons)
        normalized: list[Path | None] = []
    elif isinstance(writes, list) and writes:
        normalized = [
            _validate_relative_surface(entry, Path(project_root or PROJECT_ROOT), reasons)
            for entry in writes
        ]
        if len(set(map(str, [p for p in normalized if p]))) != len(
            [p for p in normalized if p is not None]
        ):
            _reject("allowed_writes entries must be unique", reasons)
        deduped = [p for p in normalized if p is not None]
        if len({str(p).rstrip("\\/") for p in deduped}) != len(deduped):
            _reject("allowed_writes entries must be unique", reasons)
        _check_forbidden_surfaces(normalized, list(writes), root, reasons)
    else:
        _reject("write-mode requests must declare at least one allowed write surface", reasons)
        normalized = []

    if mode == "write":
        _check_lane(request, Path(project_root or PROJECT_ROOT), normalized, reasons)

    return {
        "schema": ADMISSION_SCHEMA,
        "status": "admitted" if not reasons else "rejected",
        "task_id": request.get("task_id") if isinstance(task_id, str) else None,
        "mode": mode,
        "lane_id": request.get("lane_id") if isinstance(request.get("lane_id"), str) else None,
        "reasons": reasons,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_spawn_event(
    request: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Build the auditable spawn event for one admission decision."""
    return {
        "schema": SPAWN_AUDIT_SCHEMA,
        "recorded_at": _utc_now_iso(),
        "task_id": result.get("task_id"),
        "task_class": request.get("task_class"),
        "phase": request.get("phase"),
        "mode": result.get("mode"),
        "owner": request.get("owner"),
        "lane_id": result.get("lane_id"),
        "status": result.get("status"),
        "reasons": result.get("reasons", []),
        "allowed_toolsets": request.get("allowed_toolsets"),
        "allowed_writes": request.get("allowed_writes"),
    }


def append_spawn_event(audit_log: Path, event: dict[str, Any]) -> None:
    """Append one spawn event as a JSON line, creating parent directories."""
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with audit_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _read_request(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperAgentRouterError(f"request is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise HelperAgentRouterError("request must be a JSON object")
    return payload


def _rejection_payload(reason: str) -> str:
    return json.dumps(
        {
            "schema": ADMISSION_SCHEMA,
            "status": "rejected",
            "task_id": None,
            "mode": None,
            "lane_id": None,
            "reasons": [reason],
        }
    )


class _JsonArgumentParser(argparse.ArgumentParser):
    """Emit an exit-2 JSON verdict on usage errors instead of a bare message."""

    def error(self, message: str) -> None:
        print(_rejection_payload(f"usage error: {message}"))
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _JsonArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    admit_command = subcommands.add_parser("admit")
    admit_command.add_argument("--request", type=Path, required=True)
    admit_command.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    admit_command.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="Spawn audit log path (default: <project-root>/state/helper-agent-spawns.jsonl)",
    )
    admit_command.add_argument(
        "--no-audit-log",
        action="store_true",
        help="Skip writing the spawn audit event (testing only)",
    )
    arguments = parser.parse_args(argv)
    try:
        request = _read_request(arguments.request)
        result = admit_request(request, project_root=arguments.project_root)
        if not arguments.no_audit_log:
            audit_log = arguments.audit_log
            if audit_log is None:
                audit_log = Path(arguments.project_root) / DEFAULT_AUDIT_LOG_RELATIVE
            elif not audit_log.is_absolute():
                audit_log = Path(arguments.project_root) / audit_log
            try:
                append_spawn_event(audit_log, build_spawn_event(request, result))
            except OSError as exc:
                print(
                    json.dumps(
                        {
                            "schema": ADMISSION_SCHEMA,
                            "status": "rejected",
                            "task_id": result.get("task_id"),
                            "mode": result.get("mode"),
                            "lane_id": result.get("lane_id"),
                            "reasons": list(result.get("reasons", []))
                            + [f"spawn audit unavailable: {exc}"],
                        }
                    )
                )
                return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "admitted" else 2
    except HelperAgentRouterError as exc:
        print(
            json.dumps(
                {
                    "schema": ADMISSION_SCHEMA,
                    "status": "rejected",
                    "task_id": None,
                    "mode": None,
                    "lane_id": None,
                    "reasons": [str(exc)],
                }
            )
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — the gate must never exit 1
        # Fail-closed backstop: any unexpected error (corrupt request bytes,
        # malformed register schema, hostile paths, deep JSON recursion) is a
        # rejection, never a traceback.
        print(
            json.dumps(
                {
                    "schema": ADMISSION_SCHEMA,
                    "status": "rejected",
                    "task_id": None,
                    "mode": None,
                    "lane_id": None,
                    "reasons": [f"internal error: {type(exc).__name__}: {exc}"],
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())