"""Deterministic source fingerprints for code-correctness telemetry."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

CORRECTNESS_ROOTS = ("canonical", "scripts", "tests")
CORRECTNESS_SUFFIXES = {".py", ".sql"}
CORRECTNESS_FILES = (
    "requirements-dev.txt",
    "pyproject.toml",
    "pytest.ini",
)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _validate_source_path(project_root: Path, path: Path) -> Path:
    if _is_link_or_reparse(path):
        raise ValueError(f"Correctness source path is linked or reparse-pointed: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Correctness source path is outside workspace: {path}") from exc
    return resolved


def correctness_sources(project_root: Path) -> list[Path]:
    """Return the source files whose content determines test-result currency."""
    project_root = Path(project_root).resolve()
    paths: set[Path] = set()
    for name in CORRECTNESS_ROOTS:
        root = project_root / name
        if not root.exists():
            continue
        _validate_source_path(project_root, root)
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            _validate_source_path(project_root, current_path)
            for directory in list(directories):
                child = current_path / directory
                if directory == "__pycache__":
                    directories.remove(directory)
                    continue
                _validate_source_path(project_root, child)
            for filename in filenames:
                path = current_path / filename
                if path.suffix.lower() in CORRECTNESS_SUFFIXES:
                    paths.add(_validate_source_path(project_root, path))
    for relative in CORRECTNESS_FILES:
        path = project_root / relative
        if path.is_file():
            paths.add(_validate_source_path(project_root, path))
    return sorted(paths, key=lambda path: path.relative_to(project_root).as_posix())


def correctness_snapshot(project_root: Path) -> dict[str, Any]:
    """Hash source paths and contents into a stable workspace fingerprint."""
    project_root = Path(project_root).resolve()
    digest = hashlib.sha256()
    paths = correctness_sources(project_root)
    for path in paths:
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return {
        "source_fingerprint": digest.hexdigest(),
        "source_file_count": len(paths),
        "tested_commit": git_commit(project_root),
    }


def git_commit(project_root: Path) -> str | None:
    """Return HEAD for a Git workspace, or None outside Git."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(project_root).resolve()),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None
