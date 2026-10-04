"""CSV import adapter for the SMB business database (WF-1100 phase 1).

``pull`` reads ``<table>.csv`` files into a validated :class:`Batch`; ``load``
re-validates that batch and inserts it into one tenant database in
``IMPORT_ORDER`` within a single transaction; ``verify`` reconciles a batch
against the database without writing.

The table and column contract is consumed from ``smb_schema``
(``IMPORT_ORDER`` and ``COLUMN_SPECS``).  This is a standalone module: callers
must place ``platform/schema`` and ``platform/adapters/csv_import`` on
``sys.path`` (the lane tests do that); production code never mutates
``sys.path``.

Error policy: malformed input raises ``ValueError``; tenant, authorization, and
path violations raise ``PermissionError``; incompatible database state raises
``RuntimeError``; key conflicts remain ``sqlite3.IntegrityError``.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import smb_schema


_INTEGER_MIN = -(2**63)
_INTEGER_MAX = 2**63 - 1

_INTEGER_PATTERN = re.compile(r"-?[0-9]+")
_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Payload columns whose text values carry a validated temporal format.
_TIMESTAMP_COLUMNS = frozenset({"ordered_at", "issued_at", "paid_at", "occurred_at"})
_DATE_COLUMNS = frozenset({"due_date"})

_METADATA_COLUMNS = (
    "id",
    "client_id",
    "source_system",
    "source_id",
    "imported_at",
    "import_batch_id",
)
_INJECTED_COLUMNS = ("client_id", "source_system", "imported_at", "import_batch_id")
_CSV_METADATA_COLUMNS = ("id", "source_id")
_EXCEL_SUFFIXES = frozenset({".xls", ".xlsb", ".xlsm", ".xlsx"})


def _build_contract() -> tuple[
    tuple[str, ...],
    dict[str, tuple[tuple[str, str, bool], ...]],
    dict[str, tuple[str, ...]],
    dict[str, frozenset[str]],
]:
    """Validate the ``smb_schema`` contract and derive the CSV column sets.

    Raises ``RuntimeError`` when the declared schema is incompatible with this
    adapter, so a drifted contract fails loudly instead of importing silently.
    """
    order = tuple(smb_schema.IMPORT_ORDER)
    if len(set(order)) != len(order):
        raise RuntimeError("smb_schema declares duplicate tables in IMPORT_ORDER")
    if set(smb_schema.COLUMN_SPECS) != set(order):
        raise RuntimeError("smb_schema COLUMN_SPECS does not match IMPORT_ORDER")

    specs: dict[str, tuple[tuple[str, str, bool], ...]] = {}
    payload_columns: dict[str, tuple[str, ...]] = {}
    csv_columns: dict[str, frozenset[str]] = {}
    all_payload: set[str] = set()
    metadata_width = len(_METADATA_COLUMNS)
    for table in order:
        columns = tuple(tuple(entry) for entry in smb_schema.COLUMN_SPECS[table])
        names = tuple(name for name, _kind, _nullable in columns)
        if len(names) != len(set(names)) or names[:metadata_width] != _METADATA_COLUMNS:
            raise RuntimeError(
                f"smb_schema declares an incompatible column contract for {table!r}"
            )
        payload: tuple[str, ...] = ()
        for name, kind, _nullable in columns[metadata_width:]:
            if kind not in ("TEXT", "INTEGER"):
                raise RuntimeError(
                    f"smb_schema declares unhandled column type {kind!r} for {table}.{name}"
                )
            if name in _INJECTED_COLUMNS or name in _CSV_METADATA_COLUMNS:
                raise RuntimeError(
                    f"smb_schema declares unexpected payload column {table}.{name}"
                )
            if name.endswith("_at") and name not in _TIMESTAMP_COLUMNS:
                raise RuntimeError(
                    f"smb_schema declares unhandled timestamp column {table}.{name}"
                )
            if name.endswith("_date") and name not in _DATE_COLUMNS:
                raise RuntimeError(
                    f"smb_schema declares unhandled date column {table}.{name}"
                )
            payload += (name,)
        all_payload.update(payload)
        specs[table] = columns
        payload_columns[table] = payload
        csv_columns[table] = frozenset(_CSV_METADATA_COLUMNS + payload)

    for name in _TIMESTAMP_COLUMNS | _DATE_COLUMNS:
        if name not in all_payload:
            raise RuntimeError(f"smb_schema does not declare the temporal column {name!r}")

    return order, specs, payload_columns, csv_columns


_IMPORT_ORDER, _TABLE_SPECS, _PAYLOAD_COLUMNS, _CSV_COLUMN_SETS = _build_contract()


@dataclass(frozen=True)
class Batch:
    """A CSV import batch for one tenant, source system, and import run."""

    client_id: str
    source_system: str
    imported_at: str
    import_batch_id: str
    records: dict[str, list[dict[str, str | int | None]]]


def pull(
    directory: Path,
    *,
    client_id: str,
    source_system: str,
    imported_at: str,
    import_batch_id: str,
) -> Batch:
    """Read the ``<table>.csv`` files in ``directory`` into a new batch.

    Missing tables yield empty record lists.  Unknown CSV filenames, Excel
    files, symlinked inputs, malformed headers, malformed cells, duplicate row
    keys, and forbidden provenance columns fail instead of being skipped.
    Tenant and provenance fields come from the arguments and cannot be
    supplied by the CSV files.
    """
    _require_nonempty_identifier(client_id, "client_id")
    _require_nonempty_identifier(source_system, "source_system")
    _require_imported_at(imported_at)
    _require_nonempty_identifier(import_batch_id, "import_batch_id")

    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"CSV directory is not a directory: {directory}")

    sources = _discover_table_files(directory)
    records: dict[str, list[dict[str, str | int | None]]] = {}
    for table in _IMPORT_ORDER:
        path = sources.get(table)
        records[table] = _read_table(path, table) if path is not None else []

    return Batch(
        client_id=client_id,
        source_system=source_system,
        imported_at=imported_at,
        import_batch_id=import_batch_id,
        records=records,
    )


def load(conn: sqlite3.Connection, batch: Batch) -> dict[str, int]:
    """Insert ``batch`` into ``conn``'s tenant database.

    The batch is re-validated first.  Rows must match the database tenant and
    the connection must enforce foreign keys with no active transaction.
    Tables are inserted in ``IMPORT_ORDER`` within one transaction; exact
    existing rows are idempotent skips, while any differing row sharing a
    primary or source key aborts the whole batch.  Returns inserted counts for
    every table.
    """
    if not isinstance(batch, Batch):
        raise ValueError("load requires a Batch instance")
    _validate_batch_fields(batch)
    _require_tenant(conn, batch.client_id)
    _require_foreign_keys(conn)
    _require_no_transaction(conn)
    _require_business_tables(conn)
    validated = _revalidate_records(batch)

    counts = {table: 0 for table in _IMPORT_ORDER}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in _IMPORT_ORDER:
            columns = [name for name, _kind, _nullable in _TABLE_SPECS[table]]
            column_list = ", ".join(columns)
            select_sql = (
                f"SELECT {column_list} FROM {table} "
                "WHERE id = ? OR (source_system = ? AND source_id = ?)"
            )
            placeholders = ", ".join("?" for _ in columns)
            insert_sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"
            for record in validated[table]:
                row = _candidate_row(batch, record)
                values = _row_tuple(row, columns)
                matching = [
                    _row_values(existing, columns)
                    for existing in conn.execute(
                        select_sql, (row["id"], batch.source_system, row["source_id"])
                    )
                ]
                if len(matching) == 1 and matching[0] == values:
                    continue
                if matching:
                    raise sqlite3.IntegrityError(
                        f"{table}: record {row['id']!r} conflicts with an existing "
                        "row; changed provenance requires a new source identity"
                    )
                conn.execute(insert_sql, values)
                counts[table] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def verify(conn: sqlite3.Connection, batch: Batch) -> dict[str, dict[str, int | bool]]:
    """Reconcile ``batch`` against ``conn``'s tenant database without writing.

    For each table, database rows are scoped by tenant, source system, and
    batch ID.  ``matched`` counts rows with complete equality; ``ok`` requires
    exact set equality between the batch and the scoped database rows.
    """
    if not isinstance(batch, Batch):
        raise ValueError("verify requires a Batch instance")
    _validate_batch_fields(batch)
    _require_tenant(conn, batch.client_id)
    _require_business_tables(conn)
    validated = _revalidate_records(batch)

    report: dict[str, dict[str, int | bool]] = {}
    for table in _IMPORT_ORDER:
        columns = [name for name, _kind, _nullable in _TABLE_SPECS[table]]
        column_list = ", ".join(columns)
        rows = conn.execute(
            f"SELECT {column_list} FROM {table} "
            "WHERE client_id = ? AND source_system = ? AND import_batch_id = ?",
            (batch.client_id, batch.source_system, batch.import_batch_id),
        ).fetchall()
        actual = Counter(_row_values(row, columns) for row in rows)
        expected = Counter(
            _row_tuple(_candidate_row(batch, record), columns)
            for record in validated[table]
        )
        matched = sum((expected & actual).values())
        report[table] = {
            "expected": sum(expected.values()),
            "actual": sum(actual.values()),
            "matched": matched,
            "ok": expected == actual,
        }
    return report


def _discover_table_files(directory: Path) -> dict[str, Path]:
    """Map known tables to their CSV files, refusing unknown or unsafe inputs."""
    known = set(_IMPORT_ORDER)
    sources: dict[str, Path] = {}
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise PermissionError(f"symlinked CSV inputs are refused: {entry.name}")
        if entry.is_dir():
            continue
        suffix = entry.suffix.lower()
        if suffix in _EXCEL_SUFFIXES:
            raise ValueError(
                f"Excel input is not supported (export CSV instead): {entry.name}"
            )
        if suffix != ".csv":
            continue
        if entry.stem not in known:
            raise ValueError(f"unknown CSV table filename: {entry.name}")
        sources[entry.stem] = entry
    return sources


def _read_table(path: Path, table: str) -> list[dict[str, str | int | None]]:
    """Parse one ``<table>.csv`` file into canonical record dictionaries."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name}: file is not valid UTF-8") from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError(f"{path.name}: file has no header row") from None
    _check_header(path, table, header)

    records: list[dict[str, str | int | None]] = []
    for line_number, cells in enumerate(reader, start=2):
        if len(cells) != len(header):
            raise ValueError(
                f"{path.name}: row {line_number} has {len(cells)} cells but the "
                f"header declares {len(header)}"
            )
        records.append(_parse_record(path, line_number, table, dict(zip(header, cells))))
    return records


