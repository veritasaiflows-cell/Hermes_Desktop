#!/usr/bin/env python3
"""Compact profile-local turn telemetry into canonical proof and prune raw rows.

The storage contract is deliberately flat:
- one profile-local SQLite database contains recent raw metadata;
- the existing canonical SQLite database contains compact daily proof;
- no per-turn files are created;
- optional monthly archives live outside the workspace.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB
from hermes_constants import get_hermes_home

REPORT_SCHEMA = "telemetry-retention-report.v1"
ROLLUP_SCHEMA = "turn-telemetry-daily-rollup.v1"
ROLLUP_EVENT_TYPE = "telemetry.daily_rollup.v1"
ROLLUP_VALIDATOR = "telemetry_retention.daily_rollup.v1"
ARCHIVE_SCHEMA = "turn-telemetry-monthly-archive.v1"
ARCHIVE_EVENT_TYPE = "telemetry.monthly_archive.v1"
ARCHIVE_VALIDATOR = "telemetry_retention.monthly_archive.v1"
DEFAULT_HERMES_HOME = get_hermes_home()
DEFAULT_TURN_DATABASE = DEFAULT_HERMES_HOME / "telemetry" / "turn-metrics.sqlite"
DEFAULT_CANONICAL_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
DEFAULT_ARCHIVE_DIRECTORY = DEFAULT_HERMES_HOME / "telemetry" / "archive"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_PROTECTED_RECENT_DAYS = 7
DEFAULT_MAX_DATABASE_BYTES = 10 * 1024 * 1024
DELETE_BATCH_SIZE = 500
MAX_INCREMENTAL_VACUUM_STEPS = 4096
MAINTENANCE_LOCK_LEASE = timedelta(hours=1)
_MAINTENANCE_LOCK_NAME = "retention"
_MAINTENANCE_LOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_maintenance_lock (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
"""
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
_SAFE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_SAFE_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")

