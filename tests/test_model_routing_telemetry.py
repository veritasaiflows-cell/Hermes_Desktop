"""Tests for the read-only model-routing telemetry report."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.model_routing_telemetry import (  # noqa: E402
    MIN_SAMPLES,
    SCHEMA,
    build_report,
    collect_rows,
    default_turn_database,
)

COLUMNS = (
    "turn_key TEXT, session_key TEXT, model TEXT, provider TEXT, started_at TEXT, "
    "duration_ms INTEGER, api_request_count INTEGER, api_error_count INTEGER, "
    "retry_count INTEGER, api_duration_ms INTEGER, tool_round_count INTEGER, "
    "tool_call_count INTEGER, tool_error_count INTEGER, approx_input_tokens INTEGER, "
    "outcome TEXT"
)


def _timestamp(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_database(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE turn_metrics ({COLUMNS})")
        connection.executemany(
            "INSERT INTO turn_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.commit()
    finally:
        # Windows holds the file until the handle closes; TemporaryDirectory
        # cleanup fails with WinError 32 otherwise.
        connection.close()


def _row(
    model: str,
    provider: str,
    *,
    days_ago: float = 1.0,
    duration_ms: int = 100_000,
    requests: int = 10,
    errors: int = 0,
    input_tokens: int = 50_000,
    outcome: str = "complete",
) -> tuple:
    return (
        f"turn-{model}-{days_ago}-{duration_ms}-{errors}",
        "session-1",
        model,
        provider,
        _timestamp(days_ago),
        duration_ms,
        requests,
        errors,
        0,
        duration_ms // 2,
        5,
        10,
        0,
        input_tokens,
        outcome,
    )


class ModelRoutingTelemetryTests(unittest.TestCase):
    """Cover aggregation, evidence gating, and read-only guarantees."""

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.database = self.root / "turn-metrics.sqlite"

    def test_missing_database_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            build_report(self.root / "absent.sqlite")

    def test_error_rate_is_per_request_not_per_turn(self) -> None:
        # 2 turns, 20 requests, 4 errors => 20% per request (not 200% per turn).
        rows = [
            _row("model-a", "prov", requests=10, errors=2),
            _row("model-a", "prov", requests=10, errors=2, days_ago=2.0),
        ]
        _build_database(self.database, rows)
        collected = collect_rows(self.database)
        self.assertEqual(len(collected), 1)
        self.assertAlmostEqual(collected[0]["error_rate_percent"], 20.0)

    def test_latency_is_normalised_by_input_tokens(self) -> None:
        # Same wall time, double the input: normalised latency must halve.
        rows = [
            _row("heavy", "prov", duration_ms=100_000, input_tokens=100_000),
            _row("light", "prov", duration_ms=100_000, input_tokens=50_000),
        ]
        _build_database(self.database, rows)
        by_model = {row["model"]: row for row in collect_rows(self.database)}
        self.assertAlmostEqual(by_model["heavy"]["ms_per_1k_input_tokens"], 1000.0)
        self.assertAlmostEqual(by_model["light"]["ms_per_1k_input_tokens"], 2000.0)

    def test_thin_samples_are_excluded_from_ranking(self) -> None:
        rows = [_row("solid", "prov", days_ago=index) for index in range(MIN_SAMPLES)]
        rows.append(_row("thin", "prov", errors=0))
        _build_database(self.database, rows)
        report = build_report(self.database)

        ranked_models = {entry["model"] for entry in report["most_reliable"]}
        thin_models = {entry["model"] for entry in report["insufficient_evidence"]}
        self.assertIn("solid", ranked_models)
        self.assertNotIn("thin", ranked_models)
        self.assertIn("thin", thin_models)

    def test_zero_requests_yields_none_not_division_error(self) -> None:
        _build_database(self.database, [_row("idle", "prov", requests=0, input_tokens=0)])
        collected = collect_rows(self.database)
        self.assertIsNone(collected[0]["error_rate_percent"])
        self.assertIsNone(collected[0]["ms_per_1k_input_tokens"])

    def test_window_days_filters_older_turns(self) -> None:
        rows = [
            _row("recent", "prov", days_ago=1.0),
            _row("ancient", "prov", days_ago=40.0),
        ]
        _build_database(self.database, rows)
        models = {row["model"] for row in collect_rows(self.database, window_days=7)}
        self.assertEqual(models, {"recent"})

    def test_report_shape_and_schema(self) -> None:
        _build_database(self.database, [_row("model-a", "prov")])
        report = build_report(self.database, window_days=7)
        self.assertEqual(report["schema"], SCHEMA)
        for key in ("generated_at", "models", "caveats", "most_reliable", "window_days"):
            self.assertIn(key, report)
        self.assertTrue(report["caveats"], "caveats must warn about confounded comparisons")

    def test_database_is_opened_read_only(self) -> None:
        _build_database(self.database, [_row("model-a", "prov")])
        before = self.database.read_bytes()
        build_report(self.database)
        self.assertEqual(self.database.read_bytes(), before)

    def test_default_database_path_honours_env_override(self) -> None:
        target = self.root / "custom-metrics.sqlite"
        with unittest.mock.patch.dict(
            "os.environ", {"HERMES_TURN_METRICS_DB": str(target)}, clear=False
        ):
            self.assertEqual(default_turn_database(), target)

    def test_cli_json_mode_emits_valid_report(self) -> None:
        _build_database(self.database, [_row("model-a", "prov")])
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/model_routing_telemetry.py",
                "--database",
                str(self.database),
                "--as-json",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], SCHEMA)

    def test_cli_missing_database_exits_nonzero(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/model_routing_telemetry.py",
                "--database",
                str(self.root / "nope.sqlite"),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("MODEL ROUTING TELEMETRY FAIL", completed.stdout)


if __name__ == "__main__":
    unittest.main()
