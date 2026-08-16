#!/usr/bin/env python3
"""Phase-0 verification runner.

Runs a small test and smoke suite to verify the substrate before workflow expansion.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile
import time
import unittest
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB, DEFAULT_DATABASE_PATH
from scripts import workflow_router
from scripts import wiki_bootstrap
from scripts.product_research_workflow import run_product_research


def build_test_suite() -> unittest.TestSuite:
    """Discover tests independently of the caller's current working directory."""
    return unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "tests"))


def run_tests() -> tuple[bool, list[str], int]:
    """Discover tests independently of the caller's current working directory.

    Prefers pytest when available for faster feedback and richer reporting.
    Falls back to unittest discovery if pytest is not installed.
    """
    start = time.perf_counter_ns()
    pytest_available = shutil.which("pytest") is not None
    try:
        import pytest as _pytest  # noqa: F401
    except ImportError:
        pytest_available = False

    if pytest_available:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(PROJECT_ROOT / "tests"), "-q"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=300,
            )
            elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
            output = completed.stdout + completed.stderr
            failures = []
            if completed.returncode != 0:
                failures = [line for line in output.splitlines() if "FAILED" in line or "ERROR" in line][:10]
            return completed.returncode == 0, failures, elapsed_ms
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Fallback: unittest discovery.
    suite = build_test_suite()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
    failures = [
        *(str(case) for case, _trace in result.failures),
        *(str(case) for case, _trace in result.errors),
    ]
    return result.wasSuccessful(), failures, elapsed_ms


def run_phase0_smoke(database_path: Path) -> dict:
    start = time.perf_counter_ns()
    try:
        with CanonicalDB(database_path) as db:
            before_entities = db.connection.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='product_candidate' AND scope='commerce'"
            ).fetchone()[0]
            before_metrics = db.connection.execute(
                "SELECT COUNT(*) FROM metrics WHERE metric_name='commerce.viability_score'"
            ).fetchone()[0]
            before_events = db.connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'workflow.product_research.candidate_selected'"
            ).fetchone()[0]

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal,cost_per_unit_usd,retail_price_usd,notes\n"
                "Hydra Bluetooth Headset,Supplier North,38,3,84,35,18,49.99,Good first run candidate\n"
                "Budget Tripod Stand,Supplier South,12,9,42,70,11,24.99,Lower priority\n",
                encoding="utf-8",
            )
            run_product_research(catalog, database_path=database_path, top_n=1, dry_run=True)
            run_product_research(catalog, database_path=database_path, top_n=1)

            with CanonicalDB(database_path) as db:
                entity_count = db.connection.execute(
                    "SELECT COUNT(*) FROM entities WHERE entity_type='product_candidate' AND scope='commerce'"
                ).fetchone()[0]
                metric_count = db.connection.execute(
                    "SELECT COUNT(*) FROM metrics WHERE metric_name='commerce.viability_score'"
                ).fetchone()[0]
                event_count = db.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'workflow.product_research.candidate_selected'"
                ).fetchone()[0]
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "entities": entity_count,
            "metrics": metric_count,
            "events": event_count,
            "delta_entities": entity_count - before_entities,
            "delta_metrics": metric_count - before_metrics,
            "delta_events": event_count - before_events,
            "database": str(database_path),
            "duration_ms": elapsed_ms,
        }
    except Exception:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "entities": 0,
            "metrics": 0,
            "events": 0,
            "delta_entities": 0,
            "delta_metrics": 0,
            "delta_events": 0,
            "database": str(database_path),
            "duration_ms": elapsed_ms,
            "error": "phase0_smoke_failed",
        }



def run_routing_smoke(
    *,
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = PROJECT_ROOT / "state",
    index_path: Path = PROJECT_ROOT / "state" / "workflow-routing-index.json",
) -> dict:
    """Regenerate and validate the workflow routing surface."""
    start = time.perf_counter_ns()
    try:
        route_result = workflow_router.route_workflows(
            selector="WF-1000",
            answer="summary",
            validate=True,
            write_index=True,
            write_capsules=True,
            project_root=project_root,
            state_dir=state_dir,
            index_path=index_path,
        )
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "routing": route_result,
            "index_path": str(index_path),
            "state_dir": str(state_dir),
            "duration_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "routing": {},
            "index_path": str(index_path),
            "state_dir": str(state_dir),
            "duration_ms": elapsed_ms,
            "error": f"routing_smoke_failed:{type(exc).__name__}",
        }


def run_wiki_smoke(*, project_root: Path = PROJECT_ROOT) -> dict:
    """Run wiki bootstrap validation as part of startup smoke checks."""
    start = time.perf_counter_ns()
    try:
        report = wiki_bootstrap.validate_wiki(project_root=project_root)
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        report["duration_ms"] = elapsed_ms
        return report
    except Exception as exc:
        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        return {
            "status": "error",
            "issues": [f"{type(exc).__name__}: {exc}"],
            "duration_ms": elapsed_ms,
        }



