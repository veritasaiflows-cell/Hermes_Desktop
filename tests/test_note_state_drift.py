#!/usr/bin/env python3
"""Contract tests for the note/state drift monitor."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import check_note_state_drift as drift


class NoteStateDriftTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        record: dict,
        note_text: str | None,
        note_name: str = "WF-9000-Test.md",
    ) -> Path:
        """Write one state record (+ optional note) and return the state dir."""
        state_dir = root / "state" / "workflows"
        state_dir.mkdir(parents=True, exist_ok=True)
        record.setdefault("workflow_id", "WF-9000")
        record.setdefault("continuity_note", f"continuity\\{note_name}")
        (state_dir / f"{record['workflow_id']}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        if note_text is not None:
            note_dir = root / "continuity"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / note_name).write_text(note_text, encoding="utf-8")
        return state_dir

    def _scan(self, root: Path, state_dir: Path, ack: Path | None = None) -> dict:
        return drift.scan(state_dir, root, ack)

    # --- green paths -------------------------------------------------------

    def test_green_when_every_authored_fact_appears_in_note(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {
                    "effective_status": "route_only",
                    "blockers": ["No approved connector for phase 3."],
                    "stop_lines": ["No external writes without approval."],
                },
                "# Note\n\n## Current state\n`route_only` — scaffolded.\n\n"
                "## Blockers\n- No approved connector for phase 3.\n\n"
                "## Stop lines\n- No external writes without approval.\n",
            )
            result = self._scan(root, state_dir)
            self.assertEqual(result["workflows_with_drift"], 0)
            self.assertEqual(result["missing_total"], 0)

    def test_wrapped_bullet_is_matched_as_one_block(self) -> None:
        """A fact wrapped across physical lines must not read as missing."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {
                    "effective_status": "active",
                    "blockers": [
                        "No measured comparison of gated versus ungated agent "
                        "behavior beyond the WF-1000 corpus."
                    ],
                },
                "# Note\nStatus: active.\n\n## Known limitations\n"
                "- No measured comparison of gated versus ungated agent behavior\n"
                "  exists beyond the WF-1000 corpus. The counterfactual covers one\n"
                "  workflow's 30 leads; no second corpus has been measured.\n",
            )
            result = self._scan(root, state_dir)
            self.assertEqual(result["missing_total"], 0, result["workflows"])

    def test_markdown_emphasis_does_not_cause_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {"effective_status": "active", "blockers": ["Demand is unvalidated."]},
                "# Note\nStatus: active.\n\n- **Demand is unvalidated.**\n",
            )
            self.assertEqual(self._scan(root, state_dir)["missing_total"], 0)

    # --- drift detection ---------------------------------------------------

    def test_blocker_absent_from_note_is_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {
                    "effective_status": "active",
                    "blockers": ["Spend cap policy is not approved."],
                },
                "# Note\nStatus: active.\n\nEverything is proceeding well.\n",
            )
            result = self._scan(root, state_dir)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertEqual(result["missing_total"], 1)
            self.assertEqual(
                result["workflows"][0]["missing"][0]["fact"],
                "Spend cap policy is not approved.",
            )

    def test_status_absent_from_note_is_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {"effective_status": "route_only", "blockers": []},
                "# Note\n\nThis workflow is running normally.\n",
            )
            result = self._scan(root, state_dir)
            fields = [m["field"] for m in result["workflows"][0]["missing"]]
            self.assertIn("effective_status", fields)

    def test_missing_note_file_is_reported_as_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root, {"effective_status": "active", "blockers": []}, None
            )
            result = self._scan(root, state_dir)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertIn("does not exist", result["workflows"][0]["error"])

    # --- derived fields ----------------------------------------------------

    def test_derived_dependency_blockers_are_not_required_in_note(self) -> None:
        """Router-generated blockers are machine-owned; notes must not duplicate them."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            derived = "Dependency WF-1001 is not active for downstream execution."
            state_dir = self._fixture(
                root,
                {
                    "effective_status": "route_only",
                    "blockers": ["No approved connector.", derived],
                    "dependency_blockers": [derived],
                },
                "# Note\n\n## Current state\n`route_only`\n\n"
                "## Blockers\n- No approved connector.\n",
            )
            result = self._scan(root, state_dir)
            self.assertEqual(result["missing_total"], 0)
            self.assertEqual(result["workflows"][0]["derived_facts_skipped"], 1)

    # --- acknowledgments ---------------------------------------------------

    def test_acknowledged_fact_suppresses_drift_and_stays_visible(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fact = "No paying client engagement exists; demand is unvalidated."
            state_dir = self._fixture(
                root,
                {"effective_status": "active", "blockers": [fact]},
                "# Note\nStatus: active.\n\n"
                "- No buyer conversation has taken place; pricing is untested.\n",
            )
            ack_path = root / "ack.json"
            ack_path.write_text(
                json.dumps(
                    {
                        "acknowledged": {
                            drift._fact_key("WF-9000", "blockers", fact): {
                                "covered_by": "note -> 'No buyer conversation has taken place'",
                                "reason": "same claim, different words",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self._scan(root, state_dir, ack_path)
            self.assertEqual(result["workflows_with_drift"], 0)
            self.assertEqual(result["acknowledged_total"], 1)
            self.assertEqual(
                result["workflows"][0]["acknowledged"][0]["covered_by"],
                "note -> 'No buyer conversation has taken place'",
            )

    def test_reworded_state_fact_invalidates_its_acknowledgment(self) -> None:
        """An ack covers one claim, not a field in perpetuity."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_fact = "No paying client engagement exists."
            new_fact = "Three client engagements are contractually blocked on legal review."
            state_dir = self._fixture(
                root,
                {"effective_status": "active", "blockers": [new_fact]},
                "# Note\nStatus: active.\n\n- No buyer conversation has taken place.\n",
            )
            ack_path = root / "ack.json"
            ack_path.write_text(
                json.dumps(
                    {
                        "acknowledged": {
                            drift._fact_key("WF-9000", "blockers", old_fact): {
                                "covered_by": "stale",
                                "reason": "stale",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self._scan(root, state_dir, ack_path)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertEqual(result["acknowledged_total"], 0)

    def test_missing_entry_carries_ack_key_for_the_operator(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(
                root,
                {"effective_status": "active", "blockers": ["Spend cap not approved."]},
                "# Note\nStatus: active.\n",
            )
            entry = self._scan(root, state_dir)["workflows"][0]["missing"][0]
            self.assertEqual(
                entry["ack_key"],
                drift._fact_key("WF-9000", "blockers", "Spend cap not approved."),
            )

    # --- empty scan --------------------------------------------------------

    def test_empty_state_dir_is_not_reported_as_verified(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state" / "workflows"
            state_dir.mkdir(parents=True)
            result = self._scan(root, state_dir)
            self.assertEqual(result["workflows_checked"], 0)
            self.assertEqual(result["workflows_with_drift"], 0)
            self.assertEqual(
                drift.main(["--state-dir", str(state_dir), "--ack-file", str(root / "none.json")]),
                0,
            )

    # --- live repo ---------------------------------------------------------

    def test_repository_workflows_are_currently_clean(self) -> None:
        """The real repo must stay green so the cron gate means something."""
        self.assertEqual(
            drift.main([]), 0, "note/state drift detected in the live workspace"
        )


if __name__ == "__main__":
    unittest.main()
