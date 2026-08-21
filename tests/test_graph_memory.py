"""Tests for the durable graph layer: relationships table, CanonicalDB graph
methods, and the graph_memory adapter commands."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from canonical.db import CanonicalDB
from scripts import graph_memory


class RelationshipTableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "efficiens.db"
        self.db = CanonicalDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _entity(self, name: str) -> str:
        return self.db.insert("entities", {"entity_type": "project", "name": name})

    def _task(self, title: str) -> str:
        return self.db.insert("tasks", {"title": title})

    def test_add_relationship_creates_active_edge_with_provenance(self):
        entity_id = self._entity("alpha")
        task_id = self._task("build alpha")
        rel_id = self.db.add_relationship(
            "entities", entity_id, "owns", "tasks", task_id, confidence=0.9
        )
        edge = self.db.get_relationship(rel_id)
        self.assertIsNotNone(edge)
        self.assertEqual(edge["status"], "active")
        self.assertEqual(edge["subject_type"], "entities")
        self.assertEqual(edge["subject_id"], entity_id)
        self.assertEqual(edge["predicate"], "owns")
        self.assertEqual(edge["object_type"], "tasks")
        self.assertEqual(edge["object_id"], task_id)
        self.assertAlmostEqual(edge["confidence"], 0.9)
        self.assertIsNotNone(edge["provenance_id"])
        self.assertIsNotNone(edge["valid_from"])

    def test_add_relationship_rejects_missing_parts(self):
        entity_id = self._entity("alpha")
        with self.assertRaises(ValueError):
            self.db.add_relationship("", entity_id, "owns", "tasks", "x")
        with self.assertRaises(ValueError):
            self.db.add_relationship("entities", entity_id, "", "tasks", "x")
        with self.assertRaises(ValueError):
            self.db.add_relationship("entities", entity_id, "owns", "", "x")

    def test_add_relationship_rejects_inverted_validity_window(self):
        entity_id = self._entity("alpha")
        with self.assertRaises(ValueError):
            self.db.add_relationship(
                "entities", entity_id, "owns", "tasks", "x",
                valid_from="2026-08-20T00:00:00Z",
                valid_until="2026-08-10T00:00:00Z",
            )

    def test_list_relationships_filters_and_excludes_expired(self):
        entity_id = self._entity("alpha")
        task_a = self._task("a")
        task_b = self._task("b")
        self.db.add_relationship("entities", entity_id, "owns", "tasks", task_a)
        self.db.add_relationship(
            "entities", entity_id, "owns", "tasks", task_b,
            valid_from="2020-01-01T00:00:00Z",
            valid_until="2020-06-01T00:00:00Z",
        )
        active = self.db.list_relationships(subject_type="entities", subject_id=entity_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["object_id"], task_a)
        all_edges = self.db.list_relationships(
            subject_type="entities", subject_id=entity_id, include_expired=True
        )
        self.assertEqual(len(all_edges), 2)

    def test_supersede_relationship_preserves_history(self):
        entity_id = self._entity("alpha")
        task_id = self._task("a")
        rel_id = self.db.add_relationship("entities", entity_id, "owns", "tasks", task_id)
        self.db.supersede_relationship(rel_id, reason="ownership moved")
        edge = self.db.get_relationship(rel_id)
        self.assertEqual(edge["status"], "superseded")
        active = self.db.list_relationships(subject_type="entities", subject_id=entity_id)
        self.assertEqual(active, [])

    def test_find_path_returns_shortest_path(self):
        a = self._entity("a")
        b = self._entity("b")
        c = self._entity("c")
        t = self._task("t")
        self.db.add_relationship("entities", a, "depends_on", "entities", b)
        self.db.add_relationship("entities", b, "depends_on", "entities", c)
        self.db.add_relationship("entities", c, "owns", "tasks", t)
        path = self.db.find_path("entities", a, "tasks", t)
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 3)
        self.assertEqual(path[0]["subject_id"], a)
        self.assertEqual(path[-1]["object_id"], t)

    def test_find_path_returns_none_when_disconnected(self):
        a = self._entity("a")
        b = self._entity("b")
        self.db.add_relationship("entities", a, "depends_on", "entities", b)
        self.assertIsNone(self.db.find_path("entities", b, "entities", a))

    def test_find_path_ignores_superseded_edges(self):
        a = self._entity("a")
        b = self._entity("b")
        rel_id = self.db.add_relationship("entities", a, "depends_on", "entities", b)
        self.db.supersede_relationship(rel_id, reason="no longer depends")
        self.assertIsNone(self.db.find_path("entities", a, "entities", b))

    def test_affected_reverse_traversal(self):
        a = self._entity("a")
        b = self._entity("b")
        t = self._task("t")
        self.db.add_relationship("entities", a, "depends_on", "entities", b)
        self.db.add_relationship("entities", b, "owns", "tasks", t)
        edges = self.db.affected("tasks", t, max_depth=2)
        self.assertEqual(len(edges), 2)
        subjects = {edge["subject_id"] for edge in edges}
        self.assertEqual(subjects, {a, b})


class GraphMemoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "efficiens.db"
        self.db = CanonicalDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        args = graph_memory.build_parser().parse_args([*argv, "--database", str(self.db_path)])
        rc = args.func(args)
        return rc, None

    def test_validate_clean_graph_exits_zero(self):
        entity_id = self.db.insert("entities", {"entity_type": "project", "name": "x"})
        task_id = self.db.insert("tasks", {"title": "t"})
        self.db.add_relationship("entities", entity_id, "owns", "tasks", task_id)
        self.db.close()
        rc, _ = self._run(["validate"])
        self.assertEqual(rc, 0)

    def test_validate_detects_orphan_subject(self):
        task_id = self.db.insert("tasks", {"title": "t"})
        self.db.add_relationship("entities", "missing-entity", "owns", "tasks", task_id)
        self.db.close()
        rc, _ = self._run(["validate"])
        self.assertEqual(rc, 1)

    def test_validate_detects_duplicate_active_triple(self):
        entity_id = self.db.insert("entities", {"entity_type": "project", "name": "x"})
        task_id = self.db.insert("tasks", {"title": "t"})
        self.db.add_relationship("entities", entity_id, "owns", "tasks", task_id)
        from canonical.db import DuplicateRecordError
        with self.assertRaises(DuplicateRecordError):
            self.db.add_relationship("entities", entity_id, "owns", "tasks", task_id)


if __name__ == "__main__":
    unittest.main()
