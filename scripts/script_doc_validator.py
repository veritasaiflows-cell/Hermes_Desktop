#!/usr/bin/env python3
"""Validate script documentation coverage across the ``scripts/`` layer.

Deterministic gate that fails when a non-trivial script lacks a module
docstring, has no function docstrings, has no test anchor, or is never
mentioned in a markdown reference. This turns the one-off script-documentation
audit into a standing check so documentation debt cannot silently regrow.

Usage:
    python scripts/script_doc_validator.py
    python scripts/script_doc_validator.py --project-root <path>
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Scripts that are intentionally behavior-light and exempt from the
# function-docstring requirement (single-entrypoint wrappers still need a
# module docstring and a test anchor).
MIN_MODULE_DOC_WORDS = 5


def _module_doc_words(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return 0
    doc = ast.get_docstring(parsed) or ""
    return len(doc.strip().split())


def _function_doc_ratio(path: Path) -> float:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return 0.0
    funcs = [
        node
        for node in ast.walk(parsed)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not funcs:
        return 1.0
    documented = sum(1 for node in funcs if ast.get_docstring(node))
    return documented / len(funcs)


def _test_anchor(script_name: str, test_files: set[str]) -> bool:
    """Return True if a test file references the script by module name."""
    stem = script_name[:-3]
    if f"test_{stem}" in test_files:
        return True
    # Shared wrapper/aggregate test files cover several cron scripts.
    aggregate = {
        "cron_wiki_regen.py": "test_cron_wrappers",
        "cron_health_check.py": "test_cron_wrappers",
        "cron_test_gate.py": "test_cron_wrappers",
        "cron_routing_refresh.py": "test_cron_wrappers",
        "cron_queue_hygiene.py": "test_queue_hygiene",
        "cron_registration_validator.py": "test_cron_registration_validator",
        "cron_graph_freshness.py": "test_freshness_cron",
        "cron_canonical_integrity.py": "test_freshness_cron",
        "cron_graphify_artifact_monitor.py": "test_graphify_cron_automation",
        "cron_graphify_mcp_contract.py": "test_graphify_cron_automation",
        "cron_graphify_version_advisory.py": "test_graphify_cron_automation",
        "cron_graphify_code_refresh.py": "test_graphify_cron_automation",
        "cron_telemetry_harvest.py": "test_feedback_evaluation_loop",
        "cron_archive_stale_workflows.py": "test_archive_stale_workflows",
        "cron_claim_drift_check.py": "test_claim_drift_check",
        "retrieval_refresh.py": "test_freshness_cron",
        "runtime_metadata.py": "test_run_checks",
        "workflow_runner.py": "test_workflow_router",
        "concurrent_lane_manager.py": "test_concurrent_lane_manager",
    }
    return aggregate.get(script_name, "") in test_files


def _mentioned_in_markdown(script_name: str, markdown_text: str) -> bool:
    return script_name in markdown_text


def validate_script_docs(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return deterministic script-documentation findings for ``project_root``."""
    root = Path(project_root).resolve()
    scripts_dir = root / "scripts"
    tests_dir = root / "tests"
    issues: list[dict[str, str]] = []
    checked: list[str] = []

    if not scripts_dir.is_dir():
        return {
            "schema": "script-doc.v1",
            "ok": False,
            "project_root": str(root),
            "checked": [],
            "issues": [
                {
                    "code": "scripts_dir_missing",
                    "path": str(scripts_dir),
                    "message": "scripts/ directory is missing.",
                }
            ],
        }

    test_files = {p.stem for p in tests_dir.glob("test_*.py")} if tests_dir.is_dir() else set()
    markdown_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in root.rglob("*.md")
    )

    for path in sorted(scripts_dir.glob("*.py")):
        name = path.name
        checked.append(name)
        doc_words = _module_doc_words(path)
        ratio = _function_doc_ratio(path)

        if doc_words < MIN_MODULE_DOC_WORDS:
            issues.append(
                {
                    "code": "module_docstring_missing",
                    "path": str(path),
                    "message": f"Module docstring is missing or trivial ({doc_words} words).",
                }
            )
        if ratio == 0.0:
            issues.append(
                {
                    "code": "function_docstrings_missing",
                    "path": str(path),
                    "message": "No function docstrings present.",
                }
            )
        if not _test_anchor(name, test_files):
            issues.append(
                {
                    "code": "test_anchor_missing",
                    "path": str(path),
                    "message": "No test file references this script.",
                }
            )
        if not _mentioned_in_markdown(name, markdown_text):
            issues.append(
                {
                    "code": "reference_mention_missing",
                    "path": str(path),
                    "message": "Script is never mentioned in a markdown reference.",
                }
            )

    return {
        "schema": "script-doc.v1",
        "ok": not issues,
        "project_root": str(root),
        "checked": checked,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args()
    report = validate_script_docs(arguments.project_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
