#!/usr/bin/env python3
"""Deterministic freshness gate for the disposable Graphify artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "graphify-freshness.v1"
BASELINE_SCHEMA = "graphify-freshness-baseline.v1"
GRAPH_DIR_NAME = "graphify-out"
BASELINE_NAME = "freshness-baseline.json"
CODE_ROOTS = ("canonical", "scripts", "tests")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _source_path(project_root: Path, relative: str) -> Path:
    root = project_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Graphify source path is outside workspace: {relative}") from exc
    return candidate


def _code_sources(project_root: Path) -> list[str]:
    project_root = project_root.resolve()
    sources: set[str] = set()
    for name in CODE_ROOTS:
        root = project_root / name
        if _is_link_or_reparse(root):
            raise ValueError(f"Graphify source root {name} is linked or reparse-pointed")
        if not root.is_dir():
            raise ValueError(f"Graphify required source root is missing: {name}")
        _validate_artifact_path(
            project_root,
            root,
            label=f"Graphify source root {name}",
        )
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            _validate_artifact_path(
                project_root,
                current_path,
                label="Graphify source directory",
            )
            for directory in list(directories):
                child = current_path / directory
                if directory == "__pycache__":
                    directories.remove(directory)
                    continue
                _validate_artifact_path(
                    project_root,
                    child,
                    label="Graphify source directory",
                )
            for filename in filenames:
                path = current_path / filename
                if path.suffix.lower() == ".py":
                    validated = _validate_artifact_path(
                        project_root,
                        path,
                        label="Graphify source file",
                    )
                    sources.add(_relative(validated, project_root))
    return sorted(sources)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _validate_artifact_path(
    project_root: Path,
    path: Path,
    *,
    label: str,
    allow_missing: bool = False,
) -> Path:
    root = project_root.resolve()
    if _is_link_or_reparse(path):
        raise ValueError(f"{label} is linked or reparse-pointed")
    if not path.exists() and not allow_missing:
        raise FileNotFoundError(path)
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside workspace") from exc
    return resolved


def _validate_graph(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes")
    links = graph.get("links")
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise ValueError("graph nodes must be a list of objects")
    if not isinstance(links, list) or not all(isinstance(link, dict) for link in links):
        raise ValueError("graph links must be a list of objects")


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if not all(isinstance(path, str) and isinstance(metadata, dict) for path, metadata in manifest.items()):
        raise ValueError("manifest must map source paths to metadata objects")


def _validate_baseline(baseline: dict[str, Any]) -> None:
    if baseline.get("schema") != BASELINE_SCHEMA:
        raise ValueError("baseline schema is invalid")
    if not isinstance(baseline.get("graph_sha256"), str):
        raise ValueError("baseline graph hash is invalid")
    if not isinstance(baseline.get("manifest_sha256"), str):
        raise ValueError("baseline manifest hash is invalid")
    source_hashes = baseline.get("source_hashes")
    if not isinstance(source_hashes, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in source_hashes.items()
    ):
        raise ValueError("baseline source hashes are invalid")


def _graph_sources(graph: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    for node in graph.get("nodes", []):
        source = node.get("source_file")
        if isinstance(source, str) and source:
            sources.add(source.replace("\\", "/"))
    return sources


def _artifact_paths(project_root: Path) -> tuple[Path, Path, Path]:
    root = project_root.resolve()
    graph_dir = _validate_artifact_path(
        root,
        root / GRAPH_DIR_NAME,
        label="Graphify artifact directory",
    )
    graph_path = _validate_artifact_path(
        root,
        graph_dir / "graph.json",
        label="Graphify graph",
    )
    manifest_path = _validate_artifact_path(
        root,
        graph_dir / "manifest.json",
        label="Graphify manifest",
    )
    baseline_path = _validate_artifact_path(
        root,
        graph_dir / BASELINE_NAME,
        label="Graphify freshness baseline",
        allow_missing=True,
    )
    return graph_path, manifest_path, baseline_path


def write_baseline(project_root: Path) -> dict[str, Any]:
    """Record content hashes for the sources represented by the current graph."""
    project_root = Path(project_root).resolve()
    graph_path, manifest_path, baseline_path = _artifact_paths(project_root)
    graph = _load_json_object(graph_path, "graph")
    manifest = _load_json_object(manifest_path, "manifest")
    _validate_graph(graph)
    _validate_manifest(manifest)
    code_sources = _code_sources(project_root)
    missing_code_sources = sorted(set(code_sources) - _graph_sources(graph))
    if missing_code_sources:
        raise ValueError(
            "Graphify graph is missing code source coverage: "
            + ", ".join(missing_code_sources)
        )
    source_paths = sorted(set(manifest) | set(code_sources))
    source_hashes = {
        relative: _sha256(_source_path(project_root, relative))
        for relative in source_paths
        if _source_path(project_root, relative).is_file()
    }
    baseline = {
        "schema": BASELINE_SCHEMA,
        "generated_at": _utc_now(),
        "built_at_commit": graph.get("built_at_commit"),
        "graph_sha256": _sha256(graph_path),
        "manifest_sha256": _sha256(manifest_path),
        "source_hashes": source_hashes,
        "code_sources": code_sources,
    }
    serialized = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(baseline_path.parent),
            prefix=f".{BASELINE_NAME}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, baseline_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return {
        "schema": SCHEMA,
        "status": "baseline_written",
        "baseline_path": str(baseline_path),
        "source_count": len(source_hashes),
    }


def check_freshness(project_root: Path) -> dict[str, Any]:
    """Return the current Graphify freshness state without mutating the workspace."""
    project_root = Path(project_root).resolve()
    graph_path, manifest_path, baseline_path = _artifact_paths(project_root)
    graph = _load_json_object(graph_path, "graph")
    manifest = _load_json_object(manifest_path, "manifest")
    baseline = _load_json_object(baseline_path, "baseline")
    _validate_graph(graph)
    _validate_manifest(manifest)
    _validate_baseline(baseline)
    issues: list[dict[str, Any]] = []
    if _sha256(graph_path) != baseline.get("graph_sha256"):
        issues.append({"code": "graph_artifact_changed", "path": _relative(graph_path, project_root)})
    if _sha256(manifest_path) != baseline.get("manifest_sha256"):
        issues.append({"code": "manifest_changed", "path": _relative(manifest_path, project_root)})
    for relative, expected_hash in baseline.get("source_hashes", {}).items():
        source_path = _source_path(project_root, relative)
        if not source_path.is_file():
            issues.append({"code": "source_deleted", "path": relative})
        elif _sha256(source_path) != expected_hash:
            issues.append({"code": "source_changed", "path": relative})
    represented_sources = _graph_sources(graph)
    for relative in _code_sources(project_root):
        if relative not in represented_sources:
            issues.append({"code": "source_missing_from_graph", "path": relative})
    needs_update = project_root / GRAPH_DIR_NAME / "needs_update"
    if needs_update.exists():
        issues.append(
            {
                "code": "semantic_update_pending",
                "path": _relative(needs_update, project_root),
            }
        )
    return {
        "schema": SCHEMA,
        "generated_at": _utc_now(),
        "status": "stale" if issues else "fresh",
        "issues": issues,
        "graph_path": str(graph_path),
        "baseline_path": str(baseline_path),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="workspace root (default: repository containing this script)",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record the current refreshed graph and source hashes",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = (
            write_baseline(args.project_root)
            if args.write_baseline
            else check_freshness(args.project_root)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema": SCHEMA,
            "generated_at": _utc_now(),
            "status": "unavailable",
            "issues": [
                {
                    "code": "artifact_unavailable",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] in {"fresh", "baseline_written"}:
        return 0
    if report["status"] == "stale":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
