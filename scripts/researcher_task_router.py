#!/usr/bin/env python3
"""Admission gate and immutable staging for bounded Researcher work.

Only this trusted, parent-side utility may stage a source pack for the
``researcher`` profile. The profile gets no source-root parameter and cannot
alter a staged pack.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCHEMA = "researcher-task-request.v1"
PACK_SCHEMA = "researcher-source-pack.v1"
ARTIFACT_SCHEMA = "researcher-evidence-packet.v1"
ADMISSION_SCHEMA = "researcher-task-admission.v1"

# This is the authoritative routing allowlist. A task outside it never reaches
# the Researcher profile, even if its prompt asks for only observational work.
ALLOWED_TASK_CLASSES = frozenset(
    {
        "source_inventory",
        "test_discovery",
        "narrow_reproduction",
        "doc_conflict",
        "scoped_audit",
        "dependency_map",
        "acceptance_contract",
        "pre_mortem",
    }
)
_REQUIRED_REQUEST_FIELDS = {
    "schema",
    "task_id",
    "task_class",
    "phase",
    "mode",
    "objective",
    "source_files",
    "output_schema",
}
_TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ResearcherTaskError(ValueError):
    """Raised when a requested or staged task crosses the Researcher boundary."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ResearcherTaskError("source_files entries must be non-empty forward-slash paths")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ResearcherTaskError(f"source path escapes source root: {value}")
    return path


def _source_path(source_root: Path, value: object) -> Path:
    relative = _safe_relative_path(value)
    root = source_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ResearcherTaskError(f"source path escapes source root: {value}") from exc
    if not candidate.is_file():
        raise ResearcherTaskError(f"source file is unavailable: {value}")
    return candidate


def _validate_request_shape(request: object) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    if not isinstance(request, dict):
        return None, ["request must be a JSON object"]
    unexpected = sorted(set(request) - _REQUIRED_REQUEST_FIELDS)
    missing = sorted(_REQUIRED_REQUEST_FIELDS - set(request))
    if missing:
        reasons.append("missing required request fields: " + ", ".join(missing))
    if unexpected:
        reasons.append("unexpected request fields: " + ", ".join(unexpected))
    if request.get("schema") != REQUEST_SCHEMA:
        reasons.append("unsupported request schema")
    task_id = request.get("task_id")
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        reasons.append("task_id must be a lowercase stable identifier")
    task_class = request.get("task_class")
    if not isinstance(task_class, str) or task_class not in ALLOWED_TASK_CLASSES:
        reasons.append("task_class is not allowlisted")
    if request.get("phase") != "pre-implementation":
        reasons.append("phase must be pre-implementation")
    if request.get("mode") != "read-only":
        reasons.append("mode must be read-only")
    objective = request.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        reasons.append("objective must be a non-empty string")
    if request.get("output_schema") != ARTIFACT_SCHEMA:
        reasons.append("output_schema must be researcher-evidence-packet.v1")
    source_files = request.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        reasons.append("source_files must be a non-empty list")
    elif len(set(source_files)) != len(source_files):
        reasons.append("source_files must not contain duplicates")
    else:
        for item in source_files:
            try:
                _safe_relative_path(item)
            except ResearcherTaskError as exc:
                reasons.append(str(exc))
    return request, reasons


def admit_request(request: object, source_root: Path) -> dict[str, Any]:
    """Validate task class, write boundary, and exact source scope before staging."""
    normalized, reasons = _validate_request_shape(request)
    if normalized is not None and not reasons:
        for source_file in normalized["source_files"]:
            try:
                _source_path(source_root, source_file)
            except ResearcherTaskError as exc:
                reasons.append(str(exc))
    task_id = normalized.get("task_id") if normalized else None
    task_class = normalized.get("task_class") if normalized else None
    return {
        "schema": ADMISSION_SCHEMA,
        "status": "admitted" if not reasons else "rejected",
        "task_id": task_id,
        "task_class": task_class,
        "reasons": reasons,
    }


def _pack_fingerprint(manifest_without_fingerprint: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(manifest_without_fingerprint).encode("utf-8"))


