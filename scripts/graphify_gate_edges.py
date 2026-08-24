#!/usr/bin/env python3
"""Reconcile static workspace-status gate declarations into Graphify edges."""
from __future__ import annotations

import argparse
import ast
import json
import os
import tempfile
from pathlib import Path
from typing import Any


GRAPH_DIR_NAME = "graphify-out"
GRAPH_NAME = "graph.json"
OWNER_PATH = Path("scripts/workspace_status.py")
EDGE_ORIGIN = "workspace_gate_contract"
EDGE_RELATION = "runs_gate"


def _project_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"gate target must be a workspace-relative path: {path}")
    return candidate.as_posix()


def declared_gates(project_root: Path) -> list[dict[str, Any]]:
    """Read static DEFAULT_GATES entries without importing the status script."""
    source_path = project_root / OWNER_PATH
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(node, "target", None), ast.Name)
        and node.target.id == "DEFAULT_GATES"
    ]
    if len(assignments) != 1:
        raise ValueError("workspace_status.py must declare exactly one static DEFAULT_GATES assignment")
    value = assignments[0].value
    if not isinstance(value, (ast.List, ast.Tuple)):
        raise ValueError("DEFAULT_GATES must be a static list or tuple")

    gates: list[dict[str, Any]] = []
    for gate in value.elts:
        if not isinstance(gate, (ast.List, ast.Tuple)) or len(gate.elts) != 3:
            raise ValueError("each DEFAULT_GATES entry must contain label, command, and timeout")
        label = ast.literal_eval(gate.elts[0])
        command = ast.literal_eval(gate.elts[1])
        if not isinstance(label, str) or not label:
            raise ValueError("gate labels must be non-empty strings")
        if not isinstance(command, (list, tuple)) or not command or not isinstance(command[0], str):
            raise ValueError(f"gate {label!r} must have a static script command")
        target = _project_relative(command[0])
        target_path = project_root / target
        if not target_path.is_file():
            raise ValueError(f"gate {label!r} targets a missing script: {target}")
        gates.append(
            {
                "label": label,
                "target": target,
                "source_location": f"L{gate.elts[1].elts[0].lineno}",
            }
        )
    return gates


def _file_node_id(graph: dict[str, Any], source_file: str) -> str:
    candidates = [
        node["id"]
        for node in graph["nodes"]
        if isinstance(node, dict)
        and node.get("source_file") == source_file
        and node.get("file_type") == "code"
        and node.get("source_location") == "L1"
        and isinstance(node.get("id"), str)
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Graphify graph must contain exactly one file node for {source_file}; found {len(candidates)}"
        )
    return candidates[0]


def _expected_edges(project_root: Path, graph: dict[str, Any]) -> list[dict[str, Any]]:
    source_file = OWNER_PATH.as_posix()
    source_node = _file_node_id(graph, source_file)
    edges = []
    for gate in declared_gates(project_root):
        edges.append(
            {
                "_origin": EDGE_ORIGIN,
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "context": f"workspace_status.DEFAULT_GATES:{gate['label']}",
                "relation": EDGE_RELATION,
                "source": source_node,
                "source_file": source_file,
                "source_location": gate["source_location"],
                "target": _file_node_id(graph, gate["target"]),
                "weight": 1.0,
            }
        )
    return edges


def _load_graph(graph_path: Path) -> dict[str, Any]:
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("Graphify graph must be a JSON object")
    if not isinstance(graph.get("nodes"), list):
        raise ValueError("Graphify graph nodes must be a list")
    if not isinstance(graph.get("links"), list):
        raise ValueError("Graphify graph links must be a list")
    return graph


def _edge_identity(edge: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        edge.get(field)
        for field in (
            "_origin",
            "source",
            "target",
            "relation",
            "context",
            "source_file",
            "source_location",
            "confidence",
            "confidence_score",
            "weight",
        )
    )


def gate_edge_issues(project_root: Path) -> list[dict[str, Any]]:
    """Return missing static gate-edge evidence without changing graph.json."""
    if not (project_root / OWNER_PATH).is_file():
        return []
    graph = _load_graph(project_root / GRAPH_DIR_NAME / GRAPH_NAME)
    expected = _expected_edges(project_root, graph)
    actual = {
        _edge_identity(edge)
        for edge in graph["links"]
        if isinstance(edge, dict) and edge.get("_origin") == EDGE_ORIGIN
    }
    issues = []
    for edge in expected:
        if _edge_identity(edge) not in actual:
            issues.append(
                {
                    "code": "declared_gate_edge_missing",
                    "label": edge["context"].rsplit(":", 1)[1],
                    "required_refresh_command": "python scripts/graphify_gate_edges.py",
                    "source": edge["source_file"],
                    "source_location": edge["source_location"],
                    "target": next(
                        node["source_file"]
                        for node in graph["nodes"]
                        if isinstance(node, dict) and node.get("id") == edge["target"]
                    ),
                }
            )
    return issues


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def reconcile(project_root: Path) -> dict[str, Any]:
    """Replace generated gate edges with the static contract currently in source."""
    graph_path = project_root / GRAPH_DIR_NAME / GRAPH_NAME
    graph = _load_graph(graph_path)

    generated_edges = _expected_edges(project_root, graph)
    preserved_edges = [
        edge
        for edge in graph["links"]
        if not isinstance(edge, dict) or edge.get("_origin") != EDGE_ORIGIN
    ]
    graph["links"] = preserved_edges + generated_edges
    _write_json(graph_path, graph)
    return {
        "schema": "graphify-gate-edges.v1",
        "status": "updated",
        "graph_path": str(graph_path),
        "edge_count": len(generated_edges),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="workspace root (default: repository containing this script)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = reconcile(args.project_root.resolve())
    except (OSError, SyntaxError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema": "graphify-gate-edges.v1",
            "status": "unavailable",
            "issues": [{"code": "gate_edge_reconciliation_failed", "detail": f"{type(exc).__name__}: {exc}"}],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "updated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
