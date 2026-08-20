"""Small, transaction-safe access layer for the Efficiens canonical database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = ROOT / "efficiens.db"
DEFAULT_SCHEMA_PATH = ROOT / "schema.sql"


class DuplicateRecordError(sqlite3.IntegrityError):
    """Raised when an insert would overwrite an existing canonical record."""


class DatabaseIntegrityError(sqlite3.DatabaseError):
    """Raised when SQLite integrity_check does not return ``ok``."""


class CanonicalDB:
    """Access the canonical database without silent overwrites.

    The class owns one SQLite connection. Every connection enables foreign keys,
    uses WAL mode, and applies the canonical schema if needed.
    """

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
        *,
        schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    ) -> None:
        self.database_path = Path(database_path)
        self.schema_path = Path(schema_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(self.schema_path.read_text(encoding="utf-8"))
        self.connection.commit()
        self._transaction_depth = 0
        self.integrity_checks_run = 0
        self.integrity_check()

    def __enter__(self) -> "CanonicalDB":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def tables(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in rows}

    def integrity_check(self) -> str:
        result = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        self.integrity_checks_run += 1
        if result != "ok":
            raise DatabaseIntegrityError(f"SQLite integrity_check failed: {result}")
        return result

    @contextmanager
    def transaction(self):
        """Run multiple canonical inserts as one rollback-safe write unit.

        ``insert()`` and ``add_provenance()`` detect this explicit transaction
        and defer their individual commits. Nested write transactions are not
        supported because SQLite has no implicit nested transaction semantics.
        """
        if self._transaction_depth or self.connection.in_transaction:
            raise RuntimeError("CanonicalDB transaction cannot be nested")
        self.connection.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
            self.integrity_check()
        finally:
            self._transaction_depth -= 1

    def add_provenance(
        self,
        *,
        source_type: str,
        source_uri: str | None = None,
        source_ref: str | None = None,
        captured_at: str | None = None,
        retrieved_at: str | None = None,
        content_hash: str | None = None,
        freshness_at: str | None = None,
        confidence: float | None = None,
        notes: str | None = None,
        provenance_id: str | None = None,
    ) -> str:
        """Insert a provenance record and return its stable identifier."""
        values = {
            "provenance_id": provenance_id or str(uuid4()),
            "source_type": source_type,
            "source_uri": source_uri,
            "source_ref": source_ref,
            "captured_at": captured_at or _utc_now(),
            "retrieved_at": retrieved_at,
            "content_hash": content_hash,
            "freshness_at": freshness_at,
            "confidence": confidence,
            "notes": notes,
        }
        return self._insert("provenance", values)

    def add_claim(
        self,
        subject_type: str,
        subject_id: str,
        claim_text: str,
        *,
        title: str | None = None,
        source_type: str = "workflow",
        source_artifact_id: str | None = None,
        source_locator: str | None = None,
        source_hash: str | None = None,
        source_version: str | None = None,
        observed_at: str | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        freshness_ttl_seconds: int | None = None,
        freshness_rule: str | None = None,
        confidence: float | None = None,
        authority_class: str = "review_only",
        verification_method: str | None = None,
        status: str = "active",
        contradiction_notes: str | None = None,
        superseded_by: str | None = None,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
        claim_id: str | None = None,
    ) -> str:
        """Insert one structured evidence claim and return its stable identifier."""
        if not claim_text.strip():
            raise ValueError("claim_text is required")
        if provenance_id and provenance:
            raise ValueError("Provide provenance_id or provenance, not both")
        if freshness_rule and "ttl:" in freshness_rule:
            try:
                ttl_parts = int(freshness_rule.split(":", 1)[1])
            except ValueError as exc:
                raise ValueError("freshness_rule ttl must be an integer") from exc
            if freshness_ttl_seconds is not None and ttl_parts != freshness_ttl_seconds:
                raise ValueError("Freshness rule ttl and freshness_ttl_seconds mismatch")
            freshness_ttl_seconds = ttl_parts

        observed_ts = _parse_utc_timestamp(observed_at)
        valid_from_ts = _parse_utc_timestamp(valid_from or observed_at)
        if valid_from_ts < observed_ts:
            raise ValueError("valid_from cannot be before observed_at")

        if valid_until is not None and freshness_ttl_seconds is not None:
            raise ValueError("Set either valid_until or freshness_ttl_seconds, not both")

        valid_until_ts = _parse_utc_timestamp(valid_until) if valid_until is not None else None
        if freshness_ttl_seconds is not None:
            valid_until_ts = observed_ts + timedelta(seconds=freshness_ttl_seconds)
            if freshness_rule is None:
                freshness_rule = f"ttl_seconds:{freshness_ttl_seconds}"

        row = {
            "claim_id": claim_id or str(uuid4()),
            "subject_type": subject_type,
            "subject_id": subject_id,
            "title": title,
            "claim_text": claim_text,
            "source_type": source_type,
            "source_artifact_id": source_artifact_id,
            "source_locator": source_locator,
            "source_hash": source_hash,
            "source_version": source_version,
            "observed_at": _to_iso8601(observed_ts),
            "valid_from": _to_iso8601(valid_from_ts),
            "valid_until": _to_iso8601(valid_until_ts) if valid_until_ts else None,
            "freshness_rule": freshness_rule,
            "confidence": confidence,
            "authority_class": authority_class,
            "verification_method": verification_method,
            "status": status,
            "contradiction_notes": contradiction_notes,
            "superseded_by": superseded_by,
            "provenance_id": provenance_id,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }

        if provenance:
            row["provenance_id"] = self._insert_provenance_in_transaction(provenance)

        return self._insert("claims", row)

    def list_claims(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        status: str | None = None,
        authority_class: str | None = None,
        include_expired: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """List claims matching filters and return row dictionaries."""
        filters: list[str] = ["1=1"]
        parameters: list[Any] = []

        if subject_type is not None:
            filters.append("subject_type = ?")
            parameters.append(subject_type)
        if subject_id is not None:
            filters.append("subject_id = ?")
            parameters.append(subject_id)
        if status is not None:
            filters.append("status = ?")
            parameters.append(status)
        if authority_class is not None:
            filters.append("authority_class = ?")
            parameters.append(authority_class)

        if not include_expired:
            filters.append("(valid_until IS NULL OR valid_until >= ?)")
            parameters.append(now or _utc_now())

        query = (
            "SELECT * FROM claims WHERE "
            + " AND ".join(filters)
            + " ORDER BY observed_at DESC, updated_at DESC"
        )
        rows = self.connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Fetch one claim by identifier."""
        row = self.connection.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return dict(row) if row is not None else None

    def get_active_claims(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        *,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """Shortcut for active, non-expired claims."""
        return self.list_claims(
            subject_type=subject_type,
            subject_id=subject_id,
            status="active",
            include_expired=False,
            now=now,
        )

    def invalidate_claim(
        self,
        claim_id: str,
        *,
        reason: str,
        invalidated_by: str | None = None,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        """Invalidate one claim while preserving full audit trail."""
        if not reason.strip():
            raise ValueError("reason is required")
        updates = {
            "status": "invalidated",
            "contradiction_notes": reason,
            "invalidated_by": invalidated_by,
        }
        self.update(
            "claims",
            claim_id,
            updates,
            provenance_id=provenance_id,
            provenance=provenance,
        )

    def insert(
        self,
        table: str,
        values: Mapping[str, Any],
        *,
        record_id: str | None = None,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> str:
        """Insert one canonical record and return its primary-key value.

        Inserts are atomic. Existing primary keys and unique records raise
        DuplicateRecordError rather than being updated or replaced. Records
        with a provenance column receive an explicit provenance record when the
        caller does not provide one.
        """
        if table == "provenance":
            raise ValueError("Use add_provenance() for provenance records")
        if provenance_id and provenance:
            raise ValueError("Provide provenance_id or provenance, not both")

        row = dict(values)
        columns = self._columns(table)
        primary_key = self._primary_key(table)
        if primary_key is None:
            raise ValueError(f"Table has no supported primary key: {table}")
        row.setdefault(primary_key, record_id or str(uuid4()))

        now = _utc_now()
        if "created_at" in columns:
            row.setdefault("created_at", now)
        if "updated_at" in columns:
            row.setdefault("updated_at", now)
        if "provenance_id" in columns and "provenance_id" not in row:
            if provenance_id:
                row["provenance_id"] = provenance_id
            elif provenance:
                row["provenance_id"] = self._insert_provenance_in_transaction(provenance)
            else:
                row["provenance_id"] = self._insert_provenance_in_transaction(
                    {
                        "source_type": "workspace",
                        "source_ref": f"canonical:{table}",
                        "captured_at": now,
                        "notes": "Automatic provenance for direct CanonicalDB insert",
                    }
                )
        return self._insert(table, row)

    def update(
        self,
        table: str,
        record_id: str,
        changes: Mapping[str, Any],
        *,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> str:
        """Update one record while preserving its prior state and audit event.

        The prior row is written to ``version_history`` and an update event is
        written before the target row is mutated, all within one transaction.
        Any failure rolls back the history, event, provenance, and update
        together, so the database cannot contain a partial audit trail.
        """
        if table == "provenance":
            raise ValueError("Use add_provenance() for provenance records")
        if table in {"events", "version_history"}:
            raise ValueError(f"{table} is append-only and cannot be updated")
        if provenance_id and provenance:
            raise ValueError("Provide provenance_id or provenance, not both")

        requested_changes = dict(changes)
        if not requested_changes:
            raise ValueError("At least one change is required")

        columns = self._columns(table)
        primary_key = self._primary_key(table)
        if primary_key is None:
            raise ValueError(f"Table has no supported primary key: {table}")
        unknown = set(requested_changes) - columns
        if unknown:
            raise ValueError(f"Unknown columns for {table}: {sorted(unknown)}")
        if primary_key in requested_changes:
            raise ValueError(f"Primary key cannot be updated: {primary_key}")
        if "created_at" in requested_changes:
            raise ValueError("created_at cannot be updated")
        if "provenance_id" in requested_changes:
            requested_provenance_id = requested_changes["provenance_id"]
            if requested_provenance_id is None:
                raise ValueError("provenance_id cannot be cleared by update")
            if provenance is not None:
                raise ValueError(
                    "Provide provenance or changes['provenance_id'], not both"
                )
            if provenance_id is not None and requested_provenance_id != provenance_id:
                raise ValueError("Conflicting provenance_id values")

        now = _utc_now()
        applied_changes = dict(requested_changes)
        if "updated_at" in columns:
            applied_changes["updated_at"] = now

        audit_provenance_id: str | None = provenance_id or requested_changes.get(
            "provenance_id"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current_row = self.connection.execute(
                f"SELECT * FROM {_quote_identifier(table)} "
                f"WHERE {_quote_identifier(primary_key)} = ?",
                (record_id,),
            ).fetchone()
            if current_row is None:
                raise KeyError(f"No {table} record exists with id {record_id!r}")
            current = dict(current_row)

            if audit_provenance_id is None and provenance is not None:
                audit_provenance_id = self._insert_provenance_in_transaction(provenance)
            elif audit_provenance_id is None:
                audit_provenance_id = self._insert_provenance_in_transaction(
                    {
                        "source_type": "workspace",
                        "source_ref": f"canonical:{table}:{record_id}",
                        "captured_at": now,
                        "notes": "Automatic provenance for canonical record update",
                    }
                )
            if "provenance_id" in columns and "provenance_id" not in applied_changes:
                applied_changes["provenance_id"] = audit_provenance_id

            version_number = self.connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 "
                "FROM version_history WHERE subject_type = ? AND subject_id = ?",
                (table, record_id),
            ).fetchone()[0]
            snapshot_json = json.dumps(current, sort_keys=True)
            payload_json = json.dumps(
                {
                    "changes": requested_changes,
                    "applied_changes": applied_changes,
                    "version_number": version_number,
                },
                sort_keys=True,
            )

            self._insert_sql(
                "version_history",
                {
                    "version_id": str(uuid4()),
                    "subject_type": table,
                    "subject_id": record_id,
                    "version_number": version_number,
                    "operation": "update",
                    "snapshot_json": snapshot_json,
                    "changed_at": now,
                    "provenance_id": audit_provenance_id,
                },
            )
            self._insert_sql(
                "events",
                {
                    "event_id": str(uuid4()),
                    "event_type": "record.updated",
                    "subject_type": table,
                    "subject_id": record_id,
                    "payload_json": payload_json,
                    "provenance_id": audit_provenance_id,
                    "occurred_at": now,
                    "recorded_at": now,
                },
            )

            assignments = ", ".join(
                f"{_quote_identifier(column)} = ?" for column in applied_changes
            )
            result = self.connection.execute(
                f"UPDATE {_quote_identifier(table)} SET {assignments} "
                f"WHERE {_quote_identifier(primary_key)} = ?",
                [*applied_changes.values(), record_id],
            )
            if result.rowcount != 1:
                raise DatabaseIntegrityError(
                    f"Expected one updated {table} record, got {result.rowcount}"
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        self.integrity_check()
        return record_id

    def _insert(self, table: str, values: Mapping[str, Any]) -> str:
        in_explicit_transaction = self._transaction_depth > 0
        if in_explicit_transaction:
            self._insert_sql(table, values)
        else:
            with self.connection:
                self._insert_sql(table, values)
            self.integrity_check()
        return str(values[self._primary_key(table)])

    def record_run(
        self,
        *,
        request_type: str,
        route_selected: str | None = None,
        tools_json: list[str] | dict[str, Any] | None = None,
        model_or_agent: str | None = None,
        input_size: int | None = None,
        handoff_size: int | None = None,
        duration_ms: int | None = None,
        resource_usage_json: dict[str, Any] | None = None,
        errors_json: list[str] | dict[str, Any] | None = None,
        retries: int = 0,
        verification_result: str | None = None,
        user_correction: str | None = None,
        final_outcome: str | None = None,
        acceptance_status: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        provenance_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """Append one metadata-only run telemetry row to the canonical ledger.

        No raw prompts, tool payloads, credentials, or sensitive file contents
        are accepted. Free-text fields (user_correction, final_outcome) are
        redacted to category-only tokens unless explicitly marked safe.
        """
        if not request_type or not request_type.strip():
            raise ValueError("request_type is required")

        now = _utc_now()
        row: dict[str, Any] = {
            "run_id": run_id or str(uuid4()),
            "request_type": request_type.strip(),
            "route_selected": _redact_free_text(route_selected),
            "tools_json": _serialize_json(tools_json),
            "model_or_agent": _redact_free_text(model_or_agent),
            "input_size": _non_negative_int(input_size),
            "handoff_size": _non_negative_int(handoff_size),
            "duration_ms": _non_negative_int(duration_ms),
            "resource_usage_json": _serialize_json(resource_usage_json),
            "errors_json": _serialize_json(errors_json),
            "retries": max(0, retries),
            "verification_result": _redact_free_text(verification_result),
            "user_correction": _redact_free_text(user_correction),
            "final_outcome": _redact_free_text(final_outcome),
            "acceptance_status": _redact_free_text(acceptance_status),
            "started_at": started_at or now,
            "completed_at": completed_at,
        }
        return self.insert("run_metrics", row, provenance_id=provenance_id)

    def get_run_metrics(
        self,
        *,
        request_type: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent run telemetry rows ordered by start time descending."""
        filters: list[str] = ["1=1"]
        parameters: list[Any] = []
        if request_type:
            filters.append("request_type = ?")
            parameters.append(request_type)
        if since:
            filters.append("started_at >= ?")
            parameters.append(since)

        query = (
            "SELECT * FROM run_metrics WHERE "
            + " AND ".join(filters)
            + " ORDER BY started_at DESC LIMIT ?"
        )
        parameters.append(max(1, limit))
        rows = self.connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def record_user_correction(
        self,
        correction: str,
        *,
        request_type: str | None = None,
        run_id: str | None = None,
    ) -> str | None:
        """Attach a user-correction token to a run_metrics row.

        This is the deterministic path an agent calls when the user steers
        mid-turn. Callers should pass a short category token (e.g.
        ``wrong_route``, ``scope_too_broad``) rather than raw user text; the
        value is redacted to a category token before storage (same policy as
        ``record_run``), so no raw user content is persisted. Returns the
        updated run_id, or None when no matching row exists.
        """
        token = _redact_free_text(correction)
        if token is None:
            return None
        if run_id is not None:
            row = self.connection.execute(
                "SELECT run_id FROM run_metrics WHERE run_id = ?", (run_id,)
            ).fetchone()
        else:
            filters = ["1=1"]
            parameters: list[Any] = []
            if request_type:
                filters.append("request_type = ?")
                parameters.append(request_type)
            row = self.connection.execute(
                "SELECT run_id FROM run_metrics WHERE "
                + " AND ".join(filters)
                + " ORDER BY started_at DESC, rowid DESC LIMIT 1",
                parameters,
            ).fetchone()
        if row is None:
            return None
        target_run_id = row[0]
        self.update("run_metrics", target_run_id, {"user_correction": token})
        return target_run_id

    def get_routing_cache(
        self,
        cache_key: str,
        *,
        now: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a non-expired routing cache entry by key."""
        row = self.connection.execute(
            "SELECT * FROM routing_cache WHERE cache_key = ? AND expires_at >= ?",
            (cache_key, now or _utc_now()),
        ).fetchone()
        if row is None:
            return None
        return {
            "cache_key": row["cache_key"],
            "payload": json.loads(row["payload_json"]),
            "source_signatures": json.loads(row["source_signatures_json"]),
            "generated_at": row["generated_at"],
            "ttl_seconds": row["ttl_seconds"],
            "expires_at": row["expires_at"],
        }

    def set_routing_cache(
        self,
        cache_key: str,
        *,
        payload: dict[str, Any],
        source_signatures: dict[str, Any],
        ttl_seconds: int = 300,
        provenance_id: str | None = None,
    ) -> str:
        """Upsert a routing cache entry with an explicit TTL."""
        now = _utc_now_datetime()
        expires = now + timedelta(seconds=ttl_seconds)
        row = {
            "cache_key": cache_key,
            "payload_json": json.dumps(payload, sort_keys=True),
            "source_signatures_json": json.dumps(source_signatures, sort_keys=True),
            "generated_at": _to_iso8601(now),
            "ttl_seconds": ttl_seconds,
            "expires_at": _to_iso8601(expires),
            "provenance_id": provenance_id,
        }
        return self._insert("routing_cache", row)

    def clear_expired_routing_cache(
        self,
        *,
        now: str | None = None,
    ) -> int:
        """Delete expired routing cache entries and return the deleted count."""
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM routing_cache WHERE expires_at < ?",
                (now or _utc_now(),),
            )
        return cursor.rowcount

    def delete_routing_cache_with_mismatched_signatures(
        self,
        *,
        current_signatures: dict[str, Any],
    ) -> int:
        """Delete rows whose cached source_signatures no longer match.

        This prevents stale cache hits between scheduled refreshes and keeps
        the routing_cache table from growing with invalid-but-not-yet-expired
        rows.
        """
        with self.connection:
            cursor = self.connection.execute("SELECT cache_key, source_signatures_json FROM routing_cache")
            rows = cursor.fetchall()
            stale_keys = [
                row["cache_key"]
                for row in rows
                if json.loads(row["source_signatures_json"]) != current_signatures
            ]
            if not stale_keys:
                return 0
            placeholders = ",".join("?" * len(stale_keys))
            deleted = self.connection.execute(
                f"DELETE FROM routing_cache WHERE cache_key IN ({placeholders})",
                stale_keys,
            )
        return deleted.rowcount

    def add_relationship(
        self,
        subject_type: str,
        subject_id: str,
        predicate: str,
        object_type: str,
        object_id: str,
        *,
        confidence: float | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
        rel_id: str | None = None,
    ) -> str:
        """Insert one asserted relationship edge and return its stable identifier.

        Edges link canonical records by identifier; they never re-state facts.
        Every edge carries a provenance reference so the graph stays auditable.
        """
        if not subject_type.strip() or not subject_id.strip():
            raise ValueError("subject_type and subject_id are required")
        if not predicate.strip():
            raise ValueError("predicate is required")
        if not object_type.strip() or not object_id.strip():
            raise ValueError("object_type and object_id are required")
        if provenance_id and provenance:
            raise ValueError("Provide provenance_id or provenance, not both")

        valid_from_ts = _parse_utc_timestamp(valid_from)
        valid_until_ts = _parse_utc_timestamp(valid_until) if valid_until is not None else None
        if valid_until_ts is not None and valid_until_ts < valid_from_ts:
            raise ValueError("valid_until cannot be before valid_from")

        row = {
            "subject_type": subject_type.strip(),
            "subject_id": subject_id.strip(),
            "predicate": predicate.strip(),
            "object_type": object_type.strip(),
            "object_id": object_id.strip(),
            "status": "active",
            "confidence": confidence,
            "valid_from": _to_iso8601(valid_from_ts),
            "valid_until": _to_iso8601(valid_until_ts) if valid_until_ts else None,
            "superseded_by": None,
        }
        return self.insert(
            "relationships",
            row,
            record_id=rel_id,
            provenance_id=provenance_id,
            provenance=provenance,
        )

    def list_relationships(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        predicate: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        status: str | None = "active",
        include_expired: bool = False,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """List relationship edges matching filters, newest first."""
        filters: list[str] = ["1=1"]
        parameters: list[Any] = []

        if subject_type is not None:
            filters.append("subject_type = ?")
            parameters.append(subject_type)
        if subject_id is not None:
            filters.append("subject_id = ?")
            parameters.append(subject_id)
        if predicate is not None:
            filters.append("predicate = ?")
            parameters.append(predicate)
        if object_type is not None:
            filters.append("object_type = ?")
            parameters.append(object_type)
        if object_id is not None:
            filters.append("object_id = ?")
            parameters.append(object_id)
        if status is not None:
            filters.append("status = ?")
            parameters.append(status)

        if not include_expired:
            filters.append("(valid_until IS NULL OR valid_until >= ?)")
            parameters.append(now or _utc_now())

        query = (
            "SELECT * FROM relationships WHERE "
            + " AND ".join(filters)
            + " ORDER BY created_at DESC"
        )
        rows = self.connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_relationship(self, rel_id: str) -> dict[str, Any] | None:
        """Fetch one relationship edge by identifier."""
        row = self.connection.execute(
            "SELECT * FROM relationships WHERE rel_id = ?", (rel_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def supersede_relationship(
        self,
        rel_id: str,
        *,
        reason: str,
        superseded_by: str | None = None,
        provenance_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark one relationship edge superseded while preserving the audit trail."""
        if not reason.strip():
            raise ValueError("reason is required")
        updates = {
            "status": "superseded",
            "superseded_by": superseded_by,
        }
        self.update(
            "relationships",
            rel_id,
            updates,
            provenance_id=provenance_id,
            provenance=provenance,
        )

    def find_path(
        self,
        start_type: str,
        start_id: str,
        end_type: str,
        end_id: str,
        *,
        max_depth: int = 8,
        now: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Return the shortest active edge path between two canonical records.

        Breadth-first traversal over active, non-expired relationship edges.
        Returns a list of edge dictionaries from start to end, or None when no
        path exists within max_depth.
        """
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        now_ts = now or _utc_now()
        start = (start_type, start_id)
        end = (end_type, end_id)
        if start == end:
            return []
        queue: list[tuple[tuple[str, str], list[dict[str, Any]]]] = [(start, [])]
        visited: set[tuple[str, str]] = {start}
        while queue:
            node, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            edges = self.list_relationships(
                subject_type=node[0],
                subject_id=node[1],
                status="active",
                include_expired=False,
                now=now_ts,
            )
            for edge in edges:
                neighbor = (edge["object_type"], edge["object_id"])
                new_path = [*path, edge]
                if neighbor == end:
                    return new_path
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, new_path))
        return None

    def affected(
        self,
        object_type: str,
        object_id: str,
        *,
        max_depth: int = 2,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active edges that point at a record (reverse traversal).

        Answers "what depends on X": all active relationships whose object is
        the given record, up to max_depth hops.
        """
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        now_ts = now or _utc_now()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        frontier: list[tuple[str, str]] = [(object_type, object_id)]
        for _ in range(max_depth):
            next_frontier: list[tuple[str, str]] = []
            for node in frontier:
                edges = self.list_relationships(
                    object_type=node[0],
                    object_id=node[1],
                    status="active",
                    include_expired=False,
                    now=now_ts,
                )
                for edge in edges:
                    if edge["rel_id"] in seen:
                        continue
                    seen.add(edge["rel_id"])
                    results.append(edge)
                    next_frontier.append((edge["subject_type"], edge["subject_id"]))
            frontier = next_frontier
            if not frontier:
                break
        return results

    def _insert_provenance_in_transaction(self, values: Mapping[str, Any]) -> str:
        row = {
            "provenance_id": values.get("provenance_id", str(uuid4())),
            "source_type": values["source_type"],
            "source_uri": values.get("source_uri"),
            "source_ref": values.get("source_ref"),
            "captured_at": values.get("captured_at", _utc_now()),
            "retrieved_at": values.get("retrieved_at"),
            "content_hash": values.get("content_hash"),
            "freshness_at": values.get("freshness_at"),
            "confidence": values.get("confidence"),
            "notes": values.get("notes"),
        }
        self._insert_sql("provenance", row)
        return row["provenance_id"]

    def _insert_sql(self, table: str, values: Mapping[str, Any]) -> None:
        if table not in self.tables():
            raise ValueError(f"Unsupported canonical table: {table}")
        columns = self._columns(table)
        unknown = set(values) - columns
        if unknown:
            raise ValueError(f"Unknown columns for {table}: {sorted(unknown)}")
        names = list(values)
        quoted_names = ", ".join(_quote_identifier(name) for name in names)
        placeholders = ", ".join("?" for _ in names)
        sql = f"INSERT INTO {_quote_identifier(table)} ({quoted_names}) VALUES ({placeholders})"
        try:
            self.connection.execute(sql, [values[name] for name in names])
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if "unique constraint failed" in message or "primary key" in message:
                raise DuplicateRecordError(str(exc)) from exc
            raise

    def _columns(self, table: str) -> set[str]:
        if table not in self.tables():
            raise ValueError(f"Unsupported canonical table: {table}")
        return {row[1] for row in self.connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")}

    def _primary_key(self, table: str) -> str | None:
        rows = self.connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        keys = [row[1] for row in rows if row[5] == 1]
        return keys[0] if len(keys) == 1 else None


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return '"' + identifier.replace('"', '""') + '"'


def _redact_free_text(value: str | None) -> str | None:
    """Redact free-form text to a category token to avoid leaking content.

    Allowed values are short category tokens: lowercase letters, digits,
    hyphens, underscores, periods, and colons. Anything else is summarized to
    "redacted:content". A token containing a secret-like pattern is rejected.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    token = stripped.replace(" ", "_")
    if _looks_like_secret(token):
        return "redacted:secret"
    if len(token) > 40 or not re.fullmatch(r"[a-z0-9_.:\-]+", token):
        return "redacted:content"
    return token


def _looks_like_secret(token: str) -> bool:
    """Detect common secret-bearing prefixes or high-entropy fragments."""
    lowered = token.lower()
    secret_prefixes = (
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "passwd",
        "credential",
        "private_key",
        "bearer",
        "sk-",
        "ghp_",
        "pat-",
    )
    return any(
        lowered.startswith(prefix)
        or f"_{prefix}" in lowered
        or f"={prefix}" in lowered
        or f":{prefix}" in lowered
        for prefix in secret_prefixes
    )


def _serialize_json(value: Any) -> str | None:
    """Serialize structured metadata to compact canonical JSON, or null."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return json.dumps({"value": _redact_free_text(value)}, sort_keys=True)
        return json.dumps(parsed, sort_keys=True)
    return json.dumps(value, sort_keys=True, default=str)


def _non_negative_int(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


def _utc_now() -> str:
    return _to_iso8601(_utc_now_datetime())


def _utc_now_datetime() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _to_iso8601(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc_timestamp(value: str | None) -> datetime:
    text = value or _to_iso8601(_utc_now_datetime())
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
