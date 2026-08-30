import json
import os
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile

from scripts import wiki_bootstrap


class WikiBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp())
        self.project_root = self.tempdir / "project"
        self.project_root.mkdir(parents=True)

        # Fixtures embed a placeholder timestamp token that _write() rewrites to
        # the current UTC time. This keeps freshness-window checks deterministic:
        # daily-freshness pages (e.g. change-log.md) must not age past wall-clock
        # between the time the fixture was authored and the time the test runs.
        self.generated_time = wiki_bootstrap._utc_now()

        (self.project_root / "scripts").mkdir(parents=True)

        # Minimal workspace scaffolding used by manifest checks.
        (self.project_root / "AGENTS.md").write_text("agent identity", encoding="utf-8")
        (self.project_root / "GOVERNANCE.md").write_text("governance", encoding="utf-8")
        (self.project_root / "README.md").write_text("readme", encoding="utf-8")
        (self.project_root / "references").mkdir()
        (self.project_root / "references/operating-procedures.md").write_text("ops", encoding="utf-8")
        (self.project_root / "references/memory-routing.md").write_text("memory routing", encoding="utf-8")
        (self.project_root / "prompts").mkdir(parents=True)
        (self.project_root / "prompts/hermes-trustworthy-work-operating-model-prompt.md").write_text("prompt", encoding="utf-8")
        (self.project_root / "tests").mkdir(parents=True)
        (self.project_root / "tests/test_run_checks.py").write_text("check", encoding="utf-8")
        (self.project_root / "scripts/run_checks.py").write_text("checks", encoding="utf-8")
        (self.project_root / "scripts/concurrent_lane_manager.py").write_text("lane", encoding="utf-8")
        (self.project_root / "references/concurrent-lane-control-plane.md").write_text("lane control", encoding="utf-8")
        (self.project_root / "prompts/workspace-workflow-readiness-audit-2026-08-15.md").write_text(
            "readiness audit", encoding="utf-8"
        )
        copyfile(
            Path("scripts/wiki_bootstrap.py"),
            self.project_root / "scripts/wiki_bootstrap.py",
        )

        self.wiki_root = self.project_root / "wiki"
        self.wiki_root.mkdir()

    def tearDown(self):
        for item in sorted(self.tempdir.rglob("*"), reverse=True):
            if item.is_file():
                item.unlink()
            else:
                os.rmdir(item)

    def _write(self, relative_path: str, content: str) -> Path:
        path = self.project_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Substitute the fixed generated_time token so fixtures stay fresh
        # relative to the wall clock at run time. Only the exact ISO token is
        # replaced; date substrings inside filenames are left untouched.
        content = content.replace("2026-08-15T00:00:00Z", self.generated_time)
        path.write_text(content, encoding="utf-8")
        return path

    def _bootstrap_base_pages(self):
        self._write(
            "wiki/index.md",
            """# Index\n\n- page_type: navigation_map\n- owner: AGENTS.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - AGENTS.md\n  - GOVERNANCE.md\n  - README.md\n- source_hashes:\n  - AGENTS.md: pending\n  - GOVERNANCE.md: pending\n  - README.md: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: AGENTS.md -> references/operating-procedures.md\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - references/operating-procedures.md\n\n""",
        )
        self._write(
            "wiki/source-map/ownership.md",
            """# Source map\n\n- page_type: source_map\n- owner: references/operating-procedures.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - AGENTS.md\n  - GOVERNANCE.md\n  - references/operating-procedures.md\n- source_hashes:\n  - AGENTS.md: pending\n  - GOVERNANCE.md: pending\n  - references/operating-procedures.md: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: references/operating-procedures.md\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - AGENTS.md\n\n""",
        )
        self._write(
            "wiki/syntheses/system-map.md",
            """# Synthesis\n\n- page_type: synthesis\n- owner: references/operating-procedures.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - references/operating-procedures.md\n  - references/memory-routing.md\n  - prompts/hermes-trustworthy-work-operating-model-prompt.md\n- source_hashes:\n  - references/operating-procedures.md: pending\n  - references/memory-routing.md: pending\n  - prompts/hermes-trustworthy-work-operating-model-prompt.md: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: references/operating-procedures.md\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - prompts/hermes-trustworthy-work-operating-model-prompt.md\n\n""",
        )
        self._write(
            "wiki/decisions/initial.md",
            """# Decisions\n\n- page_type: decision_map\n- owner: references/operating-procedures.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - references/operating-procedures.md\n  - tests/test_run_checks.py\n  - scripts/run_checks.py\n- source_hashes:\n  - references/operating-procedures.md: pending\n  - tests/test_run_checks.py: pending\n  - scripts/run_checks.py: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: scripts/run_checks.py\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - scripts/run_checks.py\n\n""",
        )
        self._write(
            "wiki/gaps/open-gaps.md",
            """# Gaps\n\n- page_type: gap_register\n- owner: prompts/workspace-workflow-readiness-audit-2026-08-15.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - prompts/workspace-workflow-readiness-audit-2026-08-15.md\n  - references/concurrent-lane-control-plane.md\n  - scripts/concurrent_lane_manager.py\n- source_hashes:\n  - prompts/workspace-workflow-readiness-audit-2026-08-15.md: pending\n  - references/concurrent-lane-control-plane.md: pending\n  - scripts/concurrent_lane_manager.py: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: references/concurrent-lane-control-plane.md\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - references/concurrent-lane-control-plane.md\n\n""",
        )
        self._write(
            "wiki/changes/change-log.md",
            """# Changes\n\n- page_type: change_log\n- owner: scripts/wiki_bootstrap.py\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - scripts/wiki_bootstrap.py\n  - AGENTS.md\n  - prompts/hermes-trustworthy-work-operating-model-prompt.md\n- source_hashes:\n  - scripts/wiki_bootstrap.py: pending\n  - AGENTS.md: pending\n  - prompts/hermes-trustworthy-work-operating-model-prompt.md: pending\n- freshness_rule: daily\n- authority_boundary: review_only\n- promotion_path: scripts/wiki_bootstrap.py\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - scripts/wiki_bootstrap.py\n\n""",
        )

    def test_publish_and_validate_pass(self):
        self._bootstrap_base_pages()

        publish_report = wiki_bootstrap.publish_wiki(project_root=self.project_root)
        self.assertEqual("fresh", publish_report["status"])

        validate = wiki_bootstrap.validate_wiki(project_root=self.project_root)
        self.assertEqual("fresh", validate["status"])
        self.assertTrue(validate["lkg_present"])

        manifest_payload = json.loads(
            (self.project_root / "wiki/bootstrap-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(wiki_bootstrap.MANIFEST_SCHEMA, manifest_payload["schema"])

    def test_missing_required_page_fails(self):
        self._bootstrap_base_pages()
        wiki_bootstrap.publish_wiki(project_root=self.project_root)

        (self.project_root / "wiki/gaps/open-gaps.md").unlink()
        validate = wiki_bootstrap.validate_wiki(project_root=self.project_root)
        self.assertEqual("stale", validate["status"])

        issue_types = {issue["type"] for issue in validate["issues"]}
        self.assertIn("missing_required_page", issue_types)

    def test_forbidden_authority_language_fails(self):
        self._bootstrap_base_pages()
        self._write(
            "wiki/index.md",
            """# Index\n\n- page_type: navigation_map\n- owner: AGENTS.md\n- status: current\n- generated_time: 2026-08-15T00:00:00Z\n- source_artifacts:\n  - AGENTS.md\n  - GOVERNANCE.md\n  - README.md\n- source_hashes:\n  - AGENTS.md: pending\n  - GOVERNANCE.md: pending\n  - README.md: pending\n- freshness_rule: weekly\n- authority_boundary: review_only\n- promotion_path: AGENTS.md -> references/operating-procedures.md\n- warnings: none\n- next_action: keep in sync\n- source_map:\n  - references/operating-procedures.md\n\nThis page claims unlimited authority over all changes.\n\n""",
        )

        with self.assertRaises(RuntimeError):
            wiki_bootstrap.publish_wiki(project_root=self.project_root)

    def test_lkg_backup_created(self):
        self._bootstrap_base_pages()
        publish_report = wiki_bootstrap.publish_wiki(project_root=self.project_root)
        self.assertEqual("fresh", publish_report["status"])

        lkg_root = self.project_root / "wiki/.lkg"
        self.assertTrue((lkg_root / "bootstrap-manifest.json").exists())

        backups = list(lkg_root.glob("bootstrap-manifest.*.json"))

        index_path = self.project_root / "wiki/index.md"
        current_index = index_path.read_text(encoding="utf-8")
        self._write("wiki/index.md", current_index + "\n\nAppended regeneration verification marker.\n")
        publish_report = wiki_bootstrap.publish_wiki(project_root=self.project_root)
        self.assertEqual("fresh", publish_report["status"])
        backups_after = sorted(lkg_root.glob("bootstrap-manifest.*.json"))
        self.assertGreaterEqual(len(backups_after), len(backups))


    def _age_page(self, relative_path: str, timestamp: str) -> None:
        """Rewrite a page's generated_time to simulate the passage of time."""
        path = self.project_root / relative_path
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")
        for index, line in enumerate(lines):
            if line.strip().startswith("- generated_time:"):
                lines[index] = f"- generated_time: {timestamp}"
                break
        path.write_text("\n".join(lines), encoding="utf-8")

    def _publish_with_real_hashes(self) -> None:
        """Publish the fixture wiki.

        `_build_candidate_manifest` always computes real digests, so a normal
        publish is sufficient; `pending` markers live only in page markdown and
        never reach the manifest.
        """
        self._bootstrap_base_pages()
        wiki_bootstrap.publish_wiki(project_root=self.project_root)

    def test_reattest_clears_time_only_staleness(self):
        """A page stale purely from age is re-attested and republishes cleanly."""
        self._publish_with_real_hashes()

        self._age_page("wiki/index.md", "2026-01-01T00:00:00Z")
        stale = wiki_bootstrap.validate_wiki(project_root=self.project_root)
        self.assertEqual("stale", stale["status"])
        self.assertIn(
            "stale_freshness", {issue["type"] for issue in stale["issues"]}
        )

        result = wiki_bootstrap.reattest_wiki(project_root=self.project_root)
        self.assertEqual("reattested", result["status"])
        self.assertEqual(
            ["wiki/index.md"], [entry["page"] for entry in result["reattested"]]
        )
        self.assertEqual([], result["refused"])

        # The deadlock is closed: publish now succeeds where it previously could not.
        publish_report = wiki_bootstrap.publish_wiki(project_root=self.project_root)
        self.assertEqual("fresh", publish_report["status"])

    def test_reattest_refuses_when_source_actually_changed(self):
        """Real source drift must NOT be papered over by a timestamp bump."""
        self._publish_with_real_hashes()

        self._age_page("wiki/index.md", "2026-01-01T00:00:00Z")
        # Mutate a declared source artifact so the content genuinely diverges.
        (self.project_root / "AGENTS.md").write_text(
            "agent identity CHANGED", encoding="utf-8"
        )

        result = wiki_bootstrap.reattest_wiki(project_root=self.project_root)
        self.assertEqual("refused", result["status"])
        self.assertEqual([], result["reattested"])

        refused_pages = {entry["page"] for entry in result["refused"]}
        self.assertIn("wiki/index.md", refused_pages)

        # The stale timestamp must survive untouched - no silent attestation.
        content = (self.project_root / "wiki/index.md").read_text(encoding="utf-8")
        self.assertIn("- generated_time: 2026-01-01T00:00:00Z", content)

    def test_reattest_refuses_pending_hashes(self):
        """A `pending` hash never proved anything, so it cannot be re-attested.

        A normal publish always writes real digests, so this defends the
        hand-edited / legacy-manifest case by injecting `pending` directly.
        """
        self._publish_with_real_hashes()

        manifest_path = self.project_root / "wiki/bootstrap-manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["pages"]["wiki/index.md"]["source_hashes"]["AGENTS.md"] = "pending"
        manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

        self._age_page("wiki/index.md", "2026-01-01T00:00:00Z")
        result = wiki_bootstrap.reattest_wiki(project_root=self.project_root)

        self.assertEqual("refused", result["status"])
        reasons = {
            detail["reason"]
            for entry in result["refused"]
            if entry.get("reason") == "source_drift"
            for detail in entry.get("detail", [])
        }
        self.assertIn("unproven_pending_hash", reasons)

    def test_reattest_is_noop_when_wiki_is_fresh(self):
        """Nothing stale means nothing to re-attest; timestamps stay put."""
        self._publish_with_real_hashes()
        before = (self.project_root / "wiki/index.md").read_text(encoding="utf-8")

        result = wiki_bootstrap.reattest_wiki(project_root=self.project_root)

        self.assertEqual("noop", result["status"])
        self.assertEqual([], result["reattested"])
        self.assertEqual([], result["refused"])
        self.assertEqual(
            before, (self.project_root / "wiki/index.md").read_text(encoding="utf-8")
        )

    def test_reattest_preserves_crlf_line_endings(self):
        """Re-attestation must not rewrite a CRLF page into LF."""
        self._publish_with_real_hashes()

        path = self.project_root / "wiki/index.md"
        path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
        self._age_page_crlf(path, "2026-01-01T00:00:00Z")

        result = wiki_bootstrap.reattest_wiki(project_root=self.project_root)
        self.assertEqual("reattested", result["status"])

        raw = path.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def _age_page_crlf(self, path: Path, timestamp: str) -> None:
        raw = path.read_bytes().decode("utf-8")
        lines = raw.split("\r\n")
        for index, line in enumerate(lines):
            if line.strip().startswith("- generated_time:"):
                lines[index] = f"- generated_time: {timestamp}"
                break
        path.write_bytes("\r\n".join(lines).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
