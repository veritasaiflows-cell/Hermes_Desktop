#!/usr/bin/env python3
"""Tests for scripts/cron_lane_lease_check.py."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import cron_lane_lease_check


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class LeaseWatchdogScanTests(unittest.TestCase):

    NOW = datetime(2026, 8, 23, 1, 0, 0, tzinfo=timezone.utc)

    def _register(self, tmp: Path, rows: list[tuple]) -> Path:
        register = tmp / "lane-register.sqlite"
        conn = sqlite3.connect(register)
        conn.execute(
            "CREATE TABLE lanes (lane_id TEXT, owner TEXT, status TEXT, lease_expires_at TEXT)"
        )
        conn.executemany("INSERT INTO lanes VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        return register

    def test_missing_register_is_clean(self):
        expiring, expired = cron_lane_lease_check._scan(
            Path("no/such/file.sqlite"), self.NOW, 60
        )
        self.assertEqual((expiring, expired), ([], []))

    def test_healthy_lanes_are_silent(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._register(
                Path(d),
                [("lane-a", "agent-main", "running", _iso(self.NOW + timedelta(hours=3)))],
            )
            expiring, expired = cron_lane_lease_check._scan(reg, self.NOW, 60)
        self.assertEqual(expiring, [])
        self.assertEqual(expired, [])

    def test_terminal_lanes_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._register(
                Path(d),
                [("done", "a", "complete", _iso(self.NOW - timedelta(days=1))),
                 ("dead", "a", "blocked", None)],
            )
            expiring, expired = cron_lane_lease_check._scan(reg, self.NOW, 60)
        self.assertEqual((expiring, expired), ([], []))

    def test_expiring_within_window_warns(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._register(
                Path(d),
                [("warn", "agent-main", "leased", _iso(self.NOW + timedelta(minutes=30)))],
            )
            expiring, expired = cron_lane_lease_check._scan(reg, self.NOW, 60)
        self.assertEqual([e["lane_id"] for e in expiring], ["warn"])
        self.assertEqual(expired, [])

    def test_expired_active_lane_flags(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._register(
                Path(d),
                [("dead", "agent-main", "running", _iso(self.NOW - timedelta(minutes=5)))],
            )
            expiring, expired = cron_lane_lease_check._scan(reg, self.NOW, 60)
        self.assertEqual(expiring, [])
        self.assertEqual([e["lane_id"] for e in expired], ["dead"])
        self.assertEqual(expired[0]["reason"], "lease_expired_while_active")

    def test_missing_expiry_flags(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._register(
                Path(d), [("nolease", "agent-main", "running", None)]
            )
            _, expired = cron_lane_lease_check._scan(reg, self.NOW, 60)
        self.assertEqual(expired[0]["reason"], "missing_lease_expiry")


class LeaseWatchdogMainTests(unittest.TestCase):

    def test_green_exit_zero_silent_stdout(self):
        with patch.object(cron_lane_lease_check, "_scan", return_value=([], [])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cron_lane_lease_check.main()
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")

    def test_degraded_exit_one_with_payload(self):
        expiring = [{"lane_id": "x", "owner": "o", "status": "running", "lease_expires_at": "t"}]
        with patch.object(cron_lane_lease_check, "_scan", return_value=(expiring, [])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cron_lane_lease_check.main()
        self.assertEqual(rc, 1)
        self.assertIn("LANE LEASE DEGRADED", buf.getvalue())
        payload = json.loads(buf.getvalue().split("\n", 1)[1])
        self.assertEqual(payload["expiring_soon"], expiring)


if __name__ == "__main__":
    unittest.main(verbosity=2)