def _check_header(path: Path, table: str, header: list[str]) -> None:
    """Require exactly the id, source_id, and payload columns, in any order."""
    expected = _csv_columns(table)
    expected_set = set(expected)
    seen: set[str] = set()
    for column in header:
        if column in seen:
            raise ValueError(f"{path.name}: duplicate header column {column!r}")
        seen.add(column)
    unknown = [column for column in header if column not in expected_set]
    if unknown:
        injected = [column for column in unknown if column in _INJECTED_COLUMNS]
        if injected:
            raise ValueError(
                f"{path.name}: provenance columns cannot be imported from CSV: "
                f"{injected}"
            )
        raise ValueError(f"{path.name}: unknown header columns: {unknown}")
    missing = [column for column in expected if column not in seen]
    if missing:
        raise ValueError(f"{path.name}: missing header columns: {missing}")


def _parse_record(
    path: Path, line_number: int, table: str, cells: Mapping[str, str]
) -> dict[str, str | int | None]:
    """Validate one CSV row and return the canonical record dictionary."""
    where = f"{path.name} row {line_number}"
    record: dict[str, str | int | None] = {}
    for column in _CSV_METADATA_COLUMNS:
        record[column] = _require_nonempty_text(cells[column], where, column)
    for name, kind, nullable in _payload_spec(table):
        cell = cells[name]
        if cell == "":
            if not nullable:
                raise ValueError(f"{where}: column {name!r} requires a value")
            record[name] = None
        elif kind == "INTEGER":
            record[name] = _parse_integer(cell, where, name)
        else:
            _require_column_format(cell, where, name)
            record[name] = cell
    return record


