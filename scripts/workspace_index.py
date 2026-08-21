#!/usr/bin/env python3
"""Build and query an explicit, source-grounded SQLite full-text index.

Only paths supplied as ``SourceSpec`` values (or ``index --source`` arguments)
are crawled. The index is a derived locator: callers must open the reported
source citation before treating a result as evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Sequence


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = WORKSPACE_ROOT / "vector" / "indexes" / "workspace-index.sqlite"
DEFAULT_EXTENSIONS = frozenset({".csv", ".md", ".rst", ".sql", ".txt"})
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hermes",
        "__pycache__",
        "config",
        "configuration",
        "credentials",
        "secrets",
        "tmp",
    }
)
EXCLUDED_SUFFIXES = frozenset({".db", ".key", ".log", ".pem", ".pyc", ".tmp"})


@dataclass(frozen=True)
class SourceSpec:
    """An explicitly approved filesystem source and its provenance metadata."""

    path: Path | str
    profile: str = "explicit-local-source"
    authority_class: str = "source"
    extensions: frozenset[str] = DEFAULT_EXTENSIONS

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("Source profile must not be empty")
        if not self.authority_class.strip():
            raise ValueError("Authority class must not be empty")
        normalized = frozenset(_normalize_extension(value) for value in self.extensions)
        if not normalized:
            raise ValueError("At least one text extension must be approved")
        object.__setattr__(self, "extensions", normalized)

    @property
    def resolved_path(self) -> Path:
        path = Path(self.path)
        if path.is_symlink():
            raise ValueError(f"Symlinked source paths are not allowed: {path}")
        resolved = path.resolve(strict=True)
        if not resolved.is_file() and not resolved.is_dir():
            raise ValueError(f"Source must be a file or directory: {path}")
        return resolved


@dataclass(frozen=True)
class BuildSummary:
    indexed_documents: int
    skipped_files: int
    source_profiles: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    source_path: str
    citation: str
    source_hash: str
    source_family: str
    authority_class: str
    retrieval_mode: str
    score: float
    freshness_state: str
    warnings: tuple[str, ...]
    excerpt: str


def build_index(index_path: Path | str, sources: Sequence[SourceSpec]) -> BuildSummary:
    """Refresh documents found only under the supplied approved sources.

    Entries outside the supplied source paths are retained. Entries beneath an
    approved directory that no longer exist are removed, so subsequent searches
    do not return vanished documents from that declared source.
    """

    if not sources:
        raise ValueError("At least one explicitly approved source is required")

    destination = Path(index_path).resolve()
    documents: list[tuple[Path, SourceSpec, str, str]] = []
    approved_roots: list[Path] = []
    skipped_files = 0
    for source in sources:
        root = source.resolved_path
        approved_roots.append(root)
        for path in _iter_approved_files(root, source.extensions):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped_files += 1
                continue
            documents.append((path, source, content, _content_hash(content)))

    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    try:
        _initialize_database(connection)
        with connection:
            for root in approved_roots:
                _delete_documents_beneath(connection, root)
            for path, source, content, content_hash in documents:
                _upsert_document(connection, path, source, content, content_hash)
    finally:
        connection.close()

    return BuildSummary(
        indexed_documents=len(documents),
        skipped_files=skipped_files,
        source_profiles=tuple(sorted({source.profile for source in sources})),
    )


def search_index(
    index_path: Path | str,
    query: str,
    *,
    limit: int = 10,
    allow_stale: bool = False,
    exact: bool = False,
) -> list[SearchResult]:
    """Return full-text locators, rejecting hash-drifted sources by default."""

    if limit < 1:
        raise ValueError("Search limit must be at least one")
    fts_query = _to_fts_query(query, exact=exact)
    resolved_index_path = Path(index_path)
    if not resolved_index_path.is_file():
        raise ValueError(f"Workspace index is unavailable: {index_path}")
    connection = sqlite3.connect(
        f"file:{resolved_index_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT d.source_path, d.source_family, d.authority_class, "
            "d.content_hash, d.content, bm25(documents_fts) AS score "
            "FROM documents_fts "
            "JOIN documents d ON d.document_id = documents_fts.rowid "
            "WHERE documents_fts MATCH ? "
            "ORDER BY score, d.source_path LIMIT ?",
            (fts_query, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise ValueError(f"Workspace index is unavailable: {index_path}") from exc
        raise
    finally:
        connection.close()

    results: list[SearchResult] = []
    for row in rows:
        freshness_state, warnings = _freshness(Path(row["source_path"]), row["content_hash"])
        if freshness_state != "fresh" and not allow_stale:
            continue
        line_start, line_end, excerpt = _excerpt_for_query(row["content"], query)
        results.append(
            SearchResult(
                source_path=row["source_path"],
                citation=f"{row['source_path']}:L{line_start}-L{line_end}",
                source_hash=row["content_hash"],
                source_family=row["source_family"],
                authority_class=row["authority_class"],
                retrieval_mode="exact_full_text" if exact else "full_text",
                score=float(row["score"]),
                freshness_state=freshness_state,
                warnings=tuple(warnings),
                excerpt=excerpt,
            )
        )
    return results


def index_status(index_path: Path | str) -> dict[str, object]:
    """Return availability, size, and source-hash freshness for the index."""
    resolved = Path(index_path)
    if not resolved.is_file():
        return {
            "index_path": str(resolved),
            "available": False,
            "document_count": 0,
            "indexed_at": None,
            "stale_source_count": 0,
        }

    connection = sqlite3.connect(
        f"file:{resolved.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        count_row = connection.execute(
            "SELECT COUNT(*) AS document_count, MAX(indexed_at) AS indexed_at FROM documents"
        ).fetchone()
        rows = connection.execute(
            "SELECT source_path, content_hash FROM documents"
        ).fetchall()
    finally:
        connection.close()

    stale_count = 0
    for row in rows:
        path = Path(row["source_path"])
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            stale_count += 1
            continue
        if _content_hash(content) != row["content_hash"]:
            stale_count += 1

    return {
        "index_path": str(resolved),
        "available": True,
        "document_count": count_row["document_count"] or 0,
        "indexed_at": count_row["indexed_at"],
        "stale_source_count": stale_count,
    }


def _initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS documents (
            document_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL UNIQUE,
            source_family TEXT NOT NULL,
            authority_class TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(content);
        """
    )


