from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from canonical.db import CanonicalDB
from scripts import telemetry_retention


def _create_turn_database(path: Path, rows: list[dict[str, object]]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE turn_metrics (
                turn_key TEXT PRIMARY KEY,
                session_key TEXT,
                task_key TEXT,
                platform TEXT,
                model TEXT,
                provider TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                api_request_count INTEGER NOT NULL,
                api_error_count INTEGER NOT NULL,
                retry_count INTEGER NOT NULL,
                api_duration_ms INTEGER NOT NULL,
                tool_round_count INTEGER NOT NULL,
                tool_call_count INTEGER NOT NULL,
                tool_error_count INTEGER NOT NULL,
                tool_duration_ms INTEGER NOT NULL,
                tool_names_json TEXT NOT NULL,
                approx_input_tokens INTEGER,
                message_count INTEGER,
                non_provider_tool_ms INTEGER NOT NULL,
                final_response_seen INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                error_category TEXT,
                collector_version TEXT,
                metric_semantics TEXT,
                api_work_ms INTEGER,
                api_wall_ms INTEGER,
                tool_work_ms INTEGER,
                tool_wall_ms INTEGER,
                observed_external_wall_ms INTEGER,
                created_at TEXT NOT NULL
            );
            """
        )
        for row in rows:
            payload = {
                "turn_key": row["turn_key"],
                "session_key": None,
                "task_key": None,
                "platform": "desktop",
                "model": "gpt-5.6-sol",
                "provider": "openai-codex",
                "started_at": row["completed_at"],
                "completed_at": row["completed_at"],
                "duration_ms": row.get("duration_ms", 1000),
                "api_request_count": row.get("api_request_count", 1),
                "api_error_count": row.get("api_error_count", 0),
                "retry_count": row.get("retry_count", 0),
                "api_duration_ms": row.get("api_duration_ms", 800),
                "tool_round_count": 0,
                "tool_call_count": row.get("tool_call_count", 0),
                "tool_error_count": row.get("tool_error_count", 0),
                "tool_duration_ms": row.get("tool_duration_ms", 0),
                "tool_names_json": row.get("tool_names_json", "[]"),
                "approx_input_tokens": row.get("approx_input_tokens", 1000),
                "message_count": 2,
                "non_provider_tool_ms": 200,
                "final_response_seen": 1,
                "outcome": row.get("outcome", "complete"),
                "error_category": row.get("error_category"),
                "collector_version": "1.1.0",
                "metric_semantics": "turn-metrics.v2",
                "api_work_ms": row.get("api_duration_ms", 800),
                "api_wall_ms": row.get("api_duration_ms", 800),
                "tool_work_ms": row.get("tool_duration_ms", 0),
                "tool_wall_ms": row.get("tool_duration_ms", 0),
                "observed_external_wall_ms": (
                    row.get("api_duration_ms", 800) + row.get("tool_duration_ms", 0)
                ),
                "created_at": row["completed_at"],
            }
            columns = list(payload)
            connection.execute(
                f"INSERT INTO turn_metrics ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [payload[column] for column in columns],
            )
        connection.commit()
    finally:
        connection.close()


class TelemetryRetentionTests(unittest.TestCase):
    def test_rolls_up_before_pruning_and_is_idempotent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            archive_directory = root / "archive"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "old-1", "completed_at": "2026-01-01T01:00:00Z"},
                    {
                        "turn_key": "old-2",
                        "completed_at": "2026-01-01T02:00:00Z",
                        "api_error_count": 1,
                        "error_category": "APIConnectionError",
                    },
                    {"turn_key": "current", "completed_at": "2026-04-02T10:00:00Z"},
                ],
            )
            with CanonicalDB(canonical_database):
                pass

            first = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=archive_directory,
                archive_months=False,
                now="2026-04-02T12:00:00Z",
                retention_days=30,
                max_database_bytes=10 * 1024 * 1024,
            )
            second = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=archive_directory,
                archive_months=False,
                now="2026-04-02T12:01:00Z",
                retention_days=30,
                max_database_bytes=10 * 1024 * 1024,
            )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute(
                    "SELECT turn_key FROM turn_metrics ORDER BY turn_key"
                ).fetchall()
            finally:
                connection.close()
            with CanonicalDB(canonical_database, read_only=True) as db:
                events = db.connection.execute(
                    "SELECT payload_json FROM events "
                    "WHERE event_type = 'telemetry.daily_rollup.v1'"
                ).fetchall()
                validations = db.connection.execute(
                    "SELECT result, evidence_json FROM validation_results "
                    "WHERE subject_type = 'telemetry_daily_rollup'"
                ).fetchall()

            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["raw_rows_deleted"], 2)
            self.assertEqual(first["rollups_created"], 1)
            self.assertEqual(second["raw_rows_deleted"], 0)
            self.assertEqual(second["rollups_created"], 0)
            self.assertEqual(retained, [("current",)])
            self.assertEqual(len(events), 1)
            self.assertEqual(len(validations), 1)
            self.assertEqual(validations[0][0], "pass")
            rollup = json.loads(events[0][0])
            evidence = json.loads(validations[0][1])
            self.assertEqual(rollup["source_row_count"], 2)
            self.assertEqual(evidence["source_sha256"], rollup["source_sha256"])
            self.assertFalse(archive_directory.exists())

    def test_optional_monthly_archive_is_single_compressed_safe_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            archive_directory = root / "profile-telemetry" / "archive"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "old-1", "completed_at": "2026-01-01T01:00:00Z"},
                    {"turn_key": "old-2", "completed_at": "2026-01-02T02:00:00Z"},
                ],
            )
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute("ALTER TABLE turn_metrics ADD COLUMN raw_payload TEXT")
                connection.execute("UPDATE turn_metrics SET raw_payload = 'RAW_SECRET_CONTENT'")
                connection.commit()
            finally:
                connection.close()
            with CanonicalDB(canonical_database):
                pass

            report = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=archive_directory,
                archive_months=True,
                now="2026-04-02T12:00:00Z",
                retention_days=30,
                max_database_bytes=10 * 1024 * 1024,
            )

            archive_files = list(archive_directory.glob("*.jsonl.gz"))
            temporary_files = list(archive_directory.glob("*.tmp"))
            self.assertEqual(report["archives_written"], 1)
            self.assertEqual([path.name for path in archive_files], ["2026-01.jsonl.gz"])
            self.assertEqual(temporary_files, [])
            with gzip.open(archive_files[0], "rt", encoding="utf-8") as handle:
                archived_rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual([row["turn_key"] for row in archived_rows], ["old-1", "old-2"])
            self.assertNotIn("raw_payload", archived_rows[0])
            self.assertNotIn("RAW_SECRET_CONTENT", json.dumps(archived_rows))

            archive_sha256 = hashlib.sha256(archive_files[0].read_bytes()).hexdigest()
            with CanonicalDB(canonical_database, read_only=True) as db:
                manifests = db.connection.execute(
                    "SELECT payload_json FROM events "
                    "WHERE event_type = 'telemetry.monthly_archive.v1'"
                ).fetchall()
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0][0])
            self.assertEqual(manifest["archive_sha256"], archive_sha256)
            self.assertEqual(manifest["source_row_count"], 2)

    def test_content_bearing_allowlisted_value_fails_before_proof_or_archive(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            archive_directory = root / "profile-telemetry" / "archive"
            _create_turn_database(
                turn_database,
                [
                    {
                        "turn_key": "unsafe",
                        "completed_at": "2026-01-01T01:00:00Z",
                        "tool_names_json": json.dumps(
                            ["patient Jane Doe diagnosis and prompt content"]
                        ),
                    }
                ],
            )
            with CanonicalDB(canonical_database):
                pass

            with self.assertRaisesRegex(ValueError, "Unsafe telemetry value"):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    archive_directory=archive_directory,
                    archive_months=True,
                    now="2026-04-02T12:00:00Z",
                    retention_days=30,
                    max_database_bytes=10 * 1024 * 1024,
                )

            with CanonicalDB(canonical_database, read_only=True) as db:
                proof_count = db.connection.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE event_type IN (?, ?)",
                    (
                        telemetry_retention.ROLLUP_EVENT_TYPE,
                        telemetry_retention.ARCHIVE_EVENT_TYPE,
                    ),
                ).fetchone()[0]
            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute("SELECT turn_key FROM turn_metrics").fetchall()
            finally:
                connection.close()
            self.assertEqual(proof_count, 0)
            self.assertEqual(retained, [("unsafe",)])
            self.assertFalse(archive_directory.exists())

    def test_existing_archive_rejects_duplicate_turn_keys(self):
        with TemporaryDirectory() as directory:
            archive = Path(directory) / "2026-01.jsonl.gz"
            row = {
                "turn_key": "duplicate",
                "completed_at": "2026-01-01T01:00:00Z",
            }
            archive.write_bytes(telemetry_retention._gzip_json_lines([row, row]))

            with self.assertRaisesRegex(ValueError, "duplicate turn key"):
                telemetry_retention._read_archive(archive)

    def test_modified_existing_archive_fails_before_new_raw_rows_are_deleted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            archive_directory = root / "profile-telemetry" / "archive"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "old-1", "completed_at": "2026-01-01T01:00:00Z"},
                    {"turn_key": "current", "completed_at": "2026-04-02T10:00:00Z"},
                ],
            )
            with CanonicalDB(canonical_database):
                pass
            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=archive_directory,
                archive_months=True,
                now="2026-04-02T12:00:00Z",
                retention_days=30,
                max_database_bytes=10 * 1024 * 1024,
            )

            archive = archive_directory / "2026-01.jsonl.gz"
            archived_rows = telemetry_retention._read_archive(archive)
            archived_rows[0]["duration_ms"] = int(archived_rows[0]["duration_ms"]) + 1
            archive.write_bytes(telemetry_retention._gzip_json_lines(archived_rows))

            connection = sqlite3.connect(turn_database)
            connection.row_factory = sqlite3.Row
            try:
                source = dict(
                    connection.execute(
                        "SELECT * FROM turn_metrics WHERE turn_key = 'current'"
                    ).fetchone()
                )
                source.update(
                    {
                        "turn_key": "old-2",
                        "started_at": "2026-01-02T01:00:00Z",
                        "completed_at": "2026-01-02T01:00:00Z",
                        "created_at": "2026-01-02T01:00:00Z",
                    }
                )
                columns = list(source)
                connection.execute(
                    f"INSERT INTO turn_metrics ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)})",
                    [source[column] for column in columns],
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(ValueError, "canonical archive manifest"):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    archive_directory=archive_directory,
                    archive_months=True,
                    now="2026-04-02T12:05:00Z",
                    retention_days=30,
                    max_database_bytes=10 * 1024 * 1024,
                )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute(
                    "SELECT turn_key FROM turn_metrics ORDER BY turn_key"
                ).fetchall()
            finally:
                connection.close()
            self.assertIn(("old-2",), retained)

    def test_archive_write_does_not_touch_predictable_temporary_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            archive_directory = root / "profile-telemetry" / "archive"
            archive_directory.mkdir(parents=True)
            predictable = archive_directory / "2026-01.jsonl.gz.tmp"
            predictable.write_bytes(b"unrelated-sentinel")
            _create_turn_database(
                turn_database,
                [{"turn_key": "old", "completed_at": "2026-01-01T01:00:00Z"}],
            )
            with CanonicalDB(canonical_database):
                pass

            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=archive_directory,
                archive_months=True,
                now="2026-04-02T12:00:00Z",
                retention_days=30,
                max_database_bytes=10 * 1024 * 1024,
            )

            self.assertEqual(predictable.read_bytes(), b"unrelated-sentinel")
            self.assertTrue((archive_directory / "2026-01.jsonl.gz").is_file())

    def test_size_budget_prunes_oldest_proven_rows_and_reclaims_pages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            rows = [
                {
                    "turn_key": f"old-{index:03d}",
                    "completed_at": "2026-01-01T01:00:00Z",
                }
                for index in range(80)
            ]
            rows.append({"turn_key": "current", "completed_at": "2026-04-02T10:00:00Z"})
            _create_turn_database(turn_database, rows)
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute("ALTER TABLE turn_metrics ADD COLUMN raw_padding BLOB")
                connection.execute(
                    "UPDATE turn_metrics SET raw_padding = randomblob(8192) "
                    "WHERE turn_key != 'current'"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertGreater(turn_database.stat().st_size, 128 * 1024)
            with CanonicalDB(canonical_database):
                pass

            report = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                archive_directory=root / "archive",
                archive_months=False,
                now="2026-04-02T12:00:00Z",
                retention_days=3650,
                max_database_bytes=128 * 1024,
            )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute(
                    "SELECT turn_key FROM turn_metrics ORDER BY turn_key"
                ).fetchall()
                auto_vacuum = connection.execute("PRAGMA auto_vacuum").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(report["status"], "ok")
            self.assertGreater(report["size_rows_deleted"], 0)
            self.assertLessEqual(report["database_size_bytes"], 128 * 1024)
            self.assertIn(("current",), retained)
            self.assertEqual(auto_vacuum, 2)

    def test_size_check_reclaims_existing_freelist_before_selecting_victims(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "old", "completed_at": "2026-01-01T01:00:00Z"},
                    {"turn_key": "current", "completed_at": "2026-04-02T10:00:00Z"},
                ],
            )
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                connection.execute("VACUUM")
                connection.execute("CREATE TABLE disposable_padding (payload BLOB)")
                connection.executemany(
                    "INSERT INTO disposable_padding(payload) VALUES (randomblob(8192))",
                    [() for _ in range(80)],
                )
                connection.commit()
                connection.execute("DELETE FROM disposable_padding")
                connection.commit()
            finally:
                connection.close()
            self.assertGreater(turn_database.stat().st_size, 128 * 1024)
            with CanonicalDB(canonical_database):
                pass

            report = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-02T12:00:00Z",
                retention_days=3650,
                max_database_bytes=128 * 1024,
            )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute(
                    "SELECT turn_key FROM turn_metrics ORDER BY turn_key"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(report["size_rows_deleted"], 0)
            self.assertEqual(retained, [("current",), ("old",)])
            self.assertLessEqual(report["database_size_bytes"], 128 * 1024)

    def test_new_daily_rollup_extends_the_stored_hash_chain_after_rerun(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "day-1", "completed_at": "2026-01-01T01:00:00Z"},
                    {"turn_key": "day-2", "completed_at": "2026-01-02T01:00:00Z"},
                    {"turn_key": "day-3", "completed_at": "2026-04-03T01:00:00Z"},
                ],
            )
            with CanonicalDB(canonical_database):
                pass

            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-03T12:00:00Z",
                retention_days=3650,
            )
            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-04T12:00:00Z",
                retention_days=3650,
            )

            with CanonicalDB(canonical_database, read_only=True) as db:
                payloads = [
                    json.loads(row[0])
                    for row in db.connection.execute(
                        "SELECT payload_json FROM events "
                        "WHERE event_type = 'telemetry.daily_rollup.v1' ORDER BY rowid"
                    )
                ]
            self.assertEqual([payload["day"] for payload in payloads], [
                "2026-01-01",
                "2026-01-02",
                "2026-04-03",
            ])
            self.assertEqual(
                payloads[2]["previous_rollup_sha256"],
                payloads[1]["rollup_sha256"],
            )

    def test_new_day_chains_from_a_late_correction_in_append_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [
                    {"turn_key": "day-1", "completed_at": "2026-01-01T01:00:00Z"},
                    {"turn_key": "day-2", "completed_at": "2026-01-02T01:00:00Z"},
                    {"turn_key": "day-3", "completed_at": "2026-04-03T01:00:00Z"},
                ],
            )
            with CanonicalDB(canonical_database):
                pass
            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-03T12:00:00Z",
                retention_days=3650,
            )
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute(
                    "UPDATE turn_metrics SET duration_ms = duration_ms + 1 "
                    "WHERE turn_key = 'day-1'"
                )
                connection.commit()
            finally:
                connection.close()

            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-04T12:00:00Z",
                retention_days=3650,
            )

            with CanonicalDB(canonical_database, read_only=True) as db:
                payloads = [
                    json.loads(row[0])
                    for row in db.connection.execute(
                        "SELECT payload_json FROM events "
                        "WHERE event_type = 'telemetry.daily_rollup.v1' ORDER BY rowid"
                    )
                ]
            self.assertEqual(
                [payload["day"] for payload in payloads],
                ["2026-01-01", "2026-01-02", "2026-01-01", "2026-04-03"],
            )
            self.assertEqual(
                payloads[3]["previous_rollup_sha256"],
                payloads[2]["rollup_sha256"],
            )

    def test_failed_canonical_proof_never_deletes_raw_rows(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "old", "completed_at": "2026-01-01T01:00:00Z"}],
            )
            with CanonicalDB(canonical_database):
                pass

            with (
                patch.object(
                    telemetry_retention,
                    "_persist_rollup",
                    side_effect=RuntimeError("proof write failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "proof write failed"),
            ):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    now="2026-04-02T12:00:00Z",
                    retention_days=30,
                )

            connection = sqlite3.connect(turn_database)
            try:
                count = connection.execute("SELECT COUNT(*) FROM turn_metrics").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1)

    def test_corrupted_canonical_rollup_never_authorizes_deletion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "old", "completed_at": "2026-01-01T01:00:00Z"}],
            )
            with CanonicalDB(canonical_database):
                pass
            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-02T12:00:00Z",
                retention_days=3650,
            )

            connection = sqlite3.connect(canonical_database)
            try:
                payload = json.loads(
                    connection.execute(
                        "SELECT payload_json FROM events "
                        "WHERE event_type = 'telemetry.daily_rollup.v1'"
                    ).fetchone()[0]
                )
                payload["rollup_sha256"] = "0" * 64
                connection.execute(
                    "UPDATE events SET payload_json = ? "
                    "WHERE event_type = 'telemetry.daily_rollup.v1'",
                    (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(ValueError, "rollup ledger"):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    now="2026-04-02T12:01:00Z",
                    retention_days=30,
                )

            connection = sqlite3.connect(turn_database)
            try:
                count = connection.execute("SELECT COUNT(*) FROM turn_metrics").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1)

    def test_compare_before_delete_rejects_a_concurrent_raw_row_update(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "old", "completed_at": "2026-01-01T01:00:00Z"}],
            )
            with CanonicalDB(canonical_database):
                pass
            original_persist = telemetry_retention._persist_rollup
            mutated = False

            def persist_then_mutate(db, payload):
                nonlocal mutated
                result = original_persist(db, payload)
                if not mutated:
                    connection = sqlite3.connect(turn_database)
                    try:
                        connection.execute(
                            "UPDATE turn_metrics SET duration_ms = duration_ms + 1 "
                            "WHERE turn_key = 'old'"
                        )
                        connection.commit()
                    finally:
                        connection.close()
                    mutated = True
                return result

            with (
                patch.object(
                    telemetry_retention,
                    "_persist_rollup",
                    side_effect=persist_then_mutate,
                ),
                self.assertRaisesRegex(RuntimeError, "changed during maintenance"),
            ):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    now="2026-04-02T12:00:00Z",
                    retention_days=30,
                )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute(
                    "SELECT turn_key, duration_ms FROM turn_metrics"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(retained, [("old", 1001)])

    def test_active_utc_day_is_protected_when_it_alone_exceeds_size_budget(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "current", "completed_at": "2026-04-02T10:00:00Z"}],
            )
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute("ALTER TABLE turn_metrics ADD COLUMN raw_padding BLOB")
                connection.execute(
                    "UPDATE turn_metrics SET raw_padding = randomblob(256000)"
                )
                connection.commit()
            finally:
                connection.close()
            with CanonicalDB(canonical_database):
                pass

            report = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-02T12:00:00Z",
                retention_days=90,
                max_database_bytes=64 * 1024,
            )

            self.assertEqual(report["status"], "degraded")
            self.assertEqual(report["raw_rows_deleted"], 0)
            self.assertEqual(report["size_rows_deleted"], 0)
            self.assertEqual(report["retained_rows"], 1)


    def test_retention_rejects_a_window_shorter_than_feedback_evaluation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "protected", "completed_at": "2026-04-08T10:00:00Z"}],
            )
            with CanonicalDB(canonical_database):
                pass

            with self.assertRaisesRegex(ValueError, "protected recent window"):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    now="2026-04-10T12:00:00Z",
                    retention_days=6,
                    protected_recent_days=7,
                    max_database_bytes=10 * 1024 * 1024,
                )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute("SELECT turn_key FROM turn_metrics").fetchall()
            finally:
                connection.close()
            self.assertEqual(retained, [("protected",)])

    def test_timezone_less_raw_timestamp_is_rejected_without_deletion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [{"turn_key": "ambiguous", "completed_at": "2026-01-01T01:00:00"}],
            )
            with CanonicalDB(canonical_database):
                pass

            with self.assertRaisesRegex(ValueError, "explicit UTC offset"):
                telemetry_retention.maintain_telemetry(
                    turn_database=turn_database,
                    canonical_database=canonical_database,
                    now="2026-04-02T12:00:00Z",
                    retention_days=30,
                    max_database_bytes=10 * 1024 * 1024,
                )

            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute("SELECT turn_key FROM turn_metrics").fetchall()
            finally:
                connection.close()
            self.assertEqual(retained, [("ambiguous",)])

    def test_offset_timestamps_are_normalized_before_rollup_ordering(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [
                    {
                        "turn_key": "earlier-utc",
                        "completed_at": "2026-01-01T01:30:00+01:00",
                    },
                    {
                        "turn_key": "later-utc",
                        "completed_at": "2026-01-01T00:45:00Z",
                    },
                ],
            )
            with CanonicalDB(canonical_database):
                pass

            telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-02T12:00:00Z",
                retention_days=365,
                max_database_bytes=10 * 1024 * 1024,
            )

            with CanonicalDB(canonical_database, read_only=True) as db:
                payload = json.loads(
                    db.connection.execute(
                        "SELECT payload_json FROM events "
                        "WHERE event_type = 'telemetry.daily_rollup.v1'"
                    ).fetchone()[0]
                )
            self.assertEqual(payload["period_start"], "2026-01-01T00:30:00Z")
            self.assertEqual(payload["period_end"], "2026-01-01T00:45:00Z")

    def test_size_cap_preserves_recent_closed_evaluation_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            turn_database = root / "turn-metrics.sqlite"
            canonical_database = root / "efficiens.db"
            _create_turn_database(
                turn_database,
                [
                    {
                        "turn_key": "recent-closed",
                        "completed_at": "2026-04-09T10:00:00Z",
                    }
                ],
            )
            connection = sqlite3.connect(turn_database)
            try:
                connection.execute("ALTER TABLE turn_metrics ADD COLUMN raw_padding BLOB")
                connection.execute(
                    "UPDATE turn_metrics SET raw_padding = randomblob(300000)"
                )
                connection.commit()
            finally:
                connection.close()
            with CanonicalDB(canonical_database):
                pass

            report = telemetry_retention.maintain_telemetry(
                turn_database=turn_database,
                canonical_database=canonical_database,
                now="2026-04-10T12:00:00Z",
                retention_days=90,
                protected_recent_days=7,
                max_database_bytes=64 * 1024,
            )

            self.assertEqual(report["status"], "degraded")
            self.assertEqual(report["size_rows_deleted"], 0)
            self.assertEqual(report["protected_recent_days"], 7)
            connection = sqlite3.connect(turn_database)
            try:
                retained = connection.execute("SELECT turn_key FROM turn_metrics").fetchall()
            finally:
                connection.close()
            self.assertEqual(retained, [("recent-closed",)])

    def test_maintenance_lock_serializes_retention_without_a_lock_file(self):
        with TemporaryDirectory() as temp_dir:
            turn_database = Path(temp_dir) / "turn-metrics.sqlite"
            _create_turn_database(
                turn_database,
                [{"turn_key": "protected", "completed_at": "2026-04-02T10:00:00Z"}],
            )
            now = datetime(2026, 4, 2, 12, tzinfo=timezone.utc)

            with telemetry_retention._maintenance_lock(turn_database, now=now):
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    with telemetry_retention._maintenance_lock(turn_database, now=now):
                        self.fail("a concurrent retention lease must not be granted")

            with telemetry_retention._maintenance_lock(turn_database, now=now):
                connection = sqlite3.connect(turn_database)
                try:
                    lock_count = connection.execute(
                        "SELECT COUNT(*) FROM telemetry_maintenance_lock"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(lock_count, 1)

            connection = sqlite3.connect(turn_database)
            try:
                lock_count = connection.execute(
                    "SELECT COUNT(*) FROM telemetry_maintenance_lock"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO telemetry_maintenance_lock"
                    "(lock_name, owner_id, expires_at) VALUES (?, ?, ?)",
                    ("retention", "stale-owner", "2026-04-02T10:00:00Z"),
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(lock_count, 0)

            with telemetry_retention._maintenance_lock(turn_database, now=now):
                pass

            connection = sqlite3.connect(turn_database)
            try:
                lock_count = connection.execute(
                    "SELECT COUNT(*) FROM telemetry_maintenance_lock"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(lock_count, 0)


if __name__ == "__main__":
    unittest.main()
