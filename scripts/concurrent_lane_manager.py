#!/usr/bin/env python3
"""Concurrent lane and write-lease manager.

This script provides a durable register for concurrent lane planning, lease grants,
path-based collision checks, and status transitions.

Core idea:

- A parent job groups one or more lanes.
- A lane has a stable identity and explicit allowed write surfaces.
- Active lanes cannot overlap allowed-write surfaces.
- Leases are finite and can be renewed.
- Terminal closeout requires proof artifacts and a verified status transition.

The durable register is stored in SQLite so status updates are atomic and
race-resistant.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import argparse
import math
from pathlib import Path
import json
import os
import re
import sqlite3
import sys
import uuid
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_REGISTER_PATH = PROJECT_ROOT / "state" / "concurrent-lane-register.sqlite"

VALID_STATUSES = ("planned", "leased", "running", "blocked", "cancelled", "complete")
WRITE_MODES = ("write", "read-only")
ACTIVE_WRITE_STATUSES = ("planned", "leased", "running")
STATUS_TRANSITIONS = {
    "planned": {"leased", "blocked", "cancelled"},
    "leased": {"running", "blocked", "cancelled"},
    "running": {"blocked", "cancelled", "complete"},
    "blocked": {"leased", "running", "cancelled"},
    "cancelled": {"planned"},
    "complete": set(),
}

DEFAULT_FORBIDDEN_SURFACES = [
    ".env",
    ".git/",
    "AGENTS.md",
    "GOVERNANCE.md",
    "canonical/schema.sql",
    "credentials/",
    "config/",
    "state/ACTIVE_WORKFLOWS.md",
    "state/WORKFLOW_ALIAS_INDEX.md",
    "state/workflow-control-overrides.json",
    "state/concurrent-lane-register.sqlite",
]


class LaneManagerError(ValueError):
    """Raised for input or policy violations."""


def _utc_now() -> str:
    """Return the current UTC time as a compact ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with optional ``Z``) into a UTC datetime."""
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _as_bool_json(value: Any) -> str:
    """Serialize ``value`` to compact canonical JSON for deterministic diffs."""
    # Compact canonical JSON for deterministic diffs and equality checks.
    return json.dumps(value, sort_keys=True)


def _normalize_json_list(values: list[str]) -> list[str]:
    """Return a deduplicated, sorted list of non-empty strings."""
    return sorted({value for value in values if value})


def _normalize_path(raw: str, project_root: Path) -> str:
    """Validate and normalize a workspace-relative lease surface path."""
    if not raw:
        raise LaneManagerError("Path cannot be empty")
    value = raw.strip()
    if not value:
        raise LaneManagerError("Path cannot be blank")
    if any(token in value for token in ("*", "?", "[") ):
        raise LaneManagerError(f"Glob patterns are not allowed in lease surfaces: {raw!r}")
    if "~" in value:
        raise LaneManagerError(f"Home-directory references are not allowed for lease paths: {raw!r}")

    workspace_relative = value.replace("\\", "/").lstrip("/")
    parts = Path(workspace_relative).parts
    if any(part == ".." for part in parts):
        raise LaneManagerError(f"Path traversal is not allowed: {raw!r}")
    if len(parts) == 0 or parts[0].lower() in (".", ""):
        raise LaneManagerError(f"Invalid workspace-relative path: {raw!r}")

    explicit_directory = value.endswith("/") or value.endswith("\\")
    candidate = (project_root / workspace_relative).resolve()
    root = project_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LaneManagerError(f"Path must be under workspace root: {raw!r}") from exc

    normalized = os.path.normcase(str(candidate))
    if candidate.exists() and candidate.is_dir() or explicit_directory:
        # Keep explicit directory surfaces bounded for collision checks.
        if not normalized.endswith(os.sep):
            normalized += os.sep

    return normalized


def _path_overlaps(lhs: str, rhs: str) -> bool:
    """Return True if two normalized paths are equal or one is a parent of the other."""
    lhs_norm = os.path.normcase(lhs)
    rhs_norm = os.path.normcase(rhs)
    if lhs_norm == rhs_norm:
        return True

    lhs_key = lhs_norm.rstrip(os.sep)
    rhs_key = rhs_norm.rstrip(os.sep)
    return lhs_key.startswith(rhs_key + os.sep) or rhs_key.startswith(lhs_key + os.sep)


def _load_json_list(blob: str | None, *, field_name: str) -> list[str]:
    """Parse a stored JSON list field, raising ``LaneManagerError`` on malformed data."""
    if not blob:
        return []
    try:
        value = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise LaneManagerError(f"Invalid JSON stored for {field_name}") from exc

    if not isinstance(value, list):
        raise LaneManagerError(f"Stored {field_name} must be a JSON list")
    return value


def _resolve_workflow_ids(project_root: Path) -> set[str]:
    """Return the set of known workflow IDs from the live workflow queue."""
    # Validate against the live workflow queue when available.
    state_file = project_root / "state" / "ACTIVE_WORKFLOWS.md"
    legacy_state_file = project_root / "state" / "active_workflows.json"
    source = state_file if state_file.exists() else legacy_state_file if legacy_state_file.exists() else None
    if source is None or not source.exists():
        return set()

    payload_text = source.read_text(encoding="utf-8")
    text = payload_text.strip()
    if source.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        match = re.search(r"```\s*json\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
        if not match:
            raise LaneManagerError(f"Could not parse workflow control surface: {source}")
        payload = json.loads(match.group(1))

    entries = payload.get("workflows", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise LaneManagerError(f"Workflow queue payload malformed: {source}")

    workflow_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise LaneManagerError(f"Invalid workflow entry in {source}: {entry!r}")
        workflow_id = entry.get("workflow_id")
        if not isinstance(workflow_id, str):
            raise LaneManagerError(f"Workflow entry missing workflow_id in {source}: {entry!r}")
        workflow_ids.add(workflow_id.upper())

    return workflow_ids


class ConcurrentLaneManager:
    def __init__(self, *, project_root: Path, register_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.register_path = (
            register_path.resolve()
            if register_path.is_absolute()
            else (self.project_root / register_path).resolve()
        )
        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def register(self) -> str:
        return str(self.register_path.as_posix())

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.register_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            self._ensure_schema(connection)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            self._ensure_schema(connection)

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lane_register_meta (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS parent_jobs (
                job_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workstream TEXT NOT NULL,
                owner TEXT NOT NULL,
                objective TEXT,
                parent_status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                acceptance_command TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS lanes (
                lane_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workstream TEXT NOT NULL,
                owner TEXT NOT NULL,
                lane_mode TEXT NOT NULL CHECK (lane_mode IN ('write', 'read-only')),
                status TEXT NOT NULL CHECK(status IN ('planned', 'leased', 'running', 'blocked', 'cancelled', 'complete')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                attempt_id TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                retry_count INTEGER NOT NULL DEFAULT 0,
                allowed_writes_json TEXT NOT NULL DEFAULT '[]',
                forbidden_writes_json TEXT NOT NULL DEFAULT '[]',
                proof_artifacts_json TEXT NOT NULL DEFAULT '[]',
                stop_lines_json TEXT NOT NULL DEFAULT '[]',
                acceptance_command TEXT,
                lease_granted_at TEXT,
                lease_expires_at TEXT,
                start_at TEXT,
                end_at TEXT,
                incident_code TEXT,
                incident_notes TEXT,
                session_id TEXT,
                runner_backend TEXT,
                expected_model TEXT,
                actual_model TEXT,
                FOREIGN KEY (job_id) REFERENCES parent_jobs(job_id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS lane_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                lane_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                revision INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY (lane_id) REFERENCES lanes(lane_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_lanes_job_id ON lanes (job_id);
            CREATE INDEX IF NOT EXISTS idx_lanes_status ON lanes (status);
            CREATE INDEX IF NOT EXISTS idx_lanes_workflow ON lanes (workflow_id);
            CREATE INDEX IF NOT EXISTS idx_lanes_mode ON lanes (lane_mode);
            CREATE INDEX IF NOT EXISTS idx_events_lane ON lane_events (lane_id, event_id);
            """
        )

    def _get_revision(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT meta_value FROM lane_register_meta WHERE meta_key = 'revision'"
        ).fetchone()
        if row is None:
            return 0
        return int(row[0])

    def _bump_revision(self, connection: sqlite3.Connection) -> int:
        now = _utc_now()
        current = self._get_revision(connection) + 1
        connection.execute(
            "INSERT INTO lane_register_meta (meta_key, meta_value, updated_at) "
            "VALUES ('revision', ?, ?) "
            "ON CONFLICT(meta_key) DO UPDATE SET meta_value = ?, updated_at = ?",
            (str(current), now, str(current), now),
        )
        return current

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        lane_id: str,
        attempt_id: str,
        event_type: str,
        actor: str,
        revision: int,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO lane_events (lane_id, attempt_id, event_type, event_at, actor, revision, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lane_id,
                attempt_id,
                event_type,
                _utc_now(),
                actor,
                revision,
                _as_bool_json(details),
            ),
        )

    def _fetch_lane(self, connection: sqlite3.Connection, lane_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM lanes WHERE lane_id = ?", (lane_id,)
        ).fetchone()

    def _normalize_writes(self, paths: list[str] | None, *, allow_empty: bool = True) -> list[str]:
        normalized: list[str] = []
        for raw in paths or []:
            normalized.append(_normalize_path(raw, self.project_root))

        # Deduplicate with deterministic ordering.
        unique = sorted(set(normalized))
        if not unique and not allow_empty:
            raise LaneManagerError("At least one allowed write is required for write mode")
        return unique

    def _load_parent_job(self, connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM parent_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    def _assert_workflow_known(self, workflow_id: str) -> None:
        known = _resolve_workflow_ids(self.project_root)
        if known and workflow_id.upper() not in known:
            raise LaneManagerError(f"Unknown workflow_id {workflow_id!r}; not in active queue")

    def _assert_status_transition(self, *, current: str, requested: str) -> None:
        if requested == current:
            return
        allowed = STATUS_TRANSITIONS.get(current)
        if allowed is None or requested not in allowed:
            raise LaneManagerError(f"Invalid lane transition {current!r} -> {requested!r}")

    def _iter_lane_rows(self, connection: sqlite3.Connection, *, lane_id: str | None = None):
        if lane_id:
            return connection.execute(
                "SELECT * FROM lanes WHERE lane_id = ? ORDER BY updated_at DESC",
                (lane_id,),
            ).fetchall()
        return connection.execute("SELECT * FROM lanes ORDER BY updated_at DESC, lane_id ASC").fetchall()

    def _decode_fields(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "lane_id": row["lane_id"],
            "job_id": row["job_id"],
            "workflow_id": row["workflow_id"],
            "workstream": row["workstream"],
            "owner": row["owner"],
            "mode": row["lane_mode"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
            "attempt_id": row["attempt_id"],
            "attempt": row["attempt"],
            "retry_count": row["retry_count"],
            "allowed_writes": _load_json_list(row["allowed_writes_json"], field_name="allowed_writes"),
            "forbidden_writes": _load_json_list(row["forbidden_writes_json"], field_name="forbidden_writes"),
            "proof_artifacts": _load_json_list(row["proof_artifacts_json"], field_name="proof_artifacts"),
            "stop_lines": _load_json_list(row["stop_lines_json"], field_name="stop_lines"),
            "acceptance_command": row["acceptance_command"],
            "lease_granted_at": row["lease_granted_at"],
            "lease_expires_at": row["lease_expires_at"],
            "start_at": row["start_at"],
            "end_at": row["end_at"],
            "incident_code": row["incident_code"],
            "incident_notes": row["incident_notes"],
            "session_id": row["session_id"],
            "runner_backend": row["runner_backend"],
            "expected_model": row["expected_model"],
            "actual_model": row["actual_model"],
        }

    def _candidate_collisions(
        self,
        connection: sqlite3.Connection,
        *,
        lane_id: str,
        requested_writes: list[str],
        statuses: tuple[str, ...] = ACTIVE_WRITE_STATUSES,
    ) -> list[dict[str, Any]]:
        if not requested_writes:
            return []

        collisions: list[dict[str, Any]] = []
        others = connection.execute(
            """
            SELECT lane_id, status, owner, allowed_writes_json
            FROM lanes
            WHERE lane_id != ? AND status IN ({placeholders})
            """.format(
                placeholders=", ".join("?" * len(statuses))
            ),
            (lane_id, *statuses),
        ).fetchall()

        for other in others:
            overlap = []
            for candidate in requested_writes:
                for existing in _load_json_list(other["allowed_writes_json"], field_name="allowed_writes"):
                    if _path_overlaps(candidate, existing):
                        overlap.append({"candidate": candidate, "existing": existing})
            if overlap:
                collisions.append(
                    {
                        "lane_id": lane_id,
                        "conflicting_lane": other["lane_id"],
                        "conflicting_status": other["status"],
                        "owner": other["owner"],
                        "overlaps": overlap,
                    }
                )
        return collisions

    def _check_forbidden_defaults(self, paths: list[str]) -> None:
        normalized_forbidden = [_normalize_path(value, self.project_root) for value in DEFAULT_FORBIDDEN_SURFACES]
        for path in paths:
            for forbidden in normalized_forbidden:
                if _path_overlaps(path, forbidden):
                    raise LaneManagerError(
                        f"Allowed write {path!r} is forbidden by policy (conflicts with {forbidden!r})"
                    )

    def _build_lane_id(self, workflow_id: str, workstream: str) -> str:
        if not workflow_id or not workflow_id.strip():
            raise LaneManagerError("workflow_id is required")
        if not workstream or not workstream.strip():
            raise LaneManagerError("workstream is required")
        return f"{workflow_id.strip()}::{workstream.strip()}"

    def plan_lane(
        self,
        *,
        parent_job_id: str,
        workflow_id: str,
        workstream: str,
        owner: str,
        lane_id: str | None = None,
        lane_mode: str = "write",
        allowed_writes: list[str] | None = None,
        forbidden_writes: list[str] | None = None,
        attempt_id: str | None = None,
        acceptance_command: str | None = None,
        stop_lines: list[str] | None = None,
        actor: str = "main-session",
        session_id: str | None = None,
        objective: str | None = None,
        job_owner: str | None = None,
        job_retry: int = 0,
        run_target: str | None = None,
    ) -> dict[str, Any]:
        """Register a planned lane (and its parent job) in the durable register."""
        if lane_mode not in WRITE_MODES:
            raise LaneManagerError(f"Invalid lane mode: {lane_mode!r}")
        if not owner.strip():
            raise LaneManagerError("owner is required")

        self._assert_workflow_known(workflow_id)

        requested_id = lane_id or self._build_lane_id(workflow_id, workstream)
        normalized_allowed = self._normalize_writes(
            allowed_writes,
            allow_empty=(lane_mode != "write"),
        )
        normalized_forbidden = self._normalize_writes(forbidden_writes, allow_empty=True)

        if lane_mode == "read-only" and normalized_allowed:
            raise LaneManagerError("Read-only lanes cannot declare allowed_writes")

        self._check_forbidden_defaults(normalized_allowed)

        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = self._bump_revision(connection)

            existing = self._fetch_lane(connection, requested_id)
            if existing is not None:
                raise LaneManagerError(
                    f"Lane already exists: {requested_id!r} (status={existing['status']})"
                )

            parent_job = self._load_parent_job(connection, parent_job_id)
            if parent_job is None:
                workflow = workflow_id.strip().upper()
                connection.execute(
                    "INSERT INTO parent_jobs "
                    "(job_id, workflow_id, workstream, owner, objective, parent_status, created_at, updated_at, acceptance_command, retry_count) "
                    "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 0)",
                    (
                        parent_job_id,
                        workflow,
                        workstream.strip(),
                        (job_owner or owner).strip(),
                        objective,
                        now,
                        now,
                        acceptance_command,
                    ),
                )
            else:
                expected_workflow_id = workflow_id.strip().upper()
                if parent_job["workflow_id"] != expected_workflow_id:
                    raise LaneManagerError(
                        "Parent job workflow_id mismatch: "
                        f"expected {parent_job['workflow_id']!r}, got {expected_workflow_id!r}"
                    )
                if job_owner is not None and parent_job["owner"] != job_owner.strip():
                    raise LaneManagerError(
                        "Parent job owner mismatch: "
                        f"expected {parent_job['owner']!r}, got {job_owner.strip()!r}"
                    )
                # Keep job metadata aligned with the latest explicit values.
                connection.execute(
                    "UPDATE parent_jobs "
                    "SET updated_at = ?, acceptance_command = COALESCE(?, acceptance_command) "
                    "WHERE job_id = ?",
                    (now, acceptance_command, parent_job_id),
                )

            collisions = self._candidate_collisions(
                connection,
                lane_id=requested_id,
                requested_writes=normalized_allowed,
                statuses=ACTIVE_WRITE_STATUSES,
            )
            if collisions:
                raise LaneManagerError(f"Collision detected while planning {requested_id}: {collisions}")

            effective_attempt = str(attempt_id or uuid.uuid4())
            connection.execute(
                "INSERT INTO lanes ("
                "lane_id, job_id, workflow_id, workstream, owner, lane_mode, status, "
                "created_at, updated_at, revision, attempt_id, attempt, retry_count, "
                "allowed_writes_json, forbidden_writes_json, proof_artifacts_json, stop_lines_json, "
                "acceptance_command, session_id, runner_backend" ") "
                "VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sqlite')",
                (
                    requested_id,
                    parent_job_id,
                    workflow_id.strip().upper(),
                    workstream.strip(),
                    owner,
                    lane_mode,
                    now,
                    now,
                    revision,
                    effective_attempt,
                    job_retry + 1,
                    job_retry,
                    _as_bool_json(_normalize_json_list(normalized_allowed)),
                    _as_bool_json(_normalize_json_list(normalized_forbidden)),
                    _as_bool_json(_normalize_json_list([])),
                    _as_bool_json(_normalize_json_list(stop_lines or [])),
                    acceptance_command,
                    session_id,
                ),
            )

            self._append_event(
                connection,
                lane_id=requested_id,
                attempt_id=effective_attempt,
                event_type="lane.planned",
                actor=actor,
                revision=revision,
                details={
                    "workstream": workstream,
                    "owner": owner,
                    "mode": lane_mode,
                    "allowed_writes": normalized_allowed,
                    "forbidden_writes": normalized_forbidden,
                    "job_id": parent_job_id,
                    "target": run_target,
                    "allowed_modes": WRITE_MODES,
                },
            )
            row = self._fetch_lane(connection, requested_id)
            return self._decode_fields(row)

    def lease_lane(
        self,
        lane_id: str,
        *,
        owner: str,
        duration_minutes: float = 120.0,
        actor: str = "main-session",
        refresh: bool = False,
        run_target: str | None = None,
    ) -> dict[str, Any]:
        """Grant or refresh a finite lease on a planned lane."""
        if not math.isfinite(duration_minutes) or duration_minutes <= 0:
            raise LaneManagerError("Lease duration_minutes must be a positive finite number")
        now = _utc_now()
        lease_expires = (
            datetime.now(timezone.utc) + timedelta(minutes=float(duration_minutes))
        ).replace(microsecond=0)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch_lane(connection, lane_id)
            if row is None:
                raise LaneManagerError(f"Unknown lane: {lane_id!r}")

            current = row["status"]
            allowed = row["allowed_writes_json"] if row["allowed_writes_json"] else "[]"
            requested_writes = _load_json_list(allowed, field_name="allowed_writes")

            if current == "leased" and not refresh:
                raise LaneManagerError(
                    f"Lane {lane_id!r} is already leased; use --refresh to extend"
                )
            if current not in ("planned", "leased"):
                raise LaneManagerError(
                    f"Cannot lease lane in {current!r} status: {lane_id!r}"
                )

            if row["owner"] != owner:
                raise LaneManagerError(
                    f"Owner mismatch for lane {lane_id!r}: expected {row['owner']!r}, got {owner!r}"
                )

            collisions = self._candidate_collisions(
                connection,
                lane_id=lane_id,
                requested_writes=requested_writes,
                statuses=ACTIVE_WRITE_STATUSES,
            )
            if collisions:
                raise LaneManagerError(f"Collision detected on lease for {lane_id}: {collisions}")

            revision = self._bump_revision(connection)
            accepted_status = "leased"
            now_utc = datetime.fromisoformat(now.replace("Z", "+00:00"))
            self._assert_status_transition(current=current, requested=accepted_status)

            connection.execute(
                "UPDATE lanes "
                "SET status=?, updated_at=?, revision=?, lease_granted_at=?, lease_expires_at=?, attempt_id=? "
                "WHERE lane_id=?",
                (
                    accepted_status,
                    now,
                    revision,
                    now,
                    lease_expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    row["attempt_id"],
                    lane_id,
                ),
            )

            self._append_event(
                connection,
                lane_id=lane_id,
                attempt_id=row["attempt_id"],
                event_type="lane.leased",
                actor=actor,
                revision=revision,
                details={
                    "lease_expires_at": lease_expires.isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    ),
                    "duration_minutes": duration_minutes,
                    "refresh": refresh,
                    "run_target": run_target,
                },
            )
            updated = self._fetch_lane(connection, lane_id)
            return self._decode_fields(updated)

    def start_lane(self, lane_id: str, *, actor: str = "main-session") -> dict[str, Any]:
        """Transition a leased lane to running, rejecting expired leases."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch_lane(connection, lane_id)
            if row is None:
                raise LaneManagerError(f"Unknown lane: {lane_id!r}")

            if row["status"] != "leased":
                raise LaneManagerError(f"Only leased lanes can start: {lane_id!r}")

            expires_at = _parse_utc(row["lease_expires_at"])
            if expires_at is None:
                raise LaneManagerError(f"Active lease metadata missing for {lane_id!r}")
            if datetime.now(timezone.utc) > expires_at:
                raise LaneManagerError(f"Lease expired for {lane_id!r}")

            revision = self._bump_revision(connection)
            self._assert_status_transition(current="leased", requested="running")
            connection.execute(
                "UPDATE lanes "
                "SET status=?, start_at=?, updated_at=?, revision=? "
                "WHERE lane_id=?",
                ("running", now, now, revision, lane_id),
            )
            self._append_event(
                connection,
                lane_id=lane_id,
                attempt_id=row["attempt_id"],
                event_type="lane.started",
                actor=actor,
                revision=revision,
                details={"status": "running"},
            )
            updated = self._fetch_lane(connection, lane_id)
            return self._decode_fields(updated)

    def set_status(
        self,
        lane_id: str,
        status_value: str,
        *,
        actor: str = "main-session",
        incident_code: str | None = None,
        incident_notes: str | None = None,
        proof_artifacts: list[str] | None = None,
        acceptance_command: str | None = None,
        force_proof: bool = False,
    ) -> dict[str, Any]:
        """Transition a lane to a new status, enforcing the transition table."""
        if status_value not in VALID_STATUSES:
            raise LaneManagerError(f"Invalid status value: {status_value!r}")

        now = _utc_now()
        proofs = self._normalize_writes(proof_artifacts, allow_empty=True) if proof_artifacts else []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._fetch_lane(connection, lane_id)
            if row is None:
                raise LaneManagerError(f"Unknown lane: {lane_id!r}")

            current = row["status"]
            self._assert_status_transition(current=current, requested=status_value)

            if status_value in {"complete", "cancelled", "blocked"} and current in {"running", "blocked", "cancelled", "complete", "leased", "planned"}:
                if status_value == "complete":
                    if not force_proof and not proofs:
                        raise LaneManagerError(
                            f"Complete status for lane {lane_id!r} requires proof artifacts"
                        )
                    for proof_path in proofs:
                        if not Path(proof_path).exists():
                            raise LaneManagerError(
                                f"Proof artifact does not exist: {proof_path!r}"
                            )

            revision = self._bump_revision(connection)

            next_status = status_value
            end_at = row["end_at"]
            if status_value in {"complete", "cancelled", "blocked"}:
                end_at = now

            proof_payload = proofs if proofs else _load_json_list(row["proof_artifacts_json"], field_name="proof_artifacts")
            connection.execute(
                "UPDATE lanes "
                "SET status=?, incident_code=?, incident_notes=?, updated_at=?, revision=?, "
                "end_at=?, acceptance_command=COALESCE(?, acceptance_command), "
                "proof_artifacts_json=? "
                "WHERE lane_id=?",
                (
                    next_status,
                    incident_code,
                    incident_notes,
                    now,
                    revision,
                    end_at,
                    acceptance_command,
                    _as_bool_json(_normalize_json_list(proof_payload)),
                    lane_id,
                ),
            )
            self._append_event(
                connection,
                lane_id=lane_id,
                attempt_id=row["attempt_id"],
                event_type=f"lane.{next_status}",
                actor=actor,
                revision=revision,
                details={
                    "from": current,
                    "to": status_value,
                    "incident_code": incident_code,
                    "incident_notes": incident_notes,
                    "proof_artifacts": proof_payload,
                },
            )
            return self._decode_fields(self._fetch_lane(connection, lane_id))

    def complete_lane(
        self,
        lane_id: str,
        *,
        actor: str = "main-session",
        proof_artifacts: list[str] | None = None,
        acceptance_command: str | None = None,
        force_proof: bool = False,
    ) -> dict[str, Any]:
        """Complete a lane, requiring proof artifacts unless force_proof is set."""
        return self.set_status(
            lane_id,
            "complete",
            actor=actor,
            proof_artifacts=proof_artifacts,
            acceptance_command=acceptance_command,
            force_proof=force_proof,
        )

    def list_lanes(
        self,
        *,
        lane_id: str | None = None,
        include_terminal: bool = True,
        status_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List lanes, optionally filtered by ID, status, or terminal state."""
        with self._connect() as connection:
            rows = self._iter_lane_rows(connection, lane_id=lane_id)
            payload: list[dict[str, Any]] = []
            for row in rows:
                lane = self._decode_fields(row)
                if status_filter and lane["status"] not in status_filter:
                    continue
                if not include_terminal and lane["status"] in {"complete", "cancelled"}:
                    continue
                payload.append(lane)
            return payload

    def validate(self) -> dict[str, Any]:
        """Sweep the register for collisions, expired leases, and integrity issues."""
        now = datetime.now(timezone.utc)
        hard_failures: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        collisions: list[dict[str, Any]] = []
        expired_leases: list[dict[str, str]] = []
        active_rows: list[sqlite3.Row] = []
        running_rows: list[sqlite3.Row] = []
        terminal_lanes: list[dict[str, Any]] = []

        with self._connect() as connection:
            lanes = connection.execute("SELECT * FROM lanes ORDER BY lane_id ASC").fetchall()
            parent_jobs = {
                row["job_id"]: row
                for row in connection.execute("SELECT * FROM parent_jobs").fetchall()
            }
            total_lanes = len(lanes)

            for row in lanes:
                lane = self._decode_fields(row)
                if lane["status"] in ACTIVE_WRITE_STATUSES:
                    active_rows.append(row)
                if lane["status"] == "running":
                    running_rows.append(row)

                # Basic shape checks.
                if not lane["owner"]:
                    hard_failures.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "missing_owner",
                            "message": "Lane owner is required.",
                        }
                    )

                parent_job = parent_jobs.get(lane["job_id"])
                if parent_job is None:
                    hard_failures.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "missing_parent_job",
                            "message": "Lane references a missing parent job.",
                        }
                    )
                elif parent_job["workflow_id"] != lane["workflow_id"]:
                    hard_failures.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "parent_workflow_mismatch",
                            "message": "Lane workflow_id does not match its parent job.",
                        }
                    )

                if lane["status"] in {"running", "leased"} and not lane["lease_expires_at"]:
                    hard_failures.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "missing_lease",
                            "message": "Active leased/running lane must have a lease expiration.",
                        }
                    )

                if lane["status"] == "running" and not lane["start_at"]:
                    hard_failures.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "running_without_start",
                            "message": "Running lane must have start_at.",
                        }
                    )

                if lane["status"] in {"complete", "cancelled", "blocked"} and not lane["end_at"]:
                    warnings.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "missing_end_at",
                            "message": f"Terminal lane {lane['status']} has no end_at.",
                        }
                    )

                if lane["status"] == "complete":
                    proof_count = len(lane["proof_artifacts"])
                    if proof_count == 0:
                        hard_failures.append(
                            {
                                "lane_id": lane["lane_id"],
                                "code": "complete_without_proof",
                                "message": "Complete lanes must include proof_artifacts.",
                            }
                        )
                    terminal_lanes.append(
                        {"lane_id": lane["lane_id"], "proof_count": proof_count}
                    )

                if lane["mode"] == "write" and not lane["allowed_writes"]:
                    warnings.append(
                        {
                            "lane_id": lane["lane_id"],
                            "code": "empty_allowed_writes",
                            "message": "Write lane has no declared allowed_writes.",
                        }
                    )

            # Collision and forbidden checks are on active, potentially conflicting lanes.
            for idx, current in enumerate(active_rows):
                current_writes = _load_json_list(current["allowed_writes_json"], field_name="allowed_writes")
                current_status = current["status"]
                current_lane_id = current["lane_id"]
                if current_status == "running" and not current["start_at"]:
                    hard_failures.append(
                        {
                            "lane_id": current_lane_id,
                            "code": "running_without_start",
                            "message": "Active running lane missing start timestamp.",
                        }
                    )
                lease_expires = _parse_utc(current["lease_expires_at"])
                if current_status in {"leased", "running"}:
                    if lease_expires is None or now > lease_expires:
                        expired = {
                            "lane_id": current_lane_id,
                            "status": current_status,
                            "lease_expires_at": current["lease_expires_at"],
                        }
                        expired_leases.append(expired)
                        hard_failures.append(
                            {
                                **expired,
                                "code": "expired_lease",
                                "message": "Active lane lease has expired or is invalid.",
                            }
                        )

                for forbidden in _load_json_list(current["forbidden_writes_json"], field_name="forbidden_writes"):
                    for candidate in current_writes:
                        if _path_overlaps(candidate, forbidden):
                            hard_failures.append(
                                {
                                    "lane_id": current_lane_id,
                                    "code": "lane_forbidden_overlap",
                                    "message": f"Lane {current_lane_id!r} allowed write overlaps forbidden policy {forbidden!r}",
                                }
                            )

                for j in range(idx + 1, len(active_rows)):
                    other = active_rows[j]
                    other_writes = _load_json_list(other["allowed_writes_json"], field_name="allowed_writes")
                    if other["lane_mode"] != "write" or current["lane_mode"] != "write":
                        continue
                    overlaps = []
                    for current_path in current_writes:
                        for other_path in other_writes:
                            if _path_overlaps(current_path, other_path):
                                overlaps.append({"current": current_path, "other": other_path})
                    if overlaps:
                        collisions.append(
                            {
                                "lane_a": current["lane_id"],
                                "lane_b": other["lane_id"],
                                "overlaps": overlaps,
                            }
                        )

            return {
                "ok": len(hard_failures) == 0,
                "register": self.register,
                "revision": self._get_revision(connection),
                "generated_at": now.isoformat().replace("+00:00", "Z"),
                "lane_count": total_lanes,
                "active_lanes": len(active_rows),
                "running_lanes": len(running_rows),
                "hard_failures": hard_failures,
                "warnings": warnings,
                "collisions": collisions,
                "expired_leases": expired_leases,
                "terminal_lanes": terminal_lanes,
                "active_lanes_snapshot": [self._decode_fields(row) for row in active_rows],
            }

    def status_packet(self, *, include_terminal: bool = False) -> dict[str, Any]:
        """Return a compact status packet for the register and its lanes."""
        with self._connect() as connection:
            register_revision = self._get_revision(connection)

        return {
            "project_root": str(self.project_root.as_posix()),
            "register": self.register,
            "generated_at": _utc_now(),
            "register_revision": register_revision,
            "lanes": self.list_lanes(include_terminal=include_terminal),
        }


