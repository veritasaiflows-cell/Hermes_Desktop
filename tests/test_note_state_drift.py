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
                drift.main(
                    ["--state-dir", str(state_dir), "--ack-file", str(root / "none.json"), "--allow-no-fleet"]
                ),
                0,
            )

    def test_custom_state_dir_does_not_move_the_fleet_presence_signal(self) -> None:
        """The WF-1200 record lives with the other fleet artifacts under the project root,
        not under whatever --state-dir was passed (that only selects which records are scanned)."""
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            result = drift.scan(elsewhere, root, None)
            self.assertEqual(result["roster"]["status"], "ok", result["roster"])

    def test_relative_state_dir_is_a_structured_result_not_a_traceback(self) -> None:
        """QA round 4: a relative --state-dir made relative_to() raise before any handler ran."""
        import os

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self._fixture(root, {"effective_status": "active"}, "# Note\nStatus: active.\n")
            previous = Path.cwd()
            os.chdir(root)
            try:
                result = drift.scan(Path("state/workflows"), root, None)
            finally:
                os.chdir(previous)
            self.assertEqual(result["workflows_checked"], 1)
            self.assertEqual(result["workflows_with_drift"], 0, result["workflows"])
            self.assertIsNone(result["workflows"][0]["error"])

    def test_state_dir_outside_the_project_root_is_a_structured_finding_not_a_traceback(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as other:
            root = Path(directory)
            external = Path(other) / "workflows"
            external.mkdir()
            (external / "WF-8000.json").write_text(
                json.dumps({"workflow_id": "WF-8000", "continuity_note": "continuity\\x.md"}), encoding="utf-8"
            )
            result = drift.scan(external, root, None)
            self.assertEqual(result["workflows_checked"], 1)
            report = result["workflows"][0]
            self.assertEqual(report["workflow_id"], "WF-8000")
            self.assertIsNotNone(report["state_record"])
            self.assertTrue(report["error"] or report["missing"], report)

    def test_main_with_external_state_dir_exits_nonzero_without_a_traceback(self) -> None:
        from unittest import mock

        with TemporaryDirectory() as directory, TemporaryDirectory() as other:
            root = Path(directory)
            external = Path(other) / "workflows"
            external.mkdir()
            (external / "WF-8000.json").write_text(
                json.dumps({"workflow_id": "WF-8000", "continuity_note": "continuity\\x.md"}), encoding="utf-8"
            )
            with mock.patch.object(drift, "PROJECT_ROOT", root):
                code = drift.main(
                    ["--state-dir", str(external), "--ack-file", str(root / "none.json"), "--allow-no-fleet"]
                )
            self.assertEqual(code, 1)

    def test_continuity_note_pointing_outside_the_project_root_is_a_finding_not_a_traceback(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as other:
            root = Path(directory)
            outside = Path(other) / "outside.md"
            outside.write_text("# Note\nStatus: active.\n", encoding="utf-8")
            for pointer in (str(outside), "..\\..\\escape.md"):
                with self.subTest(pointer=pointer):
                    state_dir = self._fixture(
                        root, {"effective_status": "active", "continuity_note": pointer}, None
                    )
                    result = drift.scan(state_dir, root, None)
                    report = result["workflows"][0]
                    self.assertTrue(report["error"], report)
                    self.assertEqual(result["workflows_with_drift"], 1)

    def test_unreadable_continuity_note_is_a_finding_not_a_traceback(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(root, {"effective_status": "active"}, None)
            note = root / "continuity" / "WF-9000-Test.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_bytes(b"\xff\xfe\x80 not utf-8\n")
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertIn("unreadable", result["workflows"][0]["error"])

    def test_corrupt_state_record_is_a_structured_error_not_a_traceback(self) -> None:
        """A bad record must not crash the whole scan before the roster is checked."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state" / "workflows"
            state_dir.mkdir(parents=True)
            (state_dir / "WF-7000.json").write_text("{not json", encoding="utf-8")
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["workflows_checked"], 1)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertIn("state record", result["workflows"][0]["error"])
            self.assertTrue(drift.has_drift(result))

    def test_state_record_that_is_a_directory_is_a_structured_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state" / "workflows"
            (state_dir / "WF-7001.json").mkdir(parents=True)
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["workflows_with_drift"], 1)
            self.assertIn("state record", result["workflows"][0]["error"])

    def test_corrupt_record_does_not_hide_roster_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state" / "workflows"
            state_dir.mkdir(parents=True)
            (state_dir / "WF-7000.json").write_text("[]", encoding="utf-8")
            result = drift.scan(state_dir, root, None, require_fleet=True)
            self.assertEqual(result["roster"]["status"], "drift")
            self.assertEqual(result["workflows_with_drift"], 1)

    # --- registry -> note roster (A19 extension) -----------------------------

    def _roster_root(self, root: Path, registry: dict, note_block: str | None) -> Path:
        """Project root with a registry, a WF-1200 note, and an empty state dir."""
        from scripts import fleet_roster_block as roster

        (root / "state" / "workflows").mkdir(parents=True, exist_ok=True)
        (root / "continuity").mkdir(parents=True, exist_ok=True)
        (root / "state" / "fleet-role-registry.json").write_text(json.dumps(registry), encoding="utf-8")
        body = "# WF-1200\n\n" + (note_block if note_block is not None else "hand written table\n")
        (root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").write_text(body, encoding="utf-8")
        # one unrelated, clean workflow so the scan is not an "empty scan"
        self._fixture(root, {"workflow_id": "WF-9000", "effective_status": "x"}, "# n\nx\n")
        # the independent fleet-presence signal: the WF-1200 workflow record
        (root / "state" / "workflows" / "WF-1200.json").write_text(
            json.dumps({"workflow_id": "WF-1200", "effective_status": "route_only"}), encoding="utf-8"
        )
        return root

    _REG = {
        "schema": "fleet-role-registry.v1",
        "workflow_id": "WF-1200",
        "roles": {
            "senior_engineer": {
                "profile": "seniorengineer", "provider": "openai-codex", "model": "gpt-6.1-sol",
                "status": "admissible", "modes": ["read-only"], "task_classes": ["analysis"],
                "min_lane_retry_count": 1,
            }
        },
        "lane_exceptions": [],
    }

    def test_registry_roster_in_sync_is_clean(self) -> None:
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "ok", result["roster"])
            self.assertEqual(result["roster_drift"], 0)

    def test_registry_change_without_note_regeneration_is_roster_drift(self) -> None:
        from scripts import fleet_roster_block as roster

        stale = roster.render_block(self._REG)
        changed = json.loads(json.dumps(self._REG))
        changed["roles"]["senior_engineer"]["model"] = "other-model"
        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), changed, stale)
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift")
            self.assertEqual(result["roster_drift"], 1)

    def test_hand_written_roster_without_markers_is_roster_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, None)
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift")

    def test_roster_drift_makes_main_exit_nonzero(self) -> None:
        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, None)
            # main() resolves the registry relative to PROJECT_ROOT, so assert via scan + exit rule.
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertTrue(drift.has_drift(result))

    def test_no_registry_means_roster_check_is_skipped_not_failed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(root, {"effective_status": "active"}, "# Note\nStatus: active.\n")
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["roster"]["status"], "skipped")
            self.assertEqual(result["roster_drift"], 0)
            self.assertFalse(drift.has_drift(result))

    def test_empty_workflow_scan_with_roster_drift_still_fails(self) -> None:
        """An empty workflow scan must not mask roster drift (main() ordering bug)."""
        from unittest import mock

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state" / "workflows").mkdir(parents=True)
            (root / "continuity").mkdir()
            (root / "state" / "fleet-role-registry.json").write_text(
                json.dumps(self._REG), encoding="utf-8"
            )
            (root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").write_text(
                "# WF-1200\n\nhand written, no markers\n", encoding="utf-8"
            )
            with mock.patch.object(drift, "PROJECT_ROOT", root):
                code = drift.main(
                    ["--state-dir", str(root / "state" / "workflows"),
                     "--ack-file", str(root / "none.json")]
                )
            self.assertEqual(code, 1)

    def test_empty_workflow_scan_without_registry_still_passes(self) -> None:
        from unittest import mock

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state" / "workflows").mkdir(parents=True)
            with mock.patch.object(drift, "PROJECT_ROOT", root):
                code = drift.main(
                    ["--state-dir", str(root / "state" / "workflows"),
                     "--ack-file", str(root / "none.json"), "--allow-no-fleet"]
                )
            self.assertEqual(code, 0)

    def test_main_fails_closed_when_no_fleet_artifacts_exist_at_all(self) -> None:
        """QA round 3: the all-absent end state of a staged removal is drift unless explicitly allowed."""
        from unittest import mock

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state" / "workflows").mkdir(parents=True)
            with mock.patch.object(drift, "PROJECT_ROOT", root):
                code = drift.main(
                    ["--state-dir", str(root / "state" / "workflows"), "--ack-file", str(root / "none.json")]
                )
            self.assertEqual(code, 1)

    def test_scan_default_still_skips_a_project_with_no_fleet(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state" / "workflows").mkdir(parents=True)
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "skipped")
            required = drift.scan(root / "state" / "workflows", root, None, require_fleet=True)
            self.assertEqual(required["roster"]["status"], "drift")
            self.assertIn("required", required["roster"]["reason"])

    def test_missing_wf1200_record_while_registry_and_note_exist_is_drift(self) -> None:
        """QA round 3: the presence signal itself may not be deletable without detection."""
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            (root / "state" / "workflows" / "WF-1200.json").unlink()
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])
            self.assertIn("WF-1200 record", result["roster"]["reason"])

    def test_wf1200_record_that_is_a_directory_is_drift(self) -> None:
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            record = root / "state" / "workflows" / "WF-1200.json"
            record.unlink()
            record.mkdir()
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])

    def test_staged_removal_of_each_artifact_in_turn_never_ends_in_a_clean_pass(self) -> None:
        """Delete registry, note and record one at a time under the default (fail-closed) main()."""
        from unittest import mock
        from scripts import fleet_roster_block as roster

        for order in (
            ("registry", "note", "record"),
            ("record", "note", "registry"),
            ("note", "record", "registry"),
        ):
            with self.subTest(order=order), TemporaryDirectory() as directory:
                root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
                paths = {
                    "registry": root / "state" / "fleet-role-registry.json",
                    "note": root / "continuity" / "WF-1200-Agent-Fleet-Roles.md",
                    "record": root / "state" / "workflows" / "WF-1200.json",
                }
                for name in order:
                    paths[name].unlink()
                    with mock.patch.object(drift, "PROJECT_ROOT", root):
                        code = drift.main(
                            ["--state-dir", str(root / "state" / "workflows"),
                             "--ack-file", str(root / "none.json")]
                        )
                    self.assertEqual(code, 1, f"deleting {name} (order {order}) must not pass")

    def test_deleted_registry_with_a_roster_note_present_is_drift_not_skipped(self) -> None:
        """QA finding 4: removing the authoritative registry must not switch the check off."""
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            (root / "state" / "fleet-role-registry.json").unlink()
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])
            self.assertIn("registry", result["roster"]["reason"])
            self.assertTrue(drift.has_drift(result))

    def test_registry_path_that_is_a_directory_is_drift(self) -> None:
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            registry = root / "state" / "fleet-role-registry.json"
            registry.unlink()
            registry.mkdir()
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])

    def test_no_registry_and_no_roster_note_is_skipped(self) -> None:
        """A project with no fleet at all has nothing to verify."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = self._fixture(root, {"effective_status": "active"}, "# Note\nStatus: active.\n")
            self.assertFalse((root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").exists())
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["roster"]["status"], "skipped")

    def test_both_artifacts_deleted_but_wf1200_record_present_is_drift(self) -> None:
        """QA round 2: deleting the registry AND the note must not make the control vanish."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state" / "workflows"
            state_dir.mkdir(parents=True)
            (state_dir / "WF-1200.json").write_text(
                json.dumps({"workflow_id": "WF-1200", "effective_status": "route_only"}), encoding="utf-8"
            )
            self.assertFalse((root / "state" / "fleet-role-registry.json").exists())
            self.assertFalse((root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").exists())
            result = drift.scan(state_dir, root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])
            self.assertTrue(drift.has_drift(result))

    def test_registry_present_but_note_deleted_is_drift(self) -> None:
        from scripts import fleet_roster_block as roster

        with TemporaryDirectory() as directory:
            root = self._roster_root(Path(directory), self._REG, roster.render_block(self._REG))
            (root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").unlink()
            result = drift.scan(root / "state" / "workflows", root, None)
            self.assertEqual(result["roster"]["status"], "drift", result["roster"])

    def test_missing_state_dir_does_not_mask_roster_drift(self) -> None:
        """Same class as the empty-scan bug: an early 'nothing to check' exit must not skip the roster."""
        from unittest import mock

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "continuity").mkdir()
            (root / "state").mkdir()
            (root / "state" / "fleet-role-registry.json").write_text(json.dumps(self._REG), encoding="utf-8")
            (root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").write_text(
                "# WF-1200\n\nno markers\n", encoding="utf-8"
            )
            with mock.patch.object(drift, "PROJECT_ROOT", root):
                code = drift.main(["--state-dir", str(root / "state" / "absent"), "--ack-file", str(root / "n.json")])
            self.assertEqual(code, 1)

    # --- live repo ---------------------------------------------------------

    def test_repository_workflows_are_currently_clean(self) -> None:
        """The real repo must stay green so the cron gate means something."""
        self.assertEqual(
            drift.main([]), 0, "note/state drift detected in the live workspace"
        )


if __name__ == "__main__":
    unittest.main()
