#!/usr/bin/env python3
"""Refresh exact and semantic retrieval indexes from one approved source manifest."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import vector_memory_index, workspace_index

DEFAULT_MANIFEST = PROJECT_ROOT / "vector" / "retrieval-sources.json"
FORBIDDEN_PARTS = {"tmp", "secrets", "credentials", "configuration", "config", ".git"}
SUPPORTED_SCHEMAS = ("retrieval-sources.v1", "retrieval-sources.v2")
DEFAULT_WORKSPACE_FAMILY = "explicit-local-source"
DEFAULT_WORKSPACE_AUTHORITY = "source"
DEFAULT_MEMORY_FAMILY = "explicit-local-memory"
DEFAULT_MEMORY_AUTHORITY = "semantic_memory"
DEFAULT_CHUNKING = "markdown_heading"
DEFAULT_SOURCE_TYPE = "file"


@dataclass(frozen=True)
class RetrievalSource:
    """One approved retrieval source with its resolved indexing metadata."""

    path: Path
    relative_path: str
    chunking: str = DEFAULT_CHUNKING
    source_type: str = DEFAULT_SOURCE_TYPE
    source_family: str | None = None
    authority_class: str | None = None

    def workspace_spec(self) -> "workspace_index.SourceSpec":
        return workspace_index.SourceSpec(
            self.path,
            profile=self.source_family or DEFAULT_WORKSPACE_FAMILY,
            authority_class=self.authority_class or DEFAULT_WORKSPACE_AUTHORITY,
            chunking=self.chunking,
        )

    def memory_spec(self) -> "vector_memory_index.SourceSpec":
        return vector_memory_index.SourceSpec(
            self.path,
            profile=self.source_family or DEFAULT_MEMORY_FAMILY,
            authority_class=self.authority_class or DEFAULT_MEMORY_AUTHORITY,
            source_type=self.source_type,
            chunking=self.chunking,
        )


def _resolve_approved_path(raw: object, root: Path) -> Path:
    relative = Path(str(raw))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(part.lower() in FORBIDDEN_PARTS for part in relative.parts)
    ):
        raise ValueError(f"Not an approved relative source: {raw}")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Not an approved relative source: {raw}") from exc
    if not resolved.is_file():
        raise ValueError(f"Approved retrieval source is missing: {raw}")
    return resolved


def _entry_from_mapping(raw: dict[str, Any], root: Path) -> RetrievalSource:
    if "path" not in raw:
        raise ValueError("Retrieval source entry requires a path")
    resolved = _resolve_approved_path(raw["path"], root)
    chunking = str(raw.get("chunking", DEFAULT_CHUNKING))
    if chunking not in workspace_index.VALID_CHUNKING:
        raise ValueError(f"Unsupported chunking policy: {chunking}")
    source_type = str(raw.get("source_type", DEFAULT_SOURCE_TYPE))
    if not source_type.strip():
        raise ValueError("Retrieval source type must not be empty")
    family = raw.get("source_family")
    authority = raw.get("authority_class")
    for label, value in (("source family", family), ("authority class", authority)):
        if value is not None and not str(value).strip():
            raise ValueError(f"Retrieval {label} must not be empty")
    return RetrievalSource(
        path=resolved,
        relative_path=resolved.relative_to(root).as_posix(),
        chunking=chunking,
        source_type=source_type,
        source_family=str(family) if family is not None else None,
        authority_class=str(authority) if authority is not None else None,
    )


def load_source_entries(
    manifest_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[RetrievalSource]:
    """Load approved sources from a v1 (path list) or v2 (metadata) manifest."""
    root = Path(project_root).resolve()
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    schema = payload.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise ValueError("Unsupported retrieval source manifest schema")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Retrieval source manifest requires a non-empty sources list")

    entries: list[RetrievalSource] = []
    seen: set[Path] = set()
    for raw in raw_sources:
        if isinstance(raw, dict):
            if schema == "retrieval-sources.v1":
                raise ValueError("Schema v1 sources must be plain relative paths")
            entry = _entry_from_mapping(raw, root)
        else:
            resolved = _resolve_approved_path(raw, root)
            entry = RetrievalSource(
                path=resolved,
                relative_path=resolved.relative_to(root).as_posix(),
            )
        if entry.path in seen:
            continue
        seen.add(entry.path)
        entries.append(entry)
    return entries


def load_sources(manifest_path: Path, *, project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Load existing, workspace-relative approved files (v1-compatible path view)."""
    return [
        entry.path
        for entry in load_source_entries(Path(manifest_path), project_root=project_root)
    ]


def _summary_fields(summary: Any) -> dict[str, Any]:
    fields = {
        "indexed_documents": int(getattr(summary, "indexed_documents", 0)),
        "skipped_files": int(getattr(summary, "skipped_files", 0)),
    }
    for name in ("embedded_documents", "embedding_errors"):
        if hasattr(summary, name):
            fields[name] = int(getattr(summary, name))
    return fields


def refresh_indexes(
    *,
    project_root: Path = PROJECT_ROOT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Refresh both indexes from exactly the same approved source set."""
    root = Path(project_root).resolve()
    entries = load_source_entries(Path(manifest_path), project_root=root)
    workspace_summary = workspace_index.build_index(
        root / "vector" / "indexes" / "workspace-index.sqlite",
        [entry.workspace_spec() for entry in entries],
    )
    vector_summary = vector_memory_index.build_index(
        root / "vector" / "indexes" / "vector-memory.sqlite",
        [entry.memory_spec() for entry in entries],
    )
    return {
        "status": "ok",
        "schema": "retrieval-refresh.v1",
        "manifest": str(Path(manifest_path).resolve()),
        "source_count": len(entries),
        "workspace_index": _summary_fields(workspace_summary),
        "vector_index": _summary_fields(vector_summary),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    arguments = parser.parse_args()
    try:
        summary = refresh_indexes(manifest_path=arguments.manifest)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