def _delete_documents_beneath(connection: sqlite3.Connection, root: Path) -> None:
    root_text = str(root)
    if root.is_file():
        rows = connection.execute(
            "SELECT document_id FROM documents WHERE source_path = ?", (root_text,)
        ).fetchall()
    else:
        prefix_pattern = _escape_sql_like(root_text) + _escape_sql_like(os.sep) + "%"
        rows = connection.execute(
            "SELECT document_id FROM documents "
            "WHERE source_path = ? OR source_path LIKE ? ESCAPE '\\'",
            (root_text, prefix_pattern),
        ).fetchall()
    for row in rows:
        connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row[0],))
        connection.execute("DELETE FROM documents WHERE document_id = ?", (row[0],))


def _upsert_document(
    connection: sqlite3.Connection,
    path: Path,
    source: SourceSpec,
    content: str,
    content_hash: str,
) -> None:
    indexed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cursor = connection.execute(
        "INSERT INTO documents (source_path, source_family, authority_class, content_hash, indexed_at, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(path), source.profile, source.authority_class, content_hash, indexed_at, content),
    )
    connection.execute(
        "INSERT INTO documents_fts(rowid, content) VALUES (?, ?)",
        (cursor.lastrowid, content),
    )


def _iter_approved_files(root: Path, extensions: frozenset[str]) -> Iterable[Path]:
    candidates = [root] if root.is_file() else root.rglob("*")
    for path in sorted(candidates):
        if path.is_symlink() or not path.is_file():
            continue
        if _path_contains_excluded_directory(path, root):
            continue
        if _is_allowed_text_file(path, extensions):
            yield path.resolve()


