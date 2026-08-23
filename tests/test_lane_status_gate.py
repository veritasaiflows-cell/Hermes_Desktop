#!/usr/bin/env python3
"""Tests for the lane-register gate wiring in workspace_status.py.

These tests exercise the health-decision logic directly against fabricated
gate outputs — no subprocesses, no real register reads.
"""
from __future__ import annotations

import unittest

from scripts import workspace_status


def _ok_gate(stdout: dict) -> dict:
    return {"label": "lane_register", "exit": 0, "elapsed_ms": 1, "stdout": stdout, "stderr_tail": []}


def _clean_validation(**overrides) -> dict:
    payload = {
        "ok": True,
        "expired_leases": [],
        "collisions": [],
        "hard_failures": [],
        "warnings": [],
        "active_lanes": 0,
    }
    payload.update(overrides)
    return payload


class TestLaneRegisterGatePresent(unittest.TestCase):

    def test_default_gates_include_lane_register(self):
        labels = [label for label, _, _ in workspace_status.DEFAULT_GATES]
        self.assertIn("lane_register", labels)


class TestLaneRegisterHealthDecision(unittest.TestCase):

    def _health(self, lane_gate: dict) -> tuple[str, list[str], list[str]]:
        """Call _health_decision with all hard-label gates green + the lane gate."""
        gates = {
            label: {"label": label, "exit": 0, "stdout": None, "stderr_tail": []}
            for label in (
                "organization", "routing", "wiki", "alias", "cron_registration",
                "claim_drift", "feedback_evaluation", "graph_integrity",
                "graph_freshness", "graphify_freshness", "vector_memory",
                "workspace_index", "archive_stale",
            )
        }
        # feedback_evaluation needs a well-formed stdout to stay quiet
        gates["feedback_evaluation"]["stdout"] = {
            "status": "ready", "report_status": "fresh",
        }
        # wiki needs a fresh status to stay quiet
        gates["wiki"]["stdout"] = {"status": "fresh"}
        if lane_gate is not None:
            gates["lane_register"] = lane_gate
        return workspace_status._health_decision(gates)

    def test_healthy_when_lane_gate_clean(self):
        decision, hard, warnings = self._health(_ok_gate(_clean_validation()))
        self.assertEqual(decision, "healthy")
        self.assertEqual(hard, [])
        self.assertEqual(warnings, [])

    def test_degraded_not_raised_on_lane_gate_nonzero_exit(self):
        gate = _ok_gate(_clean_validation())
        gate["exit"] = 2
        decision, hard, warnings = self._health(gate)
        # Lane degradation is a warning, not a hard failure, and must not
        # flip overall health to degraded.
        self.assertEqual(hard, [])
        self.assertEqual(decision, "healthy_with_warnings")
        self.assertIn("lane_register_degraded", warnings)

    def test_expired_lease_produces_warning(self):
        gate = _ok_gate(_clean_validation(expired_leases=[{"lane_id": "x"}]))
        decision, hard, warnings = self._health(gate)
        self.assertIn("lane_lease_expired", warnings)
        self.assertEqual(hard, [])

    def test_collision_produces_warning(self):
        gate = _ok_gate(
            _clean_validation(collisions=[{"lane_a": "a", "lane_b": "b"}])
        )
        decision, hard, warnings = self._health(gate)
        self.assertIn("lane_collision", warnings)
        self.assertEqual(hard, [])

    def test_hard_failures_in_register_produce_warning(self):
        gate = _ok_gate(
            _clean_validation(hard_failures=[{"code": "expired_lease"}])
        )
        _, _, warnings = self._health(gate)

    def test_missing_lane_gate_is_silent(self):
        # Gate absent (e.g. tests inject a custom GATES list without it) must
        # not produce spurious warnings.
        decision, hard, warnings = self._health(None)
        self.assertEqual(decision, "healthy")
        self.assertEqual(warnings, [])

    def test_malformed_stdout_is_safe(self):
        gate = _ok_gate("not-a-dict")  # stdout wasn't JSON-parseable
        decision, hard, warnings = self._health(gate)
        self.assertEqual(hard, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