def _parse_args() -> argparse.Namespace:
    """Parse the lane-manager CLI (plan/lease/start/complete/status subcommands)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Workspace root (default: current workspace)",
    )
    parser.add_argument(
        "--register",
        default=str(DEFAULT_REGISTER_PATH),
        help="Path for sqlite lane register",
    )
    parser.add_argument("--actor", default="main-session", help="Actor metadata for ledger events")

    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Register a planned lane")
    plan_parser.add_argument("--parent-job-id", required=True)
    plan_parser.add_argument("--workflow-id", required=True)
    plan_parser.add_argument("--workstream", required=True)
    plan_parser.add_argument("--owner", required=True)
    plan_parser.add_argument("--lane-id", default=None)
    plan_parser.add_argument("--mode", default="write", choices=WRITE_MODES)
    plan_parser.add_argument("--allowed-write", action="append", default=[])
    plan_parser.add_argument("--forbidden-write", action="append", default=[])
    plan_parser.add_argument("--job-owner", default=None)
    plan_parser.add_argument("--objective", default=None)
    plan_parser.add_argument("--stop-line", action="append", default=[])
    plan_parser.add_argument("--attempt-id", default=None)
    plan_parser.add_argument("--acceptance-command", default=None)
    plan_parser.add_argument("--retry", type=int, default=0)
    plan_parser.add_argument("--session-id", default=None)

    lease_parser = subparsers.add_parser("lease", help="Grant or refresh a lease")
    lease_parser.add_argument("lane_id")
    lease_parser.add_argument("--owner", required=True)
    lease_parser.add_argument("--duration-minutes", default=120.0, type=float)
    lease_parser.add_argument("--refresh", action="store_true")
    lease_parser.add_argument("--run-target", default=None)

    start_parser = subparsers.add_parser("start", help="Start a leased lane")
    start_parser.add_argument("lane_id")

    status_parser = subparsers.add_parser("status", help="Show current lanes")
    status_parser.add_argument("--lane-id", default=None)
    status_parser.add_argument("--status", action="append", default=[])  # filter
    status_parser.add_argument("--include-terminal", action="store_true")
    status_parser.add_argument("--validate", action="store_true")

    set_status_parser = subparsers.add_parser(
        "set-status", help="Set lane status with required transition checks"
    )
    set_status_parser.add_argument("lane_id")
    set_status_parser.add_argument("--status-value", required=True, choices=VALID_STATUSES)
    set_status_parser.add_argument("--incident-code", default=None)
    set_status_parser.add_argument("--incident-notes", default=None)
    set_status_parser.add_argument("--proof", action="append", default=[])
    set_status_parser.add_argument("--acceptance-command", default=None)
    set_status_parser.add_argument("--force-proof", action="store_true")

    complete_parser = subparsers.add_parser("complete", help="Mark a lane complete")
    complete_parser.add_argument("lane_id")
    complete_parser.add_argument("--proof", action="append", default=[])
    complete_parser.add_argument("--acceptance-command", default=None)
    complete_parser.add_argument("--force-proof", action="store_true")

    block_parser = subparsers.add_parser("block", help="Mark a lane blocked")
    block_parser.add_argument("lane_id")
    block_parser.add_argument("incident_code")
    block_parser.add_argument("--notes", default="")

    validate_parser = subparsers.add_parser("validate", help="Run validator and print results")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a lane")
    cancel_parser.add_argument("lane_id")
    cancel_parser.add_argument("incident_code")

    return parser.parse_args()


def _normalize_output(value: Any) -> dict[str, Any]:
    """Wrap a non-dict result in a ``{"result": ...}`` envelope."""
    if isinstance(value, dict):
        return value
    return {"result": value}


def main() -> int:
    """Dispatch the lane-manager CLI command and print the JSON result."""
    arguments = _parse_args()
    try:
        project_root = Path(arguments.project_root)
        register_path = Path(arguments.register)
        manager = ConcurrentLaneManager(project_root=project_root, register_path=register_path)

        if arguments.command == "plan":
            result = manager.plan_lane(
                parent_job_id=arguments.parent_job_id,
                workflow_id=arguments.workflow_id,
                workstream=arguments.workstream,
                owner=arguments.owner,
                lane_id=arguments.lane_id,
                lane_mode=arguments.mode,
                allowed_writes=arguments.allowed_write,
                forbidden_writes=arguments.forbidden_write,
                attempt_id=arguments.attempt_id,
                acceptance_command=arguments.acceptance_command,
                stop_lines=arguments.stop_line,
                actor=arguments.actor,
                session_id=arguments.session_id,
                objective=arguments.objective,
                job_owner=arguments.job_owner,
                job_retry=arguments.retry,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "lease":
            result = manager.lease_lane(
                arguments.lane_id,
                owner=arguments.owner,
                duration_minutes=arguments.duration_minutes,
                actor=arguments.actor,
                refresh=arguments.refresh,
                run_target=arguments.run_target,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "start":
            result = manager.start_lane(arguments.lane_id, actor=arguments.actor)
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "set-status":
            result = manager.set_status(
                arguments.lane_id,
                arguments.status_value,
                actor=arguments.actor,
                incident_code=arguments.incident_code,
                incident_notes=arguments.incident_notes,
                proof_artifacts=arguments.proof,
                acceptance_command=arguments.acceptance_command,
                force_proof=arguments.force_proof,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "complete":
            result = manager.complete_lane(
                arguments.lane_id,
                actor=arguments.actor,
                proof_artifacts=arguments.proof,
                acceptance_command=arguments.acceptance_command,
                force_proof=arguments.force_proof,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "cancel":
            result = manager.set_status(
                arguments.lane_id,
                "cancelled",
                actor=arguments.actor,
                incident_code=arguments.incident_code,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "block":
            result = manager.set_status(
                arguments.lane_id,
                "blocked",
                actor=arguments.actor,
                incident_code=arguments.incident_code,
                incident_notes=arguments.notes,
            )
            print(json.dumps(_normalize_output(result), indent=2, sort_keys=True))
            return 0

        if arguments.command == "status":
            payload = manager.status_packet(include_terminal=arguments.include_terminal)
            lanes = payload["lanes"]
            if arguments.status:
                filter_statuses = set(arguments.status)
                lanes = [lane for lane in lanes if lane["status"] in filter_statuses]
                payload["lanes"] = lanes
            if arguments.validate:
                payload["validation"] = manager.validate()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        if arguments.command == "validate":
            payload = manager.validate()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if payload.get("ok") else 2

        return 1
    except LaneManagerError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
