#!/usr/bin/env python3
"""Cron registration validator.

Verifies that the operational cron jobs registered in the Hermes scheduler
map to existing workspace scripts. This is intentionally separate from the
job scheduler so it can be run by tests and by cron_health_check.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_SCRIPTS = Path.home() / "AppData" / "Local" / "hermes" / "scripts"

EXPECTED_JOBS = [
    ("a1_wiki_regen.py", PROJECT_ROOT / "scripts" / "cron_wiki_regen.py"),
    ("a2_green_gate.py", PROJECT_ROOT / "scripts" / "cron_health_check.py"),
    ("a2_full_test_gate.py", PROJECT_ROOT / "scripts" / "cron_test_gate.py"),
    ("a3_routing_cache_sweep.py", PROJECT_ROOT / "scripts" / "cron_routing_cache_sweep.py"),
    ("a4_archive_stale_workflows.py", PROJECT_ROOT / "scripts" / "cron_archive_stale_workflows.py"),
    ("a5_routing_refresh.py", PROJECT_ROOT / "scripts" / "cron_routing_refresh.py"),
    ("a6_alias_sweep.py", PROJECT_ROOT / "scripts" / "cron_alias_sweep.py"),
    ("a7_queue_hygiene.py", PROJECT_ROOT / "scripts" / "cron_queue_hygiene.py"),
    ("a8_telemetry_harvest.py", PROJECT_ROOT / "scripts" / "cron_telemetry_harvest.py"),
    ("a9_claim_drift_check.py", PROJECT_ROOT / "scripts" / "cron_claim_drift_check.py"),
    ("a10_graph_freshness.py", PROJECT_ROOT / "scripts" / "cron_graph_freshness.py"),
    ("a11_workspace_status.py", PROJECT_ROOT / "scripts" / "workspace_status.py"),
    ("a12_retrieval_refresh.py", PROJECT_ROOT / "scripts" / "cron_retrieval_refresh.py"),
    ("a13_canonical_integrity.py", PROJECT_ROOT / "scripts" / "cron_canonical_integrity.py"),
]

DIRECT_REPO_JOBS: list[tuple[str, Path]] = []

_TARGET_RE = re.compile(r'TARGET\s*=\s*Path\(r"([^"]+)"\)')


def _resolve_wrapper_target(wrapper_path: Path) -> Path | None:
    if not wrapper_path.is_file():
        return None
    text = wrapper_path.read_text(encoding="utf-8")
    match = _TARGET_RE.search(text)
    if not match:
        return None
    return Path(match.group(1))


def validate_cron_registration() -> dict[str, Any]:
    """Return a validation report for cron job script targets."""
    failures: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []

    for wrapper_name, expected_target in EXPECTED_JOBS:
        wrapper_path = HERMES_SCRIPTS / wrapper_name
        resolved = _resolve_wrapper_target(wrapper_path)
        checked.append(
            {
                "job": wrapper_name,
                "wrapper_exists": wrapper_path.is_file(),
                "expected_target": str(expected_target),
                "resolved_target": str(resolved) if resolved else None,
                "target_exists": expected_target.is_file() if resolved else False,
            }
        )
        if not wrapper_path.is_file():
            failures.append({"job": wrapper_name, "code": "wrapper_missing", "message": f"Wrapper script missing: {wrapper_path}"})
        elif resolved != expected_target:
            failures.append(
                {
                    "job": wrapper_name,
                    "code": "target_mismatch",
                    "message": f"Expected target {expected_target}, got {resolved}",
                }
            )
        elif not expected_target.is_file():
            failures.append({"job": wrapper_name, "code": "target_missing", "message": f"Repo script missing: {expected_target}"})

    for job_name, repo_script in DIRECT_REPO_JOBS:
        checked.append(
            {
                "job": job_name,
                "expected_target": str(repo_script),
                "target_exists": repo_script.is_file(),
            }
        )
        if not repo_script.is_file():
            failures.append({"job": job_name, "code": "target_missing", "message": f"Repo script missing: {repo_script}"})

    return {
        "ok": len(failures) == 0,
        "checked": checked,
        "failures": failures,
    }


def main() -> int:
    report = validate_cron_registration()
    if report["ok"]:
        print("CRON REGISTRATION OK")
        return 0
    print("CRON REGISTRATION FAIL")
    for failure in report["failures"]:
        print(f"  {failure['job']}: {failure['code']} - {failure['message']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
