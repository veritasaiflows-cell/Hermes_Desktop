from pathlib import Path
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from canonical.db import CanonicalDB
from scripts.product_research_workflow import run_product_research


class ProductResearchWorkflowTests(unittest.TestCase):
    def test_product_research_rolls_back_every_write_when_a_metric_insert_fails(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"
            original_insert_sql = CanonicalDB._insert_sql
            metric_insert_count = 0

            def fail_on_second_metric_insert(db, table, values):
                nonlocal metric_insert_count
                if table == "metrics":
                    metric_insert_count += 1
                    if metric_insert_count == 2:
                        raise sqlite3.IntegrityError("injected metric write failure")
                return original_insert_sql(db, table, values)

            with patch.object(CanonicalDB, "_insert_sql", new=fail_on_second_metric_insert):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected metric write failure"):
                    run_product_research(catalog, database_path=db_path, top_n=1)

            with CanonicalDB(db_path) as db:
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM provenance").fetchone()[0], 0)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 0)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 0)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_product_research_replays_identical_input_without_duplicate_writes(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            first = run_product_research(catalog, database_path=db_path, top_n=1)
            self.assertIn("bundle_sha256", first)
            replayed = run_product_research(catalog, database_path=db_path, top_n=1)

            self.assertEqual(replayed["mode"], "replayed")
            self.assertEqual(replayed["written"], first["written"])
            self.assertEqual(replayed["bundle_sha256"], first["bundle_sha256"])
            with CanonicalDB(db_path) as db:
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM provenance").fetchone()[0], 1)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 1)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 8)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 1)

    def test_product_research_split_controls_change_run_key(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n"
                "Heavy Widget,Supplier B,15,21,60,90\n"
                "Foldable Keyboard,Supplier C,35,7,55,40\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            first = run_product_research(catalog, database_path=db_path, top_n=1, holdout_fraction=0.0)
            second = run_product_research(
                catalog,
                database_path=db_path,
                top_n=1,
                holdout_fraction=0.5,
            )

            self.assertNotEqual(first["mode"], "replayed")
            self.assertNotEqual(second["mode"], "replayed")
            self.assertNotEqual(first.get("bundle_sha256"), second.get("bundle_sha256"))

            with CanonicalDB(db_path) as db:
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 2)

    def test_product_research_replay_staleness_marks_claim_gap(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            run_product_research(catalog, database_path=db_path, top_n=1)
            with CanonicalDB(db_path) as db:
                claim_id, run_id = db.connection.execute(
                    "SELECT claim_id, subject_id FROM claims WHERE subject_type = 'workflow_runs' AND status = 'active' ORDER BY observed_at DESC LIMIT 1"
                ).fetchone()
                db.connection.execute(
                    "UPDATE claims SET valid_until = ?, status = 'expired' WHERE claim_id = ?",
                    ("2000-01-01T00:00:00Z", claim_id),
                )
                db.connection.commit()

            stale = run_product_research(catalog, database_path=db_path, top_n=1)

            self.assertEqual(stale["mode"], "replayed_stale")
            self.assertEqual(stale["freshness"]["status"], "stale")
            self.assertEqual(stale["freshness"]["reason"], "No active provenance claim for matching workflow run key.")
            with CanonicalDB(db_path) as db:
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 1)
                self.assertEqual(
                    db.connection.execute(
                        "SELECT COUNT(*) FROM claims WHERE subject_type='workflow_runs' AND subject_id = ?",
                        (run_id,),
                    ).fetchone()[0],
                    1,
                )

    def test_product_research_reports_confidence_and_split_profiles(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n"
                "Heavy Widget,Supplier B,15,21,60,90\n"
                "Foldable Keyboard,Supplier C,35,7,55,40\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            summary = run_product_research(
                catalog,
                database_path=db_path,
                top_n=3,
                min_viability_score=0.0,
                holdout_fraction=0.3,
                adversarial_fraction=0.1,
            )

            profile = summary["confidence_profile"]
            self.assertEqual(summary["selected_count"], len(summary["written"]))
            self.assertEqual(
                profile["evaluation_splits"]["live"]
                + profile["evaluation_splits"]["holdout"]
                + profile["evaluation_splits"]["adversarial"],
                3,
            )
            if summary["selected_count"] > 0:
                candidate = summary["written"][0]
                self.assertIn("score_primary", candidate)
                self.assertIn("score_secondary", candidate)
                self.assertIn("score_disagreement", candidate)
                self.assertAlmostEqual(
                    candidate["viability_score"],
                    round((0.6 * candidate["score_primary"]) + (0.4 * candidate["score_secondary"]), 4),
                )

    def test_product_research_replay_detects_bundle_tampering(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            run_product_research(catalog, database_path=db_path, top_n=1)

            with CanonicalDB(db_path) as db:
                row = db.connection.execute(
                    "SELECT run_id, result_json FROM workflow_runs LIMIT 1"
                ).fetchone()
                self.assertIsNotNone(row)
                stored = json.loads(row["result_json"])
                stored["written"].append({"tampered": True})
                db.connection.execute(
                    "UPDATE workflow_runs SET result_json = ? WHERE run_id = ?",
                    (json.dumps(stored, sort_keys=True), row["run_id"]),
                )
                db.connection.commit()

            with self.assertRaisesRegex(
                RuntimeError,
                "missing integrity metadata or has been tampered",
            ):
                run_product_research(catalog, database_path=db_path, top_n=1)

    def test_product_research_writes_top_candidates_and_records_metrics_and_events(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal,cost_per_unit_usd,retail_price_usd,notes\n"
                "Aero Travel Mug,Supplier A,45,2,80,30,9.5,19.99,Great launch candidate\n"
                "Heavy Widget,Supplier B,15,21,60,90,6,15.00,High saturation\n"
                "Foldable Keyboard,Supplier C,35,7,55,40,14,49.99,Stable demand\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            summary = run_product_research(catalog, database_path=db_path, top_n=2, min_viability_score=0.0)

            self.assertEqual(summary["mode"], "write")
            self.assertEqual(summary["selected_count"], 2)
            self.assertEqual(len(summary["written"]), 2)

            with CanonicalDB(db_path) as db:
                entity_names = [
                    row[0]
                    for row in db.connection.execute(
                        "SELECT name FROM entities WHERE entity_type = 'product_candidate' ORDER BY name"
                    )
                ]
                self.assertEqual(len(entity_names), 2)
                self.assertEqual(entity_names, ["Aero Travel Mug", "Foldable Keyboard"])

                metric_count = db.connection.execute(
                    "SELECT COUNT(*) FROM metrics WHERE dimensions_json LIKE '%workflow%product_research%'"
                ).fetchone()[0]
                self.assertGreaterEqual(metric_count, 10)

                written_id = summary["written"][0]["entity_id"]
                events = db.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE subject_type = 'entities' AND subject_id = ?",
                    (written_id,),
                ).fetchone()[0]
                self.assertEqual(events, 1)

    def test_product_research_honors_dry_run_without_writing(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal,cost_per_unit_usd,retail_price_usd,notes\n"
                "Aero Travel Mug,Supplier A,45,2,80,30,9.5,19.99,Great launch candidate\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            summary = run_product_research(
                catalog,
                database_path=db_path,
                top_n=1,
                min_viability_score=0.0,
                dry_run=True,
            )

            self.assertEqual(summary["mode"], "dry_run")
            self.assertEqual(summary["selected_count"], 1)
            with CanonicalDB(db_path) as db:
                self.assertEqual(
                    db.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                    0,
                )

    def test_product_research_filters_by_min_score_and_validates_catalog_header(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "wrong,header\n1,2\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            with self.assertRaisesRegex(ValueError, "missing required columns"):
                run_product_research(catalog, database_path=db_path, top_n=1, min_viability_score=0.0)

            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal,cost_per_unit_usd,retail_price_usd,notes\n"
                "Aero Travel Mug,Supplier A,45,2,10,90,9.5,19.99,Great\n",
                encoding="utf-8",
            )
            summary = run_product_research(catalog, database_path=db_path, top_n=1, min_viability_score=0.95)

            self.assertEqual(summary["selected_count"], 0)
            self.assertEqual(summary["written"], [])


if __name__ == "__main__":
    unittest.main()
