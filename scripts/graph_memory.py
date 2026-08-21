#!/usr/bin/env python3
"""Durable graph-memory adapter for the Efficiens workspace.

Executable commands for the graph layer declared in `graph/README.md`:

- `add_edge`    — assert one relationship edge (provenance-gated)
- `neighbors`   — list active edges from a record
- `path`        — shortest active path between two records (BFS)
- `affected`    — reverse traversal: what depends on a record
- `validate`    — orphan/contradiction sweep over the graph

Edges link canonical records by identifier; they never re-state facts. The
canonical record remains authoritative. See `references/graph-memory.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB  # noqa: E402


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_add_edge(args: argparse.Namespace) -> int:
    with CanonicalDB(args.database) as db:
        rel_id = db.add_relationship(
            args.subject_type,
            args.subject_id,
            args.predicate,
            args.object_type,
            args.object_id,
            confidence=args.confidence,
            valid_from=args.valid_from,
            valid_until=args.valid_until,
            provenance={
                "source_type": "graph_memory",
                "source_ref": args.source_ref or "graph-memory-cli",
                "confidence": args.confidence,
                "notes": args.notes,
            },
        )
    _print_json({"status": "ok", "rel_id": rel_id})
    return 0


def cmd_neighbors(args: argparse.Namespace) -> int:
    with CanonicalDB(args.database) as db:
        edges = db.list_relationships(
            subject_type=args.subject_type,
            subject_id=args.subject_id,
            predicate=args.predicate,
            status=args.status,
            include_expired=args.include_expired,
        )
    _print_json({"status": "ok", "count": len(edges), "edges": edges})
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    with CanonicalDB(args.database) as db:
        path = db.find_path(
            args.start_type,
            args.start_id,
            args.end_type,
            args.end_id,
            max_depth=args.max_depth,
        )
    if path is None:
        _print_json({"status": "no_path", "path": None})
        return 1
    _print_json({"status": "ok", "hops": len(path), "path": path})
    return 0


def cmd_affected(args: argparse.Namespace) -> int:
    with CanonicalDB(args.database) as db:
        edges = db.affected(
            args.object_type,
            args.object_id,
            max_depth=args.max_depth,
        )
    _print_json({"status": "ok", "count": len(edges), "edges": edges})
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Sweep the graph for orphan edges and contradictory active edges.

    Orphan: an edge whose subject or object record does not exist in the
    canonical database. Contradiction: two active edges with the same
    subject/predicate/object triple (duplicate assertion).

    Exit 0 when clean; exit 1 when issues are found (cron-friendly).
    """
    with CanonicalDB(args.database, read_only=True) as db:
        edges = db.list_relationships(status=None, include_expired=True)
        tables = db.tables()
        issues: list[dict] = []
        seen_triples: set[tuple[str, str, str, str, str]] = set()

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

    if issues:
        _print_json({"status": "issues", "count": len(issues), "issues": issues})
        return 1
    _print_json({"status": "ok", "count": 0, "issues": []})
    return 0


def _primary_key_of(db: CanonicalDB, table: str) -> str:
    rows = db.connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    keys = [row[1] for row in rows if row[5] == 1]
    if len(keys) != 1:
        raise ValueError(f"Table {table} has no single primary key")
    return keys[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graph_memory.py",
        description="Durable graph-memory adapter for the Efficiens workspace.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--database",
        default=str(Path(__file__).resolve().parents[1] / "canonical" / "efficiens.db"),
        help="canonical SQLite database path (default: canonical/efficiens.db)",
    )

    add_edge = subparsers.add_parser("add_edge", parents=[common], help="assert one relationship edge")
    add_edge.add_argument("--subject-type", required=True)
    add_edge.add_argument("--subject-id", required=True)
    add_edge.add_argument("--predicate", required=True)
    add_edge.add_argument("--object-type", required=True)
    add_edge.add_argument("--object-id", required=True)
    add_edge.add_argument("--confidence", type=float, default=None)
    add_edge.add_argument("--valid-from", default=None)
    add_edge.add_argument("--valid-until", default=None)
    add_edge.add_argument("--source-ref", default=None)
    add_edge.add_argument("--notes", default=None)
    add_edge.set_defaults(func=cmd_add_edge)

    neighbors = subparsers.add_parser("neighbors", parents=[common], help="list active edges from a record")
    neighbors.add_argument("--subject-type", required=True)
    neighbors.add_argument("--subject-id", required=True)
    neighbors.add_argument("--predicate", default=None)
    neighbors.add_argument("--status", default="active")
    neighbors.add_argument("--include-expired", action="store_true")
    neighbors.set_defaults(func=cmd_neighbors)

    path = subparsers.add_parser("path", parents=[common], help="shortest active path between two records")
    path.add_argument("--start-type", required=True)
    path.add_argument("--start-id", required=True)
    path.add_argument("--end-type", required=True)
    path.add_argument("--end-id", required=True)
    path.add_argument("--max-depth", type=int, default=8)
    path.set_defaults(func=cmd_path)

    affected = subparsers.add_parser("affected", parents=[common], help="what depends on a record")
    affected.add_argument("--object-type", required=True)
    affected.add_argument("--object-id", required=True)
    affected.add_argument("--max-depth", type=int, default=2)
    affected.set_defaults(func=cmd_affected)

    validate = subparsers.add_parser("validate", parents=[common], help="orphan/contradiction sweep")
    validate.set_defaults(func=cmd_validate)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