# Explicit allowlist: future raw/content-bearing columns are excluded by default.
SAFE_TURN_COLUMNS = (
    "turn_key",
    "session_key",
    "task_key",
    "platform",
    "model",
    "provider",
    "started_at",
    "completed_at",
    "duration_ms",
    "api_request_count",
    "api_error_count",
    "retry_count",
    "api_duration_ms",
    "api_work_ms",
    "api_wall_ms",
    "tool_round_count",
    "tool_call_count",
    "tool_error_count",
    "tool_duration_ms",
    "tool_names_json",
    "approx_input_tokens",
    "message_count",
    "non_provider_tool_ms",
    "final_response_seen",
    "outcome",
    "error_category",
    "collector_version",
    "metric_semantics",
    "tool_work_ms",
    "tool_wall_ms",
    "observed_external_wall_ms",
    "error_cause_types_json",
    "error_status_codes_json",
    "error_host",
    "error_diagnostics_dropped_count",
    "tool_diagnostics_dropped_count",
    "created_at",
)
_VERSIONED_TIMING_COLUMNS = (
    "api_work_ms",
    "api_wall_ms",
    "tool_work_ms",
    "tool_wall_ms",
    "observed_external_wall_ms",
)
_VERSIONED_ONLY_COLUMNS = (
    "collector_version",
    "metric_semantics",
    *_VERSIONED_TIMING_COLUMNS,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Telemetry timestamps require an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_timestamp_utc(value: str) -> str:
    return _parse_timestamp(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _acquire_maintenance_lock(database_path: Path, *, now: datetime) -> str:
    owner_id = secrets.token_hex(16)
    expires_at = (now.astimezone(timezone.utc) + MAINTENANCE_LOCK_LEASE).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        connection.execute(_MAINTENANCE_LOCK_SCHEMA)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT owner_id, expires_at FROM telemetry_maintenance_lock "
            "WHERE lock_name = ?",
            (_MAINTENANCE_LOCK_NAME,),
        ).fetchone()
        if existing is not None:
            try:
                active_until = _parse_timestamp(str(existing[1]))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Telemetry maintenance lock is malformed") from exc
            if active_until > now.astimezone(timezone.utc):
                raise RuntimeError("Telemetry maintenance is already active")
            connection.execute(
                "DELETE FROM telemetry_maintenance_lock WHERE lock_name = ?",
                (_MAINTENANCE_LOCK_NAME,),
            )
        connection.execute(
            "INSERT INTO telemetry_maintenance_lock(lock_name, owner_id, expires_at) "
            "VALUES (?, ?, ?)",
            (_MAINTENANCE_LOCK_NAME, owner_id, expires_at),
        )
        connection.commit()
        return owner_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _release_maintenance_lock(database_path: Path, *, owner_id: str) -> None:
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM telemetry_maintenance_lock "
            "WHERE lock_name = ? AND owner_id = ?",
            (_MAINTENANCE_LOCK_NAME, owner_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def _maintenance_lock(database_path: Path, *, now: datetime) -> Iterator[None]:
    owner_id = _acquire_maintenance_lock(database_path, now=now)
    try:
        yield
    finally:
        _release_maintenance_lock(database_path, owner_id=owner_id)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _available_safe_columns(connection: sqlite3.Connection) -> list[str]:
    existing = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(turn_metrics)")
    }
    required = {"turn_key", "completed_at"}
    missing = sorted(required - existing)
    if missing:
        raise ValueError(f"turn_metrics missing required columns: {', '.join(missing)}")
    return [column for column in SAFE_TURN_COLUMNS if column in existing]


def _read_rows(
    connection: sqlite3.Connection,
    *,
    columns: list[str],
    where: str = "1=1",
    parameters: Iterable[object] = (),
) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    selected = ",".join(f'"{column}"' for column in columns)
    rows = connection.execute(
        f"SELECT {selected} FROM turn_metrics WHERE {where} "
        "ORDER BY completed_at, turn_key",
        tuple(parameters),
    ).fetchall()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(_normalize_safe_row(dict(row)))
    normalized.sort(
        key=lambda row: (str(row.get("completed_at") or ""), str(row["turn_key"]))
    )
    return normalized


def _normalize_safe_row(value: dict[str, Any]) -> dict[str, Any]:
    for column in ("started_at", "completed_at", "created_at"):
        if value.get(column):
            value[column] = _format_timestamp_utc(str(value[column]))
    if not value.get("metric_semantics"):
        for column in _VERSIONED_ONLY_COLUMNS:
            value.pop(column, None)
    _validate_safe_row(value)
    return value


def _unsafe_value(column: str) -> ValueError:
    return ValueError(f"Unsafe telemetry value in {column}")


def _validate_token(value: Any, column: str, *, optional: bool = True) -> None:
    if value in (None, "") and optional:
        return
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise _unsafe_value(column)


def _validate_json_list(
    value: Any,
    column: str,
    *,
    maximum_items: int,
    item_validator,
) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str):
        raise _unsafe_value(column)
    try:
        items = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _unsafe_value(column) from exc
    if not isinstance(items, list) or len(items) > maximum_items:
        raise _unsafe_value(column)
    for item in items:
        if not item_validator(item):
            raise _unsafe_value(column)


def _validate_safe_row(value: dict[str, Any]) -> None:
    _validate_token(value.get("turn_key"), "turn_key", optional=False)
    for column in (
        "session_key",
        "task_key",
        "platform",
        "model",
        "provider",
        "outcome",
        "collector_version",
        "metric_semantics",
    ):
        _validate_token(value.get(column), column)

    error_category = value.get("error_category")
    if error_category not in (None, ""):
        if not isinstance(error_category, str) or any(
            not _SAFE_EXCEPTION_RE.fullmatch(item)
            for item in error_category.split(",")
        ):
            raise _unsafe_value("error_category")

    host = value.get("error_host")
    if host not in (None, "") and (
        not isinstance(host, str) or not _SAFE_HOST_RE.fullmatch(host)
    ):
        raise _unsafe_value("error_host")

    _validate_json_list(
        value.get("tool_names_json"),
        "tool_names_json",
        maximum_items=20,
        item_validator=lambda item: isinstance(item, str)
        and bool(_SAFE_TOKEN_RE.fullmatch(item)),
    )
    _validate_json_list(
        value.get("error_cause_types_json"),
        "error_cause_types_json",
        maximum_items=8,
        item_validator=lambda item: isinstance(item, str)
        and bool(_SAFE_EXCEPTION_RE.fullmatch(item)),
    )
    _validate_json_list(
        value.get("error_status_codes_json"),
        "error_status_codes_json",
        maximum_items=16,
        item_validator=lambda item: isinstance(item, int)
        and not isinstance(item, bool)
        and 100 <= item <= 599,
    )

    integer_columns = (
        "duration_ms",
        "api_request_count",
        "api_error_count",
        "retry_count",
        "api_duration_ms",
        "api_work_ms",
        "api_wall_ms",
        "tool_round_count",
        "tool_call_count",
        "tool_error_count",
        "tool_duration_ms",
        "tool_work_ms",
        "tool_wall_ms",
        "approx_input_tokens",
        "message_count",
        "non_provider_tool_ms",
        "observed_external_wall_ms",
        "error_diagnostics_dropped_count",
        "tool_diagnostics_dropped_count",
    )
    for column in integer_columns:
        item = value.get(column)
        if item is not None and (
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 <= item <= 1_000_000_000_000
        ):
            raise _unsafe_value(column)
    final_response_seen = value.get("final_response_seen")
    if final_response_seen is not None and final_response_seen not in (0, 1):
        raise _unsafe_value("final_response_seen")


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return int(round(ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction))


