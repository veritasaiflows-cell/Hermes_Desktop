#!/usr/bin/env python3
"""One-time graph backfill for the durable relationships layer.

Materializes the minimal edge vocabulary for records that already exist in the
canonical database but were written before the graph layer existed:

- entities -> has_metric -> metrics   (from metrics.dimensions_json.entity_id)
- workflow_runs -> produced -> entities (from workflow_runs.result_json.written)
- workflows -> depends_on -> workflows (from state/ACTIVE_WORKFLOWS.md)

Idempotent: existing active edges with the same subject/predicate/object triple
are reused, never duplicated. Every new edge carries a provenance record with
source_type 'graph_backfill'.

Exit 0 on success (with a JSON summary), 1 on failure. Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


def _existing_triples(db: CanonicalDB) -> set[tuple[str, str, str, str, str]]:
    rows = db.connection.execute(
        "SELECT subject_type, subject_id, predicate, object_type, object_id "
        "FROM relationships WHERE status = 'active'"
    ).fetchall()
    return {
        (row["subject_type"], row["subject_id"], row["predicate"], row["object_type"], row["object_id"])
        for row in rows
    }


def _assert_edge(
    db: CanonicalDB,
    triples: set[tuple[str, str, str, str, str]],
    provenance_id: str,
    subject_type: str,
    subject_id: str,
    predicate: str,
    object_type: str,
    object_id: str,
    confidence: float | None,
    counters: dict[str, int],
) -> None:
    triple = (subject_type, subject_id, predicate, object_type, object_id)
    if triple in triples:
        counters["reused"] += 1
        return
    db.add_relationship(
        subject_type,
        subject_id,
        predicate,
        object_type,
        object_id,
        confidence=confidence,
        provenance_id=provenance_id,
    )
    triples.add(triple)
    counters["asserted"] += 1


def _load_workflow_entries(state_dir: Path = PROJECT_ROOT / "state") -> dict[str, dict[str, Any]]:
    """Load active workflow entries from the provided state directory."""
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

    return {
        _normalize_workflow_id(entry["workflow_id"]): entry
        for entry in entries
        if isinstance(entry, dict) and "workflow_id" in entry
    }


def backfill_workflow_dependencies(
    db: CanonicalDB,
    triples: set[tuple[str, str, str, str, str]],
    provenance_id: str,
    counters: dict[str, int],
    state_dir: Path = PROJECT_ROOT / "state",
) -> None:
    """Assert workflows -> depends_on -> workflows edges from ACTIVE_WORKFLOWS.md."""
    entries = _load_workflow_entries(state_dir)
    for workflow_id, entry in entries.items():
        dependencies = entry.get("depends_on")
        if not dependencies:
            continue
        for dependency in _normalize_dependency_list(dependencies, owner=workflow_id):
            _assert_edge(
                db,
                triples,
                provenance_id,
                "workflows",
                workflow_id,
                "depends_on",
                "workflows",
                dependency,
                None,
                counters,
            )


def backfill_graph(
    database_path: str | Path = DEFAULT_DATABASE,
    state_dir: Path | None = None,
) -> dict:
    """Materialize missing graph edges for pre-graph canonical records."""
    counters = {"asserted": 0, "reused": 0}
    if state_dir is None:
        state_dir = PROJECT_ROOT / "state"
    with CanonicalDB(database_path) as db:
        provenance_id = db.add_provenance(
            source_type="graph_backfill",
            source_ref="graph-backfill:relationships",
            notes="One-time backfill of durable graph edges from existing canonical records",
            confidence=1.0,
        )
        triples = _existing_triples(db)

        # 1. entities -> has_metric -> metrics
        metric_rows = db.connection.execute(
            "SELECT metric_id, dimensions_json FROM metrics "
            "WHERE dimensions_json IS NOT NULL"
        ).fetchall()
        for row in metric_rows:
            try:
                dimensions = json.loads(row["dimensions_json"] or "{}")
            except json.JSONDecodeError:
                continue
            entity_id = dimensions.get("entity_id")
            if not entity_id:
                continue
            _assert_edge(
                db,
                triples,
                provenance_id,
                "entities",
                str(entity_id),
                "has_metric",
                "metrics",
                row["metric_id"],
                None,
                counters,
            )

        # 2. workflow_runs -> produced -> entities
        run_rows = db.connection.execute(
            "SELECT run_id, result_json FROM workflow_runs WHERE result_json IS NOT NULL"
        ).fetchall()
        for row in run_rows:
            try:
                result = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                continue
            for written in result.get("written", []):
                entity_id = written.get("entity_id")
                if not entity_id:
                    continue
                _assert_edge(
                    db,
                    triples,
                    provenance_id,
                    "workflow_runs",
                    row["run_id"],
                    "produced",
                    "entities",
                    str(entity_id),
                    None,
                    counters,
                )

        # 3. workflows -> depends_on -> workflows
        backfill_workflow_dependencies(db, triples, provenance_id, counters, state_dir)

    return {
        "status": "ok",
        "edges_asserted": counters["asserted"],
        "edges_reused": counters["reused"],
        "provenance_id": provenance_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
        help="canonical SQLite database path (default: canonical/efficiens.db)",
    )
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / "state"),
        help="directory containing ACTIVE_WORKFLOWS.md (default: state/)",
    )
    args = parser.parse_args()
    try:
        summary = backfill_graph(args.database, state_dir=Path(args.state_dir))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"GRAPH BACKFILL FAIL: {exc}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