def stage_request(request: object, source_root: Path, active_root: Path) -> dict[str, Any]:
    """Copy only admitted sources into a fresh immutable pack and atomically activate it."""
    admission = admit_request(request, source_root)
    if admission["status"] != "admitted":
        raise ResearcherTaskError("request rejected: " + "; ".join(admission["reasons"]))
    assert isinstance(request, dict)  # Established by admit_request.

    active = Path(active_root).resolve()
    active.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{active.name}.staging-", dir=active.parent))
    try:
        sources_dir = staging / "sources"
        entries: list[dict[str, Any]] = []
        for source_name in request["source_files"]:
            original = _source_path(source_root, source_name)
            destination = sources_dir / _safe_relative_path(source_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            payload = destination.read_bytes()
            entries.append(
                {
                    "path": source_name,
                    "sha256": _sha256_bytes(payload),
                    "line_count": len(payload.decode("utf-8").splitlines()),
                }
            )

        manifest: dict[str, Any] = {
            "schema": PACK_SCHEMA,
            "task_id": request["task_id"],
            "task_class": request["task_class"],
            "phase": request["phase"],
            "mode": request["mode"],
            "objective": request["objective"],
            "output_schema": request["output_schema"],
            "source_files": entries,
        }
        manifest["source_pack_sha256"] = _pack_fingerprint(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        backup = active.parent / f".{active.name}.previous"
        if backup.exists():
            shutil.rmtree(backup)
        if active.exists():
            os.replace(active, backup)
        os.replace(staging, active)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "schema": ADMISSION_SCHEMA,
        "status": "staged",
        "task_id": request["task_id"],
        "task_class": request["task_class"],
        "active_root": str(active),
        "source_pack_sha256": manifest["source_pack_sha256"],
        "source_count": len(entries),
        "reasons": [],
    }


def load_active_pack(active_root: Path) -> dict[str, Any]:
    """Load a staged pack only if its contract and every copied source hash still match."""
    active = Path(active_root).resolve()
    manifest_path = active / "manifest.json"
    if not manifest_path.is_file():
        raise ResearcherTaskError("no active source pack")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearcherTaskError("active pack manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != PACK_SCHEMA:
        raise ResearcherTaskError("active pack has unsupported schema")

    request_like = {
        "schema": REQUEST_SCHEMA,
        "task_id": manifest.get("task_id"),
        "task_class": manifest.get("task_class"),
        "phase": manifest.get("phase"),
        "mode": manifest.get("mode"),
        "objective": manifest.get("objective"),
        "source_files": [entry.get("path") for entry in manifest.get("source_files", []) if isinstance(entry, dict)],
        "output_schema": manifest.get("output_schema"),
    }
    _normalized, reasons = _validate_request_shape(request_like)
    if reasons:
        raise ResearcherTaskError("active pack violates task contract: " + "; ".join(reasons))

    expected_fingerprint = manifest.get("source_pack_sha256")
    fingerprint_input = dict(manifest)
    fingerprint_input.pop("source_pack_sha256", None)
    if not isinstance(expected_fingerprint, str) or _pack_fingerprint(fingerprint_input) != expected_fingerprint:
        raise ResearcherTaskError("active pack manifest fingerprint mismatch")

    sources_root = active / "sources"
    entries = manifest.get("source_files")
    if not isinstance(entries, list):
        raise ResearcherTaskError("active pack source_files is invalid")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ResearcherTaskError("active pack source entry is invalid")
        path = entry.get("path")
        if not isinstance(path, str) or path in seen:
            raise ResearcherTaskError("active pack source entries are invalid")
        seen.add(path)
        copied = _source_path(sources_root, path)
        if entry.get("sha256") != _sha256_bytes(copied.read_bytes()):
            raise ResearcherTaskError(f"active pack source hash mismatch: {path}")
    manifest["sources_root"] = sources_root
    return manifest


def _read_request(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearcherTaskError(f"request is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ResearcherTaskError("request must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("admit", "stage"):
        command = subcommands.add_parser(name)
        command.add_argument("--request", type=Path, required=True)
        command.add_argument("--source-root", type=Path, required=True)
        if name == "stage":
            command.add_argument(
                "--active-root", type=Path, default=PROJECT_ROOT / "tmp" / "researcher-active"
            )
    arguments = parser.parse_args(argv)
    try:
        request = _read_request(arguments.request)
        if arguments.command == "admit":
            result = admit_request(request, arguments.source_root)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "admitted" else 2
        result = stage_request(request, arguments.source_root, arguments.active_root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ResearcherTaskError as exc:
        print(json.dumps({"schema": ADMISSION_SCHEMA, "status": "rejected", "reasons": [str(exc)]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