def _sum(rows: list[dict[str, Any]], column: str) -> int:
    return sum(int(row.get(column) or 0) for row in rows)


def _daily_rollup(
    day: str,
    rows: list[dict[str, Any]],
    *,
    generated_at: str,
    previous_rollup_sha256: str | None,
) -> dict[str, Any]:
    source_sha256 = _sha256(rows)
    durations = [int(row.get("duration_ms") or 0) for row in rows]
    outcomes = Counter(str(row.get("outcome") or "unknown") for row in rows)
    providers = Counter(
        f"{row.get('provider') or 'unknown'}:{row.get('model') or 'unknown'}"
        for row in rows
    )
    versions = sorted(
        {
            str(row.get("collector_version"))
            for row in rows
            if row.get("collector_version")
        }
    )
    semantics = sorted(
        {
            str(row.get("metric_semantics"))
            for row in rows
            if row.get("metric_semantics")
        }
    )
    payload: dict[str, Any] = {
        "schema": ROLLUP_SCHEMA,
        "day": day,
        "generated_at": generated_at,
        "source_row_count": len(rows),
        "source_sha256": source_sha256,
        "previous_rollup_sha256": previous_rollup_sha256,
        "collector_versions": versions,
        "metric_semantics": semantics,
        "versioned_timing_row_count": sum(
            1 for row in rows if row.get("metric_semantics")
        ),
        "period_start": str(rows[0]["completed_at"]),
        "period_end": str(rows[-1]["completed_at"]),
        "outcomes": dict(sorted(outcomes.items())),
        "provider_models": dict(sorted(providers.items())),
        "api_request_count": _sum(rows, "api_request_count"),
        "api_error_count": _sum(rows, "api_error_count"),
        "retry_count": _sum(rows, "retry_count"),
        "tool_call_count": _sum(rows, "tool_call_count"),
        "tool_error_count": _sum(rows, "tool_error_count"),
        "duration_p50_ms": _percentile(durations, 0.50),
        "duration_p95_ms": _percentile(durations, 0.95),
        "duration_max_ms": max(durations) if durations else None,
        "max_peak_input_tokens": max(
            (int(row.get("approx_input_tokens") or 0) for row in rows),
            default=0,
        ),
    }
    for column in _VERSIONED_TIMING_COLUMNS:
        if any(row.get(column) is not None for row in rows):
            payload[f"{column}_total"] = _sum(rows, column)
    payload["rollup_sha256"] = _sha256(payload)
    return payload


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validated_rollup_ledger(db: CanonicalDB) -> list[dict[str, Any]]:
    rows = db.connection.execute(
        "SELECT e.payload_json, e.subject_id, e.provenance_id, p.content_hash "
        "FROM events AS e "
        "LEFT JOIN provenance AS p ON p.provenance_id = e.provenance_id "
        "WHERE e.event_type = ? ORDER BY e.rowid",
        (ROLLUP_EVENT_TYPE,),
    ).fetchall()
    ledger: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for payload_json, subject_id, provenance_id, provenance_hash in rows:
        try:
            payload = json.loads(str(payload_json))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid telemetry rollup ledger payload") from exc
        if not isinstance(payload, dict) or payload.get("schema") != ROLLUP_SCHEMA:
            raise ValueError("Invalid telemetry rollup ledger schema")
        day = str(payload.get("day") or "")
        if str(subject_id) != f"turn-telemetry:{day}":
            raise ValueError("Invalid telemetry rollup ledger subject")
        source_hash = payload.get("source_sha256")
        rollup_hash = payload.get("rollup_sha256")
        if not _is_sha256(source_hash) or not _is_sha256(rollup_hash):
            raise ValueError("Invalid telemetry rollup ledger hash")
        hash_payload = dict(payload)
        hash_payload.pop("rollup_sha256", None)
        if _sha256(hash_payload) != rollup_hash:
            raise ValueError("Invalid telemetry rollup ledger content hash")
        if payload.get("previous_rollup_sha256") != previous_hash:
            raise ValueError("Invalid telemetry rollup ledger chain")
        if provenance_hash != source_hash or not provenance_id:
            raise ValueError("Invalid telemetry rollup ledger provenance")

        validations = db.connection.execute(
            "SELECT evidence_json, provenance_id FROM validation_results "
            "WHERE subject_type = 'telemetry_daily_rollup' AND subject_id = ? "
            "AND validator = ? AND result = 'pass'",
            (subject_id, ROLLUP_VALIDATOR),
        ).fetchall()
        expected_evidence = {
            "schema": ROLLUP_SCHEMA,
            "source_row_count": payload.get("source_row_count"),
            "source_sha256": source_hash,
            "rollup_sha256": rollup_hash,
            "previous_rollup_sha256": previous_hash,
        }
        validation_matches = False
        for evidence_json, validation_provenance_id in validations:
            try:
                evidence = json.loads(str(evidence_json))
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                validation_provenance_id == provenance_id
                and evidence == expected_evidence
            ):
                validation_matches = True
                break
        if not validation_matches:
            raise ValueError("Invalid telemetry rollup ledger validation")
        ledger.append(payload)
        previous_hash = str(rollup_hash)
    return ledger


