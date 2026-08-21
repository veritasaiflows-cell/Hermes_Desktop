from datetime import datetime, timedelta
import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from canonical.db import CanonicalDB, DuplicateRecordError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "canonical" / "schema.sql"


def _schema_path():
    """Return the canonical schema path, allowing env override for CI."""
    return Path(os.environ.get("EFFICIENS_SCHEMA_PATH", SCHEMA))


class CanonicalDBTests(unittest.TestCase):
    def test_read_only_connection_does_not_create_missing_database(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "missing" / "efficiens.db"

            with self.assertRaises(FileNotFoundError):
                CanonicalDB(database, read_only=True)

            self.assertFalse(database.exists())

    def test_read_only_connection_reads_but_rejects_writes(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "efficiens.db"
            with CanonicalDB(database) as db:
                db.insert("entities", {"entity_type": "project", "name": "alpha"})
            artifacts = [database, Path(f"{database}-wal"), Path(f"{database}-shm")]
            before = {
                path.name: path.read_bytes()
                for path in artifacts
                if path.exists()
            }

            with CanonicalDB(database, read_only=True) as db:
                self.assertIn("entities", db.tables())
                with self.assertRaises(sqlite3.OperationalError):
                    db.insert("entities", {"entity_type": "project", "name": "blocked"})

            after = {
                path.name: path.read_bytes()
                for path in artifacts
                if path.exists()
            }
            self.assertEqual(after, before)

    def _make_db(self):
        directory = TemporaryDirectory(ignore_cleanup_errors=True)
        db = CanonicalDB(Path(directory.name) / "test.db", schema_path=_schema_path())
        self.addCleanup(db.close)
        self.addCleanup(directory.cleanup)
        return db

    def test_record_run_writes_metadata_row(self):
        db = self._make_db()
        run_id = db.record_run(
            request_type="test_request",
            route_selected="deterministic",
            tools_json=["tool_a", "tool_b"],
            model_or_agent="kimi-k2.7-code",
            duration_ms=1234,
            errors_json=[],
            verification_result="pass",
            final_outcome="accepted",
            acceptance_status="accepted",
        )
        self.assertTrue(run_id)
        row = db.connection.execute(
            "SELECT * FROM run_metrics WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["request_type"], "test_request")
        self.assertEqual(row["route_selected"], "deterministic")
        self.assertEqual(json.loads(row["tools_json"]), ["tool_a", "tool_b"])
        self.assertEqual(row["model_or_agent"], "kimi-k2.7-code")
        self.assertEqual(row["duration_ms"], 1234)
        self.assertEqual(row["verification_result"], "pass")
        self.assertEqual(row["final_outcome"], "accepted")
        self.assertEqual(row["acceptance_status"], "accepted")

    def test_record_run_redacts_free_text_fields(self):
        db = self._make_db()
        run_id = db.record_run(
            request_type="test_request",
            user_correction="You should fix the parser to handle nested JSON",
            final_outcome="The workflow completed after I fixed the routing bug",
            route_selected="ok_value",
        )
        row = db.connection.execute(
            "SELECT user_correction, final_outcome, route_selected FROM run_metrics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["user_correction"], "redacted:content")
        self.assertEqual(row["final_outcome"], "redacted:content")
        # Short tokens survive redaction.
        self.assertEqual(row["route_selected"], "ok_value")

    def test_record_run_redacts_secret_like_tokens(self):
        db = self._make_db()
        run_id = db.record_run(
            request_type="test_request",
            model_or_agent="token=ghp_abc123secret",
            final_outcome="api_key leaked somehow",
        )
        row = db.connection.execute(
            "SELECT model_or_agent, final_outcome FROM run_metrics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["model_or_agent"], "redacted:secret")
        self.assertEqual(row["final_outcome"], "redacted:secret")

    def test_record_run_get_run_metrics_filters_by_request_type(self):
        db = self._make_db()
        first = db.record_run(request_type="type_a")
        second = db.record_run(request_type="type_b")
        a_rows = db.get_run_metrics(request_type="type_a", limit=10)
        self.assertEqual(len(a_rows), 1)
        self.assertEqual(a_rows[0]["run_id"], first)
        all_rows = db.get_run_metrics(limit=10)
        self.assertEqual(len(all_rows), 2)
        ids = {row["run_id"] for row in all_rows}
        self.assertEqual(ids, {first, second})

    def test_record_run_rejects_empty_request_type(self):
        db = self._make_db()
        with self.assertRaisesRegex(ValueError, "request_type is required"):
            db.record_run(request_type="")
        with self.assertRaisesRegex(ValueError, "request_type is required"):
            db.record_run(request_type="   ")
        with self.assertRaisesRegex(ValueError, "request_type is required"):
            db.record_run(request_type=None)  # type: ignore[arg-type]

    def test_record_user_correction_attaches_token_to_latest_run(self):
        db = self._make_db()
        first = db.record_run(request_type="type_a")
        second = db.record_run(request_type="type_b")

        updated = db.record_user_correction("wrong_route", request_type="type_b")
        self.assertEqual(updated, second)

        row = db.connection.execute(
            "SELECT user_correction FROM run_metrics WHERE run_id = ?", (second,)
        ).fetchone()
        self.assertEqual(row["user_correction"], "wrong_route")
        untouched = db.connection.execute(
            "SELECT user_correction FROM run_metrics WHERE run_id = ?", (first,)
        ).fetchone()
        self.assertIsNone(untouched["user_correction"])

    def test_record_user_correction_redacts_free_text(self):
        db = self._make_db()
        run_id = db.record_run(request_type="type_a")
        updated = db.record_user_correction(
            "Please stop using the wrong parser for nested JSON", run_id=run_id
        )
        self.assertEqual(updated, run_id)
        row = db.connection.execute(
            "SELECT user_correction FROM run_metrics WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["user_correction"], "redacted:content")

    def test_record_user_correction_returns_none_when_no_matching_run(self):
        db = self._make_db()
        self.assertIsNone(db.record_user_correction("wrong_route"))
        self.assertIsNone(db.record_user_correction("wrong_route", run_id="missing-id"))

    def test_connection_enables_foreign_keys_and_initializes_schema(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            self.assertEqual(db.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIn("entities", db.tables())
            self.assertIn("routing_cache", db.tables())
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_routing_cache_round_trip_and_expiry(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            self.assertIsNone(db.get_routing_cache("missing"))
            db.set_routing_cache(
                "wf-1000|summary",
                payload={"workflow_id": "WF-1000"},
                source_signatures={"state/active.json": {"sha256": "abc"}},
                ttl_seconds=60,
            )
            cached = db.get_routing_cache("wf-1000|summary")
            self.assertIsNotNone(cached)
            self.assertEqual(cached["payload"]["workflow_id"], "WF-1000")
            db.close()

    def test_delete_routing_cache_with_mismatched_signatures(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            db.set_routing_cache(
                "wf-1000|summary",
                payload={"workflow_id": "WF-1000"},
                source_signatures={"state/active.json": {"sha256": "abc"}},
                ttl_seconds=300,
            )
            db.set_routing_cache(
                "wf-1001|summary",
                payload={"workflow_id": "WF-1001"},
                source_signatures={"state/active.json": {"sha256": "def"}},
                ttl_seconds=300,
            )
            deleted = db.delete_routing_cache_with_mismatched_signatures(
                current_signatures={"state/active.json": {"sha256": "abc"}}
            )
            self.assertEqual(deleted, 1)
            self.assertIsNotNone(db.get_routing_cache("wf-1000|summary"))
            self.assertIsNone(db.get_routing_cache("wf-1001|summary"))
            db.close()

    def test_quote_identifier_rejects_dangerous_names(self):
        from canonical.db import _quote_identifier

        valid = ("entities", "workflow_id", "_private", "table1")
        for name in valid:
            self.assertTrue(_quote_identifier(name).startswith('"'))

        invalid = (
            "table; DROP TABLE entities;--",
            "column name",
            "1st_column",
            'quote"attack',
            " hyphen ",
        )
        for name in invalid:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    _quote_identifier(name)

    def test_insert_records_provenance_and_timestamps(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            provenance_id = db.add_provenance(
                source_type="test",
                source_ref="test-case-1",
                confidence=0.95,
            )
            entity_id = db.insert(
                "entities",
                {"entity_type": "person", "name": "Randall", "status": "active"},
                provenance_id=provenance_id,
            )
            row = db.connection.execute(
                "SELECT entity_id, provenance_id, created_at, updated_at "
                "FROM entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            self.assertEqual(row[0], entity_id)
            self.assertEqual(row[1], provenance_id)
            self.assertTrue(row[2])
            self.assertTrue(row[3])
            self.assertGreaterEqual(db.integrity_checks_run, 2)
            db.close()

    def test_duplicate_record_is_refused_without_overwrite(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            record_id = "entity-fixed-id"
            db.insert(
                "entities",
                {"entity_type": "project", "name": "Workspace"},
                record_id=record_id,
            )
            with self.assertRaises(DuplicateRecordError):
                db.insert(
                    "entities",
                    {"entity_type": "project", "name": "Changed Workspace"},
                    record_id=record_id,
                )
            name = db.connection.execute(
                "SELECT name FROM entities WHERE entity_id = ?", (record_id,)
            ).fetchone()[0]
            self.assertEqual(name, "Workspace")
            db.close()

    def test_failed_insert_rolls_back_transaction(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            with self.assertRaises(sqlite3.IntegrityError):
                db.insert("entities", {"entity_type": "project", "name": None})
            self.assertEqual(
                db.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                0,
            )
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_update_rolls_back_audit_records_when_mutation_fails(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            record_id = "entity-rollback-id"
            db.insert(
                "entities",
                {"entity_type": "project", "name": "Workspace", "status": "active"},
                record_id=record_id,
            )

            with self.assertRaises(sqlite3.IntegrityError):
                db.update("entities", record_id, {"name": None})

            current = db.connection.execute(
                "SELECT name, status FROM entities WHERE entity_id = ?", (record_id,)
            ).fetchone()
            history_count = db.connection.execute(
                "SELECT COUNT(*) FROM version_history WHERE subject_id = ?", (record_id,)
            ).fetchone()[0]
            event_count = db.connection.execute(
                "SELECT COUNT(*) FROM events WHERE subject_id = ?", (record_id,)
            ).fetchone()[0]
            provenance_count = db.connection.execute(
                "SELECT COUNT(*) FROM provenance"
            ).fetchone()[0]

            self.assertEqual(tuple(current), ("Workspace", "active"))
            self.assertEqual(history_count, 0)
            self.assertEqual(event_count, 0)
            self.assertEqual(provenance_count, 1)
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_repeated_updates_increment_versions_and_snapshot_each_prior_state(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            record_id = "entity-sequence-id"
            db.insert(
                "entities",
                {"entity_type": "project", "name": "Workspace", "status": "active"},
                record_id=record_id,
            )

            db.update("entities", record_id, {"status": "archived"})
            db.update("entities", record_id, {"status": "restored"})

            versions = db.connection.execute(
                "SELECT version_number, snapshot_json FROM version_history "
                "WHERE subject_type = ? AND subject_id = ? ORDER BY version_number",
                ("entities", record_id),
            ).fetchall()
            events = db.connection.execute(
                "SELECT COUNT(*) FROM events WHERE subject_type = ? AND subject_id = ?",
                ("entities", record_id),
            ).fetchone()[0]

            self.assertEqual([row[0] for row in versions], [1, 2])
            self.assertEqual(json.loads(versions[0][1])["status"], "active")
            self.assertEqual(json.loads(versions[1][1])["status"], "archived")
            self.assertEqual(events, 2)
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_update_of_non_provenance_table_creates_audit_provenance(self):
        with TemporaryDirectory() as directory:
            with CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA) as db:
                run_id = db.insert(
                    "run_metrics",
                    {"request_type": "test", "started_at": "2026-08-15T00:00:00Z"},
                )

                db.update("run_metrics", run_id, {"final_outcome": "accepted"})

                version_provenance = db.connection.execute(
                    "SELECT provenance_id FROM version_history "
                    "WHERE subject_type = ? AND subject_id = ?",
                    ("run_metrics", run_id),
                ).fetchone()[0]
                event_provenance = db.connection.execute(
                    "SELECT provenance_id FROM events WHERE subject_type = ? AND subject_id = ?",
                    ("run_metrics", run_id),
                ).fetchone()[0]

                self.assertIsNotNone(version_provenance)
                self.assertEqual(event_provenance, version_provenance)

    def test_update_refuses_immutable_provenance_and_append_only_audit_tables(self):
        with TemporaryDirectory() as directory:
            with CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA) as db:
                provenance_id = db.add_provenance(
                    source_type="test", source_ref="immutable-provenance"
                )
                event_id = db.insert(
                    "events",
                    {
                        "event_type": "test.event",
                        "payload_json": "{}",
                        "occurred_at": "2026-08-15T00:00:00Z",
                        "recorded_at": "2026-08-15T00:00:00Z",
                    },
                )
                version_id = db.insert(
                    "version_history",
                    {
                        "subject_type": "entities",
                        "subject_id": "entity-audit-id",
                        "version_number": 1,
                        "operation": "insert",
                        "snapshot_json": "{}",
                        "changed_at": "2026-08-15T00:00:00Z",
                    },
                )

                with self.assertRaisesRegex(ValueError, "Use add_provenance"):
                    db.update("provenance", provenance_id, {"notes": "rewritten"})
                with self.assertRaisesRegex(ValueError, "append-only"):
                    db.update("events", event_id, {"event_type": "rewritten"})
                with self.assertRaisesRegex(ValueError, "append-only"):
                    db.update("version_history", version_id, {"operation": "rewritten"})

    def test_update_uses_caller_supplied_change_provenance_for_all_audit_rows(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            record_id = "entity-explicit-provenance-id"
            db.insert(
                "entities",
                {"entity_type": "project", "name": "Workspace", "status": "active"},
                record_id=record_id,
            )
            provenance_id = db.add_provenance(
                source_type="test",
                source_ref="update-explicit-provenance",
            )

            db.update(
                "entities",
                record_id,
                {"status": "archived", "provenance_id": provenance_id},
            )

            current_provenance = db.connection.execute(
                "SELECT provenance_id FROM entities WHERE entity_id = ?", (record_id,)
            ).fetchone()[0]
            version_provenance = db.connection.execute(
                "SELECT provenance_id FROM version_history "
                "WHERE subject_type = ? AND subject_id = ?",
                ("entities", record_id),
            ).fetchone()[0]
            event_provenance = db.connection.execute(
                "SELECT provenance_id FROM events WHERE subject_type = ? AND subject_id = ?",
                ("entities", record_id),
            ).fetchone()[0]

            self.assertEqual(current_provenance, provenance_id)
            self.assertEqual(version_provenance, provenance_id)
            self.assertEqual(event_provenance, provenance_id)
            db.close()

    def test_update_rejects_conflicting_provenance_inputs(self):
        with TemporaryDirectory() as directory:
            with CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA) as db:
                record_id = db.insert(
                    "entities",
                    {"entity_type": "project", "name": "Workspace"},
                )
                first_provenance_id = db.add_provenance(
                    source_type="test", source_ref="first-provenance"
                )
                second_provenance_id = db.add_provenance(
                    source_type="test", source_ref="second-provenance"
                )

                with self.assertRaisesRegex(ValueError, "Conflicting provenance_id"):
                    db.update(
                        "entities",
                        record_id,
                        {"provenance_id": first_provenance_id},
                        provenance_id=second_provenance_id,
                    )

                self.assertEqual(
                    db.connection.execute(
                        "SELECT COUNT(*) FROM version_history WHERE subject_id = ?",
                        (record_id,),
                    ).fetchone()[0],
                    0,
                )

    def test_update_snapshots_previous_row_and_records_event(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            record_id = "entity-versioned-id"
            db.insert(
                "entities",
                {"entity_type": "project", "name": "Workspace", "status": "active"},
                record_id=record_id,
            )

            db.update("entities", record_id, {"status": "archived"})

            current = db.connection.execute(
                "SELECT name, status FROM entities WHERE entity_id = ?", (record_id,)
            ).fetchone()
            version = db.connection.execute(
                "SELECT version_number, operation, snapshot_json, provenance_id "
                "FROM version_history WHERE subject_type = ? AND subject_id = ?",
                ("entities", record_id),
            ).fetchone()
            event = db.connection.execute(
                "SELECT event_type, subject_type, subject_id, payload_json, provenance_id "
                "FROM events WHERE subject_type = ? AND subject_id = ?",
                ("entities", record_id),
            ).fetchone()
            current_provenance = db.connection.execute(
                "SELECT provenance_id FROM entities WHERE entity_id = ?", (record_id,)
            ).fetchone()[0]

            self.assertEqual(tuple(current), ("Workspace", "archived"))
            self.assertEqual(tuple(version[:2]), (1, "update"))
            snapshot = json.loads(version[2])
            self.assertEqual(snapshot["entity_id"], record_id)
            self.assertEqual(snapshot["entity_type"], "project")
            self.assertEqual(snapshot["name"], "Workspace")
            self.assertEqual(snapshot["status"], "active")
            self.assertEqual(tuple(event[:3]), ("record.updated", "entities", record_id))
            self.assertEqual(json.loads(event[3])["changes"], {"status": "archived"})
            self.assertEqual(json.loads(event[3])["version_number"], 1)
            self.assertEqual(version[3], current_provenance)
            self.assertEqual(event[4], current_provenance)
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_claim_ledger_add_list_and_expiry_filter(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            observed = "2026-08-15T12:00:00Z"
            provenance_id = db.add_provenance(source_type="test", source_ref="claim-source")

            claim_id = db.add_claim(
                "entity",
                "entity-1",
                "Product candidate list was generated from source catalog",
                title="product_research_candidate_export",
                source_artifact_id="catalog.csv",
                source_locator="catalog.csv:0-100",
                source_hash="sha256:abc",
                observed_at=observed,
                freshness_ttl_seconds=3600,
                authority_class="system",
                verification_method="offline-scorecard",
                confidence=0.9,
                provenance_id=provenance_id,
            )

            active_now = db.get_active_claims("entity", "entity-1", now=observed)
            self.assertEqual(len(active_now), 1)

            active_half_hour = db.get_active_claims("entity", "entity-1", now="2026-08-15T12:30:00Z")
            self.assertEqual(len(active_half_hour), 1)

            expired = db.get_active_claims("entity", "entity-1", now="2026-08-15T13:30:00Z")
            self.assertEqual(expired, [])

            included_with_override = db.list_claims(
                subject_type="entity",
                subject_id="entity-1",
                include_expired=True,
                now="2026-08-15T13:30:00Z",
            )
            self.assertEqual(len(included_with_override), 1)
            self.assertEqual(included_with_override[0]["claim_id"], claim_id)
            db.close()

    def test_invalidate_claim_writes_version_and_event(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            provenance_id = db.add_provenance(source_type="test", source_ref="claim-source")
            claim_id = db.add_claim(
                "workflow_runs",
                "run-1",
                "Initial claim text that must be superseded",
                authority_class="system",
                status="active",
                provenance_id=provenance_id,
            )

            db.invalidate_claim(
                claim_id,
                reason="New source evidence contradicted this claim",
                invalidated_by="system-reviewer",
                provenance_id=provenance_id,
            )

            claim = db.get_claim(claim_id)
            self.assertIsNotNone(claim)
            self.assertEqual(claim["status"], "invalidated")
            self.assertEqual(claim["invalidated_by"], "system-reviewer")

            history = db.connection.execute(
                "SELECT COUNT(*) FROM version_history WHERE subject_type = 'claims' AND subject_id = ?",
                (claim_id,),
            ).fetchone()[0]
            event = db.connection.execute(
                "SELECT COUNT(*) FROM events WHERE subject_type = 'claims' AND subject_id = ?",
                (claim_id,),
            ).fetchone()[0]
            self.assertEqual(history, 1)
            self.assertEqual(event, 1)
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

    def test_can_seed_product_research_business_record(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            provenance_id = db.add_provenance(
                source_type="phase0-seed",
                source_ref="workflow:product_research",
                confidence=0.91,
            )
            entity_id = db.insert(
                "entities",
                {
                    "entity_type": "product_candidate",
                    "name": "Hydra Bluetooth Headset",
                    "status": "candidate",
                    "scope": "commerce",
                    "confidence": 0.81,
                },
                provenance_id=provenance_id,
            )
            metric_id = db.insert(
                "metrics",
                {
                    "metric_name": "commerce.viability_score",
                    "metric_value": 0.72,
                    "unit": "ratio",
                    "dimensions_json": '{"workflow":"product_research", "entity_id":"' + entity_id + '"}',
                    "provenance_id": provenance_id,
                    "measured_at": "2026-08-15T00:00:00Z",
                }
            )

            row = db.connection.execute(
                "SELECT entity_type, name, status, scope, confidence FROM entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            metric = db.connection.execute(
                "SELECT metric_name, metric_value FROM metrics WHERE metric_id = ?",
                (metric_id,),
            ).fetchone()

            self.assertEqual(row[0], "product_candidate")
            self.assertEqual(row[1], "Hydra Bluetooth Headset")
            self.assertEqual(row[2], "candidate")
            self.assertEqual(row[3], "commerce")
            self.assertTrue(row[4] >= 0.8)
            self.assertEqual(metric[0], "commerce.viability_score")
            self.assertEqual(metric[1], 0.72)
            self.assertEqual(db.integrity_check(), "ok")
            db.close()


if __name__ == "__main__":
    unittest.main()
