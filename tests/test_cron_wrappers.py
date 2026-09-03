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

from scripts import cron_health_check, cron_routing_refresh, cron_wiki_regen


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
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ) as run_mock:
            rc = cron_health_check.main()
        self.assertEqual(rc, 0)
        self.assertEqual(run_mock.call_count, 5)

    def test_hard_fail_when_routing_stale(self):
        fake_routing = {"exit": 3, "stdout": json.dumps({"routing_freshness": {"status": "stale"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
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
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
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
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
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
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 0)

    def test_graph_checks_are_not_run_by_a2(self):
        # Graph depth checks (orphan sweep, coverage drift) live on A10/A11;
        # A2 stays a fast liveness probe and must not page on the same drift.
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ) as run_mock:
            rc = cron_health_check.main()
        labels = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(rc, 0)
        self.assertEqual(run_mock.call_count, 5)
        self.assertNotIn("graph_validate", labels)
        self.assertNotIn("graph_freshness", labels)

    def test_hard_fail_when_vector_memory_index_empty(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 0}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)

    def test_stale_but_populated_vector_index_is_soft_warning(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {
            "exit": 1,
            "stdout": json.dumps(
                {"document_count": 145, "status": "degraded", "stale_source_count": 14}
            ),
            "stderr": "",
        }
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 0)

    def test_missing_vector_index_is_hard_fail(self):
        fake_routing = {"exit": 0, "stdout": json.dumps({"routing_freshness": {"status": "fresh"}}), "stderr": ""}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {"exit": 2, "stdout": "", "stderr": "index missing"}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
        ):
            rc = cron_health_check.main()
        self.assertEqual(rc, 1)

    def test_routing_timeout_treated_as_hard_fail(self):
        fake_routing = {"exit": 124, "stdout": "", "stderr": "timeout after 300s"}
        fake_wiki = {
            "exit": 0,
            "stdout": json.dumps({"status": "fresh"}),
            "stderr": "",
        }
        fake_aliases = {"exit": 0, "stdout": "", "stderr": ""}
        fake_cron_reg = {"exit": 0, "stdout": "CRON REGISTRATION OK", "stderr": ""}
        fake_vector = {"exit": 0, "stdout": json.dumps({"document_count": 5}), "stderr": ""}
        with patch.object(
            cron_health_check,
            "_run",
            side_effect=[
                fake_routing,
                fake_wiki,
                fake_aliases,
                fake_cron_reg,
                fake_vector,
            ],
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


class RoutingRefreshLogicTests(unittest.TestCase):
    def test_timeout_fails_closed(self):
        with patch.object(
            cron_routing_refresh.subprocess,
            "run",
            side_effect=cron_routing_refresh.subprocess.TimeoutExpired(
                cmd=["workflow_router.py"],
                timeout=cron_routing_refresh.TIMEOUT_SECONDS,
            ),
        ):
            rc = cron_routing_refresh.main()

        self.assertEqual(rc, 1)

    def test_malformed_router_output_fails_closed(self):
        fake_result = subprocess_result(returncode=0, stdout="not-json")
        with patch.object(
            cron_routing_refresh.subprocess,
            "run",
            return_value=fake_result,
        ):
            rc = cron_routing_refresh.main()

        self.assertEqual(rc, 1)

    def test_non_object_router_output_fails_closed(self):
        fake_result = subprocess_result(returncode=0, stdout="[]")
        with patch.object(
            cron_routing_refresh.subprocess,
            "run",
            return_value=fake_result,
        ):
            rc = cron_routing_refresh.main()

        self.assertEqual(rc, 1)

    def test_refresh_command_validates_before_writing(self):
        fake_result = subprocess_result(
            returncode=0,
            stdout=json.dumps({"routing_index_stale": False}),
        )
        with patch.object(
            cron_routing_refresh.subprocess,
            "run",
            return_value=fake_result,
        ) as run_mock:
            rc = cron_routing_refresh.main()

        self.assertEqual(rc, 0)
        self.assertEqual(run_mock.call_count, 1)
        command = run_mock.call_args.args[0]
        self.assertIn("--validate", command)
        self.assertIn("--write-index", command)


def subprocess_result(*, returncode, stdout, stderr=""):
    """Build a minimal stand-in for subprocess.CompletedProcess."""
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()
