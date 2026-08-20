from pathlib import Path
import json
import os
from tempfile import TemporaryDirectory
import unittest

from canonical.db import CanonicalDB
from scripts import run_checks
from scripts.runtime_metadata import detect_active_model


class RunChecksTests(unittest.TestCase):
    def test_test_discovery_is_rooted_at_the_project_when_cwd_changes(self):
        original_cwd = Path.cwd()
        with TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                suite = run_checks.build_test_suite()
            finally:
                os.chdir(original_cwd)

        self.assertGreater(suite.countTestCases(), 0)

    def test_isolated_smoke_uses_temporary_database_and_control_plane(self):
        result = run_checks.run_isolated_smoke()

        self.assertGreaterEqual(result["smoke"]["delta_entities"], 1)
        self.assertGreaterEqual(result["smoke"]["delta_metrics"], 1)
        self.assertGreaterEqual(result["smoke"]["delta_events"], 1)
        self.assertIn("routing_index_stale", result["routing"]["routing"])
        self.assertFalse(result["routing"]["routing"]["routing_index_stale"])
        self.assertTrue(result["database"].endswith("efficiens.db"))

    def test_detect_active_model_combines_runtime_model_and_provider(self):
        original_model = os.environ.get("HERMES_RUNTIME_MODEL")
        original_provider = os.environ.get("HERMES_RUNTIME_PROVIDER")
        try:
            os.environ["HERMES_RUNTIME_MODEL"] = "gpt-5.6-terra"
            os.environ["HERMES_RUNTIME_PROVIDER"] = "openai-codex"
            self.assertEqual(
                detect_active_model(),
                "gpt-5.6-terra:openai-codex",
            )
        finally:
            if original_model is None:
                os.environ.pop("HERMES_RUNTIME_MODEL", None)
            else:
                os.environ["HERMES_RUNTIME_MODEL"] = original_model
            if original_provider is None:
                os.environ.pop("HERMES_RUNTIME_PROVIDER", None)
            else:
                os.environ["HERMES_RUNTIME_PROVIDER"] = original_provider

    def test_detect_active_model_returns_none_when_no_vars_set(self):
        original_model = os.environ.pop("HERMES_RUNTIME_MODEL", None)
        original_provider = os.environ.pop("HERMES_RUNTIME_PROVIDER", None)
        original_active = os.environ.pop("HERMES_ACTIVE_MODEL", None)
        try:
            self.assertIsNone(detect_active_model())
        finally:
            if original_model is not None:
                os.environ["HERMES_RUNTIME_MODEL"] = original_model
            if original_provider is not None:
                os.environ["HERMES_RUNTIME_PROVIDER"] = original_provider
            if original_active is not None:
                os.environ["HERMES_ACTIVE_MODEL"] = original_active

    def test_record_run_check_writes_telemetry_row(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            run_checks._record_run_check(
                db_path,
                tests_ok=True,
                test_failures=[],
                test_duration_ms=1234,
                smoke={"duration_ms": 100, "delta_entities": 1, "delta_metrics": 2},
                routing={"duration_ms": 200},
                wiki={"duration_ms": 50, "status": "fresh"},
                model_or_agent="kimi-k2.7-code",
                started_at="2026-08-19T00:00:00Z",
            )
            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="run_checks", limit=1)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["request_type"], "run_checks")
                self.assertEqual(row["model_or_agent"], "kimi-k2.7-code")
                self.assertEqual(row["duration_ms"], 1584)
                self.assertEqual(
                    row["input_size"],
                    run_checks._json_payload_size({"tests_ok": True, "test_failures": []}),
                )
                self.assertEqual(
                    row["handoff_size"],
                    run_checks._json_payload_size(
                        {
                            "smoke": {"duration_ms": 100, "delta_entities": 1, "delta_metrics": 2},
                            "routing": {"duration_ms": 200},
                            "wiki": {"duration_ms": 50, "status": "fresh"},
                        }
                    ),
                )
                self.assertEqual(row["verification_result"], "pass")
                resource = json.loads(row["resource_usage_json"])
                self.assertEqual(resource["test_count"], 1)
                self.assertEqual(resource["smoke_entities_delta"], 1)
                self.assertEqual(resource["smoke_metrics_delta"], 2)
                self.assertEqual(resource["unit_test_duration_ms"], 1234)

    def test_previous_retry_count_resets_after_accepted_run(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with CanonicalDB(db_path) as db:
                # First run: no prior row → retries 0.
                self.assertEqual(run_checks._previous_retry_count(db), 0)
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    acceptance_status="rejected",
                    retries=0,
                )
                # Prior run rejected → this run is retry 1.
                self.assertEqual(run_checks._previous_retry_count(db), 1)
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    acceptance_status="rejected",
                    retries=1,
                )
                # Prior run rejected again → retry 2.
                self.assertEqual(run_checks._previous_retry_count(db), 2)
                db.record_run(
                    request_type="run_checks",
                    route_selected="deterministic",
                    acceptance_status="accepted",
                    retries=2,
                )
                # Prior run accepted → chain resets to 0.
                self.assertEqual(run_checks._previous_retry_count(db), 0)

    def test_record_run_check_marks_fail_when_wiki_stale(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            run_checks._record_run_check(
                db_path,
                tests_ok=True,
                test_failures=[],
                test_duration_ms=500,
                smoke={},
                routing={},
                wiki={"duration_ms": 50, "status": "stale"},
            )
            with CanonicalDB(db_path) as db:
                rows = db.get_run_metrics(request_type="run_checks", limit=1)
                self.assertEqual(rows[0]["verification_result"], "fail")
                self.assertEqual(json.loads(rows[0]["errors_json"]), ["wiki_not_fresh"])


if __name__ == "__main__":
    unittest.main()
