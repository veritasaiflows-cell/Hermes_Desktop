from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from canonical.db import CanonicalDB, DEFAULT_SCHEMA_PATH


class SchemaCurrentTests(unittest.TestCase):
    """Verify the canonical schema applies cleanly and contains expected objects."""

    EXPECTED_TABLES = {
        "provenance",
        "entities",
        "tasks",
        "decisions",
        "preferences",
        "events",
        "metrics",
        "source_freshness",
        "validation_results",
        "claims",
        "workflow_runs",
        "version_history",
        "run_metrics",
        "routing_cache",
        "relationships",
    }

    EXPECTED_INDICES = {
        "idx_tasks_status",
        "idx_events_subject",
        "idx_validation_subject",
        "idx_claims_subject",
        "idx_claims_status",
        "idx_claims_valid_until",
        "idx_workflow_runs_workflow",
        "idx_run_metrics_started",
        "idx_relationships_subject",
        "idx_relationships_object",
        "idx_relationships_unique_active",
        "idx_relationships_status",
    }

    REQUIRED_COLUMNS = {
        "entities": {"entity_id", "entity_type", "name", "status", "scope", "provenance_id", "confidence", "created_at", "updated_at"},
        "metrics": {"metric_id", "metric_name", "metric_value", "unit", "dimensions_json", "provenance_id", "measured_at"},
        "claims": {"claim_id", "subject_type", "subject_id", "title", "claim_text", "source_type", "source_artifact_id", "source_locator", "source_hash", "source_version", "observed_at", "valid_from", "valid_until", "freshness_rule", "confidence", "authority_class", "verification_method", "status", "contradiction_notes", "superseded_by", "invalidated_by", "invalidated_reason", "provenance_id", "created_at", "updated_at"},
        "workflow_runs": {"run_id", "workflow_id", "run_key", "input_hash", "source_uri", "status", "result_json", "started_at", "completed_at", "provenance_id"},
        "relationships": {"rel_id", "subject_type", "subject_id", "predicate", "object_type", "object_id", "status", "confidence", "valid_from", "valid_until", "superseded_by", "provenance_id", "created_at", "updated_at"},
    }

    def test_schema_file_exists_and_is_readable(self):
        self.assertTrue(DEFAULT_SCHEMA_PATH.exists())
        self.assertIn("CREATE TABLE", DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_schema_initializes_all_expected_tables(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                tables = db.tables()
            self.assertTrue(self.EXPECTED_TABLES.issubset(tables))

    def test_schema_enables_wal_and_foreign_keys(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                foreign_keys = db.connection.execute("PRAGMA foreign_keys").fetchone()[0]
                journal_mode = db.connection.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(journal_mode.lower(), "wal")

    def test_required_columns_exist(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                for table, expected_columns in self.REQUIRED_COLUMNS.items():
                    cursor = db.connection.execute(f"PRAGMA table_info({table})")
                    found = {row["name"] for row in cursor.fetchall()}
                    missing = expected_columns - found
                    self.assertFalse(missing, f"Table {table} missing columns: {missing}")

    def test_expected_indices_exist(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                rows = db.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
                ).fetchall()
                found = {row[0] for row in rows}
            missing = self.EXPECTED_INDICES - found
            self.assertFalse(missing, f"Missing expected indices: {missing}")

    def test_schema_reapplication_is_idempotent(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                db.insert("entities", {"entity_type": "test", "name": "x", "status": "active"})
                tables_before = db.tables()
            # Re-open should re-apply schema without error.
            with CanonicalDB(db_path) as db:
                tables_after = db.tables()
                count = db.connection.execute("SELECT COUNT(*) FROM entities WHERE entity_type = 'test'").fetchone()[0]
            self.assertEqual(tables_before, tables_after)
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
