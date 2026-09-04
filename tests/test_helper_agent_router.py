"""Tests for the deterministic helper-agent admission gate."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import concurrent_lane_manager, helper_agent_router  # noqa: E402


class _LaneFixture:
    """Build a real lane register via ConcurrentLaneManager so schema drift is caught."""

    def __init__(self, root: Path, allowed_writes: list[str] | None = None) -> None:
        self.root = root
        self.manager = concurrent_lane_manager.ConcurrentLaneManager(
            project_root=root,
            register_path=root / "state" / "concurrent-lane-register.sqlite",
        )
        self.manager.plan_lane(
            parent_job_id="job-helper-001",
            workflow_id="WF-1000",
            workstream="helper-gate-fixture",
            owner="agent-main",
            lane_id="WF-1000::helper-gate-fixture",
            lane_mode="write",
            allowed_writes=(
                allowed_writes if allowed_writes is not None else ["derived/helper-notes/"]
            ),
        )

    def lease(self, owner: str = "agent-main", duration_minutes: float = 60.0) -> None:
        self.manager.lease_lane(
            "WF-1000::helper-gate-fixture",
            owner=owner,
            duration_minutes=duration_minutes,
        )

    def start(self) -> None:
        self.manager.start_lane("WF-1000::helper-gate-fixture")


def _write_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema": helper_agent_router.REQUEST_SCHEMA,
        "task_id": "helper-042",
        "task_class": "research",
        "phase": "pre-implementation",
        "mode": "read-only",
        "objective": "Summarize the affected modules before implementation.",
        "scope": "Read-only summary of scripts/ and tests/ layout.",
        "allowed_toolsets": ["read_files", "search_files"],
        "allowed_writes": [],
        "max_duration_minutes": 30,
        "owner": "agent-main",
    }
    request.update(overrides)
    return request


def _write_mode_request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "write",
        "task_class": "documentation",
        "phase": "implementation",
        "objective": "Draft the module notes file.",
        "scope": "Write one notes file under derived/.",
        "allowed_toolsets": ["read_files", "write_file"],
        "allowed_writes": ["derived/helper-notes/notes.md"],
        "lane_id": "WF-1000::helper-gate-fixture",
    }
    base.update(overrides)
    return _write_request(**base)


class HelperAgentRouterTests(unittest.TestCase):
    # --- schema shape ---

    def test_declared_read_only_request_is_admitted(self) -> None:
        result = helper_agent_router.admit_request(_write_request())

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["schema"], helper_agent_router.ADMISSION_SCHEMA)
        self.assertEqual(result["mode"], "read-only")

    def test_unknown_fields_are_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(extra_field="x"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("unexpected request fields" in r for r in result["reasons"]))

    def test_missing_fields_are_rejected(self) -> None:
        request = _write_request()
        del request["objective"]

        result = helper_agent_router.admit_request(request)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("missing required request fields" in r for r in result["reasons"]))

    def test_unsupported_schema_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(schema="other.v1"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("unsupported request schema" in r for r in result["reasons"]))

    def test_malformed_task_id_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(task_id="BAD ID!"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("task_id" in r for r in result["reasons"]))

    def test_empty_objective_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(objective="   "))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("objective" in r for r in result["reasons"]))

    def test_empty_scope_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(scope=""))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("scope" in r for r in result["reasons"]))

    def test_undeclared_task_class_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(task_class="autonomy"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("task_class is not allowlisted" in r for r in result["reasons"]))

    def test_zero_max_duration_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(max_duration_minutes=0))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("max_duration_minutes" in r for r in result["reasons"]))

    def test_non_integer_max_duration_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(max_duration_minutes="30"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("max_duration_minutes" in r for r in result["reasons"]))

    def test_max_duration_above_cap_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(max_duration_minutes=481))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("max_duration_minutes" in r for r in result["reasons"]))

    # --- mode consistency ---

    def test_unspecified_mode_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(mode="read-write"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("mode must be read-only or write" in r for r in result["reasons"]))

    def test_read_only_with_implementation_phase_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(phase="implementation"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("phase must be pre-implementation" in r for r in result["reasons"]))

    def test_read_only_with_nonempty_allowed_writes_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_writes=["derived/notes.md"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("read-only request must not declare allowed_writes" in r for r in result["reasons"])
        )

    def test_read_only_with_write_capable_toolset_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["read_files", "write_file"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("write-capable toolset" in r for r in result["reasons"]), result["reasons"]
        )

    def test_read_only_with_lane_id_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(lane_id="WF-1000::helper-gate-fixture")
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("lane_id" in r for r in result["reasons"]))

    def test_read_only_with_terminal_toolset_is_rejected(self) -> None:
        """terminal can write files, so a read-only admission may not declare it."""
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["read_files", "terminal"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("write-capable toolset" in r for r in result["reasons"]), result["reasons"]
        )

    # --- write mode requires a leased covering lane ---

    def test_write_request_without_lane_id_is_rejected(self) -> None:
        request = _write_mode_request()
        del request["lane_id"]

        result = helper_agent_router.admit_request(request)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("lane_id" in r for r in result["reasons"]))

    def test_write_request_with_missing_register_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            result = helper_agent_router.admit_request(
                _write_mode_request(), project_root=Path(directory)
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("lane register" in r for r in result["reasons"]))

    def test_write_request_with_unleased_lane_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _LaneFixture(root)
            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("leased" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_expired_lease_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease(duration_minutes=1.0)
            past = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            connection = sqlite3.connect(str(fixture.manager.register_path))
            connection.execute(
                "UPDATE lanes SET lease_expires_at=? WHERE lane_id=?",
                (past, "WF-1000::helper-gate-fixture"),
            )
            connection.commit()
            connection.close()

            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("expired" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_non_covering_lane_paths_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["state/notes.md"]), project_root=root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("not covered by the leased lane" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_write_request_with_ancestor_path_beyond_lane_is_rejected(self) -> None:
        """Directionality: requesting a parent surface must not borrow lane scope."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root, allowed_writes=["derived/helper-notes/deep/"])
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived/"]), project_root=root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("not covered by the leased lane" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_write_request_with_wrong_owner_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(owner="helper-a"), project_root=root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("owner" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_leased_covering_lane_is_admitted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "admitted", result["reasons"])
        self.assertEqual(result["reasons"], [])

    def test_write_request_with_running_lane_is_admitted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            fixture.start()
            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_write_request_with_unknown_lane_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(lane_id="WF-1000::does-not-exist"), project_root=root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("unknown lane" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_blocked_lane_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            fixture.manager.set_status("WF-1000::helper-gate-fixture", "blocked")
            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("leased" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_against_read_only_lane_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = concurrent_lane_manager.ConcurrentLaneManager(
                project_root=root,
                register_path=root / "state" / "concurrent-lane-register.sqlite",
            )
            manager.plan_lane(
                parent_job_id="job-helper-002",
                workflow_id="WF-1000",
                workstream="helper-read-fixture",
                owner="agent-main",
                lane_id="WF-1000::helper-read-fixture",
                lane_mode="read-only",
            )
            manager.lease_lane("WF-1000::helper-read-fixture", owner="agent-main")
            result = helper_agent_router.admit_request(
                _write_mode_request(lane_id="WF-1000::helper-read-fixture"), project_root=root
            )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("not a write-mode lane" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_write_request_matches_lane_directory_surface_stored_in_register_format(
        self,
    ) -> None:
        """Real-register reality: lane surfaces persist as absolute normcase paths."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root, allowed_writes=["scripts/sub/"])
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                stored = json.loads(
                    connection.execute(
                        "SELECT allowed_writes_json FROM lanes WHERE lane_id=?",
                        ("WF-1000::helper-gate-fixture",),
                    )
                    .fetchone()[0]
                )
            finally:
                connection.close()
            self.assertTrue(stored, "fixture must persist real normalized surfaces")

            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["scripts/sub/notes.md"]), project_root=root
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])

    # --- path validation ---

    def test_write_request_with_path_traversal_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=["../outside.txt"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("traversal" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_glob_path_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=["derived/*.md"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("glob" in r.lower() for r in result["reasons"]), result["reasons"])

    def test_write_request_with_absolute_path_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_mode_request(allowed_writes=["C:/Users/Veritas/outside.txt"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("absolute" in r for r in result["reasons"]), result["reasons"])

    def test_write_request_with_forbidden_surface_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_mode_request(allowed_writes=["AGENTS.md"]))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("forbidden" in r for r in result["reasons"]), result["reasons"])

    def test_backslash_path_normalizes_and_admits_when_lane_covers_it(self) -> None:
        """_normalize_path accepts backslash input; policy is coverage, not format."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["derived\\helper-notes\\notes.md"]),
                project_root=root,
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])

    # --- toolset policy ---

    def test_forbidden_toolset_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["read_files", "cronjob"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("forbidden toolset" in r for r in result["reasons"]), result["reasons"])

    def test_computer_use_toolset_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["computer_use"])
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("forbidden toolset" in r for r in result["reasons"]), result["reasons"])

    def test_non_list_toolsets_are_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(allowed_toolsets="read_files"))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("allowed_toolsets must be a non-empty list" in r for r in result["reasons"])
        )

    def test_non_string_toolset_entry_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(_write_request(allowed_toolsets=[1]))

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("allowed_toolsets" in r for r in result["reasons"]))

    # --- QA challenger findings (must fail closed, never crash) ---

    def test_malformed_lane_allowed_writes_json_is_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                connection.execute(
                    "UPDATE lanes SET allowed_writes_json=? WHERE lane_id=?",
                    ("{not json", "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()

            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_non_list_lane_allowed_writes_json_is_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                connection.execute(
                    "UPDATE lanes SET allowed_writes_json=? WHERE lane_id=?",
                    ("not-a-list", "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()

            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_unparseable_lane_lease_expiry_is_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                connection.execute(
                    "UPDATE lanes SET lease_expires_at=? WHERE lane_id=?",
                    ("garbage-date", "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()

            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_naive_date_only_lease_expiry_is_rejected(self) -> None:
        """The manager writes Z-suffixed UTC leases; naive values fail closed."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                connection.execute(
                    "UPDATE lanes SET lease_expires_at=? WHERE lane_id=?",
                    ("2030-01-01", "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()

            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_corrupt_register_bytes_are_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "state" / "concurrent-lane-register.sqlite").write_bytes(b"not sqlite")
            result = helper_agent_router.admit_request(_write_mode_request(), project_root=root)

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_toolset_casing_cannot_bypass_read_only_contract(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["read_files", "Terminal"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("write-capable toolset" in r for r in result["reasons"]), result["reasons"]
        )

    def test_uppercase_write_file_cannot_bypass_read_only_contract(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(allowed_toolsets=["read_files", "WRITE_FILE"])
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])

    def test_forbidden_surface_is_checked_against_request_root(self) -> None:
        """Root-anchored forbidden names bind to the request root, not the repo.

        The lane manager refuses planning a lane on a forbidden surface, so a
        crafted (corrupted) register row is the only way to reach this state;
        the gate must still reject it at whichever --project-root was passed.
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root, allowed_writes=["sub/"])
            fixture.lease()
            import json as _json

            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                covered = _json.dumps(
                    [
                        str((root / "sub").resolve()) + "\\",
                        str((root / "AGENTS.md").resolve()),
                    ]
                )
                connection.execute(
                    "UPDATE lanes SET allowed_writes_json=? WHERE lane_id=?",
                    (covered, "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()

            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["AGENTS.md"]), project_root=root
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(any("forbidden" in r for r in result["reasons"]), result["reasons"])

    def test_not_yet_existing_lane_surface_requested_without_trailing_slash_is_admitted(
        self,
    ) -> None:
        """A leased directory surface must cover itself with or without a slash (QA F6)."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root, allowed_writes=["brand-new-dir/"])
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(allowed_writes=["brand-new-dir"]), project_root=root
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])


class HelperAgentRouterCliTests(unittest.TestCase):
    def _write_request_file(self, path: Path, request: dict[str, object]) -> Path:
        path.write_text(json.dumps(request), encoding="utf-8")
        return path

    def test_cli_admit_returns_zero_for_admitted_request(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema": helper_agent_router.REQUEST_SCHEMA,
                        "task_id": "helper-cli-001",
                        "task_class": "research",
                        "phase": "pre-implementation",
                        "mode": "read-only",
                        "objective": "Read-only reconnaissance for the CLI test.",
                        "scope": "Read-only summary; no writes.",
                        "allowed_toolsets": ["read_files"],
                        "allowed_writes": [],
                        "max_duration_minutes": 15,
                        "owner": "agent-main",
                    }
                ),
                encoding="utf-8",
            )

            exit_code = helper_agent_router.main(
                ["admit", "--request", str(request_path), "--project-root", str(root)]
            )

        self.assertEqual(exit_code, 0)

    def test_cli_admit_returns_two_for_rejected_request(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            request = {
                "schema": helper_agent_router.REQUEST_SCHEMA,
                "task_id": "helper-cli-002",
                "task_class": "research",
                "phase": "pre-implementation",
                "mode": "read-write",
                "objective": "Attempt an invalid mode through the CLI.",
                "scope": "Read-only summary; no writes.",
                "allowed_toolsets": ["read_files"],
                "allowed_writes": [],
                "max_duration_minutes": 15,
                "owner": "agent-main",
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")

            exit_code = helper_agent_router.main(
                ["admit", "--request", str(request_path), "--project-root", str(root)]
            )

        self.assertEqual(exit_code, 2)

    def test_cli_admit_returns_two_for_unreadable_request(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            request_path.write_text("{not json", encoding="utf-8")

            exit_code = helper_agent_router.main(
                ["admit", "--request", str(request_path), "--project-root", str(root)]
            )

        self.assertEqual(exit_code, 2)


class SpawnAuditLogTests(unittest.TestCase):
    """Every admission decision must leave an auditable spawn event."""

    def _admit(
        self, root: Path, request: dict[str, object], extra: list[str] | None = None
    ) -> int:
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        return helper_agent_router.main(
            ["admit", "--request", str(request_path), "--project-root", str(root)]
            + (extra or [])
        )

    def test_admitted_request_appends_audit_event_by_default(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exit_code = self._admit(root, _write_request(task_id="helper-audit-001"))

            self.assertEqual(exit_code, 0)
            audit = root / "state" / "helper-agent-spawns.jsonl"
            self.assertTrue(audit.is_file())
            event = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(event["schema"], helper_agent_router.SPAWN_AUDIT_SCHEMA)
            self.assertEqual(event["status"], "admitted")
            self.assertEqual(event["task_id"], "helper-audit-001")
            self.assertEqual(event["mode"], "read-only")

    def test_rejected_request_appends_audit_event(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exit_code = self._admit(root, _write_request(task_id="BAD ID!"))

            self.assertEqual(exit_code, 2)
            audit = root / "state" / "helper-agent-spawns.jsonl"
            event = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(event["status"], "rejected")

    def test_explicit_audit_log_path_is_honoured(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "custom-audit.jsonl"
            exit_code = self._admit(
                root, _write_request(task_id="helper-audit-002"),
                ["--audit-log", str(custom)],
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(custom.is_file())
            self.assertFalse((root / "state" / "helper-agent-spawns.jsonl").exists())

    def test_no_audit_log_opt_out_writes_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            exit_code = self._admit(root, _write_request(), ["--no-audit-log"])

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "state" / "helper-agent-spawns.jsonl").exists())

    def test_audit_write_failure_is_fail_closed(self) -> None:
        import io
        from contextlib import redirect_stdout

        with TemporaryDirectory() as directory:
            root = Path(directory)
            blocker = root / "blocker"
            blocker.mkdir()
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = self._admit(
                    root, _write_request(), ["--audit-log", str(blocker)]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("audit", buffer.getvalue().lower())

    def test_write_mode_toolsets_exclude_shell(self) -> None:
        """Pin the terminal-drift fix: a shell is not lane-bounded, ever."""
        self.assertNotIn("terminal", helper_agent_router.WRITE_MODE_TOOLSETS)
        self.assertNotIn("execute_code", helper_agent_router.WRITE_MODE_TOOLSETS)


class SpawnAuditProtectionTests(unittest.TestCase):
    """The spawn audit log must not be writable by an admitted helper."""

    def test_spawn_audit_log_is_a_forbidden_surface(self) -> None:
        self.assertIn(
            "state/helper-agent-spawns.jsonl",
            concurrent_lane_manager.DEFAULT_FORBIDDEN_SURFACES,
        )

    def test_write_request_covering_audit_log_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                _write_mode_request(
                    allowed_writes=["state/helper-agent-spawns.jsonl"]
                ),
                project_root=root,
            )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            any("forbidden" in r.lower() for r in result["reasons"]),
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()