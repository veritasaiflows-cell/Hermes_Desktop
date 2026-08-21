#!/usr/bin/env python3
"""Workflow ownership routing and control-plane validator.

This module implements a minimal control-plane for workflow routing with
freshness checks.  It supports:

- workflow selector resolution (ID or alias)
- answering selected capsule views (summary/next/blockers/helper/all)
- route-index generation and staleness validation
- generated workflow capsules under ``state/workflows/WF##.json``

The script intentionally keeps generated routing artifacts small and explicit so that
ownership and execution decisions remain auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import warnings
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB, read_only_database_uri

DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_INDEX_PATH = STATE_INDEX_PATH = DEFAULT_STATE_DIR / "workflow-routing-index.json"
DEFAULT_CAPSULE_DIR = DEFAULT_STATE_DIR / "workflows"
DEFAULT_ROUTING_CACHE_TTL_SECONDS = 300
DEFAULT_ROUTING_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"
VECTOR_INDEX_PATH = PROJECT_ROOT / "vector" / "indexes" / "vector-memory.sqlite"
ACTIVE_WORKFLOW_SOURCES = [
    DEFAULT_STATE_DIR / "ACTIVE_WORKFLOWS.md",
    DEFAULT_STATE_DIR / "active_workflows.json",
]
ALIAS_SOURCES = [
    DEFAULT_STATE_DIR / "WORKFLOW_ALIAS_INDEX.md",
    DEFAULT_STATE_DIR / "workflow_alias_index.json",
]
OVERRIDE_SOURCE = DEFAULT_STATE_DIR / "workflow-control-overrides.json"
WORKFLOW_BASENAME = "state/workflows/{workflow_id}.json"
CONTINUITY_DIR = PROJECT_ROOT / "continuity"

ROUTER_SCHEMA = "workflow-routing-index.v1"
CAPSULE_SCHEMA = "workflow_capsule.v1"
ROUTER_REFRESH_COMMAND = (
    "python scripts/workflow_router.py --all --answer summary --validate --write-index"
)


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    sha256: str
    size: int
    modified_at: str
    exists: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_workflow_id(value: str) -> str:
    return value.strip().upper()


def _json_from_markdown(text: str) -> dict[str, Any]:
    json_block = re.search(r"```\s*json\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if json_block:
        text = json_block.group(1)
    text = text.strip()
    if not text:
        raise ValueError("No JSON payload in workflow control markdown")
    return json.loads(text)


def _load_json_surface(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing workflow control file: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return _json_from_markdown(text)


def _first_existing(paths: Iterable[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError(
        "No source file found. Checked: "
        + ", ".join(str(path) for path in paths)
    )


def _source_fingerprint(path: Path, *, use_lightweight: bool = False) -> SourceFingerprint:
    if not path.exists():
        return SourceFingerprint(
            path=str(path.as_posix()),
            sha256="",
            size=-1,
            modified_at="",
            exists=False,
        )
    stat = path.stat()
    if use_lightweight:
        sha_value = f"L:{stat.st_size}:{int(stat.st_mtime)}"
    else:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        sha_value = digest.hexdigest()
    return SourceFingerprint(
        path=str(path.as_posix()),
        sha256=sha_value,
        size=stat.st_size,
        modified_at=_utc_datetime_from_timestamp(stat.st_mtime).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        exists=True,
    )


def _utc_datetime_from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _load_active_workflow_entries(
    *, state_dir: Path = DEFAULT_STATE_DIR,
) -> tuple[dict[str, Path], dict[str, Any]]:
    active_path = _first_existing(
        [
            state_dir / "active_workflows.json",
            state_dir / "ACTIVE_WORKFLOWS.md",
        ]
    )
    payload = _load_json_surface(active_path)
    if isinstance(payload, list):
        entries = payload
        top_level = {}
    elif isinstance(payload, dict):
        entries = payload.get("workflows", [])
        top_level = payload
    else:
        raise ValueError(f"Unsupported workflow payload type in {active_path}")

    # Schema-version guard: if the active queue declares a routing schema
    # version, it must match the router's expected version. This prevents
    # silent mis-routing when the routing contract changes.
    declared_routing_version = top_level.get("routing_schema_version")
    if declared_routing_version is not None and declared_routing_version != ROUTER_SCHEMA:
        raise ValueError(
            f"Active workflow queue declares routing_schema_version={declared_routing_version!r}, "
            f"but router expects {ROUTER_SCHEMA!r}. Regenerate the queue before routing."
        )

    entries_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid workflow entry type in {active_path}")
        if "workflow_id" not in entry or "display_name" not in entry:
            raise ValueError(f"Invalid workflow entry missing required fields in {active_path}: {entry}")
        workflow_id = _normalize_workflow_id(entry["workflow_id"])
        if workflow_id in entries_by_id:
            raise ValueError(f"Duplicate workflow id in active queue: {workflow_id}")
        entries_by_id[workflow_id] = entry
    if not entries_by_id:
        raise ValueError(f"No workflows available in {active_path}")

    source_meta = {
        "source": str(active_path.as_posix()),
        "schema": _infer_schema(top_level if isinstance(payload, dict) else payload),
        "routing_schema_version": declared_routing_version if declared_routing_version is not None else ROUTER_SCHEMA,
    }
    return entries_by_id, source_meta


def _infer_schema(payload: dict[str, Any] | list[Any]) -> str:
    if isinstance(payload, dict):
        return payload.get("schema", "workflow-queue.v1")
    return "workflow-queue.v1"


def _load_alias_index(state_dir: Path = DEFAULT_STATE_DIR) -> tuple[dict[str, str], dict[str, Any]]:
    alias_path = _first_existing(
        [
            state_dir / "workflow_alias_index.json",
            state_dir / "WORKFLOW_ALIAS_INDEX.md",
        ]
    )
    payload = _load_json_surface(alias_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Alias index must be an object in {alias_path}")
    aliases = payload.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError(f"Alias index in {alias_path} missing a dict under 'aliases'")

    normalized_aliases = {
        _normalize_alias(alias): _normalize_workflow_id(target)
        for alias, target in aliases.items()
    }
    return normalized_aliases, {
        "source": str(alias_path.as_posix()),
        "schema": payload.get("schema", "workflow-alias-index.v1"),
    }


def _load_overrides(
    state_dir: Path = DEFAULT_STATE_DIR,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    path = state_dir / "workflow-control-overrides.json"
    if not path.exists():
        return {}, {
            "source": str(path.as_posix()),
            "schema": "workflow-control-overrides.v1",
            "count": 0,
        }

    payload = _load_json_surface(path)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError(f"Override file must be a list in {path}")

    overrides: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict) or "workflow_id" not in item:
            raise ValueError(f"Invalid override entry in {path}: {item}")
        workflow_id = _normalize_workflow_id(item["workflow_id"])
        if workflow_id in overrides:
            raise ValueError(f"Duplicate override for {workflow_id} in {path}")
        overrides[workflow_id] = dict(item)

    return overrides, {
        "source": str(path.as_posix()),
        "schema": "workflow-control-overrides.v1",
        "count": len(overrides),
    }


def _resolve_selector(selector: str, entries: dict[str, dict[str, Any]], aliases: dict[str, str]) -> str:
    if not selector:
        raise ValueError("Workflow selector cannot be empty")
    normalized_selector = _normalize_alias(selector)
    normalized_id = _normalize_workflow_id(selector)

    if normalized_id in entries:
        return normalized_id

    alias_id = aliases.get(normalized_selector)
    if alias_id:
        return alias_id

    fallback = aliases.get(normalized_selector.replace("-", " "))
    if fallback:
        return fallback

    # Accept a normalized alias token if only whitespace and case differ.
    for alias_key, workflow_id in aliases.items():
        if alias_key == normalized_selector:
            return workflow_id

    matches = [
        workflow_id for workflow_id in entries if normalized_selector in workflow_id.lower()
    ]
    if len(matches) == 1:
        return matches[0]

    if matches:
        raise ValueError(f"Selector {selector!r} is ambiguous: {', '.join(sorted(matches))}")
    raise KeyError(f"Unknown workflow selector: {selector}")


def _normalize_dependency_list(value: Any, *, owner: str | None = None) -> list[str]:
    """Return a deterministic dependency list from user-facing workflow metadata."""

    if value is None:
        return []

    if isinstance(value, str):
        raw_values = [item.strip() for item in re.split(r"[;,\n]+", value)]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raise ValueError(f"Depends-on value for {owner or '(unknown)'} must be a list or string")

    normalized: list[str] = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError(f"Depends-on entry for {owner or '(unknown)'} must be text")
        normalized_id = _normalize_workflow_id(item)
        if not normalized_id:
            continue
        if normalized_id not in normalized:
            normalized.append(normalized_id)
    return normalized


def _dependency_graph(entries: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Build a stable dependency graph from active workflow entries."""

    return {
        workflow_id: _normalize_dependency_list(entry.get("depends_on"), owner=workflow_id)
        for workflow_id, entry in sorted(entries.items(), key=lambda item: item[0])
    }


