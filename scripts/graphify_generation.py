#!/usr/bin/env python3
"""Safe immutable-generation primitives for Graphify artifact publication."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from typing import Any, Iterator, Mapping
import uuid


GRAPH_DIR_NAME = "graphify-out"
GENERATIONS_DIR_NAME = "generations"
STAGING_DIR_NAME = ".staging"
CURRENT_POINTER_NAME = "current-generation.json"
LOCK_NAME = ".publication.lock"
ACCEPTANCE_NAME = "acceptance.json"
POINTER_SCHEMA = "graphify-current-generation.v1"
ACCEPTANCE_SCHEMA = "graphify-generation-acceptance.v1"
REQUIRED_ARTIFACTS = ("graph.json", "manifest.json", "freshness-baseline.json")
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GraphifyGenerationError(ValueError):
    """Raised when a Graphify generation violates publication invariants."""


@dataclass(frozen=True)
class GenerationSelection:
    """A verified immutable Graphify generation selected by the atomic pointer."""

    generation_id: str
    generation_dir: Path
    graph_path: Path
    artifact_tree_sha256: str
    source_fingerprint: dict[str, str]
    snapshot_path: str


def _utc_now() -> str:
    """Return the current UTC time in deterministic ISO-8601 form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    """Hash one regular file without loading its complete content into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether a path is a symbolic link or Windows reparse point."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _contained(root: Path, path: Path, *, label: str) -> Path:
    """Resolve a path and reject anything that escapes the supplied root."""
    resolved_root = Path(root).resolve()
    resolved_path = Path(path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise GraphifyGenerationError(f"{label} is outside the workspace") from exc
    return resolved_path


def _validate_tree(root: Path) -> Path:
    """Reject links, reparse points, and special files in an artifact tree."""
    root = Path(root)
    if not root.is_dir():
        raise GraphifyGenerationError(f"Artifact tree is not a directory: {root}")
    if _is_link_or_reparse(root):
        raise GraphifyGenerationError("Artifact tree root is linked or reparse-pointed")
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        if _is_link_or_reparse(current_path):
            raise GraphifyGenerationError("Artifact directory is linked or reparse-pointed")
        for directory in directories:
            child = current_path / directory
            if _is_link_or_reparse(child):
                raise GraphifyGenerationError("Artifact directory is linked or reparse-pointed")
            if not stat.S_ISDIR(child.lstat().st_mode):
                raise GraphifyGenerationError("Artifact directory is not a directory")
        for filename in filenames:
            child = current_path / filename
            if _is_link_or_reparse(child):
                raise GraphifyGenerationError("Artifact file is linked or reparse-pointed")
            if not stat.S_ISREG(child.lstat().st_mode):
                raise GraphifyGenerationError("Artifact tree contains a special file")
    return root.resolve()


def _validate_required_artifacts(root: Path) -> None:
    """Require the complete artifact subset consumed by freshness and MCP checks."""
    for name in REQUIRED_ARTIFACTS:
        path = root / name
        if not path.is_file() or _is_link_or_reparse(path):
            raise GraphifyGenerationError(f"Required Graphify artifact is missing or unsafe: {name}")


def artifact_tree_sha256(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """Return a stable Merkle-style digest for every regular file in an artifact tree."""
    tree = _validate_tree(root)
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in tree.rglob("*") if candidate.is_file()):
        if _is_link_or_reparse(path):
            raise GraphifyGenerationError("Artifact file is linked or reparse-pointed")
        relative = path.relative_to(tree).as_posix()
        if relative in exclude:
            continue
        encoded_relative = relative.encode("utf-8")
        digest.update(len(encoded_relative).to_bytes(8, "big"))
        digest.update(encoded_relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    """Best-effort flush a directory entry after an atomic replacement or removal."""
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write opaque bytes through a same-directory durable replacement transaction."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON through a same-directory durable replacement transaction."""
    serialized = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(path, serialized)


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy a validated artifact tree without following links or preserving links."""
    source = _validate_tree(source)
    _validate_required_artifacts(source)
    if destination.exists():
        raise GraphifyGenerationError(f"Destination already exists: {destination}")
    destination.mkdir(parents=True)
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        target_dir = destination / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for directory in directories:
            (target_dir / directory).mkdir(exist_ok=False)
        for filename in filenames:
            source_file = current_path / filename
            target_file = target_dir / filename
            if _is_link_or_reparse(source_file) or not stat.S_ISREG(source_file.lstat().st_mode):
                raise GraphifyGenerationError("Artifact tree changed while being copied")
            shutil.copyfile(source_file, target_file)
    _validate_tree(destination)
    _validate_required_artifacts(destination)


def _make_tree_read_only(tree: Path) -> None:
    """Mark every published generation file read-only before it becomes selectable."""
    root = _validate_tree(tree)
    directories: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            directories.append(path)
            continue
        if not path.is_file() or _is_link_or_reparse(path):
            raise GraphifyGenerationError("Generation contains an unsafe artifact path")
        path.chmod(stat.S_IREAD)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(stat.S_IREAD | stat.S_IEXEC)
    root.chmod(stat.S_IREAD | stat.S_IEXEC)


def _validate_generation_id(generation_id: str) -> str:
    """Validate an identifier before using it as an immutable directory name."""
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise GraphifyGenerationError("Generation ID is invalid")
    return generation_id


def _validate_source_fingerprint(source_fingerprint: Mapping[str, str]) -> dict[str, str]:
    """Validate the source hash map recorded with a candidate generation."""
    if not isinstance(source_fingerprint, Mapping):
        raise GraphifyGenerationError("Source fingerprint must be a mapping")
    normalized: dict[str, str] = {}
    for path, digest in source_fingerprint.items():
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise GraphifyGenerationError("Source fingerprint path is unsafe")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise GraphifyGenerationError("Source fingerprint hash is invalid")
        normalized[path.replace("\\", "/")] = digest
    if not normalized:
        raise GraphifyGenerationError("Source fingerprint must not be empty")
    return dict(sorted(normalized.items()))


def _validate_snapshot_path(snapshot_path: str) -> str:
    """Validate the Git-visible immutable snapshot reference stored in acceptance data."""
    candidate = Path(snapshot_path)
    if not isinstance(snapshot_path, str) or not snapshot_path or candidate.is_absolute() or ".." in candidate.parts:
        raise GraphifyGenerationError("Snapshot path is unsafe")
    return candidate.as_posix()


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """Load a JSON object while rejecting links and malformed JSON payloads."""
    if not path.is_file() or _is_link_or_reparse(path):
        raise GraphifyGenerationError(f"{label} is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GraphifyGenerationError(f"{label} is malformed") from exc
    if not isinstance(payload, dict):
        raise GraphifyGenerationError(f"{label} must be a JSON object")
    return payload


def _selection_from_pointer(project_root: Path) -> GenerationSelection:
    """Resolve and verify the immutable generation named by the current pointer."""
    root = Path(project_root).resolve()
    graph_dir = root / GRAPH_DIR_NAME
    if _is_link_or_reparse(graph_dir):
        raise GraphifyGenerationError("Graphify artifact directory is linked or reparse-pointed")
    pointer = _load_json_object(graph_dir / CURRENT_POINTER_NAME, label="Current generation pointer")
    if pointer.get("schema") != POINTER_SCHEMA:
        raise GraphifyGenerationError("Current generation pointer schema is invalid")
    generation_id = _validate_generation_id(pointer.get("generation_id"))
    expected_tree_hash = pointer.get("artifact_tree_sha256")
    if not isinstance(expected_tree_hash, str) or not _SHA256.fullmatch(expected_tree_hash):
        raise GraphifyGenerationError("Current generation pointer hash is invalid")
    generation_dir = _contained(
        graph_dir / GENERATIONS_DIR_NAME,
        graph_dir / GENERATIONS_DIR_NAME / generation_id,
        label="Selected Graphify generation",
    )
    if not generation_dir.is_dir() or _is_link_or_reparse(generation_dir):
        raise GraphifyGenerationError("Selected Graphify generation is missing or unsafe")
    _validate_required_artifacts(generation_dir)
    acceptance = _load_json_object(generation_dir / ACCEPTANCE_NAME, label="Generation acceptance record")
    if acceptance.get("schema") != ACCEPTANCE_SCHEMA:
        raise GraphifyGenerationError("Generation acceptance schema is invalid")
    if acceptance.get("generation_id") != generation_id:
        raise GraphifyGenerationError("Generation acceptance ID does not match pointer")
    actual_tree_hash = artifact_tree_sha256(generation_dir, exclude=frozenset({ACCEPTANCE_NAME}))
    if actual_tree_hash != expected_tree_hash or acceptance.get("artifact_tree_sha256") != actual_tree_hash:
        raise GraphifyGenerationError("Selected Graphify generation tree hash does not match acceptance")
    source_fingerprint = _validate_source_fingerprint(acceptance.get("source_fingerprint", {}))
    snapshot_path = _validate_snapshot_path(acceptance.get("snapshot_path"))
    return GenerationSelection(
        generation_id=generation_id,
        generation_dir=generation_dir,
        graph_path=generation_dir / "graph.json",
        artifact_tree_sha256=actual_tree_hash,
        source_fingerprint=source_fingerprint,
        snapshot_path=snapshot_path,
    )


@contextmanager
def publication_lock(project_root: Path, *, shared: bool, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Acquire a kernel-backed reader or writer lock around pointer selection and publication."""
    root = Path(project_root).resolve()
    lock_path = root / GRAPH_DIR_NAME / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(lock_path):
        raise GraphifyGenerationError("Publication lock path is linked or reparse-pointed")
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
                    msvcrt.locking(handle.fileno(), mode, 1)
                else:
                    import fcntl

                    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                    fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise GraphifyGenerationError("Timed out waiting for Graphify publication lock") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def resolve_current_generation(project_root: Path, *, lock_held: bool = False) -> GenerationSelection:
    """Read the selected generation under a shared lock and verify its complete artifact set."""
    if lock_held:
        return _selection_from_pointer(project_root)
    with publication_lock(project_root, shared=True):
        return _selection_from_pointer(project_root)


