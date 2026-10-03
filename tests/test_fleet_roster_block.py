#!/usr/bin/env python3
"""Contract tests for the generated WF-1200 role-roster block.

The role -> model roster has one owner, ``state/fleet-role-registry.json``.
The continuity note carries a *generated* mirror of it between BEGIN/END
markers so the note can never silently disagree with the gate.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import fleet_roster_block as roster

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REGISTRY = {
    "schema": "fleet-role-registry.v1",
    "workflow_id": "WF-1200",
    "roles": {
        "governor": {
            "profile": "default",
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "fallback_providers": [{"provider": "openai-codex", "model": "gpt-6-astra"}],
            "status": "parent_only",
            "modes": [],
            "task_classes": [],
        },
        "implementer": {
            "profile": "implementer",
            "provider": "ollama-cloud",
            "model": "deepseek-v4.1-flash",
            "status": "qualification_required",
            "modes": ["read-only", "write"],
            "task_classes": ["implementation"],
            "max_lane_retry_count": 0,
        },
        "senior_engineer": {
            "profile": "seniorengineer",
            "provider": "openai-codex",
            "model": "gpt-6.1-sol",
            "status": "admissible",
            "modes": ["read-only", "write"],
            "task_classes": ["implementation", "analysis"],
            "min_lane_retry_count": 1,
        },
    },
    "lane_exceptions": [
        {"role": "implementer", "lane_id": "lane-a"},
        {"role": "implementer", "lane_id": "lane-b"},
    ],
}

NOTE = "# WF\n\nintro prose\n\n{block}\n\ntrailing prose\n"


def _registry(**mutations: object) -> dict:
    reg = copy.deepcopy(REGISTRY)
    for dotted, value in mutations.items():
        node = reg
        *path, leaf = dotted.split("__")
        for part in path:
            node = node[part]
        node[leaf] = value
    return reg


class RenderTests(unittest.TestCase):
    def test_block_is_delimited_and_names_its_source(self) -> None:
        block = roster.render_block(REGISTRY)
        self.assertTrue(block.startswith(roster.BEGIN_PREFIX))
        self.assertTrue(block.rstrip().endswith(roster.END_MARKER))
        self.assertIn("state/fleet-role-registry.json", block)
        self.assertIn("do not edit", block.lower())

    def test_every_role_binding_profile_and_status_is_rendered(self) -> None:
        block = roster.render_block(REGISTRY)
        for expected in (
            "anthropic/claude-opus-5-5",
            "ollama-cloud/deepseek-v4.1-flash",
            "openai-codex/gpt-6.1-sol",
            "`seniorengineer`",
            "parent_only",
            "qualification_required",
            "admissible",
        ):
            self.assertIn(expected, block)

    def test_governor_fallback_is_rendered_with_its_binding(self) -> None:
        block = roster.render_block(REGISTRY)
        self.assertIn("openai-codex/gpt-6-astra", block)
        self.assertIn("fallback", block.lower())

    def test_repair_cycle_gates_are_rendered(self) -> None:
        block = roster.render_block(REGISTRY)
        self.assertIn(">= 1", block)  # senior_engineer min_lane_retry_count
        self.assertIn("<= 0", block)  # implementer max_lane_retry_count

    def test_lane_exceptions_are_listed_by_id_without_claiming_consumption(self) -> None:
        block = roster.render_block(REGISTRY)
        self.assertIn("lane-a", block)
        self.assertIn("lane-b", block)
        self.assertNotIn("consumed", block.lower())

    def test_render_is_deterministic_and_contains_no_timestamp(self) -> None:
        self.assertEqual(roster.render_block(REGISTRY), roster.render_block(copy.deepcopy(REGISTRY)))
        self.assertNotRegex(roster.render_block(REGISTRY), r"\d{4}-\d{2}-\d{2}")

    def test_changing_any_binding_changes_the_block(self) -> None:
        base = roster.render_block(REGISTRY)
        self.assertNotEqual(base, roster.render_block(_registry(roles__senior_engineer__model="other-model")))
        self.assertNotEqual(base, roster.render_block(_registry(roles__senior_engineer__status="blocked")))
        self.assertNotEqual(
            base,
            roster.render_block(
                _registry(roles__governor__fallback_providers=[{"provider": "x", "model": "y"}])
            ),
        )

    def test_malformed_registry_raises_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block({"roles": "nope"})


class BlockEditingTests(unittest.TestCase):
    def test_apply_replaces_only_the_marked_region(self) -> None:
        old_block = roster.render_block(_registry(roles__senior_engineer__model="old"))
        new_block = roster.render_block(REGISTRY)
        note = NOTE.format(block=old_block)
        updated = roster.apply_block(note, new_block)
        self.assertIn("intro prose", updated)
        self.assertIn("trailing prose", updated)
        self.assertEqual(roster.extract_block(updated), new_block)
        self.assertNotIn("old", roster.extract_block(updated))

    def test_apply_is_idempotent(self) -> None:
        block = roster.render_block(REGISTRY)
        once = roster.apply_block(NOTE.format(block=block), block)
        self.assertEqual(once, roster.apply_block(once, block))

    def test_apply_refuses_a_note_without_markers(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.apply_block("# no markers here\n", roster.render_block(REGISTRY))

    def test_extract_returns_none_without_markers(self) -> None:
        self.assertIsNone(roster.extract_block("# nothing\n"))


class CheckTests(unittest.TestCase):
    def _root(self, registry: dict | str, note: str | None) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "state").mkdir()
        (root / "continuity").mkdir()
        reg_text = registry if isinstance(registry, str) else json.dumps(registry)
        (root / "state" / "fleet-role-registry.json").write_text(reg_text, encoding="utf-8")
        if note is not None:
            (root / "continuity" / "WF-1200-Agent-Fleet-Roles.md").write_text(note, encoding="utf-8")
        return root

    def _check(self, root: Path) -> roster.RosterCheck:
        return roster.check_note(
            root / "state" / "fleet-role-registry.json",
            root / "continuity" / "WF-1200-Agent-Fleet-Roles.md",
            source=roster.DEFAULT_SOURCE_LABEL,
        )

    def test_in_sync_note_passes(self) -> None:
        root = self._root(REGISTRY, NOTE.format(block=roster.render_block(REGISTRY)))
        result = self._check(root)
        self.assertTrue(result.ok, result.reason)

    def test_registry_change_without_note_regeneration_is_drift(self) -> None:
        root = self._root(
            _registry(roles__senior_engineer__model="changed"),
            NOTE.format(block=roster.render_block(REGISTRY)),
        )
        result = self._check(root)
        self.assertFalse(result.ok)
        self.assertIn("out of date", result.reason)

    def test_hand_edited_block_is_drift(self) -> None:
        block = roster.render_block(REGISTRY).replace("gpt-6.1-sol", "muse-spark-1.3")
        root = self._root(REGISTRY, NOTE.format(block=block))
        self.assertFalse(self._check(root).ok)

    def test_note_without_markers_is_drift(self) -> None:
        root = self._root(REGISTRY, "# WF-1200\n\nhand written table\n")
        result = self._check(root)
        self.assertFalse(result.ok)
        self.assertIn("markers", result.reason)

    def test_missing_note_is_reported(self) -> None:
        root = self._root(REGISTRY, None)
        result = self._check(root)
        self.assertFalse(result.ok)
        self.assertIn("note", result.reason)

    def test_corrupt_registry_fails_closed(self) -> None:
        root = self._root("{not json", NOTE.format(block="x"))
        result = self._check(root)
        self.assertFalse(result.ok)
        self.assertIn("registry", result.reason)


class CliTests(unittest.TestCase):
    def test_write_then_check_round_trips_and_leaves_prose_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = root / "registry.json"
            note = root / "note.md"
            reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
            stale = roster.render_block(_registry(roles__senior_engineer__model="stale"))
            note.write_text(NOTE.format(block=stale), encoding="utf-8")
            base = ["--registry", str(reg), "--note", str(note)]
            self.assertEqual(roster.main(base + ["--check"]), 1)
            self.assertEqual(roster.main(base + ["--write"]), 0)
            self.assertEqual(roster.main(base + ["--check"]), 0)
            text = note.read_text(encoding="utf-8")
            self.assertIn("intro prose", text)
            self.assertIn("trailing prose", text)

    def test_write_refuses_to_run_without_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = root / "registry.json"
            note = root / "note.md"
            reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
            note.write_text("# no markers\n", encoding="utf-8")
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 2)
            self.assertEqual(note.read_text(encoding="utf-8"), "# no markers\n")


class MarkerRobustnessTests(unittest.TestCase):
    """Marker parsing must be exact: ambiguity is refused, never guessed at."""

    B = roster.BEGIN_MARKER
    E = roster.END_MARKER

    def test_prefix_lookalike_marker_is_not_a_marker(self) -> None:
        text = "x\n<!-- BEGIN GENERATED: fleet-role-roster-extra -->\nbody\nmore prose\n"
        self.assertIsNone(roster.extract_block(text))

    def test_marker_text_inside_a_prose_line_is_not_a_marker(self) -> None:
        text = f"see {self.B} for details\nmore prose\n"
        self.assertIsNone(roster.extract_block(text))

    def test_end_before_begin_is_malformed(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"{self.E}\nmid\n{self.B}\n")

    def test_duplicate_begin_is_malformed(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"{self.B}\na\n{self.B}\nb\n{self.E}\n")

    def test_duplicate_end_is_malformed(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"{self.B}\na\n{self.E}\nb\n{self.E}\n")

    def test_begin_without_end_is_malformed(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"{self.B}\na\nprose that must not be eaten\n")

    def test_end_without_begin_is_malformed(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"prose\n{self.E}\n")

    def test_apply_refuses_malformed_markers_and_does_not_modify_text(self) -> None:
        text = f"keep me\n{self.B}\nstale\nprose that must survive\n"
        with self.assertRaises(roster.RosterError):
            roster.apply_block(text, roster.render_block(REGISTRY))

    def test_check_reports_malformed_markers_without_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "r.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
            (root / "n.md").write_text(f"{self.B}\nonly begin\n", encoding="utf-8")
            result = roster.check_note(root / "r.json", root / "n.md")
            self.assertFalse(result.ok)
            self.assertIn("malformed", result.reason)

    def test_a_block_whose_body_contains_end_marker_text_is_refused_not_truncated(self) -> None:
        # END marker text appearing as its own line twice must never be read as one block.
        with self.assertRaises(roster.RosterError):
            roster.extract_block(f"{self.B}\n{self.E}\ninjected prose\n{self.E}\n")


class RenderRobustnessTests(unittest.TestCase):
    def test_non_mapping_lane_exception_entry_is_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(_registry(lane_exceptions=["not-a-dict"]))

    def test_exception_entry_missing_lane_id_is_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(_registry(lane_exceptions=[{"role": "implementer"}]))

    def test_non_mapping_governor_is_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(_registry(roles__governor="oops"))

    def test_non_list_exceptions_is_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(_registry(lane_exceptions="oops"))

    def test_non_mapping_registry_is_a_clear_error(self) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_provenance_names_the_actual_source_not_a_hardcoded_path(self) -> None:
        block = roster.render_block(REGISTRY, source="other/place.json")
        self.assertIn("other/place.json", block)
        self.assertNotIn("state/fleet-role-registry.json", block)

    def test_default_source_label_is_the_live_registry_path(self) -> None:
        self.assertIn("state/fleet-role-registry.json", roster.render_block(REGISTRY))


class WriteSafetyTests(unittest.TestCase):
    """--write may only ever change the marked region, and only atomically."""

    def _paths(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp)
        reg = root / "registry.json"
        note = root / "note.md"
        reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
        return reg, note

    def _stale_note(self, eol: str = "\n") -> bytes:
        stale = roster.render_block(_registry(roles__senior_engineer__model="stale"))
        text = NOTE.format(block=stale)
        return text.replace("\n", eol).encode("utf-8")

    def test_pure_crlf_note_stays_pure_crlf_and_prose_is_preserved(self) -> None:
        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            note.write_bytes(self._stale_note("\r\n"))
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 0)
            data = note.read_bytes()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "no lone LF introduced")
            self.assertIn(b"intro prose\r\n", data)
            self.assertIn(b"trailing prose\r\n", data)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--check"]), 0)

    def test_mixed_line_endings_are_refused_and_note_is_untouched(self) -> None:
        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            original = self._stale_note("\n").replace(b"intro prose\n", b"intro prose\r\n")
            note.write_bytes(original)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 2)
            self.assertEqual(note.read_bytes(), original)

    def test_invalid_utf8_note_is_refused_on_write_and_reported_on_check(self) -> None:
        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            original = b"\xff\xfe not utf8 \x80\n"
            note.write_bytes(original)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 2)
            self.assertEqual(note.read_bytes(), original)
            result = roster.check_note(reg, note)
            self.assertFalse(result.ok)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--check"]), 1)

    def test_malformed_markers_refuse_write_and_leave_note_byte_identical(self) -> None:
        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            original = f"intro\n{roster.BEGIN_MARKER}\nstale\nprose that must not be eaten\n".encode()
            note.write_bytes(original)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 2)
            self.assertEqual(note.read_bytes(), original)

    def test_missing_registry_refuses_write(self) -> None:
        with TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            original = self._stale_note()
            note.write_bytes(original)
            code = roster.main(["--registry", str(Path(tmp) / "absent.json"), "--note", str(note), "--write"])
            self.assertEqual(code, 2)
            self.assertEqual(note.read_bytes(), original)

    def test_write_is_atomic_when_the_final_replace_fails(self) -> None:
        import os
        from unittest import mock

        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            original = self._stale_note()
            note.write_bytes(original)
            with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
                code = roster.main(["--registry", str(reg), "--note", str(note), "--write"])
            self.assertEqual(code, 2)
            self.assertEqual(note.read_bytes(), original, "original note must survive a failed write")
            leftovers = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(leftovers, ["note.md", "registry.json"], "no temp file may be left behind")

    def test_custom_registry_provenance_is_not_falsified(self) -> None:
        with TemporaryDirectory() as tmp:
            reg, note = self._paths(tmp)
            note.write_bytes(self._stale_note())
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--write"]), 0)
            text = note.read_text(encoding="utf-8")
            self.assertIn("registry.json", text)
            self.assertNotIn("state/fleet-role-registry.json", text)
            self.assertEqual(roster.main(["--registry", str(reg), "--note", str(note), "--check"]), 0)


class SchemaStrictnessTests(unittest.TestCase):
    """QA round 2: wrong types must be refused, never coerced to 'none' or rendered raw."""

    def _refused(self, **mutation: object) -> None:
        with self.assertRaises(roster.RosterError):
            roster.render_block(_registry(**mutation))

    def test_falsy_wrong_typed_containers_are_refused_not_coerced(self) -> None:
        self._refused(lane_exceptions={})
        self._refused(roles__governor__modes={})
        self._refused(roles__governor__fallback_providers={})
        self._refused(roles__governor__fallback_providers="")
        self._refused(roles__governor__modes="read-only")

    def test_scalar_fields_must_be_non_empty_strings(self) -> None:
        for field in ("profile", "provider", "model", "status"):
            self._refused(**{f"roles__senior_engineer__{field}": 7})
            self._refused(**{f"roles__senior_engineer__{field}": ""})
            self._refused(**{f"roles__senior_engineer__{field}": None})

    def test_retry_gates_must_be_integers_not_bools_or_strings(self) -> None:
        self._refused(roles__senior_engineer__min_lane_retry_count="1")
        self._refused(roles__senior_engineer__min_lane_retry_count=True)
        self._refused(roles__implementer__max_lane_retry_count=1.5)

    def test_modes_must_be_a_list_of_strings(self) -> None:
        self._refused(roles__implementer__modes=["write", 3])

    def test_fallback_entries_must_have_string_provider_and_model(self) -> None:
        self._refused(roles__governor__fallback_providers=[{"provider": "x"}])
        self._refused(roles__governor__fallback_providers=["x/y"])

    def test_markdown_breaking_values_are_refused_so_a_table_cannot_be_forged(self) -> None:
        for bad in ("a|b", "a`b", "a\nb", "a\rb", "x --> y"):
            self._refused(roles__senior_engineer__model=bad)
            self._refused(roles__senior_engineer__status=bad)
            self._refused(lane_exceptions=[{"role": "implementer", "lane_id": bad}])

    def test_role_names_are_validated_like_any_other_cell(self) -> None:
        reg = _registry()
        reg["roles"]["bad|name"] = dict(reg["roles"]["senior_engineer"])
        with self.assertRaises(roster.RosterError):
            roster.render_block(reg)

    def test_absent_optional_fields_still_render(self) -> None:
        reg = _registry()
        del reg["roles"]["implementer"]["modes"]
        del reg["lane_exceptions"]
        block = roster.render_block(reg)
        self.assertIn("Recorded one-time lane exceptions: none.", block)


class CustomSourceProvenanceTests(unittest.TestCase):
    def test_case_alias_of_the_same_file_gets_the_same_label_and_no_false_drift(self) -> None:
        """QA round 3: registry.json vs REGISTRY.JSON on a case-insensitive filesystem."""
        with TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
            alias = Path(tmp) / "REGISTRY.JSON"
            if not alias.exists():
                self.skipTest("filesystem is case-sensitive; no case alias exists")
            self.assertEqual(roster._source_label(registry), roster._source_label(alias))
            note = Path(tmp) / "note.md"
            note.write_text(NOTE.format(block=roster.begin_marker("x") + "\n" + roster.END_MARKER + "\n"), encoding="utf-8")
            self.assertEqual(roster.main(["--registry", str(registry), "--note", str(note), "--write"]), 0)
            self.assertEqual(roster.main(["--registry", str(alias), "--note", str(note), "--check"]), 0)

    def test_label_is_derived_from_the_resolved_path_so_a_relative_spelling_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
            dotted = Path(tmp) / "." / "registry.json"
            self.assertEqual(roster._source_label(registry), roster._source_label(dotted))

    def test_same_basename_in_different_directories_renders_different_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            blocks = []
            for name in ("one", "two"):
                directory = Path(tmp) / name
                directory.mkdir()
                registry = directory / "registry.json"
                note = directory / "note.md"
                registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
                note.write_text(NOTE.format(block=roster.begin_marker("x") + "\n" + roster.END_MARKER + "\n"), encoding="utf-8")
                self.assertEqual(roster.main(["--registry", str(registry), "--note", str(note), "--write"]), 0)
                blocks.append(note.read_text(encoding="utf-8"))
            self.assertNotEqual(blocks[0], blocks[1])

    def test_a_note_generated_from_another_registry_of_the_same_name_fails_check(self) -> None:
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "one"
            second = Path(tmp) / "two"
            for directory in (first, second):
                directory.mkdir()
                (directory / "registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
            note = Path(tmp) / "note.md"
            note.write_text(NOTE.format(block=roster.begin_marker("x") + "\n" + roster.END_MARKER + "\n"), encoding="utf-8")
            self.assertEqual(roster.main(["--registry", str(first / "registry.json"), "--note", str(note), "--write"]), 0)
            self.assertEqual(roster.main(["--registry", str(first / "registry.json"), "--note", str(note), "--check"]), 0)
            self.assertEqual(roster.main(["--registry", str(second / "registry.json"), "--note", str(note), "--check"]), 1)

    def test_a_registry_path_containing_parentheses_still_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "Tools (x86)"
            directory.mkdir()
            registry = directory / "registry.json"
            note = Path(tmp) / "note.md"
            registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
            note.write_text(NOTE.format(block=roster.begin_marker("x") + "\n" + roster.END_MARKER + "\n"), encoding="utf-8")
            self.assertEqual(roster.main(["--registry", str(registry), "--note", str(note), "--write"]), 0)
            self.assertEqual(roster.main(["--registry", str(registry), "--note", str(note), "--check"]), 0)


class WindowsFailureTests(unittest.TestCase):
    """QA round 2: failure paths on a read-only note and in the write stream."""

    def _setup(self, tmp: str) -> tuple[Path, Path, bytes]:
        root = Path(tmp)
        registry = root / "registry.json"
        note = root / "note.md"
        registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
        original = NOTE.format(block=roster.render_block(_registry(roles__senior_engineer__model="stale"))).encode()
        note.write_bytes(original)
        return registry, note, original

    def _no_leftovers(self, tmp: str) -> None:
        self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["note.md", "registry.json"])

    def test_read_only_note_with_failed_replace_leaves_no_temp_file_and_keeps_the_note(self) -> None:
        import os
        import stat
        from unittest import mock

        with TemporaryDirectory() as tmp:
            registry, note, original = self._setup(tmp)
            os.chmod(note, stat.S_IREAD)
            try:
                with mock.patch.object(os, "replace", side_effect=PermissionError("access denied")):
                    code = roster.main(["--registry", str(registry), "--note", str(note), "--write"])
            finally:
                os.chmod(note, stat.S_IWRITE | stat.S_IREAD)
            self.assertEqual(code, 2)
            self.assertEqual(note.read_bytes(), original)
            self._no_leftovers(tmp)

    def test_fsync_failure_leaves_no_temp_file_and_keeps_the_note(self) -> None:
        import os
        from unittest import mock

        with TemporaryDirectory() as tmp:
            registry, note, original = self._setup(tmp)
            with mock.patch.object(os, "fsync", side_effect=OSError("io error")):
                code = roster.main(["--registry", str(registry), "--note", str(note), "--write"])
            self.assertEqual(code, 2)
            self.assertEqual(note.read_bytes(), original)
            self._no_leftovers(tmp)

    def test_stray_carriage_return_is_refused_and_note_is_untouched(self) -> None:
        with TemporaryDirectory() as tmp:
            registry, note, original = self._setup(tmp)
            original = original.replace(b"intro prose", b"intro\rprose")
            note.write_bytes(original)
            code = roster.main(["--registry", str(registry), "--note", str(note), "--write"])
            self.assertEqual(code, 2)
            self.assertEqual(note.read_bytes(), original)


class PerRoleFallbackRenderTests(unittest.TestCase):
    """Operator fleet-fallback change: every role's fallback chain is rendered, not just the governor's."""

    def _with_fallbacks(self) -> dict:
        reg = _registry()
        reg["roles"]["senior_engineer"]["fallback_providers"] = [
            {"provider": "anthropic", "model": "claude-sonnet-5-5"}
        ]
        reg["roles"]["implementer"]["fallback_providers"] = [
            {"provider": "ollama-cloud", "model": "glm-5.3-flash"}
        ]
        return reg

    def test_each_roles_fallback_appears_on_that_roles_row(self) -> None:
        block = roster.render_block(self._with_fallbacks())
        rows = {line.split("|")[1].strip(): line for line in block.splitlines() if line.startswith("| ")}
        self.assertIn("anthropic/claude-sonnet-5-5", rows["senior_engineer"])
        self.assertIn("ollama-cloud/glm-5.3-flash", rows["implementer"])
        self.assertNotIn("glm-5.3-flash", rows["senior_engineer"])

    def test_role_without_fallback_renders_a_dash_not_a_neighbours_chain(self) -> None:
        block = roster.render_block(self._with_fallbacks())
        rows = {line.split("|")[1].strip(): line for line in block.splitlines() if line.startswith("| ")}
        self.assertTrue(rows["governor"].count("gpt-6-astra") == 1)
        reg = _registry()
        del reg["roles"]["governor"]["fallback_providers"]
        plain = roster.render_block(reg)
        rows = {line.split("|")[1].strip(): line for line in plain.splitlines() if line.startswith("| ")}
        self.assertTrue(rows["governor"].rstrip().rstrip("|").rstrip().endswith("-"))

    def test_changing_a_non_governor_fallback_changes_the_block(self) -> None:
        base = roster.render_block(self._with_fallbacks())
        changed = self._with_fallbacks()
        changed["roles"]["implementer"]["fallback_providers"][0]["model"] = "other"
        self.assertNotEqual(base, roster.render_block(changed))

    def test_fallback_order_is_preserved(self) -> None:
        reg = self._with_fallbacks()
        reg["roles"]["qa"] = {
            "profile": "qa", "provider": "anthropic", "model": "claude-sonnet-5-5", "status": "admissible",
            "fallback_providers": [{"provider": "a", "model": "first"}, {"provider": "b", "model": "second"}],
        }
        block = roster.render_block(reg)
        self.assertLess(block.index("a/first"), block.index("b/second"))

    def test_governor_summary_line_is_still_present(self) -> None:
        self.assertIn("Governor parent-only fallback chain:", roster.render_block(self._with_fallbacks()))


class LiveRepositoryTests(unittest.TestCase):
    def test_wf1200_note_roster_matches_the_live_registry(self) -> None:
        result = roster.check_note(roster.DEFAULT_REGISTRY, roster.DEFAULT_NOTE)
        self.assertTrue(result.ok, result.reason)


if __name__ == "__main__":
    unittest.main()
