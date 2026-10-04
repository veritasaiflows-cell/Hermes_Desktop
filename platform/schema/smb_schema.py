"""Business-table schema for SMB tenant databases (lane p1-business-schema).

The declared DDL lives in ``platform/schema/business.sql`` next to this module.
``initialize`` applies that script to an identity-only tenant database inside a
single explicit transaction, or validates an already-initialized database
without changing it.  The module is standalone on purpose: ``platform`` has no
``__init__.py`` because that would shadow the standard-library ``platform``
module, so tests and sibling lanes put ``platform/schema`` on ``sys.path``
themselves.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

__all__ = ["SCHEMA_VERSION", "IMPORT_ORDER", "COLUMN_SPECS", "initialize"]

#: Version written to ``PRAGMA user_version`` once initialization succeeds.
SCHEMA_VERSION = 1

#: Business tables in creation order; parents always precede children.
IMPORT_ORDER: tuple[str, ...] = (
    "customers",
    "vendors",
    "items",
    "locations",
    "stock_levels",
    "sales_orders",
    "sales_order_lines",
    "purchase_orders",
    "purchase_order_lines",
    "invoices",
    "payments",
    "interactions",
)

_COMMON_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("id", "TEXT", False),
    ("client_id", "TEXT", False),
    ("source_system", "TEXT", False),
    ("source_id", "TEXT", False),
    ("imported_at", "TEXT", False),
    ("import_batch_id", "TEXT", False),
)

#: Complete columns in DDL order: (name, "TEXT" | "INTEGER", nullable).
COLUMN_SPECS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "customers": _COMMON_COLUMNS + (("name", "TEXT", False), ("email", "TEXT", True)),
    "vendors": _COMMON_COLUMNS + (("name", "TEXT", False), ("email", "TEXT", True)),
    "items": _COMMON_COLUMNS
    + (
        ("sku", "TEXT", False),
        ("name", "TEXT", False),
        ("unit_cost_cents", "INTEGER", False),
        ("unit_price_cents", "INTEGER", False),
    ),
    "locations": _COMMON_COLUMNS + (("name", "TEXT", False),),
    "stock_levels": _COMMON_COLUMNS
    + (
        ("item_id", "TEXT", False),
        ("location_id", "TEXT", False),
        ("quantity", "INTEGER", False),
        ("reorder_point", "INTEGER", False),
    ),
    "sales_orders": _COMMON_COLUMNS
    + (
        ("customer_id", "TEXT", True),
        ("location_id", "TEXT", False),
        ("ordered_at", "TEXT", False),
        ("status", "TEXT", False),
        ("total_cents", "INTEGER", False),
    ),
    "sales_order_lines": _COMMON_COLUMNS
    + (
        ("sales_order_id", "TEXT", False),
        ("item_id", "TEXT", False),
        ("quantity", "INTEGER", False),
        ("unit_price_cents", "INTEGER", False),
    ),
    "purchase_orders": _COMMON_COLUMNS
    + (
        ("vendor_id", "TEXT", False),
        ("location_id", "TEXT", False),
        ("ordered_at", "TEXT", False),
        ("status", "TEXT", False),
        ("total_cents", "INTEGER", False),
    ),
    "purchase_order_lines": _COMMON_COLUMNS
    + (
        ("purchase_order_id", "TEXT", False),
        ("item_id", "TEXT", False),
        ("quantity", "INTEGER", False),
        ("unit_cost_cents", "INTEGER", False),
    ),
    "invoices": _COMMON_COLUMNS
    + (
        ("customer_id", "TEXT", False),
        ("sales_order_id", "TEXT", True),
        ("issued_at", "TEXT", False),
        ("due_date", "TEXT", False),
        ("status", "TEXT", False),
        ("total_cents", "INTEGER", False),
    ),
    "payments": _COMMON_COLUMNS
    + (
        ("invoice_id", "TEXT", False),
        ("paid_at", "TEXT", False),
        ("amount_cents", "INTEGER", False),
        ("method", "TEXT", False),
    ),
    "interactions": _COMMON_COLUMNS
    + (
        ("customer_id", "TEXT", False),
        ("occurred_at", "TEXT", False),
        ("channel", "TEXT", False),
        ("notes", "TEXT", False),
    ),
}

_TENANT_IDENTITY_TABLE = "tenant_identity"

_SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "business.sql"

_CREATE_TABLE_PATTERN = re.compile(
    r"^CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE
)

# (column, referenced table, referenced column); all references keep SQLite's
# default restrictive NO ACTION behavior.
_TENANT_FOREIGN_KEY = ("client_id", _TENANT_IDENTITY_TABLE, "client_id")

_FOREIGN_KEYS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "customers": (_TENANT_FOREIGN_KEY,),
    "vendors": (_TENANT_FOREIGN_KEY,),
    "items": (_TENANT_FOREIGN_KEY,),
    "locations": (_TENANT_FOREIGN_KEY,),
    "stock_levels": (
        _TENANT_FOREIGN_KEY,
        ("item_id", "items", "id"),
        ("location_id", "locations", "id"),
    ),
    "sales_orders": (
        _TENANT_FOREIGN_KEY,
        ("customer_id", "customers", "id"),
        ("location_id", "locations", "id"),
    ),
    "sales_order_lines": (
        _TENANT_FOREIGN_KEY,
        ("sales_order_id", "sales_orders", "id"),
        ("item_id", "items", "id"),
    ),
    "purchase_orders": (
        _TENANT_FOREIGN_KEY,
        ("vendor_id", "vendors", "id"),
        ("location_id", "locations", "id"),
    ),
    "purchase_order_lines": (
        _TENANT_FOREIGN_KEY,
        ("purchase_order_id", "purchase_orders", "id"),
        ("item_id", "items", "id"),
    ),
    "invoices": (
        _TENANT_FOREIGN_KEY,
        ("customer_id", "customers", "id"),
        ("sales_order_id", "sales_orders", "id"),
    ),
    "payments": (_TENANT_FOREIGN_KEY, ("invoice_id", "invoices", "id")),
    "interactions": (_TENANT_FOREIGN_KEY, ("customer_id", "customers", "id")),
}

_SOURCE_UNIQUE_KEY = ("source_system", "source_id")

_UNIQUE_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "customers": (_SOURCE_UNIQUE_KEY,),
    "vendors": (_SOURCE_UNIQUE_KEY,),
    "items": (_SOURCE_UNIQUE_KEY, ("sku",)),
    "locations": (_SOURCE_UNIQUE_KEY,),
    "stock_levels": (_SOURCE_UNIQUE_KEY, ("item_id", "location_id")),
    "sales_orders": (_SOURCE_UNIQUE_KEY,),
    "sales_order_lines": (_SOURCE_UNIQUE_KEY,),
    "purchase_orders": (_SOURCE_UNIQUE_KEY,),
    "purchase_order_lines": (_SOURCE_UNIQUE_KEY,),
    "invoices": (_SOURCE_UNIQUE_KEY,),
    "payments": (_SOURCE_UNIQUE_KEY,),
    "interactions": (_SOURCE_UNIQUE_KEY,),
}


def initialize(conn: sqlite3.Connection) -> None:
    """Create the declared business tables, or validate an initialized database.

    The connection must target an identity-only tenant database (a
    ``tenant_identity`` table holding exactly one row) with foreign key
    enforcement enabled and no active transaction.  Creation runs as one
    explicit transaction -- never ``executescript``, whose implicit commit
    would break atomicity -- and ``PRAGMA user_version`` is set only when every
    statement succeeds.  Re-running against an initialized database validates
    the version, columns, foreign keys, uniqueness, and CHECK constraints
    declared in ``business.sql`` without changing data; partial, unexpected, or
    incompatible schemas are rejected with ``RuntimeError``, never repaired.
    """
    if conn.in_transaction:
        raise RuntimeError("initialize requires a connection with no active transaction")
    if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("initialize requires foreign key enforcement to be enabled")
    _require_identity_database(conn)

    statements = _load_statements()
    present = _present_tables(conn)
    unexpected = present - set(IMPORT_ORDER) - {_TENANT_IDENTITY_TABLE}
    if unexpected:
        raise RuntimeError(f"unexpected tables present: {sorted(unexpected)}")
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version == SCHEMA_VERSION:
        missing = set(IMPORT_ORDER) - present
        if missing:
            raise RuntimeError(
                f"schema version {SCHEMA_VERSION} is incomplete; missing tables: {sorted(missing)}"
            )
        _validate_schema(conn, statements)
        return

    if version == 0:
        if present != {_TENANT_IDENTITY_TABLE}:
            raise RuntimeError(
                "database contains a partial schema without a schema version; refusing to initialize"
            )
        _create_schema(conn, statements)
        return

    raise RuntimeError(f"unsupported schema version: {version}")


def _require_identity_database(conn: sqlite3.Connection) -> None:
    if _TENANT_IDENTITY_TABLE not in _present_tables(conn):
        raise RuntimeError("identity-only database required: tenant_identity table is missing")
    rows = conn.execute(
        f"SELECT singleton, client_id FROM {_TENANT_IDENTITY_TABLE}"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise RuntimeError("tenant_identity must contain exactly one row with singleton = 1")
    client_id = rows[0][1]
    if not isinstance(client_id, str) or not client_id:
        raise RuntimeError("tenant_identity.client_id must be a nonempty string")
    info = conn.execute(f"PRAGMA table_info({_TENANT_IDENTITY_TABLE})").fetchall()
    if [row[1] for row in info] != ["singleton", "client_id"] or [
        str(row[2]).upper() for row in info
    ] != ["INTEGER", "TEXT"]:
        raise RuntimeError("tenant_identity definition does not match the registry contract")
    if info[0][5] != 1 or not info[1][3]:
        raise RuntimeError("tenant_identity must key on singleton and require client_id")
    if ("client_id",) not in _unique_index_columns(conn, _TENANT_IDENTITY_TABLE):
        raise RuntimeError("tenant_identity.client_id must be unique")


def _present_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    }


def _load_statements() -> dict[str, str]:
    try:
        raw = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read the declared schema: {_SCHEMA_SQL_PATH}") from exc
    without_comments = "\n".join(line.split("--", 1)[0] for line in raw.splitlines())
    statements: dict[str, str] = {}
    for chunk in without_comments.split(";"):
        statement = chunk.strip()
        if not statement:
            continue
        match = _CREATE_TABLE_PATTERN.match(statement)
        if match is None:
            raise RuntimeError("business.sql may only contain CREATE TABLE statements")
        name = match.group(1).lower()
        if name in statements:
            raise RuntimeError(f"business.sql declares {name} more than once")
        statements[name] = statement
    if set(statements) != set(IMPORT_ORDER):
        raise RuntimeError("business.sql must declare exactly the tables in IMPORT_ORDER")
    return statements


def _create_schema(conn: sqlite3.Connection, statements: dict[str, str]) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in IMPORT_ORDER:
            conn.execute(statements[table])
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception as exc:
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        raise RuntimeError("failed to create the business schema") from exc


def _validate_schema(conn: sqlite3.Connection, statements: dict[str, str]) -> None:
    for table in IMPORT_ORDER:
        _validate_columns(conn, table)
        _validate_foreign_keys(conn, table)
        _validate_unique_keys(conn, table)
        _validate_declared_sql(conn, table, statements[table])


def _validate_columns(conn: sqlite3.Connection, table: str) -> None:
    actual = conn.execute(f"PRAGMA table_info({table})").fetchall()
    expected = COLUMN_SPECS[table]
    if len(actual) != len(expected):
        raise RuntimeError(f"{table}: expected {len(expected)} columns, found {len(actual)}")
    for index, (row, (name, column_type, nullable)) in enumerate(zip(actual, expected)):
        if row[1] != name:
            raise RuntimeError(f"{table}: column {index} is {row[1]!r}, expected {name!r}")
        if str(row[2]).upper() != column_type:
            raise RuntimeError(f"{table}.{name}: declared type {row[2]!r}, expected {column_type!r}")
        if bool(row[3]) == nullable:
            raise RuntimeError(f"{table}.{name}: NOT NULL definition mismatch")
        if row[4] is not None:
            raise RuntimeError(f"{table}.{name}: unexpected default value")
        if row[5] != (1 if index == 0 else 0):
            raise RuntimeError(f"{table}.{name}: primary key definition mismatch")


def _validate_foreign_keys(conn: sqlite3.Connection, table: str) -> None:
    expected = {
        (column, ref_table, ref_column, "NO ACTION", "NO ACTION")
        for column, ref_table, ref_column in _FOREIGN_KEYS[table]
    }
    actual = {
        (row[3], row[2], row[4], row[5], row[6])
        for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    }
    if actual != expected:
        raise RuntimeError(f"{table}: foreign key definitions do not match the contract")


def _validate_unique_keys(conn: sqlite3.Connection, table: str) -> None:
    actual = _unique_index_columns(conn, table)
    for key in _UNIQUE_KEYS[table]:
        if key not in actual:
            raise RuntimeError(f"{table}: missing UNIQUE{key}")


def _validate_declared_sql(conn: sqlite3.Connection, table: str, expected_sql: str) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"{table}: missing table definition in sqlite_master")
    if _normalize_sql(row[0]) != _normalize_sql(expected_sql):
        raise RuntimeError(f"{table}: stored definition does not match the declared schema")


def _unique_index_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for index_row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if not index_row[2]:
            continue
        columns = tuple(
            row[2] for row in conn.execute(f"PRAGMA index_info({index_row[1]})").fetchall()
        )
        keys.append(columns)
    return keys


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";").strip()
