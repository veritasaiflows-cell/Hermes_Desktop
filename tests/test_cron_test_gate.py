#!/usr/bin/env python3
"""Tests for scripts/cron_test_gate.py lane preflight."""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import cron_test_gate, run_checks


def _register(tmp: Path, rows: list[tuple]) -> Path:
    reg = tmp / "register.sqlite"
    conn = sqlite3.connect(reg)
    conn.execute(
        "CREATE TABLE lanes (lane_id TEXT, owner TEXT, status TEXT, allowed_writes_json TEXT)"
    )
    conn.executemany("INSERT INTO lanes VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return reg


class BlockingLaneScanTests(unittest.TestCase):

    def test_missing_register_does_not_block(self):
        self.assertEqual(
            cron_test_gate._active_write_lanes_blocking(Path("no/such.sqlite")), []
        )

    def test_terminal_and_readonly_lanes_do_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            reg = _register(
                Path(d),
                [
                    ("done", "a", "complete", json.dumps(["c:/w/scripts/x.py"])),
                    ("read", "a", "running", json.dumps([])),
                ],
            )
            self.assertEqual(cron_test_gate._active_write_lanes_blocking(reg), [])

    def test_source_scope_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            reg = _register(
                Path(d),
                [("busy", "agent-main", "running",
                  json.dumps(["c:\\users\\v\\hermesworkspace\\scripts\\workspace_status.py"]))],
            )
            blocking = cron_test_gate._active_write_lanes_blocking(reg)
        self.assertEqual([b["lane_id"] for b in blocking], ["busy"])

    def test_canonical_db_scope_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            reg = _register(
                Path(d),
                [("proof", "agent-main", "running",
                  json.dumps(["c:/users/v/hermesworkspace/canonical/efficiens.db"]))],
            )
            blocking = cron_test_gate._active_write_lanes_blocking(reg)
        self.assertEqual([b["lane_id"] for b in blocking], ["proof"])

    def test_non_surface_scope_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            reg = _register(
                Path(d),
                [("docs", "a", "running",
                  json.dumps(["c:/users/v/hermesworkspace/references/automation-layer.md"]))],
            )
            self.assertEqual(cron_test_gate._active_write_lanes_blocking(reg), [])

    def test_malformed_writes_json_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            reg = _register(Path(d), [("bad", "a", "running", "not-json")])
            self.assertEqual(cron_test_gate._active_write_lanes_blocking(reg), [])


class PreflightMainTests(unittest.TestCase):

    def test_outer_gate_allows_inner_budget_and_recording_headroom(self):
        def simulated_checks(command, **kwargs):
            if kwargs["timeout"] < run_checks.PYTEST_TIMEOUT_SECONDS + 45:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(cron_test_gate, "_active_write_lanes_blocking", return_value=[]),
            patch.object(cron_test_gate.subprocess, "run", side_effect=simulated_checks) as run_mock,
            redirect_stdout(io.StringIO()) as out,
            redirect_stderr(io.StringIO()) as err,
        ):
            rc = cron_test_gate.main()

        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("TEST GATE OK", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 420)
        self.assertGreaterEqual(cron_test_gate.TIMEOUT_SECONDS - run_checks.PYTEST_TIMEOUT_SECONDS, 60)
        self.assertEqual(
            run_mock.call_args.args[0],
            [cron_test_gate.PYTHON, "scripts/run_checks.py", "--skip-smoke", "--record-telemetry"],
        )

    def test_defers_silently_with_exit_0_when_blocking(self):
        with patch.object(
            cron_test_gate, "_active_write_lanes_blocking",
            return_value=[{"lane_id": "x", "owner": "o", "status": "running"}],
        ):
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cron_test_gate.main()
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("TEST GATE DEFERRED", err.getvalue())

    def test_outer_timeout_still_fails_closed(self):
        # Regression pin: a larger budget is not permission to accept a hang.
        with (
            patch.object(cron_test_gate, "_active_write_lanes_blocking", return_value=[]),
            patch.object(cron_test_gate.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                ["run_checks.py"], cron_test_gate.TIMEOUT_SECONDS)) as run_mock,
            redirect_stdout(io.StringIO()) as out,
        ):
            rc = cron_test_gate.main()

        self.assertEqual(rc, 1)
        self.assertIn(f"reason=timeout_after_{cron_test_gate.TIMEOUT_SECONDS}s", out.getvalue())
        run_mock.assert_called_once()

    def test_nonzero_child_exit_still_rejects_passing_output(self):
        # Regression pin: child process status outranks a pass-looking summary.
        with (
            patch.object(cron_test_gate, "_active_write_lanes_blocking", return_value=[]),
            patch.object(cron_test_gate.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "3 passed in 0.1s", "")) as run_mock,
            redirect_stdout(io.StringIO()) as out,
        ):
            rc = cron_test_gate.main()

        self.assertEqual(rc, 1)
        self.assertIn("exit=1", out.getvalue())
        run_mock.assert_called_once()

    def test_runs_suite_when_clear(self):
        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        with patch.object(
            cron_test_gate, "_active_write_lanes_blocking", return_value=[]
        ), patch.object(
            cron_test_gate.subprocess, "run", return_value=FakeCompleted()
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cron_test_gate.main()
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
