from pathlib import Path
import json
import os
import subprocess
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from canonical.db import CanonicalDB
from scripts import run_checks
from scripts.runtime_metadata import detect_active_model


class RunChecksTests(unittest.TestCase):
    def test_pytest_accepts_suite_above_old_deadline_with_bounded_headroom(self):
        # A deterministic subprocess seam models wall time without a long sleep.
        def simulated_suite(command, **kwargs):
            if kwargs["timeout"] < 250:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "3 passed in 250.0s\n", "")

        with (
            patch.dict(sys.modules, {"pytest": ModuleType("pytest")}),
            patch.object(run_checks.subprocess, "run", side_effect=simulated_suite) as run_mock,
            patch.object(run_checks, "_write_timeout_log", return_value="<test-log>"),
            patch.object(run_checks, "build_test_suite", side_effect=AssertionError("no fallback")),
        ):
            ok, failures, _elapsed, count, failure_count = run_checks.run_tests()

        self.assertTrue(ok, failures)
        self.assertEqual((count, failure_count), (3, 0))
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 360)
        self.assertEqual(
            run_mock.call_args.args[0],
            [sys.executable, "-m", "pytest", str(run_checks.PROJECT_ROOT / "tests"), "-q"],
        )

    def test_pytest_test_count_sums_executed_test_outcomes(self):
        self.assertEqual(
            run_checks._pytest_test_count("2 failed, 174 passed, 5 subtests passed in 17.9s"),
            176,
        )

    def test_pytest_outcomes_ignore_captured_fake_counts(self):
        outcomes = run_checks._pytest_outcome_counts(
            "test output says 999 passed\n"
            "================ 2 failed, 174 passed, 5 subtests passed in 17.9s ================\n"
        )

        self.assertEqual(outcomes["test_count"], 176)
        self.assertEqual(outcomes["test_failure_count"], 2)

    def test_pytest_failure_ids_only_use_short_summary(self):
        failure_ids = run_checks._pytest_failure_ids(
            "=========================== short test summary info ===========================\n"
            "FAILED tests/fake.py::test_fake - captured spoof\n"
            "=========================== short test summary info ===========================\n"
            "FAILED tests/test_real.py::test_real - AssertionError\n"
            "========================= 1 failed, 10 passed in 1.2s =========================\n"
        )

        self.assertEqual(failure_ids, ["tests/test_real.py::test_real"])

    def test_failure_hash_covers_ids_beyond_persisted_sample(self):
        shared = [f"tests/test_many.py::test_{index}" for index in range(100)]
        first = [*shared, "tests/test_many.py::test_tail_a"]
        second = [*shared, "tests/test_many.py::test_tail_b"]

        self.assertNotEqual(
            run_checks._failure_ids_sha256(first),
            run_checks._failure_ids_sha256(second),
        )

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
            project_root = Path(directory)
            (project_root / "scripts").mkdir()
            (project_root / "scripts" / "check.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            db_path = project_root / "efficiens.db"
            run_checks._record_run_check(
                db_path,
                tests_ok=True,
                test_failures=[],
                test_count=42,
                test_duration_ms=1234,
                smoke={"duration_ms": 100, "delta_entities": 1, "delta_metrics": 2},
                routing={"duration_ms": 200},
                wiki={"duration_ms": 50, "status": "fresh"},
                model_or_agent="kimi-k2.7-code",
                started_at="2026-08-19T00:00:00Z",
                project_root=project_root,
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
                self.assertEqual(resource["test_count"], 42)
                self.assertEqual(resource["source_file_count"], 1)
                self.assertEqual(len(resource["source_fingerprint"]), 64)
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

    def test_record_run_check_rejects_source_drift(self):
        with TemporaryDirectory() as directory:
            project_root = Path(directory)
            (project_root / "scripts").mkdir()
            source = project_root / "scripts" / "check.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            before = run_checks.correctness_snapshot(project_root)
            source.write_text("VALUE = 2\n", encoding="utf-8")
            db_path = project_root / "efficiens.db"

            run_checks._record_run_check(
                db_path,
                tests_ok=True,
                test_failures=[],
                test_count=1,
                test_failure_count=0,
                test_duration_ms=10,
                smoke={},
                routing={},
                wiki={},
                project_root=project_root,
                source_snapshot_before=before,
            )

            with CanonicalDB(db_path) as db:
                row = db.get_run_metrics(request_type="run_checks", limit=1)[0]
            resource = json.loads(row["resource_usage_json"])
            self.assertEqual(row["verification_result"], "fail")
            self.assertIn("source_changed_during_run", json.loads(row["errors_json"]))
            self.assertTrue(resource["source_drift"])
            self.assertIsNone(resource["source_fingerprint"])

    def test_record_run_check_persists_failure_identities(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            run_checks._record_run_check(
                db_path,
                tests_ok=False,
                test_failures=["tests/test_real.py::test_real"],
                test_count=11,
                test_failure_count=1,
                test_duration_ms=10,
                smoke={},
                routing={},
                wiki={},
                project_root=Path(directory),
            )

            with CanonicalDB(db_path) as db:
                row = db.get_run_metrics(request_type="run_checks", limit=1)[0]
            resource = json.loads(row["resource_usage_json"])
            self.assertEqual(
                resource["test_failure_ids"],
                ["tests/test_real.py::test_real"],
            )
            self.assertEqual(len(resource["test_failure_ids_sha256"]), 64)

    def test_fingerprint_failure_does_not_break_telemetry_recording(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "efficiens.db"
            with patch.object(
                run_checks,
                "correctness_snapshot",
                side_effect=OSError("locked source"),
            ):
                run_checks._record_run_check(
                    db_path,
                    tests_ok=True,
                    test_failures=[],
                    test_count=1,
                    test_failure_count=0,
                    test_duration_ms=10,
                    smoke={},
                    routing={},
                    wiki={},
                    project_root=Path(directory),
                )

            with CanonicalDB(db_path) as db:
                row = db.get_run_metrics(request_type="run_checks", limit=1)[0]
            self.assertEqual(row["verification_result"], "fail")
            self.assertIn("source_fingerprint_unavailable", json.loads(row["errors_json"]))

    def test_main_returns_nonzero_on_source_drift_without_telemetry(self):
        before = {
            "source_fingerprint": "before",
            "source_file_count": 1,
            "tested_commit": "abc",
            "fingerprint_error": None,
        }
        after = {**before, "source_fingerprint": "after"}
        arguments = SimpleNamespace(
            database=None,
            persistent_smoke=False,
            skip_smoke=True,
            record_telemetry=False,
        )
        with (
            patch.object(run_checks, "_parse_args", return_value=arguments),
            patch.object(run_checks, "detect_active_model", return_value=None),
            patch.object(
                run_checks,
                "run_tests",
                return_value=(True, [], 10, 1, 0),
            ),
            patch.object(
                run_checks,
                "_safe_correctness_snapshot",
                side_effect=[before, after],
            ),
        ):
            code = run_checks.main()

        self.assertEqual(code, 1)

    def test_pytest_timeout_returns_diagnostics_without_unittest_fallback(self) -> None:
        partial = "12 passed, 3 failed in 239.5s\nFAILED tests/test_x.py::test_y"
        timeout = subprocess.TimeoutExpired(
            cmd=["pytest"], timeout=run_checks.PYTEST_TIMEOUT_SECONDS, output=partial, stderr=""
        )
        with (
            patch.dict(sys.modules, {"pytest": ModuleType("pytest")}),
            patch.object(run_checks.subprocess, "run", side_effect=timeout),
            patch.object(
                run_checks, "_write_timeout_log", return_value="<test-log>"
            ) as log_mock,
            patch.object(
                run_checks.unittest.TestLoader, "discover",
                side_effect=AssertionError("unittest fallback must not run after timeout"),
            ),
        ):
            tests_ok, failures, _elapsed_ms, test_count, failure_count = (
                run_checks.run_tests()
            )

        self.assertFalse(tests_ok)
        self.assertEqual(failures[0], f"pytest_timeout_after_{run_checks.PYTEST_TIMEOUT_SECONDS}s")
        self.assertIn("test_y", " ".join(failures))
        self.assertEqual(test_count, 15)
        self.assertGreaterEqual(failure_count, 1)
        log_mock.assert_called_once()

    def test_importable_pytest_does_not_require_path_entry(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "3 passed in 0.1s", "")
        with (
            patch.dict(sys.modules, {"pytest": ModuleType("pytest")}),
            patch.object(run_checks.shutil, "which", return_value=None),
            patch.object(run_checks.subprocess, "run", return_value=completed) as run_mock,
            patch.object(run_checks, "build_test_suite", side_effect=AssertionError("no fallback")),
        ):
            self.assertEqual(run_checks.run_tests()[3], 3)
        self.assertEqual(run_mock.call_args.args[0][:3], [sys.executable, "-m", "pytest"])

    def test_timeout_with_passing_partial_summary_still_rejects(self):
        # Regression pin: passing assertions do not waive the process deadline.
        with (
            patch.dict(sys.modules, {"pytest": ModuleType("pytest")}),
            patch.object(run_checks.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                ["pytest"], run_checks.PYTEST_TIMEOUT_SECONDS,
                output="3 passed in 239.5s\n", stderr="")) as run_mock,
            patch.object(run_checks, "_write_timeout_log", return_value="<test-log>") as log_mock,
            patch.object(run_checks, "build_test_suite", side_effect=AssertionError("no fallback")),
        ):
            ok, failures, _elapsed, count, failure_count = run_checks.run_tests()

        self.assertFalse(ok)
        self.assertEqual(failures, [f"pytest_timeout_after_{run_checks.PYTEST_TIMEOUT_SECONDS}s"])
        self.assertEqual((count, failure_count), (3, 1))
        run_mock.assert_called_once()
        log_mock.assert_called_once()

    def test_timeout_bytes_and_mixed_streams_preserve_diagnostics(self) -> None:
        for output, stderr in ((b"2 passed in 0.1s\n", "FAILED tests/x.py::test_y\n"),
                               ("2 passed in 0.1s\n", b"FAILED tests/x.py::test_y\xff\n")):
            with (
                self.subTest(output=output),
                patch.dict(sys.modules, {"pytest": ModuleType("pytest")}),
                patch.object(run_checks.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                    ["pytest"], run_checks.PYTEST_TIMEOUT_SECONDS, output=output, stderr=stderr)),
                patch.object(run_checks, "_write_timeout_log", return_value="<test-log>") as log,
                patch.object(run_checks, "build_test_suite", side_effect=AssertionError("no fallback")),
            ):
                ok, failures, _elapsed, count, _failed = run_checks.run_tests()
            self.assertFalse(ok)
            self.assertEqual(count, 2)
            self.assertIn("test_y", " ".join(failures))
            self.assertIsInstance(log.call_args.args[0], str)

    def test_timeout_log_prunes_logs_older_than_retention(self) -> None:
        import time as _time

        with TemporaryDirectory() as directory:
            root = Path(directory)
            tmp = root / "tmp"
            tmp.mkdir()
            old = tmp / "pytest-timeout-20200101T000000Z.log"
            recent = tmp / "pytest-timeout-20990101T000000Z.log"
            unrelated = tmp / "other-old.log"
            for path in (old, recent, unrelated):
                path.write_text("x", encoding="utf-8")
            stale = _time.time() - (run_checks.TIMEOUT_LOG_RETENTION_DAYS + 1) * 86400
            os.utime(old, (stale, stale))
            os.utime(unrelated, (stale, stale))
            with patch.object(run_checks, "PROJECT_ROOT", root):
                written = run_checks._write_timeout_log("partial")
            self.assertTrue(Path(written).is_file())
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
