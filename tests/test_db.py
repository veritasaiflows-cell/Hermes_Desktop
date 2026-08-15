import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from canonical.db import CanonicalDB, DuplicateRecordError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "canonical" / "schema.sql"


class CanonicalDBTests(unittest.TestCase):
    def test_connection_enables_foreign_keys_and_initializes_schema(self):
        with TemporaryDirectory() as directory:
            db = CanonicalDB(Path(directory) / "test.db", schema_path=SCHEMA)
            self.assertEqual(db.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIn("entities", db.tables())
            self.assertEqual(db.integrity_check(), "ok")
            db.close()

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


if __name__ == "__main__":
    unittest.main()
