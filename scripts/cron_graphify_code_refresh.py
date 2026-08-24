#!/usr/bin/env python3
"""A18: Require an explicit one-shot request before Graphify publication."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Any, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.graphify_freshness import check_freshness, write_baseline
from scripts.graphify_gate_edges import reconcile
from scripts.graphify_generation import (
    current_pointer_bytes,
    publication_lock,
    publish_generation,
    restore_current_pointer,
)

FreshnessCheck = Callable[[Path], dict[str, Any]]
SNAPSHOT_SCHEMA = "graphify-source-snapshot.v1"
SOURCE_ROOTS = ("canonical", "scripts", "tests")
GRAPHIFY_PACKAGE = "graphifyy[mcp]==0.9.45"
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class SourceSnapshot:
    """An immutable, Git-visible source copy plus its exact source fingerprint."""

    generation_id: str
    snapshot_dir: Path
    workspace_root: Path
    source_fingerprint: dict[str, str]


SnapshotBuilder = Callable[[Path, str], SourceSnapshot]
SnapshotMatcher = Callable[[Path, SourceSnapshot], bool]
CandidateBuilder = Callable[[SourceSnapshot], Path]
CandidateValidator = Callable[[SourceSnapshot, Path], None]
CandidateCommandRunner = Callable[[tuple[str, ...], Path], None]
PromotionRunner = Callable[[Path, str], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_generation_id() -> str:
    """Create a unique, inspectable immutable generation identifier for one promotion."""
    return datetime.now(timezone.utc).strftime("g-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]


def _sha256(path: Path) -> str:
    """Hash a regular file without retaining its complete content in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether a path is a symbolic link or a Windows reparse point."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def source_fingerprint(project_root: Path) -> dict[str, str]:
    """Hash every governed Python source while rejecting links and special files."""
    root = Path(project_root).resolve()
    sources: dict[str, str] = {}
    for name in SOURCE_ROOTS:
        source_root = root / name
        if not source_root.is_dir() or _is_link_or_reparse(source_root):
            raise ValueError(f"Graphify source root is missing or unsafe: {name}")
        for current, directories, filenames in os.walk(source_root, followlinks=False):
            current_path = Path(current)
            if _is_link_or_reparse(current_path):
                raise ValueError("Graphify source directory is linked or reparse-pointed")
            for directory in directories:
                child = current_path / directory
                if _is_link_or_reparse(child) or not stat.S_ISDIR(child.lstat().st_mode):
                    raise ValueError("Graphify source directory is unsafe")
            for filename in filenames:
                source = current_path / filename
                if source.suffix.lower() != ".py":
                    continue
                if _is_link_or_reparse(source) or not stat.S_ISREG(source.lstat().st_mode):
                    raise ValueError("Graphify source file is unsafe")
                relative = source.relative_to(root).as_posix()
                sources[relative] = _sha256(source)
    if not sources:
        raise ValueError("Graphify source snapshot has no Python sources")
    return dict(sorted(sources.items()))


