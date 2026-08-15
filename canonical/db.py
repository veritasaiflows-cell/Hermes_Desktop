"""Small, transaction-safe access layer for the Efficiens canonical database."""

from __future__ import annotations

from datetime import datetime, timezone
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

    def _insert(self, table: str, values: Mapping[str, Any]) -> str:
        with self.connection:
            self._insert_sql(table, values)
        self.integrity_check()
        return str(values[self._primary_key(table)])

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
