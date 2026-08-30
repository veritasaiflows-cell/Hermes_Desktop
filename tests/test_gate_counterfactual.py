"""Regression tests for the WF-1004 gate counterfactual baseline.

These tests pin the honesty properties of the baseline, not just its plumbing:
the report must never claim revenue, must never invent economics for leads that
lack public evidence, and must refuse to emit a baseline from an empty corpus.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import gate_counterfactual as gc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = PROJECT_ROOT / "derived" / "research"
POLICY_PATH = PROJECT_ROOT / "state" / "commerce-launch-policy.json"


class GateCounterfactualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RESEARCH_DIR.exists():
            raise unittest.SkipTest("WF-1000 evidence corpus not present")
        cls.report = gc.build_report(
            research_dir=RESEARCH_DIR, policy_path=POLICY_PATH
        )

    def test_gated_policy_selects_nothing(self):
        """The whole case study rests on this: the real policy selected zero."""
        self.assertEqual(0, self.report["gated_result"]["selected_count"])
        self.assertEqual([], self.report["gated_result"]["selected"])

    def test_ungated_policy_selects_something(self):
        """A naive agent is not paralysed - it ships. That is the contrast."""
        self.assertGreater(self.report["ungated_result"]["selected_count"], 0)

    def test_every_naive_selection_has_a_real_failure(self):
        """No naive pick may be silently fine; each must fail a real gate."""
        for row in self.report["ungated_result"]["selected"]:
            failures = row.get("actual_fail_reasons") or []
            gaps = row.get("unresolved_evidence_gaps") or []
            self.assertTrue(
                failures or gaps,
                msg=f"{row['lead_id']} was selected by the naive policy but "
                "carries no recorded gate failure or evidence gap",
            )

    def test_corpus_has_no_duplicate_lead_ids(self):
        """The Doba verification enriches its discovery row, never duplicates it."""
        leads = gc.build_lead_table(gc.load_corpus(RESEARCH_DIR))
        ids = [lead["lead_id"] for lead in leads]
        self.assertEqual(len(ids), len(set(ids)), msg=f"duplicate lead ids: {ids}")

    def test_corpus_size_matches_recorded_evidence(self):
        """30 leads is the number quoted in the case study; pin it."""
        self.assertEqual(30, self.report["corpus"]["total_leads"])

    def test_spend_is_reported_as_avoided_cost_not_revenue(self):
        """The single most important honesty property in this repo."""
        spend = self.report["spend"]
        self.assertEqual(0.0, spend["actually_spent_usd"])
        note = spend["accounting_note"].lower()
        self.assertIn("avoided cost", note)
        self.assertIn("not earned revenue", note)

    def test_report_never_claims_revenue(self):
        """Guard against a future edit introducing a revenue claim."""
        blob = json.dumps(self.report).lower()
        for banned in ("revenue_generated", "profit_earned", "earnings_usd"):
            self.assertNotIn(banned, blob)

    def test_no_sales_volume_is_assumed(self):
        """Per-unit economics only; volume is unknown and must stay unmodelled."""
        self.assertIsNone(self.report["avoided_loss"]["sales_volume_assumed"])
        self.assertEqual(
            "per_unit_economics_only", self.report["avoided_loss"]["basis"]
        )

    def test_unquantifiable_leads_are_named_not_guessed(self):
        """Leads without public economics are listed, never assigned a value."""
        avoided = self.report["avoided_loss"]
        for row in avoided["quantified"]:
            self.assertIsNotNone(row["unit_cost_usd"])
            self.assertIsNotNone(row["assumed_retail_usd"])
        self.assertIsInstance(avoided["unquantifiable_lead_ids"], list)

    def test_gate_attribution_is_populated(self):
        """The attribution table is the sales pitch; it must not be empty."""
        attribution = self.report["gate_attribution"]
        self.assertGreater(len(attribution), 0)
        self.assertGreater(sum(attribution.values()), 0)

    def test_binding_constraint_is_identified(self):
        """Each quantified pick names what actually stopped it."""
        for row in self.report["avoided_loss"]["quantified"]:
            self.assertTrue(row["binding_constraints"])
            self.assertIn(
                row["margin_gate_verdict"],
                {"passes_margin_gate", "fails_margin_gate"},
            )

    def test_empty_corpus_is_refused(self):
        """An empty corpus must raise, never emit a hollow green baseline."""
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "research"
            empty.mkdir()
            with self.assertRaises(gc.CounterfactualError):
                gc.build_report(research_dir=empty, policy_path=POLICY_PATH)

    def test_corrupt_record_is_refused(self):
        """A malformed evidence record must fail loudly, not silently degrade."""
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "research"
            staged.mkdir()
            for name in (
                gc.DISCOVERY_RECORD,
                gc.TOPDAWG_RECORD,
                gc.DOBA_RECORD,
                gc.CJ_RECORD,
            ):
                shutil.copy2(RESEARCH_DIR / name, staged / name)
            (staged / gc.DISCOVERY_RECORD).write_text("{not json", encoding="utf-8")
            with self.assertRaises(gc.CounterfactualError):
                gc.build_report(research_dir=staged, policy_path=POLICY_PATH)

    def test_formatted_report_renders(self):
        """The human-readable path must not crash on the real corpus."""
        text = gc.format_report(self.report)
        self.assertIn("GATE COUNTERFACTUAL BASELINE", text)
        self.assertIn("AVOIDED COST", text.upper())


if __name__ == "__main__":
    unittest.main()