def _validate_generation_id(generation_id: str) -> str:
    """Validate a generation ID before using it as an immutable snapshot directory."""
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise ValueError("Graphify generation ID is invalid")
    return generation_id


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write source snapshot metadata as a compact inspectable JSON record."""
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_source_snapshot(project_root: Path, generation_id: str) -> SourceSnapshot:
    """Copy governed code into a Git-visible immutable candidate source snapshot."""
    root = Path(project_root).resolve()
    generation_id = _validate_generation_id(generation_id)
    fingerprint = source_fingerprint(root)
    snapshot_dir = root / "source" / "graphify-candidates" / generation_id
    workspace_root = snapshot_dir / "workspace"
    if snapshot_dir.exists():
        raise ValueError("Graphify source snapshot generation already exists")
    try:
        for name in SOURCE_ROOTS:
            (workspace_root / name).mkdir(parents=True, exist_ok=True)
        for relative in fingerprint:
            source = root / relative
            target = workspace_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(stat.S_IREAD)
        _write_json(
            snapshot_dir / "snapshot.json",
            {
                "schema": SNAPSHOT_SCHEMA,
                "generation_id": generation_id,
                "created_at": _utc_now(),
                "source_fingerprint": fingerprint,
                "workspace_root": "workspace",
            },
        )
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise
    snapshot = SourceSnapshot(
        generation_id=generation_id,
        snapshot_dir=snapshot_dir,
        workspace_root=workspace_root,
        source_fingerprint=fingerprint,
    )
    if not snapshot_matches_live_source(root, snapshot):
        raise ValueError("Graphify source changed while snapshot was created")
    return snapshot


def snapshot_matches_live_source(project_root: Path, snapshot: SourceSnapshot) -> bool:
    """Require both the immutable copied snapshot and live sources to match its fingerprint."""
    try:
        return (
            source_fingerprint(snapshot.workspace_root) == snapshot.source_fingerprint
            and source_fingerprint(project_root) == snapshot.source_fingerprint
        )
    except (OSError, ValueError):
        return False


def _run_candidate_command(command: tuple[str, ...], cwd: Path) -> None:
    """Run a bounded local Graphify command and redact its detail on failure."""
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if completed.returncode != 0:
        raise RuntimeError("Graphify candidate command failed")


def build_candidate_graph(
    snapshot: SourceSnapshot,
    *,
    runner: CandidateCommandRunner = _run_candidate_command,
) -> Path:
    """Extract a new graph inside the isolated snapshot, never the live artifact directory."""
    workspace_root = snapshot.workspace_root.resolve()
    graph_dir = workspace_root / "graphify-out"
    if graph_dir.exists():
        raise ValueError("Graphify candidate output directory already exists")
    command = (
        "uvx",
        "--from",
        GRAPHIFY_PACKAGE,
        "graphify",
        "extract",
        str(workspace_root),
        "--code-only",
        "--out",
        str(workspace_root),
    )
    runner(command, workspace_root)
    graph_path = graph_dir / "graph.json"
    if not graph_path.is_file() or _is_link_or_reparse(graph_path):
        raise ValueError("Graphify candidate graph was not produced safely")
    return graph_path


def _diagnose_candidate_graph(graph_path: Path) -> dict[str, Any]:
    """Run Graphify's read-only multigraph diagnostic on an isolated candidate graph."""
    completed = subprocess.run(
        [
            "uvx",
            "--from",
            GRAPHIFY_PACKAGE,
            "graphify",
            "diagnose",
            "multigraph",
            "--graph",
            str(graph_path),
            "--json",
        ],
        cwd=str(graph_path.parent),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("Graphify candidate diagnostics failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Graphify candidate diagnostics were not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Graphify candidate diagnostics were not an object")
    return payload


def _check_candidate_contract(graph_path: Path) -> dict[str, Any]:
    """Require the fixed facade to pass its strict contract against a candidate graph."""
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "cron_graphify_mcp_contract.py"),
            "--candidate-graph",
            str(graph_path),
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("Graphify candidate MCP contract failed")
    return {"status": "pass"}


def validate_candidate_graph(
    snapshot: SourceSnapshot,
    graph_path: Path,
    *,
    reconcile_candidate: Callable[..., Any] = reconcile,
    write_candidate_baseline: Callable[[Path], Any] = write_baseline,
    check_candidate_freshness: FreshnessCheck = check_freshness,
    diagnose_candidate: Callable[[Path], dict[str, Any]] = _diagnose_candidate_graph,
    check_candidate_contract: Callable[[Path], dict[str, Any]] = _check_candidate_contract,
) -> dict[str, Any]:
    """Accept only a candidate that passes graph, freshness, and facade contract gates."""
    workspace_root = snapshot.workspace_root.resolve()
    candidate_graph = Path(graph_path).resolve()
    expected_graph = workspace_root / "graphify-out" / "graph.json"
    if candidate_graph != expected_graph:
        raise ValueError("Graphify candidate graph is outside the immutable source snapshot")
    reconcile_candidate(workspace_root, graph_path=candidate_graph)
    write_candidate_baseline(workspace_root)
    freshness = check_candidate_freshness(workspace_root)
    if freshness.get("status") != "fresh":
        raise ValueError("Graphify candidate freshness gate failed")
    diagnostics = diagnose_candidate(candidate_graph)
    summary = diagnostics.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Graphify candidate diagnostics had no summary")
    invalid_counts = (
        "non_object_edges",
        "missing_endpoint_edges",
        "dangling_endpoint_edges",
        "self_loop_edges",
    )
    if any(summary.get(field) != 0 for field in invalid_counts):
        raise ValueError("Graphify candidate diagnostics found invalid graph edges")
    if summary.get("post_build_error") != "":
        raise ValueError("Graphify candidate diagnostics could not build the graph")
    contract = check_candidate_contract(candidate_graph)
    if contract.get("status") != "pass":
        raise ValueError("Graphify candidate MCP contract gate failed")
    return {
        "status": "accepted",
        "candidate_graph": str(candidate_graph),
        "freshness": freshness,
        "diagnostics": diagnostics,
        "contract": contract,
    }


def _check_selected_freshness_under_writer_lock(project_root: Path) -> dict[str, Any]:
    """Check the newly selected generation without recursively acquiring the held writer lock."""
    return check_freshness(project_root, lock_held=True)