def _path_contains_excluded_directory(path: Path, source_root: Path) -> bool:
    if source_root.is_file():
        return source_root.parent.name.lower() in EXCLUDED_DIRECTORY_NAMES
    relative_parts = path.relative_to(source_root).parts
    return any(part.lower() in EXCLUDED_DIRECTORY_NAMES for part in relative_parts[:-1])


def _is_allowed_text_file(path: Path, extensions: frozenset[str]) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in extensions
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
        and not name.startswith(".env")
        and "credential" not in name
        and "secret" not in name
    )


def _to_fts_query(query: str, *, exact: bool = False) -> str:
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not terms:
        raise ValueError("Query must contain at least one searchable term")
    if exact:
        return f'"{" ".join(terms).replace(chr(34), chr(34) * 2)}"'
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _freshness(path: Path, expected_hash: str) -> tuple[str, list[str]]:
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", ["source file is missing"]
    except (OSError, UnicodeDecodeError):
        return "unreadable", ["source file cannot be read for freshness validation"]
    if _content_hash(current) != expected_hash:
        return "stale", ["source hash changed"]
    return "fresh", []


def _excerpt_for_query(content: str, query: str) -> tuple[int, int, str]:
    match = re.search(re.escape(query), content, flags=re.IGNORECASE)
    if match is None:
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        match = re.search(re.escape(terms[0]), content, flags=re.IGNORECASE)
    start = match.start() if match else 0
    end = match.end() if match else 0
    line_start = content.count("\n", 0, start) + 1
    line_end = content.count("\n", 0, end) + 1
    lines = content.splitlines()
    excerpt_start = max(0, line_start - 2)
    excerpt_end = min(len(lines), line_end + 1)
    return line_start, line_end, "\n".join(lines[excerpt_start:excerpt_end])


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_extension(value: str) -> str:
    value = value.strip().lower()
    if not value:
        raise ValueError("Empty extension is not allowed")
    return value if value.startswith(".") else f".{value}"


def _command_index(arguments: argparse.Namespace) -> int:
    extensions = frozenset(arguments.include_extension or DEFAULT_EXTENSIONS)
    sources = [
        SourceSpec(
            path=source,
            profile=arguments.profile,
            authority_class=arguments.authority_class,
            extensions=extensions,
        )
        for source in arguments.source
    ]
    summary = build_index(arguments.index, sources)
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0


def _command_query(arguments: argparse.Namespace) -> int:
    results = search_index(
        arguments.index,
        arguments.query,
        limit=arguments.limit,
        allow_stale=arguments.allow_stale,
        exact=arguments.exact,
    )
    payload = {
        "retrieval_mode": "exact_full_text" if arguments.exact else "full_text",
        "result_count": len(results),
        "results": [asdict(result) for result in results],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _command_status(arguments: argparse.Namespace) -> int:
    summary = index_status(arguments.index)
    healthy = bool(summary["available"]) and summary["stale_source_count"] == 0
    status = "ok" if healthy else "degraded"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps({"status": status, "generated_at": generated_at, **summary}, sort_keys=True))
    return 0 if healthy else 1


def _parse_arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    index = subcommands.add_parser("index", help="Index explicitly approved text sources")
    index.add_argument("--source", action="append", required=True, help="Approved file or directory")
    index.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    index.add_argument("--profile", default="explicit-local-source")
    index.add_argument("--authority-class", default="source")
    index.add_argument(
        "--include-extension",
        action="append",
        help="Additional approved text extension, such as .py; defaults to safe document formats",
    )
    index.set_defaults(handler=_command_index)

    status = subcommands.add_parser("status", help="Report index availability and source freshness")
    status.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    status.set_defaults(handler=_command_status)

    query = subcommands.add_parser("query", help="Search a previously built index")
    query.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    query.add_argument("--query", required=True)
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--allow-stale", action="store_true")
    query.add_argument("--exact", action="store_true", help="Require the query phrase in order")
    query.set_defaults(handler=_command_query)
    return parser


def main() -> int:
    arguments = _parse_arguments().parse_args()
    try:
        return arguments.handler(arguments)
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        raise SystemExit(f"workspace-index: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