def _latest_rollup_hash(db: CanonicalDB) -> str | None:
    ledger = _validated_rollup_ledger(db)
    return str(ledger[-1]["rollup_sha256"]) if ledger else None


def _matching_rollup(
    db: CanonicalDB,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    for existing in _validated_rollup_ledger(db):
        if (
            existing.get("day") == payload["day"]
            and existing.get("source_sha256") == payload["source_sha256"]
            and existing.get("source_row_count") == payload["source_row_count"]
        ):
            return existing
    return None


def _persist_rollup(db: CanonicalDB, payload: dict[str, Any]) -> tuple[bool, str]:
    existing = _matching_rollup(db, payload)
    if existing is not None:
        return False, str(existing["rollup_sha256"])
    suffix = str(payload["source_sha256"])
    day = str(payload["day"])
    provenance_id = f"telemetry-rollup-provenance:{day}:{suffix}"
    event_id = f"telemetry-rollup-event:{day}:{suffix}"
    validation_id = f"telemetry-rollup-validation:{day}:{suffix}"
    evidence = {
        "schema": ROLLUP_SCHEMA,
        "source_row_count": payload["source_row_count"],
        "source_sha256": payload["source_sha256"],
        "rollup_sha256": payload["rollup_sha256"],
        "previous_rollup_sha256": payload["previous_rollup_sha256"],
    }
    with db.transaction():
        db.add_provenance(
            source_type="telemetry_rollup",
            source_ref=f"turn-metrics:{day}",
            captured_at=str(payload["generated_at"]),
            content_hash=str(payload["source_sha256"]),
            confidence=1.0,
            notes="Metadata-only daily turn telemetry rollup",
            provenance_id=provenance_id,
        )
        db.insert(
            "events",
            {
                "event_type": ROLLUP_EVENT_TYPE,
                "subject_type": "telemetry_daily_rollup",
                "subject_id": f"turn-telemetry:{day}",
                "payload_json": _canonical_json(payload),
                "occurred_at": str(payload["generated_at"]),
                "recorded_at": str(payload["generated_at"]),
            },
            record_id=event_id,
            provenance_id=provenance_id,
        )
        db.insert(
            "validation_results",
            {
                "subject_type": "telemetry_daily_rollup",
                "subject_id": f"turn-telemetry:{day}",
                "validator": ROLLUP_VALIDATOR,
                "result": "pass",
                "evidence_json": _canonical_json(evidence),
                "validated_at": str(payload["generated_at"]),
            },
            record_id=validation_id,
            provenance_id=provenance_id,
        )
    return True, str(payload["rollup_sha256"])


def _proven_days(db: CanonicalDB) -> set[str]:
    return {str(payload["day"]) for payload in _validated_rollup_ledger(db)}


def _gzip_json_lines(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as handle:
        for row in rows:
            handle.write((_canonical_json(row) + "\n").encode("utf-8"))
    return buffer.getvalue()


def _read_archive(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    seen_turn_keys: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if (
                not isinstance(value, dict)
                or not value.get("turn_key")
                or not value.get("completed_at")
            ):
                raise ValueError(f"Invalid telemetry archive row in {path}")
            unexpected = set(value) - set(SAFE_TURN_COLUMNS)
            if unexpected:
                raise ValueError(f"Unsafe telemetry archive row in {path}")
            normalized = _normalize_safe_row(value)
            turn_key = str(normalized["turn_key"])
            if turn_key in seen_turn_keys:
                raise ValueError(f"Telemetry archive contains a duplicate turn key in {path}")
            seen_turn_keys.add(turn_key)
            rows.append(normalized)
    return rows


def _validated_archive_manifests(
    db: CanonicalDB,
    month: str,
) -> list[dict[str, Any]]:
    subject_id = f"turn-telemetry-archive:{month}"
    rows = db.connection.execute(
        "SELECT e.payload_json, e.provenance_id, p.content_hash "
        "FROM events AS e "
        "LEFT JOIN provenance AS p ON p.provenance_id = e.provenance_id "
        "WHERE e.event_type = ? AND e.subject_id = ? ORDER BY e.rowid",
        (ARCHIVE_EVENT_TYPE, subject_id),
    ).fetchall()
    manifests: list[dict[str, Any]] = []
    for payload_json, provenance_id, provenance_hash in rows:
        try:
            manifest = json.loads(str(payload_json))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid canonical archive manifest") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != ARCHIVE_SCHEMA
            or manifest.get("month") != month
            or manifest.get("archive_name") != f"{month}.jsonl.gz"
        ):
            raise ValueError("Invalid canonical archive manifest")
        archive_hash = manifest.get("archive_sha256")
        manifest_hash = manifest.get("manifest_sha256")
        if not _is_sha256(archive_hash) or not _is_sha256(manifest_hash):
            raise ValueError("Invalid canonical archive manifest")
        hash_payload = dict(manifest)
        hash_payload.pop("manifest_sha256", None)
        if _sha256(hash_payload) != manifest_hash:
            raise ValueError("Invalid canonical archive manifest")
        if not provenance_id or provenance_hash != archive_hash:
            raise ValueError("Invalid canonical archive manifest provenance")
        expected_evidence = {
            "schema": ARCHIVE_SCHEMA,
            "archive_sha256": archive_hash,
            "source_row_count": manifest.get("source_row_count"),
            "manifest_sha256": manifest_hash,
        }
        validations = db.connection.execute(
            "SELECT evidence_json, provenance_id FROM validation_results "
            "WHERE subject_type = 'telemetry_monthly_archive' AND subject_id = ? "
            "AND validator = ? AND result = 'pass'",
            (subject_id, ARCHIVE_VALIDATOR),
        ).fetchall()
        matched = False
        for evidence_json, validation_provenance_id in validations:
            try:
                evidence = json.loads(str(evidence_json))
            except (TypeError, json.JSONDecodeError):
                continue
            if validation_provenance_id == provenance_id and evidence == expected_evidence:
                matched = True
                break
        if not matched:
            raise ValueError("Invalid canonical archive manifest validation")
        manifests.append(manifest)
    return manifests


def _verify_existing_archive(
    db: CanonicalDB,
    *,
    path: Path,
    month: str,
) -> None:
    manifests = _validated_archive_manifests(db, month)
    if not path.is_file():
        if manifests:
            raise ValueError("Canonical archive manifest exists without archive bytes")
        return
    if not manifests:
        raise ValueError("Existing archive has no canonical archive manifest")
    latest = manifests[-1]
    archive_bytes = path.read_bytes()
    if (
        hashlib.sha256(archive_bytes).hexdigest() != latest["archive_sha256"]
        or len(archive_bytes) != latest["archive_bytes"]
    ):
        raise ValueError("Existing archive does not match canonical archive manifest")


def _atomic_replace_archive(
    path: Path,
    archive_bytes: bytes,
    *,
    expected_current_sha256: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Telemetry archive destination must not be a symlink")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(archive_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_path.read_bytes() != archive_bytes:
            raise OSError("Telemetry archive temporary read-back mismatch")
        current_hash = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
        if current_hash != expected_current_sha256:
            raise RuntimeError("Telemetry archive changed during maintenance")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _persist_archive_manifest(
    db: CanonicalDB,
    *,
    manifest: dict[str, Any],
) -> None:
    month = str(manifest["month"])
    archive_hash = str(manifest["archive_sha256"])
    subject_id = f"turn-telemetry-archive:{month}"
    for existing in _validated_archive_manifests(db, month):
        if existing.get("archive_sha256") == archive_hash:
            return
    suffix = archive_hash
    provenance_id = f"telemetry-archive-provenance:{month}:{suffix}"
    with db.transaction():
        db.add_provenance(
            source_type="telemetry_archive",
            source_ref=str(manifest["archive_name"]),
            captured_at=str(manifest["generated_at"]),
            content_hash=archive_hash,
            confidence=1.0,
            notes="Metadata-only monthly turn telemetry archive",
            provenance_id=provenance_id,
        )
        db.insert(
            "events",
            {
                "event_type": ARCHIVE_EVENT_TYPE,
                "subject_type": "telemetry_monthly_archive",
                "subject_id": subject_id,
                "payload_json": _canonical_json(manifest),
                "occurred_at": str(manifest["generated_at"]),
                "recorded_at": str(manifest["generated_at"]),
            },
            record_id=f"telemetry-archive-event:{month}:{suffix}",
            provenance_id=provenance_id,
        )
        db.insert(
            "validation_results",
            {
                "subject_type": "telemetry_monthly_archive",
                "subject_id": subject_id,
                "validator": ARCHIVE_VALIDATOR,
                "result": "pass",
                "evidence_json": _canonical_json(
                    {
                        "schema": ARCHIVE_SCHEMA,
                        "archive_sha256": archive_hash,
                        "source_row_count": manifest["source_row_count"],
                        "manifest_sha256": manifest["manifest_sha256"],
                    }
                ),
                "validated_at": str(manifest["generated_at"]),
            },
            record_id=f"telemetry-archive-validation:{month}:{suffix}",
            provenance_id=provenance_id,
        )


def _archive_expired_rows(
    db: CanonicalDB,
    *,
    rows: list[dict[str, Any]],
    archive_directory: Path,
    generated_at: str,
) -> int:
    if not rows:
        return 0
    archive_root = Path(archive_directory).resolve()
    try:
        archive_root.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Monthly telemetry archives must live outside the workspace")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        month = _parse_timestamp(str(row["completed_at"])).strftime("%Y-%m")
        grouped.setdefault(month, []).append(row)

    written = 0
    for month, new_rows in sorted(grouped.items()):
        path = archive_root / f"{month}.jsonl.gz"
        _verify_existing_archive(db, path=path, month=month)
        existing_archive_hash = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
        existing_rows = _read_archive(path)
        merged: dict[str, dict[str, Any]] = {
            str(row["turn_key"]): row for row in existing_rows
        }
        for row in new_rows:
            key = str(row["turn_key"])
            existing = merged.get(key)
            if existing is not None and existing != row:
                raise ValueError(f"Archive row conflict for turn key {key}")
            merged[key] = row
        ordered = sorted(
            merged.values(),
            key=lambda row: (str(row.get("completed_at") or ""), str(row["turn_key"])),
        )
        archive_bytes = _gzip_json_lines(ordered)
        archive_hash = hashlib.sha256(archive_bytes).hexdigest()
        if not path.is_file() or path.read_bytes() != archive_bytes:
            _atomic_replace_archive(
                path,
                archive_bytes,
                expected_current_sha256=existing_archive_hash,
            )
            written += 1
        manifest = {
            "schema": ARCHIVE_SCHEMA,
            "month": month,
            "generated_at": generated_at,
            "archive_name": path.name,
            "archive_sha256": archive_hash,
            "archive_bytes": len(archive_bytes),
            "source_row_count": len(ordered),
            "period_start": str(ordered[0]["completed_at"]),
            "period_end": str(ordered[-1]["completed_at"]),
            "columns": sorted({column for row in ordered for column in row}),
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        _persist_archive_manifest(db, manifest=manifest)
    return written


def _database_size_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )


def _ensure_incremental_auto_vacuum(connection: sqlite3.Connection) -> bool:
    current_mode = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0])
    if current_mode == 2:
        return False
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
    connection.execute("VACUUM")
    if int(connection.execute("PRAGMA auto_vacuum").fetchone()[0]) != 2:
        raise RuntimeError("Unable to enable incremental auto-vacuum")
    return True


def _delete_turn_rows(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    columns: list[str],
) -> int:
    if not rows:
        return 0
    selected = ",".join(f'"{column}"' for column in columns)
    deleted = 0
    for start in range(0, len(rows), DELETE_BATCH_SIZE):
        batch = rows[start : start + DELETE_BATCH_SIZE]
        expected = {str(row["turn_key"]): _sha256(row) for row in batch}
        connection.execute("BEGIN IMMEDIATE")
        try:
            for key, expected_hash in expected.items():
                current = connection.execute(
                    f"SELECT {selected} FROM turn_metrics WHERE turn_key = ?",
                    (key,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("Raw telemetry changed during maintenance")
                current_hash = _sha256(_normalize_safe_row(dict(current)))
                if current_hash != expected_hash:
                    raise RuntimeError("Raw telemetry changed during maintenance")
            connection.executemany(
                "DELETE FROM turn_metrics WHERE turn_key = ?",
                [(key,) for key in expected],
            )
            connection.commit()
            deleted += len(expected)
        except Exception:
            connection.rollback()
            raise
    return deleted


def _reclaim_incremental_pages(
    connection: sqlite3.Connection,
) -> None:
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    previous = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    for _ in range(min(previous, MAX_INCREMENTAL_VACUUM_STEPS)):
        if previous <= 0:
            break
        connection.execute("PRAGMA incremental_vacuum")
        remaining = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        if remaining >= previous:
            break
        previous = remaining
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _full_vacuum(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("VACUUM")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def maintain_telemetry(
    *,
    turn_database: Path = DEFAULT_TURN_DATABASE,
    canonical_database: Path = DEFAULT_CANONICAL_DATABASE,
    archive_directory: Path = DEFAULT_ARCHIVE_DIRECTORY,
    archive_months: bool = False,
    now: str | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    protected_recent_days: int = DEFAULT_PROTECTED_RECENT_DAYS,
    max_database_bytes: int = DEFAULT_MAX_DATABASE_BYTES,
) -> dict[str, Any]:
    """Roll up closed days, then prune only raw rows with canonical proof."""
    generated_at = now or _utc_now()
    current = _parse_timestamp(generated_at)
    turn_path = Path(turn_database)
    if not turn_path.is_file():
        raise FileNotFoundError(f"Turn telemetry database is missing: {turn_path}")
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    if protected_recent_days < 1:
        raise ValueError("protected recent window must be at least one day")
    if retention_days < protected_recent_days:
        raise ValueError(
            "retention_days cannot be shorter than the protected recent window"
        )
    if max_database_bytes < 1:
        raise ValueError("max_database_bytes must be positive")

    lock_owner = _acquire_maintenance_lock(turn_path, now=current)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(turn_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        auto_vacuum_migrated = _ensure_incremental_auto_vacuum(connection)
        _reclaim_incremental_pages(connection)
        columns = _available_safe_columns(connection)
        all_rows = _read_rows(connection, columns=columns)
        closed_rows: dict[str, list[dict[str, Any]]] = {}
        for row in all_rows:
            completed = _parse_timestamp(str(row["completed_at"]))
            if completed.date() < current.date():
                closed_rows.setdefault(completed.date().isoformat(), []).append(row)

        created = 0
        archives_written = 0
        cutoff = current - timedelta(days=retention_days)
        protected_cutoff = current - timedelta(days=protected_recent_days)
        with CanonicalDB(Path(canonical_database)) as db:
            previous_hash = _latest_rollup_hash(db)
            for day in sorted(closed_rows):
                payload = _daily_rollup(
                    day,
                    closed_rows[day],
                    generated_at=generated_at,
                    previous_rollup_sha256=previous_hash,
                )
                rollup_created, stored_rollup_hash = _persist_rollup(db, payload)
                if rollup_created:
                    created += 1
                    previous_hash = stored_rollup_hash
            proven_days = _proven_days(db)
            expired_rows = [
                row
                for row in all_rows
                if _parse_timestamp(str(row["completed_at"])) < cutoff
                and _parse_timestamp(str(row["completed_at"])).date().isoformat()
                in proven_days
            ]
            size_candidate_rows = [
                row
                for row in all_rows
                if _parse_timestamp(str(row["completed_at"])) < protected_cutoff
                and _parse_timestamp(str(row["completed_at"])).date().isoformat()
                in proven_days
            ]
            size_budget_exceeded = _database_size_bytes(turn_path) > max_database_bytes
            archive_rows = size_candidate_rows if size_budget_exceeded else expired_rows
            if archive_months and archive_rows:
                archives_written = _archive_expired_rows(
                    db,
                    rows=archive_rows,
                    archive_directory=Path(archive_directory),
                    generated_at=generated_at,
                )

        expired_keys = [
            str(row["turn_key"])
            for row in expired_rows
        ]
        deleted = _delete_turn_rows(connection, expired_rows, columns=columns)
        if deleted:
            _reclaim_incremental_pages(connection)

        expired_key_set = set(expired_keys)
        size_rows_deleted = 0
        full_vacuum_used = auto_vacuum_migrated
        remaining_by_day: dict[str, list[dict[str, Any]]] = {}
        for row in size_candidate_rows:
            key = str(row["turn_key"])
            if key in expired_key_set:
                continue
            day = _parse_timestamp(str(row["completed_at"])).date().isoformat()
            remaining_by_day.setdefault(day, []).append(row)
        for day in sorted(remaining_by_day):
            if _database_size_bytes(turn_path) <= max_database_bytes:
                break
            size_rows_deleted += _delete_turn_rows(
                connection,
                remaining_by_day[day],
                columns=columns,
            )
            _reclaim_incremental_pages(connection)
            if (
                _database_size_bytes(turn_path) > max_database_bytes
                and not full_vacuum_used
            ):
                _full_vacuum(connection)
                full_vacuum_used = True

        connection.execute("PRAGMA optimize")
        retained_rows = connection.execute("SELECT COUNT(*) FROM turn_metrics").fetchone()[0]
    finally:
        if connection is not None:
            connection.close()
        _release_maintenance_lock(turn_path, owner_id=lock_owner)

    size_bytes = _database_size_bytes(turn_path)
    status = "ok" if size_bytes <= max_database_bytes else "degraded"
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "generated_at": generated_at,
        "raw_database": str(turn_path.resolve()),
        "canonical_database": str(Path(canonical_database).resolve()),
        "retention_days": retention_days,
        "protected_recent_days": protected_recent_days,
        "max_database_bytes": max_database_bytes,
        "database_size_bytes": size_bytes,
        "rollups_created": created,
        "raw_rows_deleted": deleted,
        "size_rows_deleted": size_rows_deleted,
        "retained_rows": int(retained_rows),
        "archives_written": archives_written,
        "auto_vacuum": "incremental",
        "auto_vacuum_migrated": auto_vacuum_migrated,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    maintain = commands.add_parser("maintain", help="roll up and prune turn telemetry")
    maintain.add_argument("--turn-database", type=Path, default=DEFAULT_TURN_DATABASE)
    maintain.add_argument("--canonical-database", type=Path, default=DEFAULT_CANONICAL_DATABASE)
    maintain.add_argument("--archive-directory", type=Path, default=DEFAULT_ARCHIVE_DIRECTORY)
    maintain.add_argument("--archive-months", action="store_true")
    maintain.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    maintain.add_argument("--max-database-bytes", type=int, default=DEFAULT_MAX_DATABASE_BYTES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        report = maintain_telemetry(
            turn_database=arguments.turn_database,
            canonical_database=arguments.canonical_database,
            archive_directory=arguments.archive_directory,
            archive_months=arguments.archive_months,
            retention_days=arguments.retention_days,
            max_database_bytes=arguments.max_database_bytes,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"schema": REPORT_SCHEMA, "status": "error", "error": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
