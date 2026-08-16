"""Tests for the cron health check (A2) and wiki regen (A1) wrappers.

These test the *logic* of the wrappers (exit codes, output shape, branching)
directly. They do NOT subprocess the real `run_checks.py` / `wiki_bootstrap.py`
because that would cause the test to be discovered and re-run by the suite
itself, creating an infinite-spawn loop.

The full end-to-end cron path is exercised by the cron scheduler in
production, not by these unit tests. These tests pin the contract.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts import cron_health_check, cron_wiki_regen


class HealthCheckLogicTests(unittest.TestCase):
    def test_green_when_routing_and_alias_and_wiki_fresh(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ) as run_mock:
            rc = cron_health_check.main()
        self.assertEqual(rc, 0)
        self.assertEqual(run_mock.call_count, 4)

    def test_hard_fail_when_routing_stale(self):
        fake_routing = {"exit": 3, "stdout": json.dumps({"routing_freshness": {"status": "stale"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)

    def test_hard_fail_when_alias_sweep_fails(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 1, "stdout": "ALIAS SWEEP DEGRADED", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)

    def test_hard_fail_when_cron_registration_fails(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 1, "stdout": "CRON REGISTRATION FAIL", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)

    def test_stale_wiki_is_soft_warning_not_hard_fail(self):
        # A2 contract: stale wiki is recoverable by A1, must not page.
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 2,
            "stdout": json.dumps({"status": "stale", "issues": [{"type": "stale_freshness"}]}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 0)

    def test_routing_timeout_treated_as_hard_fail(self):
        fake_routing = {"exit": 124, "stdout": "", "stderr": "timeout after 300s"}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[fake_routing, fake_wiki, fake_aliases, fake_cron_reg],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)


class WikiRegenLogicTests(unittest.TestCase):
    def test_fresh_publish_returns_zero(self):
        fake_result = subprocess_result(
            returncode=0,
            stdout=json.dumps({"status": "fresh", "changed_sources": []}),
        )
        with patch.object(cron_wiki_regen.subprocess, "run", return_value=fake_result):
            rc = cron_wiki_regen.main()
        self.assertEqual(rc, 0)

    def test_degraded_publish_returns_one(self):
        fake_result = subprocess_result(
            returncode=0,
            stdout=json.dumps({"status": "stale", "issues": [{"type": "x"}]}),
        )
        with patch.object(cron_wiki_regen.subprocess, "run", return_value=fake_result):
            rc = cron_wiki_regen.main()
        self.assertEqual(rc, 1)

    def test_subprocess_failure_returns_one(self):
        fake_result = subprocess_result(returncode=1, stdout="", stderr="boom")
        with patch.object(cron_wiki_regen.subprocess, "run", return_value=fake_result):
            rc = cron_wiki_regen.main()
        self.assertEqual(rc, 1)


def subprocess_result(*, returncode, stdout, stderr=""):
    """Build a minimal stand-in for subprocess.CompletedProcess."""
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()
