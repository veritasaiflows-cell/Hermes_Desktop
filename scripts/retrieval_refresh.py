#!/usr/bin/env python3
"""Refresh exact and semantic retrieval indexes from one approved source manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import vector_memory_index, workspace_index

DEFAULT_MANIFEST = PROJECT_ROOT / "vector" / "retrieval-sources.json"
FORBIDDEN_PARTS = {"tmp", "secrets", "credentials", "configuration", "config", ".git"}


def load_sources(manifest_path: Path, *, project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Load existing, workspace-relative approved files from a v1 manifest."""
    root = Path(project_root).resolve()
    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema") != "retrieval-sources.v1":
        raise ValueError("Unsupported retrieval source manifest schema")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Retrieval source manifest requires a non-empty sources list")

    sources: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_sources:
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
        if resolved not in seen:
            seen.add(resolved)
            sources.append(resolved)
    return sources


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
    sources = load_sources(Path(manifest_path), project_root=root)
    workspace_summary = workspace_index.build_index(
        root / "vector" / "indexes" / "workspace-index.sqlite",
        [
            workspace_index.SourceSpec(
                source,
                profile="explicit-local-source",
                authority_class="source",
            )
            for source in sources
        ],
    )
    vector_summary = vector_memory_index.build_index(
        root / "vector" / "indexes" / "vector-memory.sqlite",
        [
            vector_memory_index.SourceSpec(
                source,
                profile="explicit-local-memory",
                authority_class="semantic_memory",
            )
            for source in sources
        ],
    )
    return {
        "status": "ok",
        "schema": "retrieval-refresh.v1",
        "manifest": str(Path(manifest_path).resolve()),
        "source_count": len(sources),
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
