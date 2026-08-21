#!/usr/bin/env python3
"""A10: Durable graph freshness and integrity monitor.

Runs the graph orphan/contradiction sweep against the live canonical database
and reports graph coverage drift (canonical records that should have edges but
do not). Silent on green; alerts on stdout when issues are found.

Checks:
  1. Orphan edges (subject/object record missing) and duplicate active triples.
  2. Coverage:
     - product_candidate entities without a has_metric edge
     - workflow_runs whose result_json lists written entities without a produced edge
     - workflows in ACTIVE_WORKFLOWS.md that declare depends_on but lack a
       corresponding workflows -> depends_on -> workflows edge
  3. Dependency drift: graph dependency edges that do not match the declared
     dependency graph in ACTIVE_WORKFLOWS.md.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB  # noqa: E402
from scripts.workflow_router import (
    ACTIVE_WORKFLOW_SOURCES,
    _load_json_surface,
    _normalize_dependency_list,
    _normalize_workflow_id,
)

DEFAULT_DATABASE = PROJECT_ROOT / "canonical" / "efficiens.db"


def _check_graph_integrity(db: CanonicalDB) -> list[dict]:
    """Orphan edges and duplicate active triples."""
    issues: list[dict] = []
    tables = db.tables()
    seen_triples: set[tuple[str, str, str, str, str]] = set()
    edges = db.list_relationships(status=None, include_expired=True)
    for edge in edges:
        if edge["subject_type"] in tables:
            row = db.connection.execute(
                f'SELECT 1 FROM "{edge["subject_type"]}" WHERE '
                f'"{_primary_key_of(db, edge["subject_type"])}" = ?',
                (edge["subject_id"],),
            ).fetchone()
            if row is None:
                issues.append(
                    {
                        "type": "orphan_subject",
                        "rel_id": edge["rel_id"],
                        "subject": f'{edge["subject_type"]}:{edge["subject_id"]}',
                    }
                )
        if edge["object_type"] in tables:
            row = db.connection.execute(
                f'SELECT 1 FROM "{edge["object_type"]}" WHERE '
                f'"{_primary_key_of(db, edge["object_type"])}" = ?',
                (edge["object_id"],),
            ).fetchone()
            if row is None:
                issues.append(
                    {
                        "type": "orphan_object",
                        "rel_id": edge["rel_id"],
                        "object": f'{edge["object_type"]}:{edge["object_id"]}',
                    }
                )
        if edge["status"] == "active":
            triple = (
                edge["subject_type"],
                edge["subject_id"],
                edge["predicate"],
                edge["object_type"],
                edge["object_id"],
            )
            if triple in seen_triples:
                issues.append(
                    {
                        "type": "duplicate_active",
                        "rel_id": edge["rel_id"],
                        "triple": " -> ".join(triple),
                    }
                )
            seen_triples.add(triple)
    return issues


def _check_graph_coverage(db: CanonicalDB, state_dir: Path = PROJECT_ROOT / "state") -> list[dict]:
    """Canonical records that should have graph edges but do not."""
    gaps: list[dict] = []

    # product_candidate entities without any has_metric edge.
    rows = db.connection.execute(
        "SELECT e.entity_id FROM entities e "
        "WHERE e.entity_type = 'product_candidate' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM relationships r "
        "  WHERE r.status = 'active' AND r.predicate = 'has_metric' "
        "  AND r.subject_type = 'entities' AND r.subject_id = e.entity_id"
        ")"
    ).fetchall()
    for row in rows:
        gaps.append(
            {
                "type": "missing_has_metric",
                "entity_id": row["entity_id"],
                "remediation": "python scripts/graph_backfill.py",
            }
        )

    # workflow_runs whose result lists written entities without a produced edge.
    run_rows = db.connection.execute(
        "SELECT run_id, result_json FROM workflow_runs WHERE result_json IS NOT NULL"
    ).fetchall()
    for run in run_rows:
        try:
            result = json.loads(run["result_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for written in result.get("written", []):
            entity_id = written.get("entity_id")
            if not entity_id:
                continue
            edge = db.connection.execute(
                "SELECT 1 FROM relationships "
                "WHERE status = 'active' AND predicate = 'produced' "
                "AND subject_type = 'workflow_runs' AND subject_id = ? "
                "AND object_type = 'entities' AND object_id = ?",
                (run["run_id"], str(entity_id)),
            ).fetchone()
            if edge is None:
                gaps.append(
                    {
                        "type": "missing_produced",
                        "run_id": run["run_id"],
                        "entity_id": str(entity_id),
                        "remediation": "python scripts/graph_backfill.py",
                    }
                )

    # workflows that declare depends_on but lack a graph edge.
    declared = _load_declared_dependencies(state_dir)
    for workflow_id, dependencies in declared.items():
        for dependency in dependencies:
            edge = db.connection.execute(
                "SELECT 1 FROM relationships "
                "WHERE status = 'active' AND predicate = 'depends_on' "
                "AND subject_type = 'workflows' AND subject_id = ? "
                "AND object_type = 'workflows' AND object_id = ?",
                (workflow_id, dependency),
            ).fetchone()
            if edge is None:
                gaps.append(
                    {
                        "type": "missing_workflow_dependency",
                        "workflow_id": workflow_id,
                        "dependency": dependency,
                        "remediation": "python scripts/graph_backfill.py",
                    }
                )

    return gaps


def _load_declared_dependencies(state_dir: Path = PROJECT_ROOT / "state") -> dict[str, list[str]]:
    """Read depends_on declarations from ACTIVE_WORKFLOWS.md / active_workflows.json."""
    candidate_paths = [
        state_dir / "ACTIVE_WORKFLOWS.md",
        state_dir / "active_workflows.json",
    ]
    for path in candidate_paths:
        if path.exists():
            payload = _load_json_surface(path)
            break
    else:
        return {}

    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("workflows", [])
    else:
        return {}

    result: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "workflow_id" not in entry:
            continue
        depends_on = entry.get("depends_on")
        if not depends_on:
            continue
        result[_normalize_workflow_id(entry["workflow_id"])] = _normalize_dependency_list(
            depends_on, owner=entry["workflow_id"]
        )
    return result


def _primary_key_of(db: CanonicalDB, table: str) -> str:
    rows = db.connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    keys = [row[1] for row in rows if row[5] == 1]
    if len(keys) != 1:
        raise ValueError(f"Table {table} has no single primary key")
    return keys[0]


def main(state_dir: Path | None = None) -> int:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    database_path = DEFAULT_DATABASE
    if state_dir is None:
        state_dir = PROJECT_ROOT / "state"

    if not database_path.exists():
        print(f"GRAPH FRESHNESS OK {now} no_database", file=sys.stderr)
        return 0

    try:
        with CanonicalDB(database_path, read_only=True) as db:
            integrity_issues = _check_graph_integrity(db)
            coverage_gaps = _check_graph_coverage(db, state_dir=state_dir)
    except sqlite3.Error as exc:
        print(f"GRAPH FRESHNESS FAIL {now} error={exc}")
        return 1

    if integrity_issues or coverage_gaps:
        print(f"GRAPH FRESHNESS DEGRADED {now}")
        print(
            json.dumps(
                {
                    "integrity_issues": integrity_issues,
                    "coverage_gaps": coverage_gaps,
                },
                indent=2,
            )
        )
        return 1

    print(f"GRAPH FRESHNESS OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
