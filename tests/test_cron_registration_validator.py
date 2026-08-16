from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.cron_registration_validator import validate_cron_registration


class CronRegistrationValidatorTests(unittest.TestCase):
    """Verify cron job registration validator behaves correctly."""

    def test_validator_reports_expected_wrapper_jobs(self):
        report = validate_cron_registration()
        checked_names = {item["job"] for item in report["checked"]}
        expected = {
            "a1_wiki_regen.py",
            "a2_green_gate.py",
            "a2_full_test_gate.py",
            "a3_routing_cache_sweep.py",
            "a4_archive_stale_workflows.py",
            "a5_routing_refresh.py",
            "a6_alias_sweep.py",
            "a7_queue_hygiene.py",
            "a8_telemetry_harvest.py",
            "a9_claim_drift_check.py",
        }
        self.assertTrue(expected.issubset(checked_names))

    def test_validator_passes_for_existing_targets(self):
        report = validate_cron_registration()
        self.assertTrue(report["ok"], f"Failures: {report['failures']}")

    def test_validator_detects_missing_wrapper(self):
        from scripts import cron_registration_validator as validator
        original = validator.HERMES_SCRIPTS
        with TemporaryDirectory() as directory:
            validator.HERMES_SCRIPTS = Path(directory)
            try:
                report = validator.validate_cron_registration()
                self.assertFalse(report["ok"])
                codes = {failure["code"] for failure in report["failures"]}
                self.assertIn("wrapper_missing", codes)
            finally:
                validator.HERMES_SCRIPTS = original


if __name__ == "__main__":
    unittest.main()
