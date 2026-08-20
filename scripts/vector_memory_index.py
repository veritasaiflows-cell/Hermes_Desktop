#!/usr/bin/env python3
"""SQLite-backed memory index with FTS5 and local Ollama embeddings.

This module ingests explicitly approved local sources into a local SQLite
workspace and provides executable retrieval commands for the adapter names
`memory_search` (query) and `memory_get` (metadata lookup).

- full-text: always available via FTS5,
- semantic: uses local Ollama embeddings,
- hybrid: ranks using both signals.

Results are locator-style: open `source_path` and `citation` before treating a
record as truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Iterable
from typing import Sequence


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = WORKSPACE_ROOT / "tmp" / "vector-memory.sqlite"
DEFAULT_QUERY_PACKET_PATH = WORKSPACE_ROOT / "tmp" / "vector-memory-query.json"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"
DEFAULT_EMBEDDING_PROVIDER = "ollama"
DEFAULT_EXTENSIONS = frozenset(
    {".csv", ".ini", ".json", ".md", ".mdx", ".py", ".rst", ".sql", ".txt", ".toml", ".yml", ".yaml"}
)
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
VALID_RETRIEVAL_MODES = frozenset({"full_text", "semantic", "hybrid"})
DEFAULT_RETRIEVAL_MODE = "full_text"

# Conservative ceiling for embedding providers. Ollama + nomic-embed-text rejects
# payloads beyond ~8,800 chars. We chunk at a character boundary smaller than that
# to keep calls safe and then average chunk embeddings for the document vector.
DEFAULT_EMBED_CHUNK_CHAR_LIMIT = 6000

# In-process LRU cache for *query* embeddings only (never document embeddings).
# Keyed on (text, provider, model, base_url). Cleared on every build_index().
_QUERY_EMBEDDING_CACHE: "OrderedDict[tuple[str, str, str, str], list[float]]" = OrderedDict()
_QUERY_EMBEDDING_CACHE_MAXSIZE = 256
_QUERY_EMBEDDING_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0}


def clear_query_embedding_cache() -> None:
    """Drop all cached query embeddings and reset hit/miss counters."""
    _QUERY_EMBEDDING_CACHE.clear()
    _QUERY_EMBEDDING_CACHE_STATS["hits"] = 0
    _QUERY_EMBEDDING_CACHE_STATS["misses"] = 0


def query_embedding_cache_stats() -> dict[str, int]:
    """Return a snapshot of query-embedding cache hits/misses and current size."""
    return {**_QUERY_EMBEDDING_CACHE_STATS, "size": len(_QUERY_EMBEDDING_CACHE)}


@dataclass(frozen=True)
class SourceSpec:
    """An explicitly approved source location and its retrieval metadata."""

    path: Path | str
    profile: str = "explicit-local-memory"
    authority_class: str = "semantic_memory"
    source_type: str = "file"
    extensions: frozenset[str] = DEFAULT_EXTENSIONS

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("Source profile must not be empty")
        if not self.authority_class.strip():
            raise ValueError("Authority class must not be empty")
        if not self.source_type.strip():
            raise ValueError("Source type must not be empty")
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
    embedded_documents: int
    embedding_errors: int


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


def build_index(
    index_path: Path | str,
    sources: Sequence[SourceSpec],
    *,
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    disable_embedding: bool = False,
    embedding_timeout: float = 20.0,
) -> BuildSummary:
    """Build or refresh a memory index from explicit sources only.

    Indexing is scoped to supplied `sources`; every indexed document outside those
    roots is removed so stale documents from removed approvals do not linger.
    """

    if not sources:
        raise ValueError("At least one explicit source is required")

    # Query embeddings are corpus-independent, but clearing on reindex keeps the
    # cache conservative and guarantees deterministic test isolation.
    clear_query_embedding_cache()

    destination = Path(index_path).resolve()
    documents: list[tuple[Path, SourceSpec, str, str, list[float] | None]] = []
    skipped_files = 0
    embedded_documents = 0
    embedding_errors = 0
    approved_roots: list[Path] = []
    seen_paths: set[str] = set()

    for source in sources:
        root = source.resolved_path
        approved_roots.append(root)
        for path in _iter_approved_files(root, source.extensions):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped_files += 1
                continue
            if str(path) in seen_paths:
                continue
            seen_paths.add(str(path))
            content_hash = _content_hash(content)

            if disable_embedding:
                embedding = None
            else:
                embedding = _embed_text_with_chunking(
                    content,
                    chunk_char_limit=DEFAULT_EMBED_CHUNK_CHAR_LIMIT,
                    provider=embedding_provider,
                    model=embedding_model,
                    ollama_base_url=ollama_base_url,
                    timeout=embedding_timeout,
                )
                if embedding is None:
                    embedding_errors += 1
                else:
                    embedded_documents += 1

            documents.append((path, source, content, content_hash, embedding))

    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    try:
        _initialize_database(connection)
        with connection:
            for root in approved_roots:
                _delete_documents_beneath(connection, root)
            for path, source, content, content_hash, embedding in documents:
                _upsert_document(
                    connection,
                    path,
                    source,
                    content,
                    content_hash,
                    embedding,
                    embedding_provider if not disable_embedding and embedding is not None else "",
                    embedding_model if not disable_embedding and embedding is not None else "",
                )
    finally:
        connection.close()

    return BuildSummary(
        indexed_documents=len(documents),
        skipped_files=skipped_files,
        source_profiles=tuple(sorted({source.profile for source in sources})),
        embedded_documents=embedded_documents,
        embedding_errors=embedding_errors,
    )


def search_index(
    index_path: Path | str,
    query: str,
    *,
    limit: int = 10,
    allow_stale: bool = False,
    exact: bool = False,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    embedding_timeout: float = 20.0,
) -> list[SearchResult]:
    """Return ranked locators from the memory index."""

    if limit < 1:
        raise ValueError("Search limit must be at least one")
    retrieval_mode = retrieval_mode.strip().lower()
    if retrieval_mode not in VALID_RETRIEVAL_MODES:
        raise ValueError(f"Unsupported retrieval mode: {retrieval_mode}")
    if exact and retrieval_mode != "full_text":
        raise ValueError("`exact` matching is only valid for full_text retrieval")

    resolved_index_path = Path(index_path)
    if not resolved_index_path.is_file():
        raise ValueError(f"Memory index is unavailable: {index_path}")

    if retrieval_mode == "full_text":
        return _search_fulltext(
            resolved_index_path,
            query,
            limit=limit,
            allow_stale=allow_stale,
            exact=exact,
        )

    connection = sqlite3.connect(
        f"file:{resolved_index_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    try:
        if retrieval_mode == "semantic":
            return _search_semantic(
                connection,
                query,
                limit=limit,
                allow_stale=allow_stale,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                ollama_base_url=ollama_base_url,
                embedding_timeout=embedding_timeout,
            )

        if retrieval_mode == "hybrid":
            full_text_rows = _query_full_text_rows(connection, query, exact=exact)
            semantic_rows = _query_semantic_rows(
                connection,
                query,
                limit=limit * 2,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                ollama_base_url=ollama_base_url,
                embedding_timeout=embedding_timeout,
            )

            return _merge_fulltext_semantic(
                full_text_rows,
                semantic_rows,
                query,
                limit=limit,
                allow_stale=allow_stale,
            )
        raise ValueError(f"Unsupported retrieval mode: {retrieval_mode}")
    finally:
        connection.close()


def memory_search(
    query: str,
    *,
    index: Path | str = DEFAULT_INDEX_PATH,
    limit: int = 10,
    allow_stale: bool = False,
    exact: bool = False,
) -> list[SearchResult]:
    """Adapter entrypoint: default to local Ollama-backed hybrid retrieval."""

    return search_index(
        index,
        query,
        limit=limit,
        allow_stale=allow_stale,
        exact=exact,
        retrieval_mode="hybrid",
        embedding_provider=DEFAULT_EMBEDDING_PROVIDER,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        ollama_base_url=DEFAULT_OLLAMA_BASE_URL,
        embedding_timeout=20.0,
    )


def memory_get(index: Path | str, source_path: str) -> dict[str, object]:
    """Adapter entrypoint for metadata lookup by source path."""

    record = get_index_entry(index, source_path)
    if record is None:
        raise ValueError(f"memory source not indexed: {source_path}")
    return record


def build_query_packet(
    index_path: Path | str,
    query: str,
    *,
    limit: int = 10,
    allow_stale: bool = False,
    exact: bool = False,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    packet_path: Path | str | None = None,
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    embedding_timeout: float = 20.0,
) -> dict:
    """Build and optionally persist a machine-readable retrieval packet."""

    results = search_index(
        index_path,
        query,
        limit=limit,
        allow_stale=allow_stale,
        exact=exact,
        retrieval_mode=retrieval_mode,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        ollama_base_url=ollama_base_url,
        embedding_timeout=embedding_timeout,
    )

    payload = {
        "generated_at": _utc_now(),
        "query": query,
        "retrieval_mode": retrieval_mode,
        "result_count": len(results),
        "results": [asdict(result) for result in results],
        "index_path": str(Path(index_path)),
    }
    if packet_path is not None:
        resolved = Path(packet_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return payload


def get_index_entry(index_path: Path | str, source_path: str) -> dict[str, object] | None:
    """Return indexed metadata for a known source path, or None if absent."""

    resolved_index_path = Path(index_path)
    if not resolved_index_path.is_file():
        raise ValueError(f"Memory index is unavailable: {index_path}")

    connection = sqlite3.connect(
        f"file:{resolved_index_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    columns = _table_columns(connection, "memory_memories")
    selected = [
        "source_path",
        "source_family",
        "authority_class",
        "source_hash",
        "indexed_at",
        "length(content) AS bytes",
        "source_type",
    ]
    if "embedding_model" in columns:
        selected.append("embedding_model")
    if "embedding_provider" in columns:
        selected.append("embedding_provider")

    query = f"SELECT {', '.join(selected)} FROM memory_memories WHERE source_path = ?"
    try:
        row = connection.execute(
            query,
            (str(Path(source_path).resolve()),),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        connection.close()


def index_status(index_path: Path | str) -> dict[str, object]:
    """Return a lightweight status summary for the memory index.

    Useful for cron/health checks: reports document count, presence of the
    index file, and the most recent indexed timestamp. Does not block on a
    missing index; instead returns ``available=False`` so callers can decide
    whether an empty/missing index is an error.
    """
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
        columns = _table_columns(connection, "memory_memories")
        count_row = connection.execute(
            "SELECT COUNT(*) AS document_count, MAX(indexed_at) AS indexed_at FROM memory_memories"
        ).fetchone()
        stale_count = 0
        if "source_hash" in columns:
            # Count documents whose current disk hash differs from indexed hash.
            # This is a best-effort freshness signal without requiring full reindex.
            rows = connection.execute(
                "SELECT source_path, source_hash FROM memory_memories"
            ).fetchall()
            for row in rows:
                path = Path(row["source_path"])
                if path.exists():
                    current_hash = _content_hash(path.read_text(encoding="utf-8"))
                    if current_hash != row["source_hash"]:
                        stale_count += 1
        return {
            "index_path": str(resolved),
            "available": True,
            "document_count": count_row["document_count"] or 0,
            "indexed_at": count_row["indexed_at"],
            "stale_source_count": stale_count,
        }
    finally:
        connection.close()


def _initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS memory_memories (
            memory_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            source_family TEXT NOT NULL,
            authority_class TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding_model TEXT,
            embedding_provider TEXT,
            embedding_vector_json TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_memories_fts USING fts5(content);
        """
    )

    # Upgrade older DBs from earlier versions that only had the structural columns.
    table_columns = {
        row[1]: row[1]
        for row in connection.execute("PRAGMA table_info(memory_memories)").fetchall()
        if len(row) > 1
    }
    if "embedding_model" not in table_columns:
        connection.execute("ALTER TABLE memory_memories ADD COLUMN embedding_model TEXT")
    if "embedding_provider" not in table_columns:
        connection.execute("ALTER TABLE memory_memories ADD COLUMN embedding_provider TEXT")
    if "embedding_vector_json" not in table_columns:
        connection.execute("ALTER TABLE memory_memories ADD COLUMN embedding_vector_json TEXT")


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {
            row[1] if not isinstance(row, sqlite3.Row) else row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
    except sqlite3.DatabaseError:
        return set()


def _delete_documents_beneath(connection: sqlite3.Connection, root: Path) -> None:
    root_text = str(root)
    if root.is_file():
        rows = connection.execute(
            "SELECT memory_id FROM memory_memories WHERE source_path = ?",
            (root_text,),
        ).fetchall()
    else:
        prefix_pattern = _escape_sql_like(root_text) + _escape_sql_like(os.sep) + "%"
        rows = connection.execute(
            "SELECT memory_id FROM memory_memories WHERE source_path = ? OR source_path LIKE ? ESCAPE '\\'",
            (root_text, prefix_pattern),
        ).fetchall()
    for row in rows:
        memory_id = row[0]
        connection.execute("DELETE FROM memory_memories_fts WHERE rowid = ?", (memory_id,))
        connection.execute("DELETE FROM memory_memories WHERE memory_id = ?", (memory_id,))


def _upsert_document(
    connection: sqlite3.Connection,
    path: Path,
    source: SourceSpec,
    content: str,
    content_hash: str,
    embedding: list[float] | None,
    embedding_provider: str,
    embedding_model: str,
) -> None:
    indexed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cursor = connection.execute(
        "INSERT INTO memory_memories (source_path, source_type, source_family, "
        "authority_class, source_hash, indexed_at, content, embedding_model, "
        "embedding_provider, embedding_vector_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(path),
            source.source_type,
            source.profile,
            source.authority_class,
            content_hash,
            indexed_at,
            content,
            embedding_model or None,
            embedding_provider or None,
            json.dumps(embedding) if embedding is not None else None,
        ),
    )
    connection.execute(
        "INSERT INTO memory_memories_fts(rowid, content) VALUES (?, ?)",
        (cursor.lastrowid, content),
    )


