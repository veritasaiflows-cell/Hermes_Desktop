"""Tests for the graph backfill script and the A10 graph-freshness cron."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from canonical.db import CanonicalDB
from scripts import cron_graph_freshness, graph_backfill


class GraphBackfillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "efficiens.db"
        self.db = CanonicalDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed_legacy_records(self) -> tuple[str, str, str]:
        """Insert pre-graph records the way the workflow did before edges existed."""
        entity_id = self.db.insert(
            "entities",
            {"entity_type": "product_candidate", "name": "Legacy Mug", "status": "candidate"},
        )
        metric_id = self.db.insert(
            "metrics",
            {
                "metric_name": "commerce.viability_score",
                "metric_value": 0.7,
                "unit": "ratio",
                "dimensions_json": json.dumps({"entity_id": entity_id}),
                "measured_at": "2026-08-01T00:00:00Z",
            },
        )
        run_id = self.db.insert(
            "workflow_runs",
            {
                "workflow_id": "product_research",
                "run_key": "legacy-run-key",
                "input_hash": "abc",
                "status": "completed",
                "result_json": json.dumps(
                    {"written": [{"action": "inserted", "entity_id": entity_id}]}
                ),
                "started_at": "2026-08-01T00:00:00Z",
                "completed_at": "2026-08-01T00:00:00Z",
            },
        )
        return entity_id, metric_id, run_id

    def test_backfill_asserts_missing_edges(self):
        entity_id, metric_id, run_id = self._seed_legacy_records()
        summary = graph_backfill.backfill_graph(
            self.db_path, state_dir=Path(self._tmp.name) / "state"
        )
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["edges_asserted"], 2)
        self.assertEqual(summary["edges_reused"], 0)

        edges = self.db.list_relationships(status="active")
        self.assertEqual(len(edges), 2)
        predicates = {edge["predicate"] for edge in edges}
        self.assertEqual(predicates, {"has_metric", "produced"})

    def test_backfill_asserts_workflow_dependencies(self):
        state_dir = Path(self._tmp.name) / "state"
        state_dir.mkdir()
        (state_dir / "ACTIVE_WORKFLOWS.md").write_text(
            "# Workflow control surface\n\n```json\n"
            + json.dumps(
                {
                    "schema": "active-workflows.v1",
                    "workflows": [
                        {
                            "workflow_id": "WF-2000",
                            "depends_on": ["WF-1000"],
                        }
                    ],
                }
            )
            + "\n```\n",
            encoding="utf-8",
        )

        summary = graph_backfill.backfill_graph(self.db_path, state_dir=state_dir)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["edges_asserted"], 1)

        edges = self.db.list_relationships(status="active")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["subject_type"], "workflows")
        self.assertEqual(edges[0]["subject_id"], "WF-2000")
        self.assertEqual(edges[0]["predicate"], "depends_on")
        self.assertEqual(edges[0]["object_type"], "workflows")
        self.assertEqual(edges[0]["object_id"], "WF-1000")

    def test_backfill_is_idempotent(self):
        self._seed_legacy_records()
        state_dir = Path(self._tmp.name) / "state"
        first = graph_backfill.backfill_graph(self.db_path, state_dir=state_dir)
        second = graph_backfill.backfill_graph(self.db_path, state_dir=state_dir)
        self.assertEqual(first["edges_asserted"], 2)
        self.assertEqual(second["edges_asserted"], 0)
        self.assertEqual(second["edges_reused"], 2)
        self.assertEqual(
            self.db.connection.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
            2,
        )

    def test_backfill_skips_metrics_without_entity_id(self):
        self.db.insert(
            "metrics",
            {
                "metric_name": "commerce.viability_score",
                "metric_value": 0.5,
                "unit": "ratio",
                "dimensions_json": json.dumps({"workflow": "other"}),
                "measured_at": "2026-08-01T00:00:00Z",
            },
        )
        summary = graph_backfill.backfill_graph(
            self.db_path, state_dir=Path(self._tmp.name) / "state"
        )
        self.assertEqual(summary["edges_asserted"], 0)


class GraphFreshnessCronTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "efficiens.db"
        self.db = CanonicalDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_coverage_check_flags_missing_edges(self):
        entity_id = self.db.insert(
            "entities",
            {"entity_type": "product_candidate", "name": "Orphan Candidate", "status": "candidate"},
        )
        gaps = cron_graph_freshness._check_graph_coverage(
            self.db, state_dir=Path(self._tmp.name) / "state"
        )
        self.assertTrue(any(gap["type"] == "missing_has_metric" for gap in gaps))

    def test_coverage_check_clean_when_edges_exist(self):
        entity_id = self.db.insert(
            "entities",
            {"entity_type": "product_candidate", "name": "Wired Candidate", "status": "candidate"},
        )
        metric_id = self.db.insert(
            "metrics",
            {
                "metric_name": "commerce.viability_score",
                "metric_value": 0.6,
                "unit": "ratio",
                "dimensions_json": json.dumps({"entity_id": entity_id}),
                "measured_at": "2026-08-01T00:00:00Z",
            },
        )
        self.db.add_relationship("entities", entity_id, "has_metric", "metrics", metric_id)
        gaps = cron_graph_freshness._check_graph_coverage(
            self.db, state_dir=Path(self._tmp.name) / "state"
        )
        # The test fixture has no declared workflow dependencies, so the only
        # possible gap is the has_metric coverage, which we satisfied above.
        self.assertEqual(gaps, [], gaps)

    def test_coverage_check_flags_missing_workflow_dependency(self):
        state_dir = Path(self._tmp.name) / "state"
        state_dir.mkdir()
        (state_dir / "ACTIVE_WORKFLOWS.md").write_text(
            "# Workflow control surface\n\n```json\n"
            + json.dumps(
                {
                    "schema": "active-workflows.v1",
                    "workflows": [
                        {
                            "workflow_id": "WF-2000",
                            "depends_on": ["WF-1000"],
                        }
                    ],
                }
            )
            + "\n```\n",
            encoding="utf-8",
        )

        gaps = cron_graph_freshness._check_graph_coverage(self.db, state_dir=state_dir)
        matching = [
            gap for gap in gaps
            if gap["type"] == "missing_workflow_dependency"
            and gap["workflow_id"] == "WF-2000"
            and gap["dependency"] == "WF-1000"
        ]
        self.assertEqual(len(matching), 1, gaps)
        # No other workflow dependency gaps should leak from the real state dir.
        other = [gap for gap in gaps if gap["type"] == "missing_workflow_dependency" and gap not in matching]
        self.assertEqual(other, [], gaps)

    def test_integrity_check_detects_orphan_edge(self):
        task_id = self.db.insert("tasks", {"title": "t"})
        self.db.add_relationship("entities", "missing-entity", "owns", "tasks", task_id)
        issues = cron_graph_freshness._check_graph_integrity(self.db)
        self.assertTrue(any(issue["type"] == "orphan_subject" for issue in issues))


if __name__ == "__main__":
    unittest.main()
