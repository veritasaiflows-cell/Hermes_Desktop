#!/usr/bin/env python3
"""Universal Wiki bootstrap validation and atomic publish utilities.

This module keeps the workspace aligned with the bootstrap contract in
`prompts/hermes-trustworthy-work-operating-model-prompt.md` and the local
control-plane expectations.

The validator checks:
- required Wiki pages exist
- each required page carries required metadata markers
- source-map links and source artifacts resolve
- source hashes stay in sync with current workspace state
- freshness windows are respected
- forbidden authority-language is not introduced
- last-known-good (LKG) manifest is available
- publishing is atomic and keeps rollback material in `.lkg`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
import re
import tempfile
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WIKI_ROOT = PROJECT_ROOT / "wiki"
DEFAULT_MANIFEST_PATH = DEFAULT_WIKI_ROOT / "bootstrap-manifest.json"
DEFAULT_LKG_MANIFEST_PATH = DEFAULT_WIKI_ROOT / ".lkg" / "bootstrap-manifest.json"
MANIFEST_SCHEMA = "wiki-bootstrap-manifest.v1"

REQUIRED_PAGE_MARKERS = (
    "page_type",
    "owner",
    "status",
    "generated_time",
    "source_artifacts",
    "source_hashes",
    "freshness_rule",
    "authority_boundary",
    "promotion_path",
    "warnings",
    "next_action",
)

FORBIDDEN_PHRASES = (
    "unlimited authority",
    "I can do anything",
    "I can override",
    "no guardrail",
    "no human",
    "full control",
    "absolute control",
)

FRESHNESS_WINDOW_SECONDS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
    "monthly": 31 * 86400,
    "default": 7 * 86400,
}


@dataclass
class WikiReport:
    status: str
    manifest_path: str
    generated_at: str | None = None
    manifest_schema: str | None = None
    lkg_manifest: str | None = None
    lkg_present: bool = False
    issues: list[dict[str, Any]] = field(default_factory=list)
    page_results: list[dict[str, Any]] = field(default_factory=list)
    changed_sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "manifest_path": self.manifest_path,
            "manifest_schema": self.manifest_schema,
            "generated_at": self.generated_at,
            "lkg_manifest": self.lkg_manifest,
            "lkg_present": self.lkg_present,
            "issues": self.issues,
            "pages": self.page_results,
            "changed_sources": self.changed_sources,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sha256_bytes(payload: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(payload)
    return hasher.hexdigest()


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_forbidden(text: str) -> str | None:
    normalized = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in normalized:
            return phrase
    return None


def _parse_markdown_metadata(content: str) -> dict[str, Any]:
    """Parse a tiny metadata convention used in wiki pages.

    Accepts lines like:

    - key: value
    - key:
      - item
    """
    metadata: dict[str, Any] = {}
    current_list: str | None = None

    line_key_re = re.compile(r"^\s*-\s*([a-z_]+)\s*:\s*(.*)$", re.IGNORECASE)
    list_re = re.compile(r"^\s{2,}-\s*(.+)$")

    for raw_line in content.splitlines():
        line = raw_line.strip("\n")
        if not line:
            continue

        match = line_key_re.match(line)
        if match:
            key = match.group(1).lower()
            value = match.group(2).strip()
            if value:
                metadata[key] = value
                current_list = None
            else:
                if key in {"source_artifacts", "source_map", "source_hashes"}:
                    metadata[key] = [] if key != "source_hashes" else {}
                else:
                    metadata[key] = ""
                current_list = key
            continue

        list_match = list_re.match(raw_line)
        if list_match and current_list:
            item = list_match.group(1).strip()
            if current_list == "source_hashes":
                if ":" not in item:
                    continue
                name, _, digest = item.partition(":")
                source_hashes = metadata.setdefault("source_hashes", {})
                source_hashes[name.strip()] = digest.strip()
                continue

            items = metadata.setdefault(current_list, [])
            if isinstance(items, list):
                items.append(item)
            continue

        if current_list:
            current_list = None

    return metadata


def _required_page_order(project_root: Path) -> list[str]:
    return [
        "wiki/index.md",
        "wiki/source-map/ownership.md",
        "wiki/syntheses/system-map.md",
        "wiki/decisions/initial.md",
        "wiki/gaps/open-gaps.md",
        "wiki/changes/change-log.md",
    ]


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest is not a JSON object")
    return payload


def _validate_source_list(
    metadata: dict[str, Any],
    manifest_entry: dict[str, Any],
    project_root: Path,
    page_path: str,
    base: Path,
    report: WikiReport,
    page_report: dict[str, Any],
    changed_sources: list[dict[str, Any]],
) -> None:
    source_artifacts = metadata.get("source_artifacts", manifest_entry.get("source_artifacts", []))
    if isinstance(source_artifacts, str):
        source_artifacts = [source_artifacts]

    if not source_artifacts:
        report.issues.append(
            {
                "page": page_path,
                "type": "missing_source_artifacts",
                "detail": "No source artifacts declared",
            }
        )
        page_report["status"] = "stale"

    source_map = metadata.get("source_map", manifest_entry.get("source_map", []))
    if isinstance(source_map, str):
        source_map = [source_map]

    if not source_map:
        report.issues.append(
            {
                "page": page_path,
                "type": "missing_source_map",
                "detail": "No source-map links declared",
            }
        )
        page_report["status"] = "stale"

    for artifact in source_artifacts:
        if not isinstance(artifact, str):
            continue
        artifact_path = Path(artifact.strip())
        absolute = (project_root / artifact_path).resolve()
        if not absolute.exists():
            report.issues.append(
                {
                    "page": page_path,
                    "type": "missing_source_artifact",
                    "detail": artifact,
                }
            )
            page_report["status"] = "stale"
            continue

        if absolute.resolve().as_posix().startswith((project_root / "wiki").resolve().as_posix() + "/"):
            report.issues.append(
                {
                    "page": page_path,
                    "type": "self_source_reference",
                    "detail": artifact,
                }
            )
            page_report["status"] = "stale"

        manifest_hash = manifest_entry.get("source_hashes", {}).get(str(artifact_path), manifest_entry.get("source_hashes", {}).get(artifact_path.as_posix()))
        current_hash = file_sha256(absolute)
        if manifest_hash is None:
            changed_sources.append(
                {
                    "page": page_path,
                    "path": artifact,
                    "reason": "missing_in_manifest",
                    "manifest": None,
                    "current": current_hash,
                }
            )
            report.issues.append(
                {
                    "page": page_path,
                    "type": "source_hash_missing_in_manifest",
                    "detail": artifact,
                }
            )
            page_report["status"] = "stale"
        elif str(manifest_hash).strip().lower() == "pending":
            continue
        elif str(manifest_hash) != current_hash:
            changed_sources.append(
                {
                    "page": page_path,
                    "path": artifact,
                    "reason": "hash_mismatch",
                    "manifest": manifest_hash,
                    "current": current_hash,
                }
            )
            report.issues.append(
                {
                    "page": page_path,
                    "type": "source_hash_mismatch",
                    "detail": {
                        "path": artifact,
                        "manifest": manifest_hash,
                        "current": current_hash,
                    },
                }
            )
            page_report["status"] = "stale"

    for linked_source in source_map:
        if not isinstance(linked_source, str):
            continue
        link_path = project_root / linked_source.strip()
        if not link_path.exists():
            report.issues.append(
                {
                    "page": page_path,
                    "type": "missing_source_map_link",
                    "detail": linked_source,
                }
            )
            page_report["status"] = "stale"


def _check_freshness(metadata: dict[str, Any], page_path: str, report: WikiReport, page_report: dict[str, Any]) -> None:
    generated_raw = str(metadata.get("generated_time", "")).strip()
    freshness_raw = str(metadata.get("freshness_rule", "")).strip().lower()
    if not generated_raw:
        report.issues.append(
            {
                "page": page_path,
                "type": "missing_generated_time",
                "detail": "generated_time missing",
            }
        )
        page_report["status"] = "stale"
        return

    try:
        generated = _to_iso(generated_raw)
    except ValueError:
        report.issues.append(
            {
                "page": page_path,
                "type": "invalid_generated_time",
                "detail": generated_raw,
            }
        )
        page_report["status"] = "stale"
        return

    window = timedelta(seconds=FRESHNESS_WINDOW_SECONDS.get(freshness_raw, FRESHNESS_WINDOW_SECONDS["default"]))
    if datetime.now(timezone.utc) - generated > window:
        report.issues.append(
            {
                "page": page_path,
                "type": "stale_freshness",
                "detail": f"generated_time older than {freshness_raw or 'default'} freshness window",
            }
        )
        page_report["status"] = "stale"


def _check_metadata_markers(metadata: dict[str, Any], page_path: str, report: WikiReport, page_report: dict[str, Any]) -> None:
    for marker in REQUIRED_PAGE_MARKERS:
        if marker not in metadata:
            report.issues.append(
                {
                    "page": page_path,
                    "type": "missing_marker",
                    "detail": marker,
                }
            )
            page_report["status"] = "stale"
            continue

        if not isinstance(metadata[marker], (str, list, dict)):
            continue

        value = metadata[marker]
        if isinstance(value, str) and not value.strip():
            report.issues.append(
                {
                    "page": page_path,
                    "type": "empty_marker",
                    "detail": marker,
                }
            )
            page_report["status"] = "stale"


def _check_forbidden(content: str, page_path: str, report: WikiReport, page_report: dict[str, Any]) -> None:
    phrase = _is_forbidden(content)
    if phrase:
        report.issues.append(
            {
                "page": page_path,
                "type": "forbidden_authority_language",
                "detail": phrase,
            }
        )
        page_report["status"] = "stale"


def _build_candidate_manifest(project_root: Path, strict: bool = False) -> dict[str, Any]:
    wiki_root = project_root / "wiki"
    required_pages = _required_page_order(project_root)
    pages: dict[str, Any] = {}

    for page in required_pages:
        page_path = project_root / page
        if not page_path.exists():
            if strict:
                raise FileNotFoundError(f"Missing required page: {page}")
            continue

        metadata = _parse_markdown_metadata(page_path.read_text(encoding="utf-8"))
        source_artifacts = metadata.get("source_artifacts", [])
        if isinstance(source_artifacts, str):
            source_artifacts = [source_artifacts]

        source_map = metadata.get("source_map", [])
        if isinstance(source_map, str):
            source_map = [source_map]

        source_hashes: dict[str, str] = {}
        for artifact in source_artifacts:
            if not artifact:
                continue
            absolute = (project_root / artifact).resolve()
            if not absolute.exists():
                if strict:
                    raise FileNotFoundError(f"Source artifact missing: {artifact}")
                source_hashes[artifact] = "missing"
                continue
            source_hashes[artifact] = file_sha256(absolute)

        page_payload = {
            "source_artifacts": source_artifacts,
            "source_map": source_map,
            "source_hashes": source_hashes,
            "freshness_rule": metadata.get("freshness_rule", "weekly"),
            "status": metadata.get("status", "current"),
        }
        pages[page] = page_payload

    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _utc_now(),
        "generated_by": "scripts/wiki_bootstrap.py",
        "required_pages": required_pages,
        "pages": pages,
        "lkg_manifest": "wiki/.lkg/bootstrap-manifest.json",
    }


def _validate_manifest_payload(payload: dict[str, Any], project_root: Path, *, require_lkg: bool = True) -> WikiReport:
    manifest_path = project_root / "wiki" / "bootstrap-manifest.json"
    report = WikiReport(
        status="fresh",
        manifest_path=str(manifest_path.as_posix()),
        generated_at=None,
        manifest_schema=payload.get("schema"),
        lkg_manifest=str((project_root / "wiki" / ".lkg" / "bootstrap-manifest.json").as_posix()),
        lkg_present=(project_root / "wiki" / ".lkg" / "bootstrap-manifest.json").exists(),
    )

    if payload.get("schema") != MANIFEST_SCHEMA:
        report.issues.append(
            {
                "type": "schema_mismatch",
                "detail": payload.get("schema"),
            }
        )
        report.status = "stale"

    report.generated_at = payload.get("generated_at")

    required_pages = _required_page_order(project_root)
    manifest_required = payload.get("required_pages", [])
    if manifest_required != required_pages:
        report.issues.append(
            {
                "type": "required_pages_mismatch",
                "detail": {
                    "manifest": manifest_required,
                    "expected": required_pages,
                },
            }
        )
        report.status = "stale"

    page_payloads = payload.get("pages", {})
    if not isinstance(page_payloads, dict):
        report.issues.append({"type": "invalid_pages_field", "detail": "pages must be an object"})
        report.status = "stale"
        return report

    for required in required_pages:
        page_report = {"page": required, "status": "fresh"}
        page_path = (project_root / required)

        if not page_path.exists():
            report.issues.append({"type": "missing_required_page", "page": required})
            report.status = "stale"
            page_report["status"] = "stale"
            report.page_results.append(page_report)
            continue

        manifest_entry = page_payloads.get(required, {})
        if not isinstance(manifest_entry, dict):
            report.issues.append(
                {
                    "page": required,
                    "type": "manifest_entry_not_object",
                    "detail": required,
                }
            )
            report.status = "stale"
            page_report["status"] = "stale"
            report.page_results.append(page_report)
            continue

        content = page_path.read_text(encoding="utf-8")
        metadata = _parse_markdown_metadata(content)

        _check_forbidden(content, required, report, page_report)
        _check_metadata_markers(metadata, required, report, page_report)
        _validate_source_list(
            metadata=metadata,
            manifest_entry=manifest_entry,
            project_root=project_root,
            page_path=required,
            base=project_root,
            report=report,
            page_report=page_report,
            changed_sources=report.changed_sources,
        )
        _check_freshness(metadata, required, report, page_report)

        report.page_results.append(page_report)

    # Require LKG manifest for recovery in runtime validation.
    if require_lkg and not report.lkg_present:
        report.issues.append(
            {
                "type": "missing_lkg_manifest",
                "detail": "wiki/.lkg/bootstrap-manifest.json missing",
            }
        )
        report.status = "stale"

    if report.issues:
        report.status = "stale"

    return report


def validate_wiki(*, project_root: Path = PROJECT_ROOT, manifest_path: Path | None = None) -> dict[str, Any]:
    project_root = Path(project_root)
    if manifest_path is None:
        manifest_path = project_root / "wiki" / "bootstrap-manifest.json"

    if not manifest_path.exists():
        report = WikiReport(status="stale", manifest_path=str(manifest_path.as_posix()), lkg_present=False)
        report.issues.append(
            {"type": "manifest_missing", "detail": str(manifest_path)}
        )
        return report.to_dict()

    try:
        payload = _load_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        report = WikiReport(status="stale", manifest_path=str(manifest_path.as_posix()), lkg_present=False)
        report.issues.append({"type": "manifest_unreadable", "detail": str(error)})
        return report.to_dict()

    report = _validate_manifest_payload(payload, project_root, require_lkg=True)
    return report.to_dict()


def publish_wiki(*, project_root: Path = PROJECT_ROOT, manifest_path: Path | None = None) -> dict[str, Any]:
    project_root = Path(project_root)
    wiki_root = project_root / "wiki"
    if manifest_path is None:
        manifest_path = wiki_root / "bootstrap-manifest.json"

    candidate = _build_candidate_manifest(project_root=project_root, strict=True)
    report = _validate_manifest_payload(candidate, project_root, require_lkg=False)

    if report.status != "fresh":
        raise RuntimeError(f"Cannot publish: generated wiki is stale ({report.issues})")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lkg_root = wiki_root / ".lkg"
    lkg_root.mkdir(parents=True, exist_ok=True)
    lkg_manifest = lkg_root / "bootstrap-manifest.json"

    old_manifest = manifest_path
    if old_manifest.exists():
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=lkg_root) as handle:
            handle.write(old_manifest.read_bytes())
            staged_backup = Path(handle.name)
        timestamped_backup = lkg_root / (
            "bootstrap-manifest.{0}Z.json".format(_utc_now().replace(":", ""))
        )
        staged_backup.replace(timestamped_backup)
        shutil.copy2(timestamped_backup, lkg_manifest)
    else:
        # First publish: bootstrap LKG with current candidate so the check passes
        # immediately on subsequent startup validation.
        lkg_manifest.write_text(json.dumps(candidate, indent=2, sort_keys=True), encoding="utf-8")

    payload_text = json.dumps(candidate, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=manifest_path.parent) as handle:
        handle.write(payload_text)
        staged = Path(handle.name)
    staged.replace(manifest_path)

    report = validate_wiki(project_root=project_root, manifest_path=manifest_path)
    if not report["changed_sources"]:
        return report

    # Keep changed source records available to call-sites that may want to refresh only
    # impacted pages.
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("validate", "publish"),
        default="validate",
        nargs="?",
        help="Wiki bootstrap action to run",
    )
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root containing the wiki directory.",
    )
    parser.add_argument("--manifest-path", default="", help="Custom manifest file path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    project_root = Path(args.project_root)
    manifest_path = Path(args.manifest_path) if args.manifest_path else project_root / "wiki" / "bootstrap-manifest.json"

    if args.action == "validate":
        result = validate_wiki(project_root=project_root, manifest_path=manifest_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "fresh" else 2

    try:
        result = publish_wiki(project_root=project_root, manifest_path=manifest_path)
    except Exception as exc:  # pragma: no cover - CLI guard path
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
