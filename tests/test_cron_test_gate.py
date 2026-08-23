#!/usr/bin/env python3
"""Tests for scripts/cron_test_gate.py lane preflight."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import cron_test_gate


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

    def test_defers_with_exit_2_when_blocking(self):
        with patch.object(
            cron_test_gate, "_active_write_lanes_blocking",
            return_value=[{"lane_id": "x", "owner": "o", "status": "running"}],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cron_test_gate.main()
        self.assertEqual(rc, 2)
        self.assertIn("TEST GATE DEFERRED", buf.getvalue())
        self.assertIn('"blocking_lanes"', buf.getvalue())

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