def _dependency_chain_from_graph(
    db: CanonicalDB,
    workflow_id: str,
    *,
    max_depth: int = 8,
) -> tuple[list[str], list[str]]:
    """Return the transitive dependency chain from the durable graph.

    Uses active `workflows -> depends_on -> workflows` edges. Returns the chain
    in dependency-first order and any cycle/blocker messages encountered.
    """
    chain: list[str] = []
    issues: list[str] = []
    seen: set[str] = set()
    frontier: set[str] = {workflow_id}
    depth = 0

    while frontier and depth < max_depth:
        depth += 1
        next_frontier: set[str] = set()
        for node in frontier:
            for edge in db.list_relationships(
                subject_type="workflows",
                subject_id=node,
                predicate="depends_on",
                object_type="workflows",
                status="active",
            ):
                target = edge["object_id"]
                if target == workflow_id:
                    issues.append(
                        f"Graph dependency cycle detected: {workflow_id} depends on itself via {node}."
                    )
                    continue
                if target not in seen:
                    seen.add(target)
                    chain.append(target)
                    next_frontier.add(target)
        frontier = next_frontier

    if frontier:
        issues.append(
            f"Dependency chain for {workflow_id} exceeds max depth ({max_depth}); "
            "graph may contain a cycle or very deep DAG."
        )

    return chain, issues


def _dependency_entry_status_blockers(
    dependency_id: str,
    dependency_entry: dict[str, Any] | None,
) -> list[str]:
    """Return blocker strings for a single dependency entry."""
    blockers: list[str] = []
    if dependency_entry is None:
        blockers.append(f"Dependency {dependency_id} exists but has no workflow entry.")
        return blockers
    dependency_status = (
        dependency_entry.get("effective_status")
        or dependency_entry.get("lifecycle")
        or "route_only"
    )
    if dependency_status != "active":
        blockers.append(
            f"Dependency {dependency_id} is not active for downstream execution "
            f"(status={dependency_status!r})."
        )
    if dependency_entry.get("owner_action_required"):
        blockers.append(
            f"Dependency {dependency_id} requires owner action before downstream execution."
        )
    if dependency_entry.get("blockers"):
        blockers.append(
            f"Dependency {dependency_id} reports blockers: "
            + ", ".join(str(item) for item in dependency_entry.get("blockers", []))
        )
    if dependency_entry.get("stop_lines"):
        blockers.append(f"Dependency {dependency_id} has active stop-lines.")
    return blockers


def _graph_primary_dependency_analysis(
    db: CanonicalDB | None,
    workflow_id: str,
    entry: dict[str, Any],
    all_entries: dict[str, dict[str, Any]],
    *,
    max_depth: int = 8,
) -> tuple[list[str], list[str], list[str]]:
    """Graph-primary dependency analysis.

    Returns:
        - blockers: actionable dependency-derived blockers (downgrades status)
        - chain: graph-derived transitive dependency chain
        - graph_blockers: graph/markdown consistency issues (also blockers)
    """
    markdown_blockers, markdown_chain = _dependency_blockers(
        workflow_id, entry, all_entries
    )
    if db is None:
        # Fallback to markdown-only when no graph database is available.
        return markdown_blockers, markdown_chain, []

    graph_chain, graph_issues = _dependency_chain_from_graph(
        db, workflow_id, max_depth=max_depth
    )

    declared = set(
        _normalize_dependency_list(entry.get("depends_on"), owner=workflow_id)
    )
    graph_direct = {
        edge["object_id"]
        for edge in db.list_relationships(
            subject_type="workflows",
            subject_id=workflow_id,
            predicate="depends_on",
            object_type="workflows",
            status="active",
        )
    }

    consistency_issues: list[str] = []
    for dependency in declared:
        if dependency not in graph_direct:
            consistency_issues.append(
                f"Dependency {dependency} is declared but not mirrored in the graph "
                "(run `python scripts/graph_backfill.py`)."
            )
    for dependency in graph_direct:
        if dependency not in declared:
            consistency_issues.append(
                f"Dependency {dependency} is in the graph but not declared in ACTIVE_WORKFLOWS.md."
            )

    transitive_from_markdown = set(markdown_chain)
    transitive_from_markdown = {
        wid for wid in transitive_from_markdown if wid in all_entries
    }
    graph_transitive = set(graph_chain)

    if graph_transitive != transitive_from_markdown:
        only_in_graph = sorted(graph_transitive - transitive_from_markdown)
        only_in_markdown = sorted(transitive_from_markdown - graph_transitive)
        if only_in_graph:
            consistency_issues.append(
                f"Graph transitive closure includes dependencies not in markdown: "
                f"{', '.join(only_in_graph)}."
            )
        if only_in_markdown:
            consistency_issues.append(
                f"Markdown transitive closure includes dependencies not in graph: "
                f"{', '.join(only_in_markdown)}."
            )

    blockers: list[str] = [*graph_issues, *consistency_issues]
    for dependency in graph_chain:
        blockers.extend(
            _dependency_entry_status_blockers(
                dependency, all_entries.get(dependency)
            )
        )

    return blockers, graph_chain, consistency_issues