def run_one_shot_promotion(
    project_root: Path,
    generation_id: str,
    *,
    create_snapshot: SnapshotBuilder = create_source_snapshot,
    snapshot_matches: SnapshotMatcher = snapshot_matches_live_source,
    build_candidate: CandidateBuilder = build_candidate_graph,
    validate_candidate: CandidateValidator = validate_candidate_graph,
    capture_pointer: Callable[..., bytes | None] = current_pointer_bytes,
    publish: Callable[..., Any] = publish_generation,
    post_publish_check: FreshnessCheck = _check_selected_freshness_under_writer_lock,
    restore_pointer: Callable[..., None] = restore_current_pointer,
) -> dict[str, Any]:
    """Build and validate an isolated candidate before atomically selecting it once."""
    root = Path(project_root).resolve()
    generation_id = _validate_generation_id(generation_id)
    try:
        with publication_lock(root, shared=False):
            snapshot = create_snapshot(root, generation_id)
            if not snapshot_matches(root, snapshot):
                return {
                    "schema": "graphify-code-refresh.v2",
                    "status": "blocked",
                    "reason": "live_source_changed_during_snapshot",
                    "generation_id": generation_id,
                }
            candidate_graph = Path(build_candidate(snapshot)).resolve()
            validate_candidate(snapshot, candidate_graph)
            if not snapshot_matches(root, snapshot):
                return {
                    "schema": "graphify-code-refresh.v2",
                    "status": "blocked",
                    "reason": "live_source_changed_before_promotion",
                    "generation_id": generation_id,
                    "candidate_graph": str(candidate_graph),
                }
            previous_pointer = capture_pointer(root, lock_held=True)
            selection = publish(
                root,
                candidate_graph.parent,
                generation_id=generation_id,
                source_fingerprint=snapshot.source_fingerprint,
                snapshot_path=snapshot.snapshot_dir.relative_to(root).as_posix(),
                lock_held=True,
            )
            try:
                post_promotion = post_publish_check(root)
            except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
                restore_pointer(root, previous_pointer, lock_held=True)
                return {
                    "schema": "graphify-code-refresh.v2",
                    "status": "blocked",
                    "reason": "post_promotion_verification_failed",
                    "generation_id": generation_id,
                    "candidate_graph": str(candidate_graph),
                }
            if post_promotion.get("status") != "fresh":
                restore_pointer(root, previous_pointer, lock_held=True)
                return {
                    "schema": "graphify-code-refresh.v2",
                    "status": "blocked",
                    "reason": "post_promotion_verification_failed",
                    "generation_id": generation_id,
                    "candidate_graph": str(candidate_graph),
                    "post_promotion": post_promotion,
                }
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema": "graphify-code-refresh.v2",
            "status": "blocked",
            "reason": "candidate_build_or_validation_failed",
            "error_type": type(exc).__name__,
            "generation_id": generation_id,
        }
    return {
        "schema": "graphify-code-refresh.v2",
        "status": "promoted",
        "generation_id": generation_id,
        "candidate_graph": str(candidate_graph),
        "selected_graph": str(getattr(selection, "graph_path", candidate_graph)),
        "snapshot_path": snapshot.snapshot_dir.relative_to(root).as_posix(),
        "post_promotion": post_promotion,
    }


def main(
    project_root: Path = PROJECT_ROOT,
    *,
    check: FreshnessCheck = check_freshness,
    promote_once: bool = False,
    promote: PromotionRunner = run_one_shot_promotion,
) -> int:
    """Remain read-only unless a separately invoked one-shot promotion is authorized."""
    now = _utc_now()
    root = Path(project_root).resolve()
    try:
        report = check(root)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            "GRAPHIFY CODE REFRESH BLOCKED "
            f"{now} reason=freshness_check_failed error={type(exc).__name__}"
        )
        return 1

    if report.get("status") == "fresh":
        print(f"GRAPHIFY CODE REFRESH SKIP {now} reason=artifact_current", file=sys.stderr)
        return 0

    if promote_once:
        generation_id = _new_generation_id()
        try:
            outcome = promote(root, generation_id)
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            outcome = {
                "schema": "graphify-code-refresh.v2",
                "status": "blocked",
                "reason": "promotion_transaction_failed",
                "error_type": type(exc).__name__,
                "generation_id": generation_id,
            }
        status = str(outcome.get("status", "blocked"))
        label = "PROMOTED" if status == "promoted" else "BLOCKED"
        print(f"GRAPHIFY CODE REFRESH {label} {now}")
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0 if status == "promoted" else 1

    print(f"GRAPHIFY CODE REFRESH BLOCKED {now}")
    print(
        json.dumps(
            {
                "schema": "graphify-code-refresh.v2",
                "status": "blocked",
                "reason": "explicit_promotion_required",
                "writer_executed": False,
                "freshness": report,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1


def _parse_args() -> argparse.Namespace:
    """Parse the deliberate one-shot promotion control surface for A18."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="workspace root (default: repository containing this script)",
    )
    parser.add_argument(
        "--promote-once",
        action="store_true",
        help="build, validate, and atomically select one isolated Graphify generation",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(args.project_root, promote_once=args.promote_once))
