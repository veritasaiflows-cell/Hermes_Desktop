from pathlib import Path
import hashlib
import json
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from canonical.db import CanonicalDB
from scripts import product_research_workflow
from scripts.product_research_workflow import run_product_research, run_product_research_with_telemetry
from scripts.workflow_runner import WorkflowPreflightError


class ProductResearchWorkflowTests(unittest.TestCase):

    def setUp(self):
        self._orig_model = os.environ.get("HERMES_ACTIVE_MODEL")
        self._orig_runtime_model = os.environ.get("HERMES_RUNTIME_MODEL")
        self._orig_runtime_provider = os.environ.get("HERMES_RUNTIME_PROVIDER")
        os.environ.pop("HERMES_RUNTIME_MODEL", None)
        os.environ.pop("HERMES_RUNTIME_PROVIDER", None)
        os.environ["HERMES_ACTIVE_MODEL"] = "test-model"

    def tearDown(self):
        if self._orig_model is None:
            os.environ.pop("HERMES_ACTIVE_MODEL", None)
        else:
            os.environ["HERMES_ACTIVE_MODEL"] = self._orig_model
        if self._orig_runtime_model is None:
            os.environ.pop("HERMES_RUNTIME_MODEL", None)
        else:
            os.environ["HERMES_RUNTIME_MODEL"] = self._orig_runtime_model
        if self._orig_runtime_provider is None:
            os.environ.pop("HERMES_RUNTIME_PROVIDER", None)
        else:
            os.environ["HERMES_RUNTIME_PROVIDER"] = self._orig_runtime_provider

    def run_dry_preflight(
        self,
        catalog: Path,
        database_path: Path,
        **kwargs,
    ):
        summary = run_product_research(catalog, database_path=database_path, dry_run=True, **kwargs)
        self.assertEqual(summary["mode"], "dry_run")
        return summary

    @staticmethod
    def source_freshness_hash(catalog: Path) -> str:
        with open(catalog, "rb") as catalog_file:
            return hashlib.sha256(catalog_file.read()).hexdigest()

    def test_product_research_requires_dry_run_before_write_for_new_source(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            with self.assertRaisesRegex(WorkflowPreflightError, "dry-run"):
                run_product_research(catalog, database_path=db_path, top_n=1)

            self.run_dry_preflight(catalog, db_path, top_n=1)
            write_result = run_product_research(catalog, database_path=db_path, top_n=1)

            self.assertEqual(write_result["mode"], "write")
            self.assertEqual(write_result["selected_count"], 1)

    def test_product_research_writes_run_metrics_telemetry(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            self.run_dry_preflight(catalog, db_path, top_n=1)
            run_product_research_with_telemetry(catalog, database_path=db_path, top_n=1)

            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="product_research", limit=1)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["request_type"], "product_research")
                self.assertEqual(row["model_or_agent"], "test-model")
                self.assertEqual(row["route_selected"], "product_research")
                self.assertEqual(row["verification_result"], "pass")
                self.assertEqual(row["input_size"], 1)
                self.assertEqual(row["handoff_size"], 1)
                resource = json.loads(row["resource_usage_json"])
                self.assertEqual(resource["selected_count"], 1)
                self.assertEqual(resource["incoming_rows"], 1)
                self.assertEqual(resource["new_candidates"], 1)

    def test_product_research_dry_run_writes_run_metrics_telemetry(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            # A dry-run must still create a source_freshness row so the
            # workflow can proceed; otherwise the inner run_product_research
            # will reject it.
            self.run_dry_preflight(catalog, db_path, top_n=1)
            run_product_research_with_telemetry(catalog, database_path=db_path, top_n=1, dry_run=True)

            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="product_research", limit=1)
                self.assertEqual(len(rows), 1)
                resource = json.loads(rows[0]["resource_usage_json"])
                self.assertTrue(resource["dry_run"])

    def test_product_research_replay_writes_run_metrics_telemetry(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            self.run_dry_preflight(catalog, db_path, top_n=1)
            first = run_product_research_with_telemetry(catalog, database_path=db_path, top_n=1)
            replayed = run_product_research_with_telemetry(catalog, database_path=db_path, top_n=1)

            self.assertTrue(replayed.get("mode", "").startswith("replayed"))
            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="product_research", limit=5)
                self.assertGreaterEqual(len(rows), 2)
                replayed_row = rows[0]
                self.assertTrue(json.loads(replayed_row["resource_usage_json"]).get("replayed", False))
                first_row = rows[1]
                self.assertFalse(json.loads(first_row["resource_usage_json"]).get("replayed", False))

    def test_product_research_telemetry_tags_drift_flag(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,0,2,0,100\n"
                "Premium Widget,Supplier B,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            # Split assignment is hash-based on the source path, which is
            # non-deterministic across temp dirs. Force a deterministic split:
            # row 1 → live (viability 0.0), row 2 → holdout (viability ~0.7),
            # producing a cross-split gap that triggers drift_flag.
            def forced_split(*, source_row: int, **_: object) -> str:
                return "holdout" if source_row == 2 else "live"

            with patch.object(
                product_research_workflow,
                "_assign_candidate_split",
                side_effect=forced_split,
            ):
                self.run_dry_preflight(
                    catalog, db_path, top_n=2, holdout_fraction=0.5
                )
                run_product_research_with_telemetry(
                    catalog,
                    database_path=db_path,
                    top_n=2,
                    holdout_fraction=0.5,
                )

            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="product_research", limit=1)
                self.assertEqual(len(rows), 1)
                errors = json.loads(rows[0]["errors_json"])
                self.assertIn("drift_flag", errors)

    def test_product_research_blocks_write_when_catalog_hash_changes_without_new_dry_run(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            self.run_dry_preflight(catalog, db_path, top_n=1)
            run_product_research(catalog, database_path=db_path, top_n=1)

            original_hash = self.source_freshness_hash(catalog)
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,50,2,85,30\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowPreflightError, "dry-run"):
                run_product_research(catalog, database_path=db_path, top_n=1)

            self.run_dry_preflight(catalog, db_path, top_n=1)
            changed = run_product_research(catalog, database_path=db_path, top_n=1)
            self.assertEqual(changed["mode"], "write")
            self.assertNotEqual(self.source_freshness_hash(catalog), original_hash)

    def test_product_research_reuses_existing_candidates_by_business_key(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Tumbler,Supplier North,34,4,90,20\n"
                "Aero Tumbler,Supplier North,40,4,90,20\n"
                "Aero Tumbler,Supplier North,50,4,90,20\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            self.run_dry_preflight(catalog, db_path, top_n=1, min_viability_score=0.0)
            first = run_product_research(catalog, database_path=db_path, top_n=1, min_viability_score=0.0)

            self.assertEqual(first["selected_count"], 1)
            self.assertEqual(len(first["written"]), 1)

            second = run_product_research(
                catalog,
                database_path=db_path,
                top_n=2,
                min_viability_score=0.0,
            )
            self.assertEqual(second["mode"], "write")

            with CanonicalDB(db_path) as db:
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 1)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 8)
                self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_product_research_writes_source_preflight_telemetry(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n"
                "Heavy Widget,Supplier B,15,21,60,90\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            summary = self.run_dry_preflight(catalog, db_path, top_n=2, min_viability_score=0.0)
            self.assertIn("source_preflight", summary)
            self.assertIn("status", summary["source_preflight"])
            self.assertEqual(summary["source_preflight"]["status"], "passed")
            self.assertIn("telemetry", summary)
            self.assertIn("business_key_dedupe", summary["telemetry"])

    def test_product_research_rolls_back_every_write_when_a_metric_insert_fails(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"
            self.run_dry_preflight(catalog, db_path, top_n=1)
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

            self.run_dry_preflight(catalog, db_path, top_n=1)
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

            self.run_dry_preflight(catalog, db_path, top_n=1)
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

            self.run_dry_preflight(catalog, db_path, top_n=1)

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

            self.run_dry_preflight(
                catalog,
                db_path,
                top_n=3,
                min_viability_score=0.0,
                holdout_fraction=0.3,
                adversarial_fraction=0.1,
            )

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

            self.run_dry_preflight(catalog, db_path, top_n=1)

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

            self.run_dry_preflight(catalog, db_path, top_n=2, min_viability_score=0.0)

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

    def test_product_research_evaluation_profile_reports_split_drift(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n"
                "Heavy Widget,Supplier B,15,21,60,90\n"
                "Foldable Keyboard,Supplier C,35,7,55,40\n"
                "Hydra Headset,Supplier D,55,3,90,20\n"
                "Mini Projector,Supplier E,40,4,70,25\n"
                "Smart Watch,Supplier F,30,5,65,50\n"
                "Wireless Charger,Supplier G,60,2,75,35\n"
                "Budget Earbuds,Supplier H,20,8,50,80\n",
                encoding="utf-8",
            )
            db_path = Path(directory) / "efficiens.db"

            self.run_dry_preflight(
                catalog,
                db_path,
                top_n=4,
                min_viability_score=0.0,
                holdout_fraction=0.25,
                adversarial_fraction=0.125,
            )
            summary = run_product_research(
                catalog,
                database_path=db_path,
                top_n=4,
                min_viability_score=0.0,
                holdout_fraction=0.25,
                adversarial_fraction=0.125,
            )

            profile = summary["confidence_profile"]
            self.assertIn("evaluation_profile", profile)
            eval_profile = profile["evaluation_profile"]
            self.assertIn("live", eval_profile)
            self.assertIn("holdout", eval_profile)
            self.assertIn("adversarial", eval_profile)
            self.assertIn("mean_viability", eval_profile["live"])
            self.assertIn("mean_disagreement", eval_profile["live"])
            self.assertIn("sample_count", eval_profile["live"])
            self.assertIn("drift_flag", eval_profile)
            self.assertIsInstance(eval_profile["drift_flag"], bool)
            self.assertIn("drift_details", eval_profile)

            total_sample = (
                eval_profile["live"]["sample_count"]
                + eval_profile["holdout"]["sample_count"]
                + eval_profile["adversarial"]["sample_count"]
            )
            self.assertEqual(total_sample, 8)

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
            self.run_dry_preflight(
                catalog,
                db_path,
                top_n=1,
                min_viability_score=0.0,
                holdout_fraction=0.0,
                adversarial_fraction=0.0,
            )
            summary = run_product_research(catalog, database_path=db_path, top_n=1, min_viability_score=0.95)

            self.assertEqual(summary["selected_count"], 0)
            self.assertEqual(summary["written"], [])


if __name__ == "__main__":
    unittest.main()