def _dependency_blockers(
    workflow_id: str,
    entry: dict[str, Any],
    all_entries: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Markdown-only dependency analysis (fallback when graph is unavailable)."""

    direct_dependencies = _normalize_dependency_list(entry.get("depends_on"), owner=workflow_id)
    blockers: list[str] = []
    flattened: list[str] = []
    seen: set[str] = set()

    def walk(current_id: str, stack: tuple[str, ...]) -> None:
        if current_id in stack:
            blockers.append(
                f"Circular dependency detected in chain: {' -> '.join(stack + (current_id,))}"
            )
            return

        dependency_entry = all_entries.get(current_id)
        if dependency_entry is None:
            blockers.append(f"Missing dependency workflow: {current_id}")
            if current_id not in seen:
                seen.add(current_id)
                flattened.append(current_id)
            return

        if current_id not in seen:
            seen.add(current_id)
            flattened.append(current_id)

        blockers.extend(
            _dependency_entry_status_blockers(current_id, dependency_entry)
        )

        for child_id in _normalize_dependency_list(
            dependency_entry.get("depends_on"),
            owner=current_id,
        ):
            if child_id == workflow_id:
                blockers.append(
                    f"Dependency cycle detected: {workflow_id} is indirectly dependent on itself."
                )
                continue
            walk(child_id, stack + (current_id,))

    for dependency in direct_dependencies:
        walk(dependency, (workflow_id,))

    return blockers, flattened





def _apply_override(entry: dict[str, Any], override: dict[str, Any] | None) -> tuple[str, list[str]]:
    effective_status = entry.get("effective_status") or entry.get("lifecycle") or "route_only"
    blockers = list(entry.get("blockers", []))
    stop_lines = list(entry.get("stop_lines", []))
    control_override = None
    if override:
        override_status = override.get("status")
        if override_status:
            effective_status = override_status
        control_override = {
            "status": override_status,
            "set_by": override.get("set_by"),
            "set_at": override.get("set_at"),
            "reason": override.get("reason"),
            "resume_condition": override.get("resume_condition"),
        }
        if "blockers" in override and override["blockers"]:
            blockers.extend(override["blockers"])
        if "replacement_next_action" in override and override.get("replacement_next_action"):
            replacement = override["replacement_next_action"]
            if replacement and isinstance(replacement, str):
                blockers.append(replacement)
    return effective_status, blockers, stop_lines, control_override


def _build_capsule(
    entry: dict[str, Any],
    *,
    all_entries: dict[str, dict[str, Any]],
    override: dict[str, Any] | None,
    project_root: Path = PROJECT_ROOT,
    active_source: dict[str, Any],
    alias_source: dict[str, Any],
    override_source: dict[str, Any],
    routing_database_path: Path = DEFAULT_ROUTING_DATABASE,
    vector_index_path: Path | None = VECTOR_INDEX_PATH,
) -> dict[str, Any]:
    workflow_id = _normalize_workflow_id(entry["workflow_id"])
    continuity_note = entry.get("continuity_note") or entry.get("proof_artifact")
    if continuity_note:
        continuity_path = str(Path(continuity_note))
    else:
        continuity_path = ""

    if routing_database_path.exists():
        try:
            with CanonicalDB(routing_database_path, read_only=True) as db:
                (
                    dependency_blockers,
                    dependency_chain,
                    graph_dependency_blockers,
                ) = _graph_primary_dependency_analysis(
                    db, workflow_id, entry, all_entries
                )
        except Exception:
            warnings.warn(
                f"Graph-primary dependency analysis failed for {workflow_id}; "
                "falling back to markdown. Run `python scripts/graph_memory.py validate` "
                "and `python scripts/graph_backfill.py` to verify the graph layer.",
                RuntimeWarning,
            )
            dependency_blockers, dependency_chain = _dependency_blockers(
                workflow_id, entry, all_entries
            )
            graph_dependency_blockers = []
    else:
        dependency_blockers, dependency_chain = _dependency_blockers(
            workflow_id, entry, all_entries
        )
        graph_dependency_blockers = []

    effective_status, blockers, stop_lines, control_override = _apply_override(entry, override)
    if dependency_blockers and effective_status == "active":
        effective_status = "monitor_only"
    blockers.extend(dependency_blockers)
    for graph_blocker in graph_dependency_blockers:
        if graph_blocker not in blockers:
            blockers.append(graph_blocker)

    blocker_count = len(blockers)

    return {
        "schema": CAPSULE_SCHEMA,
        "workflow_id": workflow_id,
        "display_name": entry.get("display_name", workflow_id),
        "tier": entry.get("tier", "P2"),
        "priority": entry.get("priority", "medium"),
        "lifecycle": entry.get("lifecycle", effective_status),
        "readiness": entry.get("readiness", entry.get("effective_status", effective_status)),
        "effective_status": effective_status,
        "current_state": entry.get("state_description", entry.get("current_state", "unknown")),
        "next_action": entry.get("next_action", "No next action recorded."),
        "authoritative_next_action": entry.get(
            "authoritative_next_action", entry.get("next_action", "No next action recorded.")
        ),
        "implementation_script": entry.get("implementation_script"),
        "commands": entry.get(
            "commands",
            {
                "dry_run": None,
                "write": None,
            },
        ),
        "helper_safe": bool(entry.get("helper_safe", False)),
        "owner_action_required": bool(entry.get("owner_action_required", False)),
        "authority_boundary": entry.get("authority_boundary", "review_only"),
        "authority_class": entry.get("authority_class", "review_only"),
        "primary_owner_lane": entry.get("primary_owner_lane", "owner-main"),
        "secondary_consumers": list(entry.get("secondary_consumers", [])),
        "human_approval_owner": entry.get("human_approval_owner"),
        "proof_artifact": continuity_path,
        "freshness_sla": entry.get("freshness_sla", "daily"),
        "blockers": blockers,
        "stop_lines": stop_lines,
        "continuity_note": continuity_path,
        "depends_on": dependency_chain,
        "dependency_blockers": dependency_blockers,
        "graph_dependency_blockers": graph_dependency_blockers,
        "recall_context": _recall_context_for_workflow(
            workflow_id,
            dependency_chain,
            entry,
            vector_index_path=vector_index_path,
        ),
        "primary_route_artifact": f"state/workflows/{workflow_id}.json",
        "secondary_artifacts": [
            active_source["source"],
            alias_source["source"],
            override_source["source"],
        ],
        "validator_commands": entry.get(
            "validator_commands",
            [
                "python -m unittest discover -s tests -v",
                "python scripts/run_checks.py --skip-smoke",
            ],
        ),
        "default_resume_command": entry.get(
            "default_resume_command",
            f"python scripts/workflow_router.py {workflow_id} --answer next --validate",
        ),
        "routing_contract": ROUTER_SCHEMA,
        "routing_schema_version": ROUTER_SCHEMA,
        "routing_source_contracts": {
            "active_workflows": active_source["schema"],
            "alias_source": alias_source["schema"],
            "overrides": override_source["schema"],
        },
        "required_refresh_command": ROUTER_REFRESH_COMMAND,
        "control_override": control_override,
        "state_sources": [
            active_source["source"],
            alias_source["source"],
            override_source["source"],
            continuity_path,
        ],
        "blocker_count": blocker_count,
        "generated_at": _utc_now(),
    }


def _recall_context_for_workflow(
    workflow_id: str,
    dependency_chain: list[str],
    entry: dict[str, Any],
    *,
    vector_index_path: Path | None = VECTOR_INDEX_PATH,
    limit: int = 3,
) -> dict[str, Any]:
    """Build a vector-memory recall context for a workflow and its dependencies.

    Queries the local vector index for the workflow ID, its dependency IDs, and
    the workflow's current_state text. Returns a structured packet with the top
    results so the router can surface relevant notes alongside the capsule.
    """
    if vector_index_path is None:
        return {
            "available": False,
            "reason": "recall context disabled",
            "index_path": "",
            "results": [],
        }
    if not vector_index_path.is_file():
        return {
            "available": False,
            "reason": "vector index not found",
            "index_path": str(vector_index_path),
            "results": [],
        }

    terms: list[str] = [workflow_id]
    terms.extend(dependency_chain)
    state_description = entry.get("state_description", "")
    current_state = entry.get("current_state", "")
    if state_description:
        terms.append(state_description[:120])
    elif current_state:
        terms.append(current_state[:120])

    query = " ".join(terms).strip()
    if not query:
        return {
            "available": True,
            "index_path": str(vector_index_path),
            "reason": "no recall terms",
            "results": [],
        }

    try:
        from scripts import vector_memory_index

        packet = vector_memory_index.build_query_packet(
            vector_index_path,
            query,
            limit=limit,
            retrieval_mode="hybrid",
        )
        return {
            "available": True,
            "index_path": str(vector_index_path),
            "result_count": packet["result_count"],
            "results": [
                {
                    "source_path": result["source_path"],
                    "citation": result["citation"],
                    "score": result["score"],
                    "excerpt": result["excerpt"],
                    "freshness_state": result["freshness_state"],
                    "retrieval_mode": result["retrieval_mode"],
                }
                for result in packet["results"]
            ],
        }
    except Exception as exc:
        return {
            "available": False,
            "index_path": str(vector_index_path),
            "reason": str(exc),
            "results": [],
        }


def _fingerprint_sources(
    entries: dict[str, dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
    alias_source_path: Path,
    active_source_path: Path,
    override_source_path: Path,
    project_root: Path,
    vector_index_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    source_paths = {
        active_source_path,
        alias_source_path,
        override_source_path,
    }
    for entry in entries.values():
        for candidate in (entry.get("continuity_note"), entry.get("proof_artifact")):
            if not candidate:
                continue
            source_paths.add(project_root / candidate)
    if vector_index_path is not None:
        source_paths.add(vector_index_path)

    fingerprints: dict[str, dict[str, Any]] = {}
    for path in sorted(source_paths, key=lambda item: item.as_posix()):
        if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            fingerprint = _source_fingerprint(path, use_lightweight=True)
        else:
            fingerprint = _source_fingerprint(path)
        fingerprints[fingerprint.path] = {
            "sha256": fingerprint.sha256,
            "size": fingerprint.size,
            "modified_at": fingerprint.modified_at,
            "exists": fingerprint.exists,
        }

    # Keep override presence visible in index for stale diagnostics.
    for workflow_id, override in overrides.items():
        if "set_at" in override:
            continue
        # no-op; consume the map so stale-check metadata remains stable for tests.
        _ = workflow_id

    return fingerprints


def build_routing_index(
    *,
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = DEFAULT_STATE_DIR,
    index_path: Path = DEFAULT_INDEX_PATH,
    routing_database_path: Path | None = None,
    vector_index_path: Path | None = VECTOR_INDEX_PATH,
) -> dict[str, Any]:
    routing_database_path = routing_database_path or project_root / "canonical" / "efficiens.db"
    entries, active_source_meta = _load_active_workflow_entries(state_dir=state_dir)
    aliases, alias_source_meta = _load_alias_index(state_dir=state_dir)
    overrides, override_source_meta = _load_overrides(state_dir=state_dir)
    active_source_path = Path(active_source_meta["source"])
    alias_source_path = Path(alias_source_meta["source"])
    override_source_path = Path(override_source_meta["source"])

    fingerprints = _fingerprint_sources(
        entries=entries,
        overrides=overrides,
        alias_source_path=alias_source_path,
        active_source_path=active_source_path,
        override_source_path=override_source_path,
        project_root=project_root,
        vector_index_path=vector_index_path or VECTOR_INDEX_PATH,
    )

    capsule_payloads = [
        _build_capsule(
            entry,
            all_entries=entries,
            override=overrides.get(workflow_id),
            project_root=project_root,
            active_source=active_source_meta,
            alias_source=alias_source_meta,
            override_source=override_source_meta,
            routing_database_path=routing_database_path,
            vector_index_path=vector_index_path,
        )
        for workflow_id, entry in entries.items()
    ]

    index_payload = {
        "schema": ROUTER_SCHEMA,
        "routing_schema_version": ROUTER_SCHEMA,
        "generated_at": _utc_now(),
        "generated_by": "scripts/workflow_router.py",
        "source_schema": {
            "active_workflows": active_source_meta["schema"],
            "alias_index": alias_source_meta["schema"],
            "overrides": override_source_meta["schema"],
        },
        "source_signatures": fingerprints,
        "workflow_ids": sorted(entries),
        "alias_count": len(aliases),
        "active_workflow_source": active_source_meta["source"],
        "alias_source": alias_source_meta["source"],
        "override_source": override_source_meta["source"],
        "dependency_graph": _dependency_graph_from_database(entries, routing_database_path),
        "workflows": [
            {
                "workflow_id": payload["workflow_id"],
                "effective_status": payload["effective_status"],
                "blocker_count": payload["blocker_count"],
                "proof_artifact": payload["proof_artifact"],
            }
            for payload in capsule_payloads
        ],
    }

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_payload, indent=2, sort_keys=True), encoding="utf-8")
    return index_payload


def _build_stale_result(
    index_payload: dict[str, Any] | None,
    *,
    index_path: Path,
    changed: list[dict[str, Any]],
    message: str,
    current_source_contracts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_schema = {
        "active_workflows": None,
        "alias_index": None,
        "overrides": None,
    }
    if current_source_contracts:
        source_schema.update(current_source_contracts)
    if index_payload and isinstance(index_payload.get("source_schema"), dict):
        fallback = index_payload.get("source_schema")
        for key, value in fallback.items():
            source_schema.setdefault(key if key != "alias_source" else "alias_index", value)

    return {
        "routing_index_stale": True,
        "unsafe_to_trust": True,
        "required_refresh_command": ROUTER_REFRESH_COMMAND,
        "routing_index_schema": ROUTER_SCHEMA,
        "routing_source_schema": {
            "active_workflows": source_schema.get("active_workflows"),
            "alias_index": source_schema.get("alias_index"),
            "overrides": source_schema.get("overrides"),
        },
        "message": message,
        "changed_sources": changed,
        "index_path": str(index_path.as_posix()),
        "previous_index_version": None
        if index_payload is None
        else index_payload.get("generated_at"),
    }


def check_routing_freshness(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    index_path: Path = DEFAULT_INDEX_PATH,
    project_root: Path = PROJECT_ROOT,
    routing_database_path: Path | None = None,
    vector_index_path: Path | None = VECTOR_INDEX_PATH,
) -> tuple[bool, dict[str, Any]]:
    routing_database_path = routing_database_path or project_root / "canonical" / "efficiens.db"
    current_source_contracts = {
        "active_workflows": None,
        "alias_index": None,
        "overrides": None,
    }

    try:
        index_payload = _load_json_surface(index_path)
        if not isinstance(index_payload, dict):
            raise ValueError("Index payload is not an object")
    except Exception as exc:
        return True, _build_stale_result(
            None,
            index_path=index_path,
            changed=[{"path": str(index_path.as_posix()), "reason": str(exc)}],
            message="Route index missing or unreadable.",
            current_source_contracts=current_source_contracts,
        )

    try:
        active_source = Path(index_payload["active_workflow_source"])
        alias_source = Path(index_payload["alias_source"])
        override_source = Path(index_payload["override_source"])
    except Exception as exc:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[{"path": str(index_path.as_posix()), "reason": f"Missing source metadata: {exc}"}],
            message="Route index is missing required source metadata.",
            current_source_contracts=current_source_contracts,
        )

    entries, active_source_meta = _load_active_workflow_entries(state_dir=state_dir)
    aliases, alias_source_meta = _load_alias_index(state_dir=state_dir)
    overrides, override_source_meta = _load_overrides(state_dir=state_dir)
    current_source_contracts = {
        "active_workflows": active_source_meta["schema"],
        "alias_index": alias_source_meta["schema"],
        "overrides": override_source_meta["schema"],
    }

    if index_payload.get("schema") != ROUTER_SCHEMA:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": "schema",
                    "previous": index_payload.get("schema"),
                    "current": ROUTER_SCHEMA,
                }
            ],
            message="Routing index schema changed since generation.",
            current_source_contracts=current_source_contracts,
        )

    expected_source_schema = index_payload.get("source_schema", {})
    if expected_source_schema.get("active_workflows") != current_source_contracts["active_workflows"]:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": "source_schema",
                    "previous": expected_source_schema.get("active_workflows"),
                    "current": current_source_contracts["active_workflows"],
                }
            ],
            message="Active workflow source schema changed since index creation.",
            current_source_contracts=current_source_contracts,
        )

    if expected_source_schema.get("alias_index") != current_source_contracts["alias_index"]:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": "source_schema",
                    "previous": expected_source_schema.get("alias_index"),
                    "current": current_source_contracts["alias_index"],
                }
            ],
            message="Alias source schema changed since index creation.",
            current_source_contracts=current_source_contracts,
        )

    if expected_source_schema.get("overrides") != current_source_contracts["overrides"]:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": "source_schema",
                    "previous": expected_source_schema.get("overrides"),
                    "current": current_source_contracts["overrides"],
                }
            ],
            message="Override source schema changed since index creation.",
            current_source_contracts=current_source_contracts,
        )
    expected_signatures = _fingerprint_sources(
        entries=entries,
        overrides=overrides,
        alias_source_path=alias_source,
        active_source_path=active_source,
        override_source_path=override_source,
        project_root=project_root,
        vector_index_path=vector_index_path or VECTOR_INDEX_PATH,
    )

    current_signatures = {
        key: {
            "sha256": value["sha256"],
            "size": value["size"],
            "modified_at": value["modified_at"],
            "exists": value["exists"],
        }
        for key, value in expected_signatures.items()
    }

    index_signatures = index_payload.get("source_signatures", {})
    if not isinstance(index_signatures, dict):
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[{"path": str(index_path.as_posix()), "reason": "source_signatures must be a dict"}],
            message="Route index is malformed.",
        )

    changed: list[dict[str, Any]] = []
    for path, current in current_signatures.items():
        previous = index_signatures.get(path)
        if previous != current:
            changed.append(
                {
                    "path": path,
                    "previous": previous,
                    "current": current,
                }
            )

    for path in set(index_signatures) - set(current_signatures):
        changed.append(
            {
                "path": path,
                "previous": index_signatures[path],
                "current": None,
            }
        )

    if changed:
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=changed,
            message="Route index source signatures do not match current control-plane files.",
            current_source_contracts=current_source_contracts,
        )

    # Keep behavior deterministic when active workflows are aliased but index missing alias.
    if len(aliases) != index_payload.get("alias_count", -1):
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": index_payload["alias_source"],
                    "previous": {"alias_count": index_payload.get("alias_count")},
                    "current": {"alias_count": len(aliases)},
                }
            ],
            message="Alias count changed since index creation.",
            current_source_contracts=current_source_contracts,
        )

    expected_dependency_graph = _dependency_graph_from_database(entries, routing_database_path)
    if expected_dependency_graph != index_payload.get("dependency_graph"):
        return True, _build_stale_result(
            index_payload,
            index_path=index_path,
            changed=[
                {
                    "path": "dependency_graph",
                    "previous": index_payload.get("dependency_graph"),
                    "current": expected_dependency_graph,
                }
            ],
            message="Dependency graph changed since index creation.",
            current_source_contracts=current_source_contracts,
        )

    return False, index_payload


def _dependency_graph_from_database(
    entries: dict[str, dict[str, Any]],
    routing_database_path: Path = DEFAULT_ROUTING_DATABASE,
) -> dict[str, list[str]]:
    """Return the resolved dependency graph, preferring the durable database.

    Reads all active workflow dependency edges in a single query under a single
    connection and groups them by subject_id in Python, avoiding N separate
    list_relationships calls when the workflow queue grows.
    """
    if not routing_database_path.exists():
        return _dependency_graph(entries)
    try:
        uri = read_only_database_uri(routing_database_path)
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            edges = connection.execute(
                "SELECT subject_id, object_id FROM relationships "
                "WHERE subject_type = 'workflows' "
                "AND predicate = 'depends_on' "
                "AND object_type = 'workflows' "
                "AND status = 'active' "
                "AND (valid_until IS NULL OR valid_until >= ?)",
                (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),),
            ).fetchall()
        graph: dict[str, set[str]] = {wid: set() for wid in entries}
        for edge in edges:
            subject = edge["subject_id"]
            if subject in graph:
                graph[subject].add(edge["object_id"])
        return {
            workflow_id: sorted(deps)
            for workflow_id, deps in sorted(graph.items())
        }
    except Exception as exc:
        warnings.warn(
            f"Graph DB dependency lookup failed ({routing_database_path}): {exc}. "
            "Falling back to markdown dependency graph for index freshness.",
            UserWarning,
            stacklevel=2,
        )
        return _dependency_graph(entries)


def _answer_payload(capsule: dict[str, Any], answer: str) -> dict[str, Any]:
    if answer == "summary":
        payload = {
            "workflow_id": capsule["workflow_id"],
            "display_name": capsule["display_name"],
            "lifecycle": capsule["lifecycle"],
            "effective_status": capsule["effective_status"],
            "priority": capsule["priority"],
            "primary_owner_lane": capsule["primary_owner_lane"],
            "helper_safe": capsule["helper_safe"],
            "owner_action_required": capsule["owner_action_required"],
            "authority_class": capsule["authority_class"],
            "current_state": capsule["current_state"],
            "next_action": capsule["next_action"],
            "implementation_script": capsule.get("implementation_script"),
            "commands": capsule.get("commands"),
            "proof_artifact": capsule["proof_artifact"],
            "blocker_count": capsule["blocker_count"],
            "depends_on": capsule.get("depends_on", []),
            "dependency_blockers": capsule.get("dependency_blockers", []),
            "graph_dependency_blockers": capsule.get("graph_dependency_blockers", []),
            "recall_context": capsule.get("recall_context", {"available": False, "results": []}),
            "blockers": capsule["blockers"],
            "default_resume_command": capsule["default_resume_command"],
        }
        return {key: value for key, value in payload.items() if value is not None}
    if answer == "next":
        return {
            "workflow_id": capsule["workflow_id"],
            "effective_status": capsule["effective_status"],
            "next_action": capsule["next_action"],
            "authoritative_next_action": capsule["authoritative_next_action"],
            "implementation_script": capsule.get("implementation_script"),
            "commands": capsule.get("commands"),
            "owner_action_required": capsule["owner_action_required"],
            "human_approval_owner": capsule["human_approval_owner"],
            "control_override": capsule["control_override"],
        }
    if answer == "blockers":
        return {
            "workflow_id": capsule["workflow_id"],
            "effective_status": capsule["effective_status"],
            "blockers": capsule["blockers"],
            "stop_lines": capsule["stop_lines"],
            "control_override": capsule["control_override"],
        }
    if answer == "helper":
        return {
            "workflow_id": capsule["workflow_id"],
            "helper_safe": capsule["helper_safe"],
            "secondary_consumers": capsule["secondary_consumers"],
            "owner_action_required": capsule["owner_action_required"],
            "effective_status": capsule["effective_status"],
        }
    return dict(capsule)


def _authoritative_workflow_payload(
    entry: dict[str, Any],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return queue/override truth without relying on derived routing surfaces."""
    effective_status, blockers, stop_lines, control_override = _apply_override(entry, override)
    next_action = entry.get("next_action") or entry.get("authoritative_next_action")
    if override and override.get("replacement_next_action"):
        next_action = override["replacement_next_action"]
    return {
        "workflow_id": _normalize_workflow_id(entry["workflow_id"]),
        "display_name": entry.get("display_name"),
        "lifecycle": entry.get("lifecycle"),
        "readiness": entry.get("readiness"),
        "effective_status": effective_status,
        "priority": entry.get("priority"),
        "primary_owner_lane": entry.get("primary_owner_lane", "agent-main"),
        "authority_class": entry.get("authority_class", "route_only"),
        "helper_safe": bool(entry.get("helper_safe", False)),
        "owner_action_required": bool(entry.get("owner_action_required", False)),
        "current_state": entry.get("state_description") or entry.get("current_state"),
        "next_action": next_action,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "stop_lines": stop_lines,
        "control_override": control_override,
        "proof_artifact": entry.get("proof_artifact"),
        "implementation_script": entry.get("implementation_script"),
        "commands": entry.get("commands"),
    }


def route_workflows(
    selector: str | None = None,
    *,
    all_workflows: bool = False,
    answer: str = "summary",
    validate: bool = False,
    write_index: bool = False,
    write_capsules: bool = False,
    index_path: Path = DEFAULT_INDEX_PATH,
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = DEFAULT_STATE_DIR,
    routing_cache_ttl_seconds: int = DEFAULT_ROUTING_CACHE_TTL_SECONDS,
    routing_database_path: Path | None = None,
    vector_index_path: Path | None = VECTOR_INDEX_PATH,
) -> dict[str, Any]:
    if (selector is None and not all_workflows) or (selector is not None and all_workflows):
        raise ValueError("Use either selector or --all, not both/none")

    routing_database_path = routing_database_path or project_root / "canonical" / "efficiens.db"

    # Keep derived workflow capsules in sync whenever the routing index is
    # regenerated. Most callers invoke `--write-index` as part of workflow
    # updates, so implicit capsule regeneration avoids stale per-workflow artifacts.
    write_capsules = write_capsules or write_index

    entries, active_source_meta = _load_active_workflow_entries(state_dir=state_dir)
    aliases, alias_source_meta = _load_alias_index(state_dir=state_dir)
    overrides, override_source_meta = _load_overrides(state_dir=state_dir)

    if selector is not None:
        workflow_id = _resolve_selector(selector, entries, aliases)
        workflow_ids = [workflow_id]
    else:
        workflow_ids = sorted(entries)

    if not workflow_ids:
        raise KeyError("No workflows are currently in the active queue")

    cache_key: str | None = None
    cached_result: dict[str, Any] | None = None
    current_signatures: dict[str, Any] | None = None

    if routing_cache_ttl_seconds > 0 and not write_index and vector_index_path is not None:
        cache_key = _routing_cache_key(
            routing_schema_version=ROUTER_SCHEMA,
            selector=selector,
            answer=answer,
        )
        try:
            with CanonicalDB(routing_database_path) as db:
                db.clear_expired_routing_cache()
                cached = db.get_routing_cache(cache_key)
                if cached is not None:
                    current_signatures = _current_source_signatures(
                        entries=entries,
                        overrides=overrides,
                        alias_source_path=Path(alias_source_meta["source"]),
                        active_source_path=Path(active_source_meta["source"]),
                        override_source_path=Path(override_source_meta["source"]),
                        project_root=project_root,
                        routing_database_path=routing_database_path,
                        vector_index_path=vector_index_path,
                    )
                    if cached["source_signatures"] == current_signatures:
                        cached_result = dict(cached["payload"])
                        cached_result["routing_freshness"] = {
                            "status": "cached",
                            "generated_at": cached["generated_at"],
                            "expires_at": cached["expires_at"],
                            "required_refresh_command": ROUTER_REFRESH_COMMAND,
                        }
                    else:
                        cached_result = None
        except Exception:
            # Cache is advisory; fall through to live routing on any problem.
            cached_result = None

    if cached_result is not None:
        return cached_result

    index_payload: dict[str, Any] | None = None
    if write_index:
        index_payload = build_routing_index(
            project_root=project_root,
            state_dir=state_dir,
            index_path=index_path,
            routing_database_path=routing_database_path,
            vector_index_path=vector_index_path,
        )
    if validate:
        stale, stale_payload = check_routing_freshness(
            state_dir=state_dir,
            index_path=index_path,
            project_root=project_root,
            routing_database_path=routing_database_path,
            vector_index_path=vector_index_path,
        )
        if stale:
            stale_payload.setdefault("routing_index_schema", ROUTER_SCHEMA)
            stale_payload.setdefault(
                "routing_freshness",
                {
                    "status": "stale",
                    "required_refresh_command": ROUTER_REFRESH_COMMAND,
                },
            )
            stale_payload["requested_selector"] = selector or "--all"
            if selector is not None:
                stale_payload["authoritative_workflow"] = _authoritative_workflow_payload(
                    entries[workflow_ids[0]],
                    overrides.get(workflow_ids[0]),
                )
            return stale_payload
        else:
            index_payload = stale_payload

    payloads: list[dict[str, Any]] = []
    active_source_path = Path(active_source_meta["source"])
    alias_source_path = Path(alias_source_meta["source"])
    override_source_path = Path(override_source_meta["source"])
    workflows_dir = project_root / "state" / "workflows"

    for workflow_id in workflow_ids:
        if workflow_id not in entries:
            raise KeyError(f"Workflow not found in active queue: {workflow_id}")

        capsule = _build_capsule(
            entries[workflow_id],
            all_entries=entries,
            override=overrides.get(workflow_id),
            project_root=project_root,
            active_source=active_source_meta,
            alias_source=alias_source_meta,
            override_source=override_source_meta,
            routing_database_path=routing_database_path,
            vector_index_path=vector_index_path,
        )

        if write_capsules:
            workflows_dir.mkdir(parents=True, exist_ok=True)
            (project_root / capsule["primary_route_artifact"]).write_text(
                json.dumps(capsule, indent=2, sort_keys=True),
                encoding="utf-8",
            )

        payloads.append(_answer_payload(capsule, answer))

    result: dict[str, Any] = {
        "routing_index_stale": False,
        "unsafe_to_trust": False,
        "requested_selector": selector or "--all",
        "workflows": payloads,
        "answer": answer,
        "index_path": str(index_path.as_posix()),
        "routing_index_schema": ROUTER_SCHEMA,
        "required_refresh_command": ROUTER_REFRESH_COMMAND,
        "routing_source_schema": {
            "active_workflows": active_source_meta["schema"],
            "alias_index": alias_source_meta["schema"],
            "overrides": override_source_meta["schema"],
        },
    }

    if index_payload is None and index_path.exists():
        try:
            index_payload = _load_json_surface(index_path)
            if not isinstance(index_payload, dict):
                index_payload = None
        except Exception:
            index_payload = None

    if index_payload is not None:
        result["routing_index_generated_at"] = index_payload.get("generated_at")
        source_signatures = index_payload.get("source_signatures")
        result["routing_freshness"] = {
            "status": "fresh",
            "generated_at": index_payload.get("generated_at"),
            "source_signature_count": len(source_signatures) if isinstance(source_signatures, dict) else 0,
            "required_refresh_command": ROUTER_REFRESH_COMMAND,
            "schema": index_payload.get("schema"),
        }
    else:
        result["routing_freshness"] = {
            "status": "unverified",
            "required_refresh_command": ROUTER_REFRESH_COMMAND,
            "schema": ROUTER_SCHEMA,
        }

    if len(payloads) == 1:
        result["workflow"] = payloads[0]
        result["authoritative_workflow"] = _authoritative_workflow_payload(
            entries[workflow_ids[0]],
            overrides.get(workflow_ids[0]),
        )

    if routing_cache_ttl_seconds > 0 and cache_key is not None:
        try:
            current_signatures = current_signatures or _current_source_signatures(
                entries=entries,
                overrides=overrides,
                alias_source_path=Path(alias_source_meta["source"]),
                active_source_path=Path(active_source_meta["source"]),
                override_source_path=Path(override_source_meta["source"]),
                project_root=project_root,
                routing_database_path=routing_database_path,
                vector_index_path=vector_index_path,
            )
            with CanonicalDB(routing_database_path) as db:
                db.set_routing_cache(
                    cache_key,
                    payload=result,
                    source_signatures=current_signatures,
                    ttl_seconds=routing_cache_ttl_seconds,
                )
        except Exception:
            pass

    return result


def _routing_cache_key(
    *,
    routing_schema_version: str,
    selector: str | None,
    answer: str,
) -> str:
    """Build a deterministic cache key for a routing query.

    The key intentionally does NOT include validate or all_workflows flags:
    those affect whether a stale index is refreshed, not the cached routing
    answer for a given workflow and answer mode. Dropping them reduces cache
    fragmentation and improves hit rate.
    """
    target = "all" if selector is None else selector.upper()
    payload = "|".join([routing_schema_version, target, answer])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _current_source_signatures(
    *,
    entries: dict[str, dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
    alias_source_path: Path,
    active_source_path: Path,
    override_source_path: Path,
    project_root: Path,
    routing_database_path: Path | None = None,
    vector_index_path: Path | None = None,
) -> dict[str, Any]:
    """Return a compact, deterministic signature set for cache validation."""
    raw = _fingerprint_sources(
        entries=entries,
        overrides=overrides,
        alias_source_path=alias_source_path,
        active_source_path=active_source_path,
        override_source_path=override_source_path,
        project_root=project_root,
        vector_index_path=vector_index_path or VECTOR_INDEX_PATH,
    )
    signatures = {
        path: {
            "sha256": sig["sha256"],
            "size": sig["size"],
            "modified_at": sig["modified_at"],
            "exists": sig["exists"],
        }
        for path, sig in sorted(raw.items())
    }
    if routing_database_path is not None:
        graph = _dependency_graph_from_database(entries, routing_database_path)
        signatures["__routing_dependency_graph__"] = {
            "sha256": hashlib.sha256(
                json.dumps(graph, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "size": len(graph),
            "modified_at": "",
            "exists": routing_database_path.exists(),
        }
    return signatures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "selector",
        nargs="?",
        help=(
            "Workflow ID or alias (for example WF-1000 or workflow-a). "
            "Use --all for all active workflows."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Route all active workflows.",
    )
    parser.add_argument(
        "--answer",
        default="summary",
        choices=["summary", "next", "blockers", "helper", "all"],
        help="Workflow view to return.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Require route index freshness before answering.",
    )
    parser.add_argument(
        "--write-index",
        action="store_true",
        help="Regenerate the route index before routing.",
    )
    parser.add_argument(
        "--write-capsules",
        action="store_true",
        help="Regenerate workflow capsules under state/workflows. (Also implied by --write-index.)",
    )
    parser.add_argument(
        "--index-path",
        default=str(DEFAULT_INDEX_PATH),
        help="Route-index file path (default: state/workflow-routing-index.json)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the routing cache and always recompute the result.",
    )
    parser.add_argument(
        "--aliases",
        action="store_true",
        help="List all workflow aliases and exit.",
    )
    parser.add_argument(
        "--vector-index-path",
        default=str(VECTOR_INDEX_PATH),
        help="Vector memory index path for recall_context (default: vector/indexes/vector-memory.sqlite)",
    )
    parser.add_argument(
        "--skip-recall-context",
        action="store_true",
        help="Skip vector memory recall_context lookup for faster routing.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Compact single-workflow status (implies --validate --no-cache --skip-recall-context).",
    )
    return parser.parse_args()


def _compact_workflow_status(answer: dict[str, Any], selector: str) -> dict[str, Any]:
    stale = bool(answer.get("routing_index_stale"))
    freshness = answer.get("routing_freshness")
    if not isinstance(freshness, dict):
        freshness = {}
    changed_sources = []
    for change in answer.get("changed_sources", []):
        if isinstance(change, dict) and change.get("path"):
            changed_sources.append(
                {
                    "path": change["path"],
                    **({"reason": change["reason"]} if change.get("reason") else {}),
                }
            )
    return {
        "schema": "workflow-status.v1",
        "selector": selector,
        "state": answer.get("authoritative_workflow") or answer.get("workflow"),
        "routing_freshness": {
            "status": "stale" if stale else freshness.get("status", "fresh"),
            "unsafe_to_trust": bool(answer.get("unsafe_to_trust")),
            "index_path": answer.get("index_path"),
            "index_generated_at": answer.get("routing_index_generated_at"),
            "changed_sources": changed_sources,
            "required_refresh_command": answer.get("required_refresh_command"),
        },
    }


def main() -> int:
    args = _parse_args()
    try:
        if args.aliases:
            aliases = _load_alias_index(state_dir=DEFAULT_STATE_DIR)[0]
            print(
                json.dumps(
                    {
                        "aliases": {
                            alias: workflow_id
                            for alias, workflow_id in sorted(aliases.items())
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        if args.status and (not args.selector or args.all):
            raise ValueError("--status requires exactly one workflow selector")

        index_path = Path(args.index_path)
        if not index_path.is_absolute():
            index_path = PROJECT_ROOT / args.index_path

        answer = route_workflows(
            selector=args.selector,
            all_workflows=args.all,
            answer="summary" if args.status else args.answer,
            validate=args.validate or args.status,
            write_index=args.write_index,
            write_capsules=args.write_capsules,
            index_path=index_path,
            routing_cache_ttl_seconds=(
                0 if args.no_cache or args.status else DEFAULT_ROUTING_CACHE_TTL_SECONDS
            ),
            vector_index_path=(
                None
                if args.skip_recall_context or args.status
                else Path(args.vector_index_path)
            ),
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1

    if args.status:
        print(json.dumps(_compact_workflow_status(answer, args.selector), sort_keys=True))
    else:
        print(json.dumps(answer, indent=2))
    if answer.get("routing_index_stale"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