def run_isolated_smoke() -> dict:
    """Run all mutating smoke checks against disposable copies of control state."""
    with tempfile.TemporaryDirectory() as directory:
        isolated_root = Path(directory)
        state_dir = isolated_root / "state"
        shutil.copytree(PROJECT_ROOT / "state", state_dir)
        shutil.copytree(PROJECT_ROOT / "continuity", isolated_root / "continuity")
        database_path = isolated_root / "canonical" / "efficiens.db"
        smoke = run_phase0_smoke(database_path)
        routing = run_routing_smoke(
            project_root=isolated_root,
            state_dir=state_dir,
            index_path=isolated_root / "state" / "workflow-routing-index.json",
        )
        return {
            "database": str(database_path),
            "smoke": smoke,
            "routing": routing,
            "wiki": run_wiki_smoke(project_root=PROJECT_ROOT),
        }


def _record_run_check(
    database_path: Path | None,
    *,
    tests_ok: bool,
    test_failures: list[str],
    test_duration_ms: int,
    smoke: dict,
    routing: dict,
    wiki: dict,
) -> None:
    """Write a metadata-only telemetry row for the run_checks invocation."""
    target = Path(database_path) if database_path else DEFAULT_DATABASE_PATH
    errors: list[str] = []
    if not tests_ok:
        errors.append("unit_tests_failed")
    if smoke.get("error"):
        errors.append(smoke["error"])
    if routing.get("error"):
        errors.append(routing["error"])
    if wiki and wiki.get("status") != "fresh":
        errors.append("wiki_not_fresh")

    total_duration = test_duration_ms + smoke.get("duration_ms", 0)
    total_duration += routing.get("duration_ms", 0)
    total_duration += wiki.get("duration_ms", 0)

    try:
        with CanonicalDB(target) as db:
            db.record_run(
                request_type="run_checks",
                route_selected="deterministic",
                tools_json=["unittest", "phase0_smoke", "routing_smoke", "wiki_smoke"],
                duration_ms=total_duration,
                errors_json=errors,
                verification_result="pass" if not errors else "fail",
                final_outcome="accepted" if not errors else "rejected",
                acceptance_status="accepted" if not errors else "rejected",
                resource_usage_json={"test_count": len(test_failures) + (1 if tests_ok else 0)},
            )
    except Exception:
        # Telemetry must never break the caller's original result.
        pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        help="Canonical database path for an explicit persistent smoke check",
    )
    parser.add_argument(
        "--persistent-smoke",
        action="store_true",
        help="Opt in to smoke checks that mutate the specified persistent database and control plane",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Run unit tests only",
    )
    parser.add_argument(
        "--record-telemetry",
        action="store_true",
        help="Record a metadata-only run_metrics row after checks complete",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    tests_ok, test_failures, test_duration_ms = run_tests()
    if not tests_ok:
        if arguments.record_telemetry:
            _record_run_check(
                Path(arguments.database) if arguments.database else None,
                tests_ok=False,
                test_failures=test_failures,
                test_duration_ms=test_duration_ms,
                smoke={},
                routing={},
                wiki={},
            )
        return 1

    if arguments.skip_smoke:
        if arguments.record_telemetry:
            _record_run_check(
                Path(arguments.database) if arguments.database else None,
                tests_ok=True,
                test_failures=[],
                test_duration_ms=test_duration_ms,
                smoke={},
                routing={},
                wiki={},
            )
        return 0

    if arguments.persistent_smoke:
        if not arguments.database:
            raise ValueError("--persistent-smoke requires --database")
        smoke = run_phase0_smoke(Path(arguments.database))
        routing_smoke = run_routing_smoke()
        wiki_smoke = run_wiki_smoke()
    else:
        if arguments.database:
            raise ValueError("--database requires --persistent-smoke")
        isolated = run_isolated_smoke()
        smoke = isolated["smoke"]
        routing_smoke = isolated["routing"]
        wiki_smoke = isolated["wiki"]

    print("smoke-check:", smoke)
    print("routing-check:", routing_smoke)
    print("wiki-check:", wiki_smoke)

    if arguments.record_telemetry:
        _record_run_check(
            Path(arguments.database) if arguments.database else None,
            tests_ok=True,
            test_failures=[],
            test_duration_ms=test_duration_ms,
            smoke=smoke,
            routing=routing_smoke,
            wiki=wiki_smoke,
        )

    return (
        0
        if smoke["delta_entities"] >= 1
        and smoke["delta_metrics"] >= 1
        and smoke["delta_events"] >= 1
        and not routing_smoke["routing"].get("routing_index_stale", True)
        and wiki_smoke["status"] == "fresh"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
