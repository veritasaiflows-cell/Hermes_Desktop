from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from canonical.db import CanonicalDB
from scripts.cron_claim_drift_check import main as claim_drift_main
from scripts.cron_claim_drift_check import _claim_coverage
from scripts.product_research_workflow import run_product_research


class ClaimDriftCheckTests(unittest.TestCase):

    def run_dry_preflight(self, catalog: Path, database_path: Path, **kwargs):
        summary = run_product_research(catalog, database_path=database_path, dry_run=True, **kwargs)
        self.assertEqual(summary["mode"], "dry_run")
        return summary

    def _run_main_capturing_stderr(self, db_path: Path) -> tuple[int, str]:
        """Run the gate against ``db_path`` and return (exit_code, stderr)."""
        import scripts.cron_claim_drift_check as drift_module

        original_database = drift_module.DEFAULT_DATABASE
        drift_module.DEFAULT_DATABASE = db_path
        buffer = StringIO()
        try:
            with redirect_stderr(buffer):
                result = claim_drift_main()
        finally:
            drift_module.DEFAULT_DATABASE = original_database
        return result, buffer.getvalue()

    def test_green_output_distinguishes_empty_corpus_from_verified_claims(self):
        """An OK gate over zero active claims must say so, not imply coverage."""
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                db.add_claim(
                    "workflow_runs",
                    "run-expired",
                    "expired claim text",
                    title="expired",
                    source_type="workflow",
                    freshness_ttl_seconds=1,
                    observed_at="2020-01-01T00:00:00Z",
                    valid_from="2020-01-01T00:00:00Z",
                )
                db.connection.execute("UPDATE claims SET status = 'expired'")
                db.connection.commit()

            result, stderr = self._run_main_capturing_stderr(db_path)
            self.assertEqual(result, 0)
            # The row exists but none are active: the gate must not look verified.
            self.assertIn("no_active_claims", stderr)
            self.assertIn("total_claims=1", stderr)

    def test_claim_coverage_counts_by_status(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                for index in range(2):
                    db.add_claim(
                        "workflow_runs",
                        f"run-{index}",
                        "text",
                        title=f"c{index}",
                        source_type="workflow",
                        freshness_ttl_seconds=3600,
                    )
                coverage = _claim_coverage(db)
            self.assertEqual(coverage["total"], 2)
            self.assertEqual(coverage["active"], 2)

    def test_claim_coverage_on_empty_table_reports_zero_not_missing(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                coverage = _claim_coverage(db)
            self.assertEqual(coverage.get("active"), 0)
            self.assertEqual(coverage.get("total"), 0)

    def test_claim_drift_green_when_no_claims(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            # No database yet; main should report OK.
            import scripts.cron_claim_drift_check as drift_module
            original_database = drift_module.DEFAULT_DATABASE
            drift_module.DEFAULT_DATABASE = db_path
            try:
                result = claim_drift_main()
            finally:
                drift_module.DEFAULT_DATABASE = original_database
            self.assertEqual(result, 0)

    def test_claim_drift_flags_expired_claim(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )

            self.run_dry_preflight(catalog, db_path, top_n=1)
            run_product_research(catalog, database_path=db_path, top_n=1)

            with CanonicalDB(db_path) as db:
                claim_id = db.connection.execute(
                    "SELECT claim_id FROM claims WHERE subject_type = 'workflow_runs' AND status = 'active'"
                ).fetchone()[0]
                db.connection.execute(
                    "UPDATE claims SET valid_until = ? WHERE claim_id = ?",
                    ("2000-01-01T00:00:00Z", claim_id),
                )
                db.connection.commit()

            # Need to override default database path for the test.
            import scripts.cron_claim_drift_check as drift_module
            original_database = drift_module.DEFAULT_DATABASE
            drift_module.DEFAULT_DATABASE = db_path
            try:
                result = claim_drift_main()
            finally:
                drift_module.DEFAULT_DATABASE = original_database

            self.assertEqual(result, 1)

    def test_claim_drift_flags_tampered_workflow_run(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            catalog = Path(directory) / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Aero Travel Mug,Supplier A,45,2,80,30\n",
                encoding="utf-8",
            )

            self.run_dry_preflight(catalog, db_path, top_n=1)
            run_product_research(catalog, database_path=db_path, top_n=1)

            with CanonicalDB(db_path) as db:
                row = db.connection.execute(
                    "SELECT run_id, result_json FROM workflow_runs LIMIT 1"
                ).fetchone()
                stored = json_module.loads(row["result_json"])
                stored["written"].append({"tampered": True})
                db.connection.execute(
                    "UPDATE workflow_runs SET result_json = ? WHERE run_id = ?",
                    (json_module.dumps(stored, sort_keys=True), row["run_id"]),
                )
                db.connection.commit()

            import scripts.cron_claim_drift_check as drift_module
            original_database = drift_module.DEFAULT_DATABASE
            drift_module.DEFAULT_DATABASE = db_path
            try:
                result = claim_drift_main()
            finally:
                drift_module.DEFAULT_DATABASE = original_database

            self.assertEqual(result, 1)


import json as json_module


if __name__ == "__main__":
    unittest.main()