def _payload_spec(table: str) -> tuple[tuple[str, str, bool], ...]:
    return _TABLE_SPECS[table][len(_METADATA_COLUMNS):]


def _csv_columns(table: str) -> tuple[str, ...]:
    return _CSV_METADATA_COLUMNS + _PAYLOAD_COLUMNS[table]


def _require_nonempty_text(value: object, where: str, column: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{where}: column {column!r} requires a nonempty value")
    return value


def _parse_integer(value: str, where: str, column: str) -> int:
    if _INTEGER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{where}: column {column!r} requires an integer, got {value!r}")
    parsed = int(value)
    if parsed < _INTEGER_MIN or parsed > _INTEGER_MAX:
        raise ValueError(
            f"{where}: column {column!r} is outside SQLite's signed 64-bit integer range"
        )
    return parsed


def _require_column_format(value: str, where: str, column: str) -> None:
    if column in _TIMESTAMP_COLUMNS and not _is_timestamp(value):
        raise ValueError(
            f"{where}: column {column!r} requires a UTC timestamp "
            "(YYYY-MM-DDTHH:MM:SSZ)"
        )
    if column in _DATE_COLUMNS and not _is_date(value):
        raise ValueError(
            f"{where}: column {column!r} requires a calendar date (YYYY-MM-DD)"
        )


def _is_timestamp(value: str) -> bool:
    if _TIMESTAMP_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _is_date(value: str) -> bool:
    if _DATE_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _require_nonempty_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _require_imported_at(value: object) -> str:
    if not isinstance(value, str) or not _is_timestamp(value):
        raise ValueError(
            "imported_at must be a UTC timestamp of the form YYYY-MM-DDTHH:MM:SSZ"
        )
    return value


def _validate_batch_fields(batch: Batch) -> None:
    _require_nonempty_identifier(batch.client_id, "client_id")
    _require_nonempty_identifier(batch.source_system, "source_system")
    _require_imported_at(batch.imported_at)
    _require_nonempty_identifier(batch.import_batch_id, "import_batch_id")
    if not isinstance(batch.records, dict):
        raise ValueError("batch records must be a table-to-record-list mapping")


def _revalidate_records(
    batch: Batch,
) -> dict[str, list[dict[str, str | int | None]]]:
    """Re-validate the (mutable) batch records against the column contract."""
    validated: dict[str, list[dict[str, str | int | None]]] = {
        table: [] for table in _IMPORT_ORDER
    }
    for table, records in batch.records.items():
        if table not in _TABLE_SPECS:
            raise ValueError(f"batch contains an unknown table: {table!r}")
        if not isinstance(records, list):
            raise ValueError(f"batch records for {table!r} must be a list")
        for position, record in enumerate(records, start=1):
            validated[table].append(_revalidate_record(table, position, record))
    return validated


def _revalidate_record(
    table: str, position: int, record: object
) -> dict[str, str | int | None]:
    where = f"{table} record {position}"
    if not isinstance(record, Mapping):
        raise ValueError(f"{where} must be a mapping")
    expected = _CSV_COLUMN_SETS[table]
    extra = [key for key in record if key not in expected]
    if extra:
        injected = [key for key in extra if key in _INJECTED_COLUMNS]
        if injected:
            raise ValueError(
                f"{where} cannot override provenance columns: {injected}"
            )
        raise ValueError(f"{where} has unknown columns: {extra}")
    missing = [column for column in _csv_columns(table) if column not in record]
    if missing:
        raise ValueError(f"{where} is missing columns: {missing}")

    validated: dict[str, str | int | None] = {}
    for column in _CSV_METADATA_COLUMNS:
        validated[column] = _require_nonempty_text(record[column], where, column)
    for name, kind, nullable in _payload_spec(table):
        value = record[name]
        if value is None:
            if not nullable:
                raise ValueError(f"{where}: column {name!r} requires a value")
            validated[name] = None
        elif kind == "INTEGER":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{where}: column {name!r} requires an integer")
            if value < _INTEGER_MIN or value > _INTEGER_MAX:
                raise ValueError(
                    f"{where}: column {name!r} is outside SQLite's signed "
                    "64-bit integer range"
                )
            validated[name] = value
        else:
            if not isinstance(value, str) or value == "":
                raise ValueError(
                    f"{where}: column {name!r} requires a nonempty string or null"
                )
            _require_column_format(value, where, name)
            validated[name] = value
    return validated


def _candidate_row(
    batch: Batch, record: Mapping[str, str | int | None]
) -> dict[str, str | int | None]:
    """Combine a record with the batch's injected tenant and provenance fields."""
    row = dict(record)
    row["client_id"] = batch.client_id
    row["source_system"] = batch.source_system
    row["imported_at"] = batch.imported_at
    row["import_batch_id"] = batch.import_batch_id
    return row


def _row_tuple(
    row: Mapping[str, str | int | None], columns: Sequence[str]
) -> tuple[str | int | None, ...]:
    return tuple(row[column] for column in columns)


def _row_values(row: object, columns: Sequence[str]) -> tuple[object, ...]:
    """Read row values in column order, tolerating tuple or mapping factories."""
    if isinstance(row, (sqlite3.Row, Mapping)):
        return tuple(row[column] for column in columns)
    return tuple(row)


def _require_tenant(conn: sqlite3.Connection, client_id: str) -> None:
    try:
        rows = conn.execute("SELECT client_id FROM tenant_identity").fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"tenant identity is unavailable: {exc}") from exc
    if len(rows) != 1:
        raise RuntimeError("tenant identity must contain exactly one row")
    identity = _row_values(rows[0], ("client_id",))[0]
    if identity != client_id:
        raise PermissionError(
            f"batch tenant {client_id!r} does not match database identity {identity!r}"
        )


def _require_foreign_keys(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    if row is None or _row_values(row, ("foreign_keys",))[0] != 1:
        raise RuntimeError("foreign key enforcement must be enabled")


def _require_no_transaction(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("connection already has an active transaction")


def _require_business_tables(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"database schema is unavailable: {exc}") from exc
    known = {_row_values(row, ("name",))[0] for row in rows}
    missing = [table for table in _IMPORT_ORDER if table not in known]
    if missing:
        raise RuntimeError(f"database is missing business tables: {missing}")