def _query_full_text_rows(
    connection: sqlite3.Connection,
    query: str,
    *,
    exact: bool = False,
) -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query, exact=exact)
    try:
        return connection.execute(
            "SELECT m.source_path, m.source_family, m.authority_class, "
            "m.source_hash, m.content, bm25(memory_memories_fts) AS score "
            "FROM memory_memories_fts "
            "JOIN memory_memories m ON m.memory_id = memory_memories_fts.rowid "
            "WHERE memory_memories_fts MATCH ? "
            "ORDER BY score, m.source_path",
            (fts_query,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise ValueError("Memory index is unavailable") from exc
        raise


def _search_fulltext(
    index_path: Path,
    query: str,
    *,
    limit: int,
    allow_stale: bool,
    exact: bool,
) -> list[SearchResult]:
    connection = sqlite3.connect(
        f"file:{index_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = _query_full_text_rows(connection, query, exact=exact)
    finally:
        connection.close()

    results: list[SearchResult] = []
    for row in rows:
        if len(results) >= limit:
            break
        freshness_state, warnings = _freshness(Path(row["source_path"]), row["source_hash"])
        if freshness_state != "fresh" and not allow_stale:
            continue
        line_start, line_end, excerpt = _excerpt_for_query(row["content"], query)
        results.append(
            SearchResult(
                source_path=row["source_path"],
                citation=f"{row['source_path']}:L{line_start}-L{line_end}",
                source_hash=row["source_hash"],
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


def _search_semantic(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    allow_stale: bool,
    embedding_provider: str,
    embedding_model: str,
    ollama_base_url: str,
    embedding_timeout: float,
) -> list[SearchResult]:
    query_embedding = _build_query_embedding(
        query,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        ollama_base_url=ollama_base_url,
        timeout=embedding_timeout,
    )
    if query_embedding is None:
        raise ValueError("Embedding backend unavailable for semantic retrieval")

    rows = connection.execute(
        "SELECT source_path, source_family, authority_class, source_hash, content, embedding_vector_json "
        "FROM memory_memories WHERE embedding_vector_json IS NOT NULL"
    ).fetchall()

    candidates: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        embedding = _parse_embedding(row["embedding_vector_json"])
        if embedding is None:
            continue
        score = _cosine_similarity(query_embedding, embedding)
        if score is not None and score > 0:
            candidates.append((score, row))

    candidates.sort(key=lambda item: item[0], reverse=True)
    results: list[SearchResult] = []
    for score, row in candidates[:limit]:
        freshness_state, warnings = _freshness(Path(row["source_path"]), row["source_hash"])
        if freshness_state != "fresh" and not allow_stale:
            continue
        line_start, line_end, excerpt = _excerpt_for_query(row["content"], query)
        results.append(
            SearchResult(
                source_path=row["source_path"],
                citation=f"{row['source_path']}:L{line_start}-L{line_end}",
                source_hash=row["source_hash"],
                source_family=row["source_family"],
                authority_class=row["authority_class"],
                retrieval_mode="semantic",
                score=score,
                freshness_state=freshness_state,
                warnings=tuple(warnings),
                excerpt=excerpt,
            )
        )

    return results


def _query_semantic_rows(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    embedding_provider: str,
    embedding_model: str,
    ollama_base_url: str,
    embedding_timeout: float,
) -> list[tuple[sqlite3.Row, float]]:
    query_embedding = _build_query_embedding(
        query,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        ollama_base_url=ollama_base_url,
        timeout=embedding_timeout,
    )
    if query_embedding is None:
        return []

    rows = connection.execute(
        "SELECT source_path, source_family, authority_class, source_hash, content, "
        "embedding_vector_json FROM memory_memories WHERE embedding_vector_json IS NOT NULL"
    ).fetchall()

    scored: list[tuple[sqlite3.Row, float]] = []
    for row in rows:
        embedding = _parse_embedding(row["embedding_vector_json"])
        if embedding is None:
            continue
        score = _cosine_similarity(query_embedding, embedding)
        if score is None:
            continue
        scored.append((row, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _merge_fulltext_semantic(
    full_text_rows: list[sqlite3.Row],
    semantic_rows: list[tuple[sqlite3.Row, float]],
    query: str,
    *,
    limit: int,
    allow_stale: bool,
) -> list[SearchResult]:
    def row_freshness(path: str, source_hash: str):
        state, warnings = _freshness(Path(path), source_hash)
        return state, tuple(warnings)

    full_map: dict[str, dict[str, object]] = {}
    for row in full_text_rows:
        score = _normalize_fts_score(float(row["score"]))
        line_start, line_end, excerpt = _excerpt_for_query(row["content"], query)
        freshness_state, warnings = row_freshness(row["source_path"], row["source_hash"])
        full_map[row["source_path"]] = {
            "row": row,
            "line_start": line_start,
            "line_end": line_end,
            "excerpt": excerpt,
            "score": score,
            "freshness_state": freshness_state,
            "warnings": warnings,
        }

    semantic_map: dict[str, dict[str, object]] = {}
    for row, score in semantic_rows:
        line_start, line_end, excerpt = _excerpt_for_query(row["content"], query)
        freshness_state, warnings = row_freshness(row["source_path"], row["source_hash"])
        semantic_map[row["source_path"]] = {
            "row": row,
            "line_start": line_start,
            "line_end": line_end,
            "excerpt": excerpt,
            "score": max(0.0, score),
            "freshness_state": freshness_state,
            "warnings": warnings,
        }

    paths = set(full_map) | set(semantic_map)
    combined: list[SearchResult] = []
    for path in paths:
        full = full_map.get(path)
        semantic = semantic_map.get(path)
        if full is None and semantic is None:
            continue

        row = (full or semantic)["row"]
        if full and semantic:
            full_score = full["score"]
            semantic_score = semantic["score"]
            score = (0.7 * semantic_score) + (0.3 * full_score)
            if semantic_score >= full_score:
                line_start = semantic["line_start"]
                line_end = semantic["line_end"]
                excerpt = semantic["excerpt"]
                freshness_state = semantic["freshness_state"]
                warnings = tuple(sorted(set(semantic["warnings"])))
            else:
                line_start = full["line_start"]
                line_end = full["line_end"]
                excerpt = full["excerpt"]
                freshness_state = full["freshness_state"]
                warnings = tuple(sorted(set(full["warnings"])) )
        elif semantic:
            score = semantic["score"]
            line_start = semantic["line_start"]
            line_end = semantic["line_end"]
            excerpt = semantic["excerpt"]
            freshness_state = semantic["freshness_state"]
            warnings = semantic["warnings"]
        else:
            score = full["score"]
            line_start = full["line_start"]
            line_end = full["line_end"]
            excerpt = full["excerpt"]
            freshness_state = full["freshness_state"]
            warnings = full["warnings"]

        if not allow_stale and freshness_state != "fresh":
            continue
        combined.append(
            SearchResult(
                source_path=row["source_path"],
                citation=f"{row['source_path']}:L{line_start}-L{line_end}",
                source_hash=row["source_hash"],
                source_family=row["source_family"],
                authority_class=row["authority_class"],
                retrieval_mode="hybrid",
                score=float(score),
                freshness_state=freshness_state,
                warnings=tuple(sorted(set(warnings))),
                excerpt=excerpt,
            )
        )

    combined.sort(key=lambda item: item.score, reverse=True)
    return combined[:limit]


def _safe_embedding_for_text(
    text: str,
    *,
    provider: str,
    model: str,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout: float = 20.0,
) -> list[float] | None:
    if not text.strip():
        return None
    provider = provider.strip().lower()
    if provider == "" or provider == "none":
        return None
    if provider != DEFAULT_EMBEDDING_PROVIDER:
        return None

    try:
        return _ollama_embedding(text, model=model, base_url=ollama_base_url, timeout=timeout)
    except Exception:
        return None


def _embed_text_with_chunking(
    text: str,
    *,
    chunk_char_limit: int,
    provider: str,
    model: str,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout: float = 20.0,
) -> list[float] | None:
    """Return a single embedding for a possibly oversized document.

    If the document fits under ``chunk_char_limit`` it is embedded in one call.
    Otherwise the text is split into overlapping chunks, each chunk is embedded,
    and the resulting vectors are averaged. Any chunk failure causes the whole
    document to be treated as an embedding error.
    """

    if not text.strip():
        return None

    chunks = _chunk_text(text, chunk_char_limit)
    if not chunks:
        return None

    chunk_vectors: list[list[float]] = []
    for chunk in chunks:
        vector = _safe_embedding_for_text(
            chunk,
            provider=provider,
            model=model,
            ollama_base_url=ollama_base_url,
            timeout=timeout,
        )
        if vector is None:
            return None
        chunk_vectors.append(vector)

    dimensions = max(len(vector) for vector in chunk_vectors)
    sums = [0.0] * dimensions
    for vector in chunk_vectors:
        for i, value in enumerate(vector):
            sums[i] += value
    return [total / len(chunk_vectors) for total in sums]


def _build_query_embedding(
    text: str,
    *,
    embedding_provider: str,
    embedding_model: str,
    ollama_base_url: str,
    timeout: float,
) -> list[float] | None:
    """Return the embedding for a *query* string, using an in-process cache.

    A query's embedding depends only on (text, provider, model, base_url) and is
    independent of the indexed corpus, so cached vectors never go stale on
    re-index. This is the hot path for hybrid/semantic retrieval; caching avoids
    a ~130ms Ollama round-trip on repeated queries. The cache is bounded (LRU)
    and cleared on every `build_index` call as a conservative safety measure.
    """

    key = (
        text,
        embedding_provider.strip().lower(),
        embedding_model,
        ollama_base_url.rstrip("/"),
    )
    cached = _QUERY_EMBEDDING_CACHE.get(key)
    if cached is not None:
        _QUERY_EMBEDDING_CACHE.move_to_end(key)
        _QUERY_EMBEDDING_CACHE_STATS["hits"] += 1
        return list(cached)

    embedding = _safe_embedding_for_text(
        text,
        provider=embedding_provider,
        model=embedding_model,
        ollama_base_url=ollama_base_url,
        timeout=timeout,
    )
    _QUERY_EMBEDDING_CACHE_STATS["misses"] += 1
    if embedding is None:
        return None

    _QUERY_EMBEDDING_CACHE[key] = list(embedding)
    _QUERY_EMBEDDING_CACHE.move_to_end(key)
    while len(_QUERY_EMBEDDING_CACHE) > _QUERY_EMBEDDING_CACHE_MAXSIZE:
        _QUERY_EMBEDDING_CACHE.popitem(last=False)
    return list(embedding)


def _ollama_embedding(
    text: str,
    *,
    model: str,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout: float = 20.0,
) -> list[float]:
    endpoint = f"{base_url.rstrip('/')}/api/embeddings"
    payload = {"model": model, "prompt": text}
    request = urllib.request.Request(
        endpoint,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload_raw = response.read().decode("utf-8")
    body = json.loads(payload_raw)
    embedding = body.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("ollama embedding response missing embedding data")
    return [float(component) for component in embedding]


def _parse_embedding(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    cleaned: list[float] = []
    for value in data:
        try:
            cleaned.append(float(value))
        except (TypeError, ValueError):
            return None
    return cleaned


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    if not left or not right:
        return None
    min_len = min(len(left), len(right))
    if min_len == 0:
        return None
    dot_product = 0.0
    left_norm_sq = 0.0
    right_norm_sq = 0.0
    for left_value, right_value in zip(left[:min_len], right[:min_len], strict=False):
        dot_product += left_value * right_value
        left_norm_sq += left_value * left_value
        right_norm_sq += right_value * right_value
    if left_norm_sq == 0.0 or right_norm_sq == 0.0:
        return None
    return dot_product / ((left_norm_sq ** 0.5) * (right_norm_sq ** 0.5))


def _normalize_fts_score(score: float) -> float:
    return 1.0 / (1.0 + abs(score))


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


def _chunk_text(text: str, chunk_char_limit: int) -> list[str]:
    """Return a list of overlapping character-bounded chunks for long documents."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_char_limit:
        return [text]
    step = chunk_char_limit // 2
    chunks: list[str] = []
    for start in range(0, len(text), step):
        chunk = text[start : start + chunk_char_limit]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


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
        match = re.search(re.escape(terms[0]), content, flags=re.IGNORECASE) if terms else None
    start = match.start() if match else 0
    end = match.end() if match else 0
    line_start = content.count("\n", 0, start) + 1
    line_end = content.count("\n", 0, end) + 1
    lines = content.splitlines()
    excerpt_start = max(0, line_start - 2)
    excerpt_end = min(len(lines), line_end + 1)
    excerpt = "\n".join(lines[excerpt_start:excerpt_end])
    if not excerpt:
        excerpt = "\n".join(lines[:1]) if lines else ""
        line_start, line_end = 1, 1
    return line_start, line_end, excerpt


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_extension(value: str) -> str:
    value = value.strip().lower()
    if not value:
        raise ValueError("Empty extension is not allowed")
    return value if value.startswith(".") else f".{value}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    summary = build_index(
        arguments.index,
        sources,
        embedding_provider=arguments.embedding_provider,
        embedding_model=arguments.embedding_model,
        ollama_base_url=arguments.ollama_base_url,
        disable_embedding=arguments.disable_embedding,
        embedding_timeout=arguments.embedding_timeout,
    )
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0


def _configure_search_parser(
    subcommands: argparse._SubParsersAction,
    name: str,
    *,
    default_retrieval_mode: str,
    help_text: str,
) -> argparse.ArgumentParser:
    search = subcommands.add_parser(name, help=help_text)
    search.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--allow-stale", action="store_true")
    search.add_argument("--exact", action="store_true", help="Require the query phrase in order")
    search.add_argument(
        "--retrieval-mode",
        choices=sorted(VALID_RETRIEVAL_MODES),
        default=default_retrieval_mode,
    )
    search.add_argument(
        "--embedding-provider",
        default=DEFAULT_EMBEDDING_PROVIDER,
        help="Embedding provider for semantic and hybrid modes",
    )
    search.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Embedding model name for ollama",
    )
    search.add_argument(
        "--ollama-base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help="Base URL for local ollama API",
    )
    search.add_argument(
        "--embedding-timeout",
        type=float,
        default=20.0,
        help="Timeout (seconds) for local embedding requests",
    )
    search.add_argument("--disable-embedding", action="store_true", help="Skip embeddings")
    search.add_argument(
        "--packet",
        type=Path,
        default=DEFAULT_QUERY_PACKET_PATH,
        help="Write retrieval payload to this path in JSON (set to a path to persist the packet)",
    )
    search.set_defaults(handler=_command_query, retrieval_mode=default_retrieval_mode)
    return search


def _command_query(arguments: argparse.Namespace) -> int:
    retrieval_mode = arguments.retrieval_mode
    embedding_provider = arguments.embedding_provider
    if retrieval_mode == "full_text":
        embedding_provider = "none"
    payload = build_query_packet(
        arguments.index,
        arguments.query,
        limit=arguments.limit,
        allow_stale=arguments.allow_stale,
        exact=arguments.exact,
        retrieval_mode=retrieval_mode,
        packet_path=arguments.packet,
        embedding_provider="none" if arguments.disable_embedding else embedding_provider,
        embedding_model=arguments.embedding_model,
        ollama_base_url=arguments.ollama_base_url,
        embedding_timeout=arguments.embedding_timeout,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


def _command_get(arguments: argparse.Namespace) -> int:
    record = get_index_entry(arguments.index, arguments.source_path)
    if record is None:
        raise SystemExit(f"memory: source not indexed: {arguments.source_path}")
    print(json.dumps(record, sort_keys=True))
    return 0


def _command_status(arguments: argparse.Namespace) -> int:
    summary = index_status(arguments.index)
    print(json.dumps({"status": "ok", "generated_at": _utc_now(), **summary}, sort_keys=True))
    return 0


def _parse_arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    index = subcommands.add_parser("index", help="Index approved local sources")
    index.add_argument("--source", action="append", required=True, help="Approved file or directory")
    index.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    index.add_argument("--profile", default="explicit-local-memory")
    index.add_argument("--authority-class", default="semantic_memory")
    index.add_argument(
        "--include-extension",
        action="append",
        help="Additional approved extension such as .py; defaults are safe text formats",
    )
    index.add_argument(
        "--embedding-provider",
        default=DEFAULT_EMBEDDING_PROVIDER,
        help="Embedding provider for indexed documents",
    )
    index.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Embedding model name for ollama",
    )
    index.add_argument(
        "--ollama-base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help="Base URL for local ollama API",
    )
    index.add_argument(
        "--embedding-timeout",
        type=float,
        default=20.0,
        help="Timeout (seconds) for local embedding requests",
    )
    index.add_argument("--disable-embedding", action="store_true", help="Skip embedding generation")
    index.set_defaults(handler=_command_index)

    status = subcommands.add_parser("status", help="Report index availability and document count")
    status.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    status.set_defaults(handler=_command_status)

    _configure_search_parser(
        subcommands,
        "query",
        default_retrieval_mode="full_text",
        help_text="Search memory index with explicit full-text mode by default",
    )
    _configure_search_parser(
        subcommands,
        "memory_search",
        default_retrieval_mode="hybrid",
        help_text="Hybrid retrieval using local ollama embeddings + FTS",
    )

    get_entry = subcommands.add_parser("get", help="Fetch metadata for an indexed source")
    get_entry.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    get_entry.add_argument("--source-path", required=True)
    get_entry.set_defaults(handler=_command_get)

    memory_get = subcommands.add_parser("memory_get", help="Alias for get")
    memory_get.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    memory_get.add_argument("--source-path", required=True)
    memory_get.set_defaults(handler=_command_get)

    return parser


def main() -> int:
    arguments = _parse_arguments().parse_args()
    try:
        return arguments.handler(arguments)
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        raise SystemExit(f"vector-memory-index: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
