#!/usr/bin/env python3
"""Validate the governed workspace directory and retention contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_LAYER_READMES = (
    "source",
    "canonical",
    "graph",
    "vector",
    "derived",
    "telemetry",
    "state",
    "tmp",
)
FORBIDDEN_TMP_ARTIFACTS = (
    "vector-memory.sqlite",
    "workspace-index.sqlite",
    "workflow-routing-index.json",
    "canonical.db",
)


def validate_workspace_organization(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return deterministic placement-contract findings for ``project_root``."""
    root = Path(project_root).resolve()
    issues: list[dict[str, str]] = []
    checked: list[str] = []

    for layer in REQUIRED_LAYER_READMES:
        directory = root / layer
        readme = directory / "README.md"
        checked.extend((str(directory), str(readme)))
        if not directory.is_dir():
            issues.append(
                {
                    "code": "required_layer_missing",
                    "path": str(directory),
                    "message": f"Required workspace layer is missing: {layer}/",
                }
            )
        elif not readme.is_file():
            issues.append(
                {
                    "code": "layer_contract_missing",
                    "path": str(readme),
                    "message": f"Required layer contract is missing: {layer}/README.md",
                }
            )

    tmp_dir = root / "tmp"
    if tmp_dir.is_dir():
        for path in sorted(tmp_dir.iterdir()):
            if not path.is_file():
                continue
            if any(
                path.name == artifact or path.name.startswith(f"{artifact}-")
                for artifact in FORBIDDEN_TMP_ARTIFACTS
            ):
                issues.append(
                    {
                        "code": "forbidden_tmp_artifact",
                        "path": str(path),
                        "message": "Durable state or a live index must not be stored in tmp/.",
                    }
                )

    misplaced_canonical = root / "state" / "canonical.db"
    checked.append(str(misplaced_canonical))
    if misplaced_canonical.exists():
        issues.append(
            {
                "code": "canonical_database_misplaced",
                "path": str(misplaced_canonical),
                "message": "Canonical database belongs at canonical/efficiens.db, not state/canonical.db.",
            }
        )

    return {
        "schema": "workspace-organization.v1",
        "ok": not issues,
        "project_root": str(root),
        "checked": checked,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args()
    report = validate_workspace_organization(arguments.project_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