def _current_pointer_bytes_unlocked(project_root: Path) -> bytes | None:
    """Read a valid current pointer exactly as it can later be restored atomically."""
    root = Path(project_root).resolve()
    pointer_path = root / GRAPH_DIR_NAME / CURRENT_POINTER_NAME
    if not pointer_path.exists():
        return None
    if _is_link_or_reparse(pointer_path) or not pointer_path.is_file():
        raise GraphifyGenerationError("Current generation pointer is unsafe")
    _selection_from_pointer(root)
    return pointer_path.read_bytes()


def current_pointer_bytes(project_root: Path, *, lock_held: bool = False) -> bytes | None:
    """Capture the valid atomic selector before a publication that may need rollback."""
    if lock_held:
        return _current_pointer_bytes_unlocked(project_root)
    with publication_lock(project_root, shared=True):
        return _current_pointer_bytes_unlocked(project_root)


def _restore_current_pointer_unlocked(project_root: Path, previous_pointer: bytes | None) -> None:
    """Restore a prior valid pointer or remove an initial pointer that did not exist."""
    root = Path(project_root).resolve()
    graph_dir = root / GRAPH_DIR_NAME
    pointer_path = graph_dir / CURRENT_POINTER_NAME
    if previous_pointer is None:
        if pointer_path.exists():
            if _is_link_or_reparse(pointer_path):
                raise GraphifyGenerationError("Current generation pointer is unsafe")
            pointer_path.unlink()
            _fsync_directory(graph_dir)
        return
    try:
        payload = json.loads(previous_pointer.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GraphifyGenerationError("Prior generation pointer is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != POINTER_SCHEMA:
        raise GraphifyGenerationError("Prior generation pointer schema is invalid")
    _validate_generation_id(payload.get("generation_id"))
    expected_hash = payload.get("artifact_tree_sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        raise GraphifyGenerationError("Prior generation pointer hash is invalid")
    _atomic_write_bytes(pointer_path, previous_pointer)
    _selection_from_pointer(root)


def restore_current_pointer(
    project_root: Path,
    previous_pointer: bytes | None,
    *,
    lock_held: bool = False,
) -> None:
    """Recover a prior complete selection using the same atomic pointer mechanism."""
    if lock_held:
        _restore_current_pointer_unlocked(project_root, previous_pointer)
        return
    with publication_lock(project_root, shared=False):
        _restore_current_pointer_unlocked(project_root, previous_pointer)


def _publish_generation_unlocked(
    project_root: Path,
    candidate_artifact_dir: Path,
    *,
    generation_id: str,
    source_fingerprint: Mapping[str, str],
    snapshot_path: str,
) -> GenerationSelection:
    """Stage a validated candidate, atomically select it, and leave legacy artifacts unchanged."""
    root = Path(project_root).resolve()
    generation_id = _validate_generation_id(generation_id)
    fingerprint = _validate_source_fingerprint(source_fingerprint)
    snapshot_reference = _validate_snapshot_path(snapshot_path)
    graph_dir = root / GRAPH_DIR_NAME
    graph_dir.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(graph_dir):
        raise GraphifyGenerationError("Graphify artifact directory is linked or reparse-pointed")
    candidate = Path(candidate_artifact_dir).resolve()
    try:
        candidate.relative_to(graph_dir.resolve())
    except ValueError:
        pass
    else:
        raise GraphifyGenerationError("Candidate artifact must be isolated from the served Graphify directory")
    _validate_tree(candidate)
    _validate_required_artifacts(candidate)
    generation_root = graph_dir / GENERATIONS_DIR_NAME
    staging_root = graph_dir / STAGING_DIR_NAME
    generation_root.mkdir(exist_ok=True)
    staging_root.mkdir(exist_ok=True)
    if os.stat(generation_root).st_dev != os.stat(staging_root).st_dev:
        raise GraphifyGenerationError("Graphify staging and generation directories are on different volumes")
    final_generation = generation_root / generation_id
    if final_generation.exists():
        raise GraphifyGenerationError("Generation ID already exists and immutable generations cannot be replaced")
    staging_generation = staging_root / f"{generation_id}-{uuid.uuid4().hex}"
    try:
        _copy_tree(candidate, staging_generation)
        tree_hash = artifact_tree_sha256(staging_generation)
        acceptance = {
            "schema": ACCEPTANCE_SCHEMA,
            "generation_id": generation_id,
            "accepted_at": _utc_now(),
            "artifact_tree_sha256": tree_hash,
            "source_fingerprint": fingerprint,
            "snapshot_path": snapshot_reference,
        }
        _atomic_write_json(staging_generation / ACCEPTANCE_NAME, acceptance)
        if artifact_tree_sha256(staging_generation, exclude=frozenset({ACCEPTANCE_NAME})) != tree_hash:
            raise GraphifyGenerationError("Candidate artifact tree changed before acceptance")
        _make_tree_read_only(staging_generation)
        os.replace(staging_generation, final_generation)
        pointer = {
            "schema": POINTER_SCHEMA,
            "generation_id": generation_id,
            "artifact_tree_sha256": tree_hash,
            "published_at": _utc_now(),
        }
        _atomic_write_json(graph_dir / CURRENT_POINTER_NAME, pointer)
    finally:
        if staging_generation.exists():
            shutil.rmtree(staging_generation, ignore_errors=True)
    return _selection_from_pointer(root)


SNAPSHOTS_RELATIVE = ("source", "graphify-candidates")
MIN_RETAINED_GENERATIONS = 2


def _force_rmtree(path: Path) -> None:
    """Remove a tree whose files were deliberately published read-only."""

    def _on_error(function: Any, failing_path: str, _exc: Any) -> None:
        os.chmod(failing_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(failing_path)

    shutil.rmtree(path, onerror=_on_error)


def _acceptance_snapshot(root: Path, generation_dir: Path, snapshots_root: Path) -> Path | None:
    """Return the generation's snapshot dir if it lives directly in the candidates dir."""
    acceptance_path = generation_dir / ACCEPTANCE_NAME
    if not acceptance_path.is_file():
        return None
    try:
        acceptance = _load_json_object(acceptance_path, label="Generation acceptance record")
        candidate = (root / _validate_snapshot_path(acceptance.get("snapshot_path"))).resolve()
    except GraphifyGenerationError:
        return None
    # Only ever delete snapshots that live directly in the candidates dir.
    if candidate.parent == snapshots_root.resolve() and not _is_link_or_reparse(candidate):
        return candidate
    return None


def _prune_generations_unlocked(project_root: Path, *, keep: int, dry_run: bool) -> dict[str, Any]:
    root = Path(project_root).resolve()
    selection = _selection_from_pointer(root)
    generation_root = root / GRAPH_DIR_NAME / GENERATIONS_DIR_NAME
    snapshots_root = root.joinpath(*SNAPSHOTS_RELATIVE)
    generations = sorted(
        p for p in generation_root.iterdir()
        if p.is_dir() and not _is_link_or_reparse(p) and _GENERATION_ID.fullmatch(p.name)
    )
    # Generation IDs start with a UTC timestamp, so name order is age order.
    retained = {p.name for p in generations[-keep:]} | {selection.generation_id}
    protected_snapshots = {
        snapshot
        for p in generations
        if p.name in retained
        for snapshot in [_acceptance_snapshot(root, p, snapshots_root)]
        if snapshot is not None
    }
    removed: list[str] = []
    removed_snapshots: list[str] = []
    for generation_dir in generations:
        if generation_dir.name in retained:
            continue
        removed.append(generation_dir.name)
        snapshot = _acceptance_snapshot(root, generation_dir, snapshots_root)
        if snapshot in protected_snapshots:
            snapshot = None
        if snapshot is not None and snapshot.is_dir():
            removed_snapshots.append(snapshot.name)
        if dry_run:
            continue
        _force_rmtree(generation_dir)
        if snapshot is not None and snapshot.is_dir():
            _force_rmtree(snapshot)
    if not dry_run and removed:
        _selection_from_pointer(root)
    return {
        "schema": "graphify-generation-prune.v1",
        "dry_run": dry_run,
        "keep": keep,
        "selected_generation": selection.generation_id,
        "retained_generations": sorted(retained),
        "removed_generations": removed,
        "removed_snapshots": removed_snapshots,
    }


def prune_generations(project_root: Path, *, keep: int = 3, dry_run: bool = False) -> dict[str, Any]:
    """Delete all but the newest ``keep`` generations under the writer lock.

    The currently selected generation is always retained, and at least two
    generations are kept so a pointer rollback target always exists. The
    pointer is re-verified before and after deletion; an invalid pointer
    fails closed before anything is removed.
    """
    if not isinstance(keep, int) or keep < MIN_RETAINED_GENERATIONS:
        raise GraphifyGenerationError(f"keep must be an integer >= {MIN_RETAINED_GENERATIONS}")
    with publication_lock(project_root, shared=False):
        return _prune_generations_unlocked(project_root, keep=keep, dry_run=dry_run)


def publish_generation(
    project_root: Path,
    candidate_artifact_dir: Path,
    *,
    generation_id: str,
    source_fingerprint: Mapping[str, str],
    snapshot_path: str,
    lock_held: bool = False,
) -> GenerationSelection:
    """Publish one complete isolated candidate through a single atomic selector replacement."""
    if lock_held:
        return _publish_generation_unlocked(
            project_root,
            candidate_artifact_dir,
            generation_id=generation_id,
            source_fingerprint=source_fingerprint,
            snapshot_path=snapshot_path,
        )
    with publication_lock(project_root, shared=False):
        return _publish_generation_unlocked(
            project_root,
            candidate_artifact_dir,
            generation_id=generation_id,
            source_fingerprint=source_fingerprint,
            snapshot_path=snapshot_path,
        )
