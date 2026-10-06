from __future__ import annotations

import json
import io
import sqlite3
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from canonical.db import CanonicalDB
from scripts import cron_telemetry_harvest, feedback_evaluation_loop

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _seed_run_metrics(
    path: Path,
    *,
    completed_at: str,
    verification_result: str = "pass",
    acceptance_status: str = "accepted",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE run_metrics (
                run_id TEXT PRIMARY KEY,
                request_type TEXT,
                verification_result TEXT,
                acceptance_status TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO run_metrics "
            "(run_id, request_type, verification_result, acceptance_status,"
            " started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "run-1",
                "run_checks",
                verification_result,
                acceptance_status,
                completed_at,
                completed_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


class ProofNoteTests(unittest.TestCase):
    def test_missing_database_reports_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(
                cron_telemetry_harvest._proof_note(Path(directory)),
                "proof_unavailable",
            )

    def test_fresh_accepted_proof_is_silent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _seed_run_metrics(root / "canonical" / "efficiens.db", completed_at=now)
            self.assertIsNone(cron_telemetry_harvest._proof_note(root))

    def test_stale_and_rejected_proofs_are_flagged(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            _seed_run_metrics(
                root / "canonical" / "efficiens.db",
                completed_at="2020-01-01T00:00:00Z",
            )
            stale = cron_telemetry_harvest._proof_note(root)
            assert stale is not None
            self.assertTrue(stale.startswith("proof_stale"), stale)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "canonical").mkdir()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _seed_run_metrics(
                root / "canonical" / "efficiens.db",
                completed_at=now,
                verification_result="fail",
                acceptance_status="rejected",
            )
            rejected = cron_telemetry_harvest._proof_note(root)
            assert rejected is not None
            self.assertTrue(rejected.startswith("proof_not_accepted"), rejected)


def _seed_turn_metrics(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE turn_metrics (
                turn_key TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                outcome TEXT NOT NULL,
                error_category TEXT,
                api_error_count INTEGER NOT NULL,
                retry_count INTEGER NOT NULL,
                api_request_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                tool_error_count INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                api_duration_ms INTEGER NOT NULL,
                tool_duration_ms INTEGER NOT NULL,
                approx_input_tokens INTEGER
            );
            """
        )
        for index in range(3):
            connection.execute(
                """
                INSERT INTO turn_metrics (
                    turn_key, completed_at, provider, model, outcome,
                    error_category, api_error_count, retry_count,
                    api_request_count, tool_call_count, tool_error_count,
                    duration_ms, api_duration_ms, tool_duration_ms,
                    approx_input_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"turn-{index}",
                    f"2026-08-21T0{index}:00:00Z",
                    "openai-codex",
                    "gpt-5.6-terra",
                    "complete",
                    "APIConnectionError",
                    1,
                    0,
                    2,
                    1,
                    0,
                    1000,
                    900,
                    50,
                    1000,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _retention_completed(*, returncode: int = 0, status: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(
            {
                "schema": "telemetry-retention-report.v1",
                "status": status,
                "raw_rows_deleted": 0,
                "rollups_created": 0,
            }
        ),
        stderr="",
    )


class FeedbackEvaluationLoopTests(unittest.TestCase):
    def test_tool_error_signals_require_recurrence_across_turns_and_exclude_blocks(self):
        with TemporaryDirectory() as directory:
            turn_path = Path(directory) / "turn-metrics.sqlite"
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE turn_metrics (
                        turn_key TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL,
                        tool_error_count INTEGER NOT NULL,
                        tool_error_categories_json TEXT
                    )
                    """
                )
                rows = [
                    (
                        "turn-a",
                        "2026-08-21T01:00:00Z",
                        5,
                        json.dumps(
                            {
                                "search_files_invalid_regex": 3,
                                "terminal_blocked": 1,
                                "terminal_timeout": 1,
                            }
                        ),
                    ),
                    (
                        "turn-b",
                        "2026-08-21T02:00:00Z",
                        3,
                        json.dumps(
                            {"terminal_blocked": 1, "terminal_timeout": 2}
                        ),
                    ),
                    (
                        "turn-c",
                        "2026-08-21T03:00:00Z",
                        1,
                        json.dumps({"terminal_blocked": 1}),
                    ),
                ]
                connection.executemany(
                    "INSERT INTO turn_metrics VALUES (?, ?, ?, ?)", rows
                )
                connection.commit()
            finally:
                connection.close()

            signals = feedback_evaluation_loop._turn_tool_error_signals(
                turn_path,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(
                signals,
                [
                    {
                        "signal_key": "turn_tool_error:terminal_timeout",
                        "source": "turn_telemetry",
                        "category": "terminal_timeout",
                        "occurrences": 3,
                        "affected_turns": 2,
                        "recommendation": "review_tool_reliability",
                    }
                ],
            )

    def test_turn_signal_requires_minimum_repeated_count(self):
        with TemporaryDirectory() as directory:
            turn_path = Path(directory) / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute("DELETE FROM turn_metrics WHERE turn_key = ?", ("turn-2",))
                connection.commit()
            finally:
                connection.close()

            signals = feedback_evaluation_loop._turn_error_signals(
                turn_path,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(signals, [])

    def test_a8_child_failure_returns_nonzero(self):
        completed = SimpleNamespace(returncode=1, stdout='{"status":"error"}', stderr="boom")
        stdout = io.StringIO()
        with (
            patch.object(
                cron_telemetry_harvest.subprocess,
                "run",
                side_effect=[_retention_completed(), completed],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = cron_telemetry_harvest.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("FEEDBACK EVALUATION FAIL", stdout.getvalue())

    def test_a8_retention_failure_stops_before_feedback_refresh(self):
        stdout = io.StringIO()
        with (
            patch.object(
                cron_telemetry_harvest.subprocess,
                "run",
                return_value=_retention_completed(returncode=2, status="degraded"),
            ) as run_mock,
            redirect_stdout(stdout),
        ):
            exit_code = cron_telemetry_harvest.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(run_mock.call_count, 1)
        self.assertIn("TELEMETRY RETENTION FAIL", stdout.getvalue())

    def test_a8_monthly_archives_require_explicit_environment_opt_in(self):
        feedback = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "feedback-evaluation-report.v1",
                    "status": "ready",
                    "candidates": [],
                }
            ),
            stderr="",
        )
        with (
            patch.dict(
                cron_telemetry_harvest.os.environ,
                {"HERMES_TELEMETRY_MONTHLY_ARCHIVES": "1"},
            ),
            patch.object(
                cron_telemetry_harvest.subprocess,
                "run",
                side_effect=[_retention_completed(), feedback],
            ) as run_mock,
        ):
            exit_code = cron_telemetry_harvest.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run_mock.call_args_list[0].args[0][1:],
            ["scripts/telemetry_retention.py", "maintain", "--archive-months"],
        )

    def test_a8_rejects_review_status_without_valid_candidate_payload(self):
        for payload in (
            {"status": "review_required", "candidates": []},
            {
                "schema": "feedback-evaluation-report.v1",
                "status": "review_required",
                "candidates": [],
            },
        ):
            with self.subTest(payload=payload):
                completed = SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(payload),
                    stderr="",
                )
                with patch.object(
                    cron_telemetry_harvest.subprocess,
                    "run",
                    side_effect=[_retention_completed(), completed],
                ):
                    self.assertEqual(cron_telemetry_harvest.main(), 1)

    def test_acceptance_comparison_enforces_cohort_count_failures_and_duration(self):
        baseline_evidence = {
            "cohort_id": "feedback-harness-v1",
            "test_count": 4,
            "test_failure_count": 0,
            "duration_ms": 100,
        }
        baseline = {"evidence_json": json.dumps(baseline_evidence)}
        cases = {
            "cohort_mismatch": {**baseline_evidence, "cohort_id": "other"},
            "test_count_mismatch": {**baseline_evidence, "test_count": 5},
            "additional_failure": {**baseline_evidence, "test_failure_count": 1},
            "duration_over_20_percent": {**baseline_evidence, "duration_ms": 121},
        }
        for name, evidence in cases.items():
            with self.subTest(name=name):
                self.assertFalse(
                    feedback_evaluation_loop._comparison_is_acceptable(
                        baseline,
                        {"evidence_json": json.dumps(evidence)},
                    )
                )
        self.assertTrue(
            feedback_evaluation_loop._comparison_is_acceptable(
                baseline,
                {"evidence_json": json.dumps({**baseline_evidence, "duration_ms": 120})},
            )
        )

    def test_missing_or_stale_report_still_surfaces_canonical_pending_candidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = "feedback-candidate-pending"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Pending feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )

            missing = feedback_evaluation_loop.feedback_status(
                canonical_database=canonical_path,
                report_path=root / "missing.json",
                now="2026-08-21T05:00:00Z",
            )
            stale_path = root / "stale.json"
            stale_path.write_text(
                json.dumps(
                    {
                        "schema": "feedback-evaluation-report.v1",
                        "generated_at": "2026-08-19T00:00:00Z",
                        "status": "review_required",
                        "candidates": [{"candidate_id": candidate_id}],
                    }
                ),
                encoding="utf-8",
            )
            stale = feedback_evaluation_loop.feedback_status(
                canonical_database=canonical_path,
                report_path=stale_path,
                now="2026-08-21T05:00:00Z",
            )

            for status, report_status in ((missing, "unavailable"), (stale, "stale")):
                self.assertEqual(status["status"], "review_required")
                self.assertEqual(status["report_status"], report_status)
                self.assertEqual(status["pending_review_count"], 1)
                self.assertEqual(status["candidate_ids"], [candidate_id])

    def test_direct_script_entrypoint_resolves_workspace_imports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "feedback_evaluation_loop.py"),
                    "status",
                    "--database",
                    str(canonical_path),
                    "--report",
                    str(root / "missing.json"),
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )

        self.assertNotIn("ModuleNotFoundError", completed.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "unavailable")

    def test_a8_runs_feedback_refresh_and_surfaces_review_candidate(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "feedback-evaluation-report.v1",
                    "status": "review_required",
                    "candidates": [{"candidate_id": "feedback-candidate-example"}],
                }
            ),
            stderr="",
        )
        stdout = io.StringIO()
        with (
            patch.object(
                cron_telemetry_harvest.subprocess,
                "run",
                side_effect=[_retention_completed(), completed],
            ) as run_mock,
            redirect_stdout(stdout),
        ):
            exit_code = cron_telemetry_harvest.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run_mock.call_args_list[0].args[0][1:],
            ["scripts/telemetry_retention.py", "maintain"],
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0][1:],
            ["scripts/feedback_evaluation_loop.py", "refresh"],
        )
        self.assertIn("FEEDBACK REVIEW REQUIRED", stdout.getvalue())
        self.assertIn("feedback-candidate-example", stdout.getvalue())

    def test_latest_report_is_replaced_without_accumulating_files(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "derived" / "feedback-evaluation" / "latest.json"
            feedback_evaluation_loop._atomic_write_json(report, {"revision": 1})
            feedback_evaluation_loop._atomic_write_json(report, {"revision": 2})

            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), {"revision": 2})
            self.assertEqual([path.name for path in report.parent.iterdir()], ["latest.json"])

    def test_status_cli_emits_compact_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            report_path = root / "report.json"
            with CanonicalDB(canonical_path):
                pass
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "feedback-evaluation-report.v1",
                        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "status": "ready",
                        "candidates": [],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = feedback_evaluation_loop.main(
                    [
                        "status",
                        "--database",
                        str(canonical_path),
                        "--report",
                        str(report_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["pending_review_count"], 0)

    def test_status_surfaces_fresh_human_review_candidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            report_path = root / "derived" / "feedback-evaluation" / "latest.json"
            candidate_id = "feedback-candidate-example"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "feedback-evaluation-report.v1",
                        "generated_at": "2026-08-21T04:00:00Z",
                        "status": "review_required",
                        "candidates": [{"candidate_id": candidate_id}],
                    }
                ),
                encoding="utf-8",
            )

            status = feedback_evaluation_loop.feedback_status(
                canonical_database=canonical_path,
                report_path=report_path,
                now="2026-08-21T05:00:00Z",
            )

            self.assertEqual(status["status"], "review_required")
            self.assertEqual(status["pending_review_count"], 1)
            self.assertEqual(status["candidate_ids"], [candidate_id])

    def test_fixed_cohort_uses_bounded_existing_control_plane_targets(self):
        snapshot = {
            "source_fingerprint": "c" * 64,
            "source_file_count": 4,
            "tested_commit": "cohort-commit",
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout="====================== 12 passed in 1.0s ======================\n",
            stderr="",
        )
        with (
            patch.object(feedback_evaluation_loop.subprocess, "run", return_value=completed) as run_mock,
            patch.object(feedback_evaluation_loop, "correctness_snapshot", return_value=snapshot),
        ):
            result = feedback_evaluation_loop.run_fixed_cohort()

        command = run_mock.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pytest", "-q"])
        self.assertEqual(result["cohort_id"], feedback_evaluation_loop.COHORT_ID)
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["test_count"], 12)
        self.assertEqual(result["test_failure_count"], 0)
        self.assertEqual(result["source_fingerprint"], "c" * 64)

    def test_fixed_cohort_bounds_process_failures(self):
        snapshot = {"source_fingerprint": "c" * 64, "tested_commit": "test"}
        for error, category in [
            (subprocess.TimeoutExpired("private", 90, output="secret"), "timeout"),
            (OSError("secret private/path"), "launch_error"),
        ]:
            with self.subTest(category=category), patch.object(
                feedback_evaluation_loop.subprocess, "run", side_effect=error,
            ), patch.object(feedback_evaluation_loop, "correctness_snapshot", return_value=snapshot):
                result = feedback_evaluation_loop.run_fixed_cohort()
                self.assertEqual(result["execution_category"], category)
                self.assertIsNone(result["exit_code"])
                self.assertEqual(result["result"], "fail")
                self.assertEqual(result["test_count"], 0)
                self.assertNotIn("secret", json.dumps(result))
                self.assertNotIn("private", json.dumps(result))

    def test_fixed_cohort_records_safe_execution_diagnostics(self):
        snapshot = {"source_fingerprint": "c" * 64, "tested_commit": "test"}
        cases = [
            (1, "", "private/path: No module named pytest\n", "pytest_unavailable"),
            (2, "1 error in 0.1s", "secret", "collection_or_interruption"),
            (5, "no tests ran", "secret", "no_tests"),
            (1, "1 failed, 2 passed in 1s", "secret", "tests_failed"),
            (3, "", "secret", "runner_error"),
            (0, "", "secret", "no_tests"),
            (0, "2 passed in 1s", "", "none"),
        ]
        for code, stdout, stderr, category in cases:
            with self.subTest(category=category), patch.object(
                feedback_evaluation_loop.subprocess, "run",
                return_value=SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr),
            ), patch.object(feedback_evaluation_loop, "correctness_snapshot", return_value=snapshot):
                result = feedback_evaluation_loop.run_fixed_cohort()
                self.assertEqual(result["execution_category"], category)
                self.assertEqual(result["exit_code"], code)
                self.assertEqual(result["python_version"], list(sys.version_info[:3]))
                self.assertNotIn("secret", json.dumps(result))
                self.assertNotIn("private/path", json.dumps(result))
                self.assertEqual(result["result"], "pass" if category == "none" else "fail")

    def test_fixed_cohort_cannot_pass_without_executed_tests(self):
        snapshot = {
            "source_fingerprint": "c" * 64,
            "source_file_count": 4,
            "tested_commit": "cohort-commit",
        }
        completed = SimpleNamespace(returncode=0, stdout="no pytest summary\n", stderr="")
        with (
            patch.object(feedback_evaluation_loop.subprocess, "run", return_value=completed),
            patch.object(feedback_evaluation_loop, "correctness_snapshot", return_value=snapshot),
        ):
            result = feedback_evaluation_loop.run_fixed_cohort()

        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["test_count"], 0)

    def test_failed_baseline_fails_refresh_and_is_retried(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            base = {
                "cohort_id": "feedback-harness-v1",
                "test_count": 4,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "baseline",
            }
            failed = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: {
                    **base,
                    "result": "fail",
                    "test_failure_count": 1,
                },
                now="2026-08-21T04:00:00Z",
            )
            recovered = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: {
                    **base,
                    "result": "pass",
                    "test_failure_count": 0,
                },
                now="2026-08-21T04:01:00Z",
            )

            self.assertEqual(failed["status"], "failed")
            self.assertEqual(recovered["status"], "review_required")
            with CanonicalDB(canonical_path, read_only=True) as db:
                task_status = db.connection.execute(
                    "SELECT status FROM tasks"
                ).fetchone()[0]
                validations = db.connection.execute(
                    "SELECT result FROM validation_results ORDER BY validated_at"
                ).fetchall()
            self.assertEqual(task_status, "baseline_recorded")
            self.assertEqual([row[0] for row in validations], ["fail", "pass"])

    def test_unevaluated_candidate_is_rebaselined_when_cohort_version_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = feedback_evaluation_loop._candidate_id(
                "turn_api_error:APIConnectionError"
            )
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Versioned cohort candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps(
                            {
                                "cohort_id": "feedback-harness-obsolete",
                                "result": "pass",
                                "test_count": 4,
                                "test_failure_count": 0,
                                "duration_ms": 100,
                                "source_fingerprint": "a" * 64,
                                "tested_commit": "old-baseline",
                            }
                        ),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            cohort_calls: list[str] = []

            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: cohort_calls.append("baseline")
                or {
                    "cohort_id": feedback_evaluation_loop.COHORT_ID,
                    "result": "pass",
                    "test_count": 5,
                    "test_failure_count": 0,
                    "duration_ms": 110,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "new-baseline",
                },
                now="2026-08-21T04:01:00Z",
            )

            self.assertEqual(cohort_calls, ["baseline"])
            self.assertEqual(report["status"], "review_required")
            with CanonicalDB(canonical_path, read_only=True) as db:
                evidence = [
                    json.loads(row[0])
                    for row in db.connection.execute(
                        "SELECT evidence_json FROM validation_results "
                        "WHERE subject_id = ? ORDER BY validated_at",
                        (candidate_id,),
                    )
                ]
            self.assertEqual(
                [item["cohort_id"] for item in evidence],
                ["feedback-harness-obsolete", feedback_evaluation_loop.COHORT_ID],
            )

    def test_explicit_rebaseline_records_audited_pending_baseline(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-rebaseline"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Rebaseline candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
                previous_baseline_id = db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps(
                            {
                                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                                "result": "pass",
                                "test_count": 4,
                                "test_failure_count": 0,
                                "duration_ms": 100,
                                "source_fingerprint": "a" * 64,
                                "tested_commit": "old-baseline",
                            }
                        ),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )

            result = feedback_evaluation_loop.rebaseline_candidate(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                reviewer="operator",
                reason="harness_maintenance",
                cohort_runner=lambda: {
                    "cohort_id": feedback_evaluation_loop.COHORT_ID,
                    "result": "pass",
                    "test_count": 5,
                    "test_failure_count": 0,
                    "duration_ms": 105,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "fresh-baseline",
                },
                now="2026-08-21T05:00:00Z",
            )

            self.assertEqual(result["phase"], "baseline")
            with CanonicalDB(canonical_path, read_only=True) as db:
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                baselines = db.connection.execute(
                    "SELECT evidence_json FROM validation_results "
                    "WHERE subject_id = ? AND validator = ? ORDER BY validated_at, rowid",
                    (candidate_id, "feedback_evaluation.baseline.v1"),
                ).fetchall()
                event_payload = json.loads(
                    db.connection.execute(
                        "SELECT payload_json FROM events WHERE subject_id = ? "
                        "AND event_type = ?",
                        (candidate_id, "feedback_candidate_rebaselined"),
                    ).fetchone()[0]
                )

            self.assertEqual(task_status, "baseline_recorded")
            self.assertEqual(len(baselines), 2)
            self.assertEqual(json.loads(baselines[-1][0])["test_count"], 5)
            self.assertEqual(
                event_payload,
                {
                    "previous_baseline_validation_id": previous_baseline_id,
                    "reason": "harness_maintenance",
                    "reviewer": "operator",
                },
            )

    def test_explicit_rebaseline_refuses_evaluated_candidate(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-already-evaluated"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Evaluated candidate",
                        "task_type": "improvement_candidate",
                        "status": "evaluated",
                        "scope": "feedback_evaluation",
                    },
                )

            with self.assertRaisesRegex(ValueError, "pending candidate"):
                feedback_evaluation_loop.rebaseline_candidate(
                    canonical_database=canonical_path,
                    candidate_id=candidate_id,
                    reviewer="operator",
                    reason="harness_maintenance",
                    cohort_runner=lambda: self.fail("Evaluated candidates cannot rebaseline."),
                    now="2026-08-21T05:00:00Z",
                )

    def test_explicit_rebaseline_refuses_foreign_task_type_or_scope(self):
        for task_type, scope in (
            ("implementation", "feedback_evaluation"),
            ("improvement_candidate", "unrelated_scope"),
        ):
            with self.subTest(task_type=task_type, scope=scope):
                with TemporaryDirectory() as directory:
                    canonical_path = Path(directory) / "efficiens.db"
                    candidate_id = "feedback-candidate-foreign-probe"
                    with CanonicalDB(canonical_path) as db:
                        db.insert(
                            "tasks",
                            {
                                "task_id": candidate_id,
                                "title": "Foreign candidate probe",
                                "task_type": task_type,
                                "status": "candidate",
                                "scope": scope,
                            },
                        )

                    with self.assertRaisesRegex(ValueError, "requires a feedback"):
                        feedback_evaluation_loop.rebaseline_candidate(
                            canonical_database=canonical_path,
                            candidate_id=candidate_id,
                            reviewer="operator",
                            reason="harness_maintenance",
                            cohort_runner=lambda: self.fail(
                                "Foreign tasks cannot be rebaselined."
                            ),
                            now="2026-08-21T05:00:00Z",
                        )
                    with CanonicalDB(canonical_path, read_only=True) as db:
                        task_status = db.connection.execute(
                            "SELECT status FROM tasks WHERE task_id = ?",
                            (candidate_id,),
                        ).fetchone()[0]
                        baseline_rows = db.connection.execute(
                            "SELECT COUNT(*) FROM validation_results WHERE subject_id = ?",
                            (candidate_id,),
                        ).fetchone()[0]
                    self.assertEqual(task_status, "candidate")
                    self.assertEqual(baseline_rows, 0)

    def test_explicit_rebaseline_aborts_when_candidate_changed_during_cohort(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-concurrent-flip"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Concurrent flip candidate",
                        "task_type": "improvement_candidate",
                        "status": "candidate",
                        "scope": "feedback_evaluation",
                    },
                )

            def flipping_cohort_runner():
                # Simulates a concurrent evaluate/decide flipping the task
                # status while the rebaseline cohort is running.
                with CanonicalDB(canonical_path) as db:
                    db.update("tasks", candidate_id, {"status": "evaluated"})
                return {
                    "cohort_id": feedback_evaluation_loop.COHORT_ID,
                    "result": "pass",
                    "test_count": 5,
                    "test_failure_count": 0,
                    "duration_ms": 105,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "fresh-baseline",
                }

            with self.assertRaisesRegex(
                ValueError, "no longer pending; rebaseline aborted"
            ):
                feedback_evaluation_loop.rebaseline_candidate(
                    canonical_database=canonical_path,
                    candidate_id=candidate_id,
                    reviewer="operator",
                    reason="harness_maintenance",
                    cohort_runner=flipping_cohort_runner,
                    now="2026-08-21T05:00:00Z",
                )
            with CanonicalDB(canonical_path, read_only=True) as db:
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                baseline_rows = db.connection.execute(
                    "SELECT COUNT(*) FROM validation_results WHERE subject_id = ?",
                    (candidate_id,),
                ).fetchone()[0]
                event_rows = db.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE subject_id = ? "
                    "AND event_type = ?",
                    (candidate_id, "feedback_candidate_rebaselined"),
                ).fetchone()[0]
            self.assertEqual(task_status, "evaluated")
            self.assertEqual(baseline_rows, 0)
            self.assertEqual(event_rows, 0)

    def test_rebaseline_cli_fails_closed_on_failed_cohort(self):
        """A failed rebaseline cohort must exit nonzero even though the
        baseline row is recorded as baseline_failed."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = "feedback-candidate-cli-fail-closed"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "CLI fail-closed probe",
                        "task_type": "improvement_candidate",
                        "status": "candidate",
                        "scope": "feedback_evaluation",
                    },
                )
            cohort = {
                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                "result": "fail",
                "test_count": 5,
                "test_failure_count": 1,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "cli-fail",
            }

            exit_code = None
            with patch.object(
                feedback_evaluation_loop,
                "run_fixed_cohort",
                side_effect=lambda: cohort,
            ):
                with redirect_stdout(io.StringIO()):
                    exit_code = feedback_evaluation_loop.main(
                        [
                            "rebaseline",
                            candidate_id,
                            "--reviewer",
                            "operator",
                            "--reason",
                            "harness_maintenance",
                            "--database",
                            str(canonical_path),
                        ]
                    )

            self.assertEqual(exit_code, 1)

    def test_rebaseline_cli_exits_zero_on_passing_cohort(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = "feedback-candidate-cli-pass"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "CLI pass probe",
                        "task_type": "improvement_candidate",
                        "status": "candidate",
                        "scope": "feedback_evaluation",
                    },
                )
            cohort = {
                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                "result": "pass",
                "test_count": 5,
                "test_failure_count": 0,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "cli-pass",
            }

            with patch.object(
                feedback_evaluation_loop,
                "run_fixed_cohort",
                side_effect=lambda: cohort,
            ):
                with redirect_stdout(io.StringIO()):
                    exit_code = feedback_evaluation_loop.main(
                        [
                            "rebaseline",
                            candidate_id,
                            "--reviewer",
                            "operator",
                            "--reason",
                            "harness_maintenance",
                            "--database",
                            str(canonical_path),
                        ]
                    )

            self.assertEqual(exit_code, 0)

    def test_candidate_evaluation_refuses_failed_baseline(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-failed-baseline"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Failed baseline candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_failed",
                        "scope": "feedback_evaluation",
                    },
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "fail",
                        "evidence_json": json.dumps(
                            {
                                "cohort_id": "feedback-harness-v1",
                                "result": "fail",
                                "test_count": 4,
                                "test_failure_count": 1,
                                "duration_ms": 100,
                                "source_fingerprint": "a" * 64,
                                "tested_commit": "baseline",
                            }
                        ),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )

            with self.assertRaisesRegex(ValueError, "passing baseline"):
                feedback_evaluation_loop.evaluate_candidate(
                    canonical_database=canonical_path,
                    candidate_id=candidate_id,
                    cohort_runner=lambda: {
                        "cohort_id": "feedback-harness-v1",
                        "result": "pass",
                        "test_count": 4,
                        "test_failure_count": 0,
                        "duration_ms": 100,
                        "source_fingerprint": "b" * 64,
                        "tested_commit": "candidate",
                    },
                    now="2026-08-21T04:01:00Z",
                )

    def test_repeated_turn_error_creates_human_review_candidate_with_baseline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "canonical" / "efficiens.db"
            canonical_path.parent.mkdir()
            with CanonicalDB(canonical_path):
                pass

            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            report_path = root / "derived" / "feedback-evaluation" / "latest.json"
            cohort_calls: list[str] = []

            def cohort_runner() -> dict[str, object]:
                cohort_calls.append("baseline")
                return {
                    "cohort_id": "feedback-harness-v1",
                    "result": "pass",
                    "test_count": 4,
                    "test_failure_count": 0,
                    "duration_ms": 120,
                    "source_fingerprint": "a" * 64,
                    "tested_commit": "abc123",
                }

            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=report_path,
                cohort_runner=cohort_runner,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(report["status"], "review_required")
            self.assertEqual(cohort_calls, ["baseline"])
            self.assertEqual(report["created_candidate_count"], 1)
            self.assertTrue(report_path.is_file())

            with CanonicalDB(canonical_path, read_only=True) as db:
                task = db.connection.execute(
                    "SELECT task_id, status, scope FROM tasks"
                ).fetchone()
                validation = db.connection.execute(
                    "SELECT subject_type, validator, result, evidence_json "
                    "FROM validation_results"
                ).fetchone()
                decision_count = db.connection.execute(
                    "SELECT COUNT(*) FROM decisions"
                ).fetchone()[0]

            self.assertIsNotNone(task)
            self.assertEqual(task["status"], "baseline_recorded")
            self.assertEqual(task["scope"], "feedback_evaluation")
            self.assertEqual(validation["subject_type"], "improvement_candidate")
            self.assertEqual(validation["validator"], "feedback_evaluation.baseline.v1")
            self.assertEqual(validation["result"], "pass")
            self.assertEqual(json.loads(validation["evidence_json"])["cohort_id"], "feedback-harness-v1")
            self.assertEqual(decision_count, 0)

    def test_repeated_canonical_error_creates_review_candidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path) as db:
                for index in range(3):
                    db.record_run(
                        request_type="run_checks",
                        errors_json=["unit_tests_failed"],
                        verification_result="fail",
                        acceptance_status="rejected",
                        started_at=f"2026-08-21T0{index}:00:00Z",
                    )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: {
                    "cohort_id": "feedback-harness-v1",
                    "result": "pass",
                    "test_count": 4,
                    "test_failure_count": 0,
                    "duration_ms": 120,
                    "source_fingerprint": "a" * 64,
                    "tested_commit": "abc123",
                },
                now="2026-08-21T04:00:00Z",
            )

            canonical_signals = [
                signal for signal in report["signals"] if signal["source"] == "run_metrics"
            ]
            self.assertEqual(len(canonical_signals), 1)
            self.assertEqual(canonical_signals[0]["category"], "unit_tests_failed")

    def test_one_refresh_shares_one_baseline_across_new_candidates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path) as db:
                for index in range(3):
                    db.record_run(
                        request_type="run_checks",
                        errors_json=["unit_tests_failed"],
                        verification_result="fail",
                        acceptance_status="rejected",
                        started_at=f"2026-08-21T0{index}:00:00Z",
                    )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            cohort_calls: list[str] = []
            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: cohort_calls.append("baseline")
                or {
                    "cohort_id": "feedback-harness-v1",
                    "result": "pass",
                    "test_count": 4,
                    "test_failure_count": 0,
                    "duration_ms": 120,
                    "source_fingerprint": "a" * 64,
                    "tested_commit": "abc123",
                },
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(report["created_candidate_count"], 2)
            self.assertEqual(cohort_calls, ["baseline"])
            with CanonicalDB(canonical_path, read_only=True) as db:
                validation_count = db.connection.execute(
                    "SELECT COUNT(*) FROM validation_results"
                ).fetchone()[0]
            self.assertEqual(validation_count, 2)

    def test_rejected_candidate_remains_rejected_when_signal_repeats_within_window(self):
        """Same-window recurrence still honours the terminal-rejection semantics."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = feedback_evaluation_loop._candidate_id(
                "turn_api_error:APIConnectionError"
            )
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Rejected feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "rejected",
                        "scope": "feedback_evaluation",
                        "updated_at": "2026-08-20T12:00:00Z",
                    },
                )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            cohort_calls: list[str] = []
            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: cohort_calls.append("called") or {},
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["candidates"], [])
            self.assertEqual(cohort_calls, [])
            with CanonicalDB(canonical_path, read_only=True) as db:
                status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
            self.assertEqual(status, "rejected")

    def test_rejected_candidate_reopens_when_signal_recurs_after_window(self):
        """A new rolling window containing the same signal must re-open a
        previously-rejected candidate so post-fix reviews can be measured."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = feedback_evaluation_loop._candidate_id(
                "turn_api_error:APIConnectionError"
            )
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Rejected feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "rejected",
                        "scope": "feedback_evaluation",
                        # Rejected >WINDOW_DAYS before 'now' so the recurrence is fresh.
                        "updated_at": "2026-08-13T04:00:00Z",
                    },
                )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            cohort_calls: list[str] = []
            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: cohort_calls.append("called") or {
                    "cohort_id": feedback_evaluation_loop.COHORT_ID,
                    "result": "pass",
                    "test_count": 1,
                    "test_failure_count": 0,
                    "duration_ms": 1,
                    "source_fingerprint": "abc",
                    "failure_ids_sha256": "def",
                    "tested_commit": "head",
                },
                now="2026-08-21T04:00:00Z",
            )

            with CanonicalDB(canonical_path, read_only=True) as db:
                status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                events = db.connection.execute(
                    "SELECT event_type FROM events WHERE subject_id = ? AND event_type = ?",
                    (candidate_id, "feedback_candidate_reopened"),
                ).fetchall()
            self.assertNotEqual(status, "rejected")
            self.assertEqual(len(events), 1)
            self.assertEqual(len(cohort_calls), 1)
            self.assertEqual(report["created_candidate_count"], 0)

    def test_reopened_rejected_candidate_refreshes_stale_baseline(self):
        """A post-window recurrence must not reuse the rejected cycle's baseline."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            candidate_id = feedback_evaluation_loop._candidate_id(
                "turn_api_error:APIConnectionError"
            )
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Rejected feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "rejected",
                        "scope": "feedback_evaluation",
                        "updated_at": "2026-08-13T04:00:00Z",
                    },
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps(
                            {
                                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                                "result": "pass",
                                "test_count": 4,
                                "test_failure_count": 0,
                                "duration_ms": 100,
                                "source_fingerprint": "a" * 64,
                                "tested_commit": "rejected-cycle",
                            }
                        ),
                        "validated_at": "2026-08-13T04:00:00Z",
                    },
                )
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            fresh_baseline = {
                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                "result": "pass",
                "test_count": 5,
                "test_failure_count": 0,
                "duration_ms": 110,
                "source_fingerprint": "b" * 64,
                "failure_ids_sha256": "def",
                "tested_commit": "reopened-cycle",
            }

            feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                cohort_runner=lambda: fresh_baseline,
                now="2026-08-21T04:00:00Z",
            )

            with CanonicalDB(canonical_path, read_only=True) as db:
                baselines = db.connection.execute(
                    "SELECT evidence_json FROM validation_results WHERE subject_id = ? "
                    "AND validator = ? ORDER BY validated_at, rowid",
                    (candidate_id, "feedback_evaluation.baseline.v1"),
                ).fetchall()
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]

            self.assertEqual(task_status, "baseline_recorded")
            self.assertEqual(len(baselines), 2)
            self.assertEqual(json.loads(baselines[-1][0])["test_count"], 5)

    def test_safe_end_to_end_cycle_records_evidence_and_human_decision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            baseline = {
                "cohort_id": "feedback-harness-v1",
                "result": "pass",
                "test_count": 4,
                "test_failure_count": 0,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "baseline",
            }

            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "derived" / "feedback-evaluation" / "latest.json",
                cohort_runner=lambda: baseline,
                now="2026-08-21T04:00:00Z",
            )
            candidate_id = report["candidates"][0]["candidate_id"]
            feedback_evaluation_loop.evaluate_candidate(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                cohort_runner=lambda: {
                    **baseline,
                    "duration_ms": 105,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "candidate",
                },
                now="2026-08-21T04:01:00Z",
            )
            decision_id = feedback_evaluation_loop.record_decision(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                decision="accepted",
                reviewer="operator",
                now="2026-08-21T04:02:00Z",
            )

            with CanonicalDB(canonical_path, read_only=True) as db:
                counts = {
                    table: db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("tasks", "validation_results", "decisions", "events")
                }
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                decision = db.connection.execute(
                    "SELECT decision FROM decisions WHERE decision_id = ?", (decision_id,)
                ).fetchone()[0]

            self.assertEqual(task_status, "accepted")
            self.assertEqual(decision, "accepted")
            self.assertEqual(counts["tasks"], 1)
            self.assertEqual(counts["validation_results"], 2)
            self.assertEqual(counts["decisions"], 1)
            self.assertGreaterEqual(counts["events"], 3)

    def test_acceptance_refuses_to_bypass_candidate_evaluation(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-example"
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps({"duration_ms": 100, "test_count": 4}),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )

            with self.assertRaisesRegex(ValueError, "candidate evaluation"):
                feedback_evaluation_loop.record_decision(
                    canonical_database=canonical_path,
                    candidate_id=candidate_id,
                    decision="accepted",
                    reviewer="operator",
                    now="2026-08-21T04:01:00Z",
                )

            with CanonicalDB(canonical_path, read_only=True) as db:
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                decision_count = db.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

            self.assertEqual(task_status, "baseline_recorded")
            self.assertEqual(decision_count, 0)

    def test_passing_candidate_cohort_can_be_human_accepted(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-example"
            baseline = {
                "cohort_id": "feedback-harness-v1",
                "result": "pass",
                "test_count": 4,
                "test_failure_count": 0,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "baseline",
            }
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps({**baseline, "phase": "baseline"}),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )

            candidate_result = feedback_evaluation_loop.evaluate_candidate(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                cohort_runner=lambda: {
                    **baseline,
                    "duration_ms": 105,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "candidate",
                },
                now="2026-08-21T04:01:00Z",
            )
            decision_id = feedback_evaluation_loop.record_decision(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                decision="accepted",
                reviewer="operator",
                now="2026-08-21T04:02:00Z",
            )

            self.assertEqual(candidate_result["result"], "pass")
            self.assertTrue(decision_id)
            with CanonicalDB(canonical_path, read_only=True) as db:
                task_status = db.connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                ).fetchone()[0]
                decision = db.connection.execute(
                    "SELECT decision, status FROM decisions WHERE decision_id = ?", (decision_id,)
                ).fetchone()
                validator_count = db.connection.execute(
                    "SELECT COUNT(*) FROM validation_results WHERE subject_id = ?", (candidate_id,)
                ).fetchone()[0]

            self.assertEqual(task_status, "accepted")
            self.assertEqual(dict(decision), {"decision": "accepted", "status": "recorded"})
            self.assertEqual(validator_count, 2)

    def test_candidate_evaluation_pins_original_baseline_for_decision(self):
        with TemporaryDirectory() as directory:
            canonical_path = Path(directory) / "efficiens.db"
            candidate_id = "feedback-candidate-baseline-pin"
            baseline = {
                "cohort_id": "feedback-harness-v1",
                "result": "pass",
                "test_count": 4,
                "test_failure_count": 0,
                "duration_ms": 100,
                "source_fingerprint": "a" * 64,
                "tested_commit": "baseline-1",
            }
            with CanonicalDB(canonical_path) as db:
                db.insert(
                    "tasks",
                    {
                        "task_id": candidate_id,
                        "title": "Feedback candidate",
                        "task_type": "improvement_candidate",
                        "status": "baseline_recorded",
                        "scope": "feedback_evaluation",
                    },
                )
                original_baseline_id = db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps({**baseline, "phase": "baseline"}),
                        "validated_at": "2026-08-21T04:00:00Z",
                    },
                )

            feedback_evaluation_loop.evaluate_candidate(
                canonical_database=canonical_path,
                candidate_id=candidate_id,
                cohort_runner=lambda: {
                    **baseline,
                    "duration_ms": 150,
                    "source_fingerprint": "b" * 64,
                    "tested_commit": "candidate",
                },
                now="2026-08-21T04:01:00Z",
            )
            with CanonicalDB(canonical_path) as db:
                candidate_evidence = json.loads(
                    db.connection.execute(
                        "SELECT evidence_json FROM validation_results "
                        "WHERE subject_id = ? AND validator = ?",
                        (candidate_id, "feedback_evaluation.candidate.v1"),
                    ).fetchone()[0]
                )
                db.insert(
                    "validation_results",
                    {
                        "subject_type": "improvement_candidate",
                        "subject_id": candidate_id,
                        "validator": "feedback_evaluation.baseline.v1",
                        "result": "pass",
                        "evidence_json": json.dumps(
                            {**baseline, "duration_ms": 200, "tested_commit": "baseline-2"}
                        ),
                        "validated_at": "2026-08-21T04:02:00Z",
                    },
                )

            self.assertEqual(
                candidate_evidence["baseline_validation_id"], original_baseline_id
            )
            with self.assertRaisesRegex(ValueError, "acceptable cohort comparison"):
                feedback_evaluation_loop.record_decision(
                    canonical_database=canonical_path,
                    candidate_id=candidate_id,
                    decision="accepted",
                    reviewer="operator",
                    now="2026-08-21T04:03:00Z",
                )
            with CanonicalDB(canonical_path, read_only=True) as db:
                self.assertEqual(
                    db.connection.execute(
                        "SELECT COUNT(*) FROM decisions WHERE scope = 'feedback_evaluation'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    db.connection.execute(
                        "SELECT status FROM tasks WHERE task_id = ?", (candidate_id,)
                    ).fetchone()[0],
                    "evaluated",
                )


    def test_dropped_error_diagnostics_become_a_repeated_telemetry_signal(self):
        with TemporaryDirectory() as directory:
            turn_path = Path(directory) / "turn-metrics.sqlite"
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE turn_metrics (
                        turn_key TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL,
                        tool_error_count INTEGER NOT NULL,
                        tool_error_diagnostics_dropped_count INTEGER NOT NULL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO turn_metrics VALUES (?, ?, ?, ?)",
                    [
                        ("turn-a", "2026-08-21T01:00:00Z", 1, 5),
                        ("turn-b", "2026-08-21T02:00:00Z", 1, 4),
                        ("turn-c", "2026-08-21T03:00:00Z", 0, 2),
                        ("turn-old", "2026-08-01T01:00:00Z", 1, 9),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            signals = feedback_evaluation_loop._turn_dropped_diagnostics_signals(
                turn_path,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(
                signals,
                [
                    {
                        "signal_key": "turn_telemetry_dropped:dropped_error_diagnostics",
                        "source": "turn_telemetry",
                        "category": "dropped_error_diagnostics",
                        "occurrences": 9,
                        "affected_turns": 2,
                        "recommendation": "review_telemetry_observability",
                    }
                ],
            )

    def test_dropped_diagnostics_signal_tolerates_missing_column(self):
        with TemporaryDirectory() as directory:
            turn_path = Path(directory) / "turn-metrics.sqlite"
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE turn_metrics (
                        turn_key TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL,
                        tool_error_count INTEGER NOT NULL
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            signals = feedback_evaluation_loop._turn_dropped_diagnostics_signals(
                turn_path,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(signals, [])

    def test_refresh_report_adds_recommendations_trends_and_history(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            cohort = {
                "cohort_id": feedback_evaluation_loop.COHORT_ID,
                "result": "pass",
                "test_count": 1,
                "test_failure_count": 0,
                "duration_ms": 1,
                "source_fingerprint": "abc",
                "failure_ids_sha256": "def",
                "tested_commit": "head",
            }
            report_path = root / "report.json"
            history_path = root / "history.jsonl"

            first = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=report_path,
                history_path=history_path,
                cohort_runner=lambda: cohort,
                now="2026-08-21T04:00:00Z",
            )

            self.assertIn("recommendations", first)
            api_recommendation = next(
                item
                for item in first["recommendations"]
                if item["signal_key"] == "turn_api_error:APIConnectionError"
            )
            self.assertEqual(api_recommendation["surface"], "provider_config")
            self.assertEqual(api_recommendation["rank"], 1)
            self.assertEqual(
                first["signal_trends"]["turn_api_error:APIConnectionError"],
                {"previous_occurrences": None, "occurrences": 3, "delta": None},
            )
            history_lines = history_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(history_lines), 1)
            self.assertEqual(
                json.loads(history_lines[0])["signal_counts"],
                {"turn_api_error:APIConnectionError": 3},
            )

            connection = sqlite3.connect(turn_path)
            try:
                for index in (3, 4):
                    connection.execute(
                        """
                        INSERT INTO turn_metrics (
                            turn_key, completed_at, provider, model, outcome,
                            error_category, api_error_count, retry_count,
                            api_request_count, tool_call_count, tool_error_count,
                            duration_ms, api_duration_ms, tool_duration_ms,
                            approx_input_tokens
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"turn-{index}",
                            f"2026-08-21T0{index}:00:00Z",
                            "openai-codex",
                            "gpt-5.6-terra",
                            "complete",
                            "APIConnectionError",
                            1,
                            0,
                            2,
                            1,
                            0,
                            1000,
                            900,
                            50,
                            1000,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            second = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=report_path,
                history_path=history_path,
                cohort_runner=lambda: cohort,
                now="2026-08-21T05:00:00Z",
            )

            self.assertEqual(
                second["signal_trends"]["turn_api_error:APIConnectionError"],
                {"previous_occurrences": 3, "occurrences": 5, "delta": 2},
            )
            self.assertEqual(
                json.loads(
                    history_path.read_text(encoding="utf-8").splitlines()[-1]
                )["generated_at"],
                "2026-08-21T05:00:00Z",
            )

    def test_refresh_tracks_history_only_signal_as_zero(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            turn_path = root / "turn-metrics.sqlite"
            _seed_turn_metrics(turn_path)
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute("DELETE FROM turn_metrics WHERE turn_key = ?", ("turn-2",))
                connection.commit()
            finally:
                connection.close()

            signal_key = "turn_api_error:APIConnectionError"
            report_path = root / "report.json"
            history_path = root / "history.jsonl"
            history_path.write_text(
                json.dumps(
                    {
                        "schema": "feedback-evaluation-history.v1",
                        "generated_at": "2026-08-21T04:00:00Z",
                        "signal_counts": {signal_key: 255},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=report_path,
                history_path=history_path,
                cohort_runner=lambda: self.fail("No signal should trigger a baseline."),
                now="2026-08-21T05:00:00Z",
            )

            self.assertEqual(report["signals"], [])
            self.assertEqual(
                report["signal_trends"],
                {
                    signal_key: {
                        "previous_occurrences": 255,
                        "occurrences": 0,
                        "delta": -255,
                    }
                },
            )
            self.assertEqual(
                json.loads(history_path.read_text(encoding="utf-8").splitlines()[-1])["signal_counts"],
                {signal_key: 0},
            )

    def test_refresh_keeps_existing_report_when_history_append_fails(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            report_path = root / "report.json"
            report_path.write_text('{"status": "previous"}\n', encoding="utf-8")

            with patch.object(
                feedback_evaluation_loop,
                "_append_history_line",
                side_effect=OSError("history unavailable"),
            ):
                with self.assertRaisesRegex(OSError, "history unavailable"):
                    feedback_evaluation_loop.refresh_loop(
                        canonical_database=canonical_path,
                        turn_database=root / "turn-metrics.sqlite",
                        report_path=report_path,
                        history_path=root / "history.jsonl",
                        cohort_runner=lambda: self.fail("No signal should trigger a baseline."),
                        now="2026-08-21T05:00:00Z",
                    )

            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8")),
                {"status": "previous"},
            )

    def test_dropped_diagnostics_signal_creates_review_candidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            turn_path = root / "turn-metrics.sqlite"
            connection = sqlite3.connect(turn_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE turn_metrics (
                        turn_key TEXT PRIMARY KEY,
                        completed_at TEXT NOT NULL,
                        error_category TEXT,
                        api_error_count INTEGER NOT NULL,
                        tool_error_categories_json TEXT,
                        tool_error_count INTEGER NOT NULL,
                        tool_error_diagnostics_dropped_count INTEGER NOT NULL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO turn_metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("turn-a", "2026-08-21T01:00:00Z", None, 0, None, 1, 5),
                        ("turn-b", "2026-08-21T02:00:00Z", None, 0, None, 1, 4),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            report = feedback_evaluation_loop.refresh_loop(
                canonical_database=canonical_path,
                turn_database=turn_path,
                report_path=root / "report.json",
                history_path=root / "history.jsonl",
                cohort_runner=lambda: {
                    "cohort_id": feedback_evaluation_loop.COHORT_ID,
                    "result": "pass",
                    "test_count": 1,
                    "test_failure_count": 0,
                    "duration_ms": 1,
                    "source_fingerprint": "abc",
                    "failure_ids_sha256": "def",
                    "tested_commit": "head",
                },
                now="2026-08-21T04:00:00Z",
            )

            expected_id = feedback_evaluation_loop._candidate_id(
                "turn_telemetry_dropped:dropped_error_diagnostics"
            )
            self.assertEqual(report["candidates"][0]["candidate_id"], expected_id)
            self.assertEqual(report["candidates"][0]["status"], "baseline_recorded")
            dropped_recommendation = next(
                item
                for item in report["recommendations"]
                if item["signal_key"] == "turn_telemetry_dropped:dropped_error_diagnostics"
            )
            self.assertEqual(
                dropped_recommendation["surface"], "telemetry_observability"
            )

    def test_refresh_without_history_path_never_writes_default_history(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path):
                pass
            sentinel_history = root / "derived" / "history.jsonl"
            with patch.object(
                feedback_evaluation_loop,
                "DEFAULT_HISTORY_PATH",
                sentinel_history,
            ):
                report = feedback_evaluation_loop.refresh_loop(
                    canonical_database=canonical_path,
                    turn_database=root / "turn-metrics.sqlite",
                    report_path=root / "report.json",
                    cohort_runner=lambda: {
                        "cohort_id": feedback_evaluation_loop.COHORT_ID,
                        "result": "pass",
                        "test_count": 1,
                        "test_failure_count": 0,
                        "duration_ms": 1,
                        "source_fingerprint": "abc",
                        "failure_ids_sha256": "def",
                        "tested_commit": "head",
                    },
                    now="2026-08-21T04:00:00Z",
                )
            self.assertFalse(sentinel_history.exists())
            self.assertIsNone(report["source_paths"]["history"])
            self.assertEqual(report["signal_trends"], {})

    def test_signal_recommendations_route_surfaces_rank_and_summaries(self):
        signals = [
            {
                "signal_key": "turn_api_error:APIConnectionError",
                "source": "turn_telemetry",
                "category": "APIConnectionError",
                "occurrences": 10,
                "affected_turns": 5,
                "recommendation": "review_provider_recovery",
            },
            {
                "signal_key": "turn_tool_error:patch_error",
                "source": "turn_telemetry",
                "category": "patch_error",
                "occurrences": 2,
                "affected_turns": 1,
                "recommendation": "review_tool_reliability",
            },
            {
                "signal_key": "turn_tool_error:skill_manage_error",
                "source": "turn_telemetry",
                "category": "skill_manage_error",
                "occurrences": 8,
                "affected_turns": 4,
                "recommendation": "review_tool_reliability",
            },
            {
                "signal_key": "run_error:unit_tests_failed",
                "source": "run_metrics",
                "category": "unit_tests_failed",
                "occurrences": 3,
                "affected_turns": 3,
                "recommendation": "review_regression_failure",
            },
        ]

        recommendations = feedback_evaluation_loop._signal_recommendations(signals)

        self.assertEqual(
            [item["signal_key"] for item in recommendations],
            [
                "turn_api_error:APIConnectionError",
                "turn_tool_error:skill_manage_error",
                "run_error:unit_tests_failed",
                "turn_tool_error:patch_error",
            ],
        )
        self.assertEqual([item["rank"] for item in recommendations], [1, 2, 3, 4])
        self.assertEqual(
            [item["priority_score"] for item in recommendations], [50, 32, 9, 2]
        )
        self.assertEqual(
            [item["surface"] for item in recommendations],
            ["provider_config", "skill_procedure", "run_pipeline", "tool_wrapper"],
        )
        self.assertTrue(all(item["summary"] for item in recommendations))
        self.assertEqual(
            recommendations[1]["occurrences"],
            8,
        )
        self.assertEqual(recommendations[1]["affected_turns"], 4)
        self.assertEqual(
            recommendations[1]["recommendation"], "review_tool_reliability"
        )

    def test_signal_recommendations_default_unknown_categories_to_safe_surfaces(self):
        signals = [
            {
                "signal_key": "turn_tool_error:some_new_failure",
                "source": "turn_telemetry",
                "category": "some_new_failure",
                "occurrences": 4,
                "affected_turns": 2,
                "recommendation": "review_tool_reliability",
            },
            {
                "signal_key": "run_error:weird_gate",
                "source": "run_metrics",
                "category": "weird_gate",
                "occurrences": 4,
                "affected_turns": 2,
                "recommendation": "review_repeated_run_error",
            },
            {
                "signal_key": "turn_telemetry_dropped:dropped_error_diagnostics",
                "source": "turn_telemetry",
                "category": "dropped_error_diagnostics",
                "occurrences": 4,
                "affected_turns": 2,
                "recommendation": "review_telemetry_observability",
            },
        ]

        recommendations = feedback_evaluation_loop._signal_recommendations(signals)

        self.assertEqual(
            [item["surface"] for item in recommendations],
            ["tool_wrapper", "run_pipeline", "telemetry_observability"],
        )
        self.assertTrue(all(item["summary"] for item in recommendations))
        self.assertEqual(
            [item["rank"] for item in recommendations], [1, 1, 1]
        )

    def test_canonical_error_signals_include_affected_turns(self):
        """Run-metric signals must carry affected_runs so pipeline failures
        are not deprioritized to priority_score=0 in recommendations."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "efficiens.db"
            with CanonicalDB(canonical_path) as db:
                for index in range(3):
                    db.record_run(
                        request_type="run_checks",
                        errors_json=["unit_tests_failed"],
                        verification_result="fail",
                        acceptance_status="rejected",
                        started_at=f"2026-08-21T0{index}:00:00Z",
                    )

            signals = feedback_evaluation_loop._canonical_error_signals(
                canonical_path,
                now="2026-08-21T04:00:00Z",
            )

            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0]["category"], "unit_tests_failed")
            self.assertEqual(signals[0]["affected_turns"], 3)
            recommendations = feedback_evaluation_loop._signal_recommendations(signals)
            self.assertEqual(recommendations[0]["priority_score"], 9)

    def test_history_reader_skips_non_object_and_malformed_lines(self):
        """Malformed or non-object history lines must never block refreshes."""
        with TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.jsonl"
            history_path.write_text(
                "\n".join(
                    [
                        "[]",
                        "null",
                        '"a string"',
                        "not json at all",
                        json.dumps(
                            {
                                "schema": "feedback-evaluation-history.v1",
                                "generated_at": "2026-08-21T04:00:00Z",
                                "signal_counts": {"run_error:unit_tests_failed": 3},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            counts, generated_at = feedback_evaluation_loop._read_latest_history_counts(
                history_path
            )

            self.assertEqual(counts, {"run_error:unit_tests_failed": 3})
            self.assertEqual(generated_at, "2026-08-21T04:00:00Z")


if __name__ == "__main__":
    unittest.main()
