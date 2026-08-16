#!/usr/bin/env python3
"""Phase-0 verification runner.

Runs a small test and smoke suite to verify the substrate before workflow expansion.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import shutil
import tempfile
import unittest
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from canonical.db import CanonicalDB
from scripts import workflow_router
from scripts import wiki_bootstrap
from scripts.product_research_workflow import run_product_research


def build_test_suite() -> unittest.TestSuite:
    """Discover tests independently of the caller's current working directory."""
    return unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "tests"))


def run_tests() -> bool:
    suite = build_test_suite()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def run_phase0_smoke(database_path: Path) -> dict:
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
        return {
            "entities": entity_count,
            "metrics": metric_count,
            "events": event_count,
            "delta_entities": entity_count - before_entities,
            "delta_metrics": metric_count - before_metrics,
            "delta_events": event_count - before_events,
            "database": str(database_path),
        }




def run_routing_smoke(
    *,
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = PROJECT_ROOT / "state",
    index_path: Path = PROJECT_ROOT / "tmp" / "workflow-routing-index.json",
) -> dict:
    """Regenerate and validate the workflow routing surface."""
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
    if route_result.get("routing_index_stale"):
        raise RuntimeError("Workflow routing index is stale after refresh")
    if "workflow" not in route_result:
        raise RuntimeError("Routing check did not return a workflow payload")
    return {
        "routing": route_result,
        "index_path": str(index_path),
        "state_dir": str(state_dir),
    }


def run_wiki_smoke(*, project_root: Path = PROJECT_ROOT) -> dict:
    """Run wiki bootstrap validation as part of startup smoke checks."""
    report = wiki_bootstrap.validate_wiki(project_root=project_root)
    if report["status"] != "fresh":
        raise RuntimeError(f"Wiki bootstrap validation failed: {report['issues']}")
    return report



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
            index_path=isolated_root / "tmp" / "workflow-routing-index.json",
        )
        return {
            "database": str(database_path),
            "smoke": smoke,
            "routing": routing,
            "wiki": run_wiki_smoke(project_root=PROJECT_ROOT),
        }


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
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    if not run_tests():
        return 1

    if arguments.skip_smoke:
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
