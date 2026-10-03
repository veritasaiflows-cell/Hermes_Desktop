"""Hardening pins for the helper-agent admission gate.

Each test here reproduces a live-reproduced defect (exit-1 crash, blocklist
bypass, or SQLite URI misbinding) found during independent review; the gate
must turn every one into a clean exit-2 JSON rejection or correct admit.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import concurrent_lane_manager, helper_agent_router  # noqa: E402


def _read_only_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema": helper_agent_router.REQUEST_SCHEMA,
        "task_id": "hardening-001",
        "task_class": "research",
        "phase": "pre-implementation",
        "mode": "read-only",
        "objective": "Read-only reconnaissance.",
        "scope": "Read-only summary; no writes.",
        "allowed_toolsets": ["read_files"],
        "allowed_writes": [],
        "max_duration_minutes": 15,
        "owner": "agent-main",
    }
    request.update(overrides)
    return request


# The live workspace enforces the WF-1200 fleet role registry, so requests
# admitted against PROJECT_ROOT must declare a registry-bound role/model.
_LIVE_ROLE = {"role": "researcher", "model": "openai-codex/gpt-6-luna"}


def _write_mode_request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "write",
        "task_class": "documentation",
        "phase": "implementation",
        "objective": "Draft the module notes file.",
        "scope": "Write one notes file under derived/.",
        "allowed_toolsets": ["read_files", "write_file"],
        "allowed_writes": ["derived/notes.md"],
        "lane_id": "WF-HARD::lane",
    }
    base.update(overrides)
    return _read_only_request(**base)


class ExitContractTests(unittest.TestCase):
    """The gate must never crash with exit 1 from CLI-reachable input."""

    def _main_with_request_bytes(self, payload: bytes, project_root: Path) -> int:
        with TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_bytes(payload)
            audit_log = Path(directory) / "audit.jsonl"
            return helper_agent_router.main(
                [
                    "admit",
                    "--request",
                    str(request_path),
                    "--project-root",
                    str(project_root),
                    "--audit-log",
                    str(audit_log),
                ]
            )

    def test_non_utf8_request_file_is_rejected_not_crash(self) -> None:
        payload = json.dumps(_read_only_request()).encode("utf-8") + b"\xff\xfe"
        exit_code = self._main_with_request_bytes(payload, PROJECT_ROOT)

        self.assertEqual(exit_code, 2)

    def test_deeply_nested_json_request_is_rejected_not_crash(self) -> None:
        payload = ("[" * 60000 + "]" * 60000).encode("utf-8")
        exit_code = self._main_with_request_bytes(payload, PROJECT_ROOT)

        self.assertEqual(exit_code, 2)

    def test_null_byte_surface_is_rejected_not_crash(self) -> None:
        request = _write_mode_request(allowed_writes=["bad\u0000name.md"])
        exit_code = self._main_with_request_bytes(
            json.dumps(request).encode("utf-8"), PROJECT_ROOT
        )

        self.assertEqual(exit_code, 2)

    def test_wrong_schema_register_is_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            connection = sqlite3.connect(
                str(root / "state" / "concurrent-lane-register.sqlite")
            )
            connection.execute("CREATE TABLE lanes (lane_id TEXT PRIMARY KEY, foo TEXT)")
            connection.execute("INSERT INTO lanes VALUES ('WF-HARD::lane', 'x')")
            connection.commit()
            connection.close()

            exit_code = self._main_with_request_bytes(
                json.dumps(_write_mode_request()).encode("utf-8"), root
            )

        self.assertEqual(exit_code, 2)


class ToolsetFamilyTests(unittest.TestCase):
    """Name variants must not bypass the forbidden/write-capable boundaries."""

    def _reasons_for(self, toolsets: list[str], mode: str = "read-only") -> list[str]:
        request = (
            _read_only_request(allowed_toolsets=toolsets)
            if mode == "read-only"
            else _write_mode_request(allowed_toolsets=toolsets)
        )
        result = helper_agent_router.admit_request(request)
        return result["reasons"]

    def test_forbidden_toolset_hyphen_variants_are_rejected(self) -> None:
        for variant in ("computer-use", "desktop-ui", "cron-job"):
            with self.subTest(variant=variant):
                reasons = self._reasons_for(["read_files", variant])
                self.assertTrue(
                    any("forbidden toolset" in r for r in reasons),
                    f"{variant!r} admitted; reasons={reasons}",
                )

    def test_forbidden_toolset_real_environment_names_are_rejected(self) -> None:
        for variant in (
            "mcp__graphify__get_node",
            "delegate_task",
            "memory_search",
            "skills_manage",
            "project_create",
            "computer_use",
        ):
            with self.subTest(variant=variant):
                reasons = self._reasons_for(["read_files", variant])
                self.assertTrue(
                    any("forbidden toolset" in r for r in reasons),
                    f"{variant!r} admitted; reasons={reasons}",
                )

    def test_forbidden_variant_is_rejected_in_write_mode_too(self) -> None:
        reasons = self._reasons_for(
            ["read_files", "write_file", "computer-use"], mode="write"
        )

        self.assertTrue(
            any("forbidden toolset" in r for r in reasons),
            f"variant admitted in write mode; reasons={reasons}",
        )

    def test_read_only_mode_admits_only_allowlisted_toolsets(self) -> None:
        reasons = self._reasons_for(["read_files", "browser_exec"])

        self.assertTrue(
            any("read-only" in r for r in reasons),
            f"unknown toolset admitted in read-only mode; reasons={reasons}",
        )

    def test_read_only_allowlisted_toolsets_still_admit(self) -> None:
        result = helper_agent_router.admit_request(
            _read_only_request(allowed_toolsets=["read_files", "web_search"], **_LIVE_ROLE)
        )

        self.assertEqual(result["status"], "admitted", result["reasons"])


class ProjectRootUriTests(unittest.TestCase):
    """--project-root containing SQLite URI metacharacters must not misbind."""

    def test_project_root_with_uri_metacharacters_admits_valid_lane(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "pro#ject%dir"
            root.mkdir()
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-hard-001",
                workflow_id="WF-HARD",
                workstream="lane",
                owner="agent-main",
                lane_id="WF-HARD::lane",
                lane_mode="write",
                allowed_writes=["derived/notes.md"],
            )
            manager.lease_lane("WF-HARD::lane", owner="agent-main", duration_minutes=30)

            result = helper_agent_router.admit_request(
                _write_mode_request(), project_root=root
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])


class ReviewerRound2Pins(unittest.TestCase):
    """Pins from the independent reviewer's second probe round (post-fix gaps)."""

    def test_skill_manage_is_rejected_in_write_mode(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_toolsets=["read_files", "write_file", "skill_manage"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("skill_manage" in r for r in result["reasons"]), result["reasons"]
        )

    def test_setup_mcp_is_rejected_in_write_mode(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_toolsets=["read_files", "write_file", "setup_mcp"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("setup_mcp" in r for r in result["reasons"]), result["reasons"]
        )

    def test_terminal_toolset_is_rejected_in_write_mode(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_toolsets=["read_files", "write_file", "terminal"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("terminal" in r for r in result["reasons"]), result["reasons"]
        )

    def test_write_mode_rejects_non_lane_bounded_toolsets(self) -> None:
        """execute_code can shell out, so it is never admissible even in write mode."""
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_toolsets=["read_files", "write_file", "execute_code"])
        )
        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(any("execute_code" in r for r in result["reasons"]), result["reasons"])

    def test_write_mode_allows_lane_bounded_file_writers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-writers",
                workflow_id="WF-HARD",
                workstream="lane",
                owner="agent-main",
                lane_id="WF-HARD::lane",
                lane_mode="write",
                allowed_writes=["derived/notes.md"],
            )
            manager.lease_lane("WF-HARD::lane", owner="agent-main", duration_minutes=30)

            result = helper_agent_router.admit_request(
                _write_mode_request(
                    allowed_toolsets=["read_files", "search_files", "write_file", "patch"]
                ),
                project_root=root,
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_backslash_unc_surface_is_rejected_as_absolute(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=["\\\\server\\share\\x.md"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("not absolute" in r or "absolute" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_leading_space_drive_relative_surface_is_rejected_as_absolute(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=[" C:foo.txt"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("absolute" in r for r in result["reasons"]), result["reasons"]
        )

    def test_trailing_slash_variant_does_not_defeat_uniqueness(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=["derived/notes.md", "derived/notes.md/"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(any("unique" in r for r in result["reasons"]), result["reasons"])

    def test_child_of_file_surface_is_not_covered(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-file-surface",
                workflow_id="WF-HARD",
                workstream="lane",
                owner="agent-main",
                lane_id="WF-HARD::lane",
                lane_mode="write",
                allowed_writes=["derived/file.md"],
            )
            manager.lease_lane("WF-HARD::lane", owner="agent-main", duration_minutes=30)

            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived/file.md/child.txt"]),
                project_root=root,
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("not covered" in r for r in result["reasons"]), result["reasons"]
        )

    def test_file_surface_still_covers_itself(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-file-surface",
                workflow_id="WF-HARD",
                workstream="lane",
                owner="agent-main",
                lane_id="WF-HARD::lane",
                lane_mode="write",
                allowed_writes=["derived/file.md"],
            )
            manager.lease_lane("WF-HARD::lane", owner="agent-main", duration_minutes=30)

            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived/file.md"]),
                project_root=root,
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_prefix_sibling_boundary_rejects_both_directions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-prefix",
                workflow_id="WF-HARD",
                workstream="lane",
                owner="agent-main",
                lane_id="WF-HARD::lane",
                lane_mode="write",
                allowed_writes=["derived/a/b/"],
            )
            manager.lease_lane("WF-HARD::lane", owner="agent-main", duration_minutes=30)

            longer = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived/a/bc/file.md"]),
                project_root=root,
            )
            shorter = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived/a"]),
                project_root=root,
            )

        self.assertEqual(longer["status"], "rejected", longer["reasons"])
        self.assertEqual(shorter["status"], "rejected", shorter["reasons"])


class CliProcessContractTests(unittest.TestCase):
    """The real process must exit 0/2 with parseable admission JSON on stdout."""

    def _run_cli(self, request: dict[str, object]) -> "subprocess.CompletedProcess[str]":
        import subprocess

        with TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "helper_agent_router.py"),
                    "admit",
                    "--request",
                    str(request_path),
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--audit-log",
                    str(Path(directory) / "audit.jsonl"),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_admit_process_exits_zero_with_admission_json(self) -> None:
        completed = self._run_cli(_read_only_request(**_LIVE_ROLE))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], helper_agent_router.ADMISSION_SCHEMA)
        self.assertEqual(payload["status"], "admitted")

    def test_reject_process_exits_two_with_admission_json(self) -> None:
        completed = self._run_cli(_read_only_request(mode="read-write"))

        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], helper_agent_router.ADMISSION_SCHEMA)
        self.assertEqual(payload["status"], "rejected")

    def test_process_contract_keeps_workspace_audit_unchanged(self) -> None:
        """The subprocess fixture must not append test events to tracked state."""
        audit_log = PROJECT_ROOT / "state" / "helper-agent-spawns.jsonl"
        before = audit_log.read_bytes() if audit_log.exists() else None

        completed = self._run_cli(_read_only_request(task_id="isolated-process-audit", **_LIVE_ROLE))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        after = audit_log.read_bytes() if audit_log.exists() else None
        self.assertEqual(after, before)

    def test_usage_error_emits_json_verdict(self) -> None:
        import contextlib
        import io

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                helper_agent_router.main(["admit"])

        self.assertEqual(raised.exception.code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "rejected")
        self.assertTrue(payload["reasons"][0].startswith("usage error"))


if __name__ == "__main__":
    unittest.main()