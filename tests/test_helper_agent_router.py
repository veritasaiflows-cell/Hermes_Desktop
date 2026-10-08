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
        # Isolated root: no fleet role registry, so the legacy role-less contract applies.
        with TemporaryDirectory() as tmp:
            result = helper_agent_router.admit_request(_write_request(), project_root=Path(tmp))

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


class ReviewAttributionTests(unittest.TestCase):
    """Review-class spawns scoped to a lane must prove author/reviewer diversity."""

    def _review_request(self, **overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "schema": helper_agent_router.REQUEST_SCHEMA,
            "task_id": "review-attr-001",
            "task_class": "review",
            "phase": "pre-implementation",
            "mode": "read-only",
            "objective": "Independent review of the lane's integrated diff.",
            "scope": "Read-only review of the integrated result.",
            "allowed_toolsets": ["read_files", "search_files"],
            "allowed_writes": [],
            "max_duration_minutes": 30,
            "owner": "agent-main",
        }
        request.update(overrides)
        return request

    def _attributed_lane(self, root: Path, model: str) -> None:
        fixture = _LaneFixture(root)
        fixture.lease()
        connection = sqlite3.connect(str(fixture.manager.register_path))
        try:
            connection.execute(
                "UPDATE lanes SET expected_model=? WHERE lane_id=?",
                (model, "WF-1000::helper-gate-fixture"),
            )
            connection.commit()
        finally:
            connection.close()

    def test_review_spawn_with_matching_reviewer_model_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._attributed_lane(root, "gpt-5.6-terra")
            result = helper_agent_router.admit_request(
                self._review_request(
                    reviews_lane="WF-1000::helper-gate-fixture",
                    reviewer_model="gpt-5.6-terra",
                ),
                project_root=root,
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("author and reviewer must differ" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_review_spawn_with_diverse_reviewer_model_is_admitted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._attributed_lane(root, "gpt-5.6-terra")
            result = helper_agent_router.admit_request(
                self._review_request(
                    reviews_lane="WF-1000::helper-gate-fixture",
                    reviewer_model="gpt-5.6-sol",
                ),
                project_root=root,
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_review_spawn_scoped_to_lane_without_reviewer_model_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._attributed_lane(root, "gpt-5.6-terra")
            result = helper_agent_router.admit_request(
                self._review_request(reviews_lane="WF-1000::helper-gate-fixture"),
                project_root=root,
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("reviewer_model" in r for r in result["reasons"]), result["reasons"]
        )

    def test_reviewer_model_without_reviews_lane_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            self._review_request(reviewer_model="gpt-5.6-sol")
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("reviewer_model requires reviews_lane" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_review_of_lane_without_author_attribution_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = _LaneFixture(root)
            fixture.lease()
            result = helper_agent_router.admit_request(
                self._review_request(
                    reviews_lane="WF-1000::helper-gate-fixture",
                    reviewer_model="gpt-5.6-sol",
                ),
                project_root=root,
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("no author model attribution" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_review_of_unknown_lane_is_rejected_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._attributed_lane(root, "gpt-5.6-terra")
            result = helper_agent_router.admit_request(
                self._review_request(
                    reviews_lane="WF-1000::does-not-exist",
                    reviewer_model="gpt-5.6-sol",
                ),
                project_root=root,
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("unknown reviewed lane" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_reviews_lane_on_non_review_class_is_rejected(self) -> None:
        result = helper_agent_router.admit_request(
            _write_request(
                reviews_lane="WF-1000::helper-gate-fixture",
                reviewer_model="gpt-5.6-sol",
            )
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("only valid for review-class requests" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_spawn_event_records_review_attribution(self) -> None:
        request = self._review_request(
            reviews_lane="WF-1000::helper-gate-fixture",
            reviewer_model="gpt-5.6-sol",
        )
        event = helper_agent_router.build_spawn_event(
            request,
            {
                "status": "admitted",
                "task_id": request["task_id"],
                "mode": "read-only",
                "lane_id": None,
                "reasons": [],
            },
        )

        self.assertEqual(event["reviews_lane"], "WF-1000::helper-gate-fixture")
        self.assertEqual(event["reviewer_model"], "gpt-5.6-sol")


class QaReviewRouteTests(unittest.TestCase):
    """QA reviews may run on the primary OR a registry-listed QA fallback, never on the author's model.

    Operator decision 2026-10-03: QA primary is gpt-6.1-sol (also the Senior Engineer), fallback is
    claude-sonnet-5-5 (also the Governor session). Each author direction needs an admissible,
    independent route; the gate compares NORMALISED model names so a bare name cannot dodge a
    provider-qualified author record.
    """

    SOL = "openai-codex/gpt-6.1-sol"
    ASTRA = "openai-codex/gpt-6-astra"
    SONNET = "anthropic/claude-sonnet-5-5"

    def _request(self, model: str, reviewer: str | None = None, **extra: object) -> dict[str, object]:
        return {
            "schema": helper_agent_router.REQUEST_SCHEMA,
            "task_id": "qa-route-001",
            "task_class": "review",
            "phase": "pre-implementation",
            "mode": "read-only",
            "role": "qa",
            "model": model,
            "reviews_lane": "WF-1000::helper-gate-fixture",
            "reviewer_model": reviewer if reviewer is not None else model,
            "objective": "Independent review of the lane's integrated diff.",
            "scope": "Read-only review of the integrated result.",
            "allowed_toolsets": ["read_files", "search_files"],
            "allowed_writes": [],
            "max_duration_minutes": 30,
            "owner": "agent-main",
            **extra,
        }

    def _admit(self, author: str, request: dict[str, object]) -> dict[str, object]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _install_registry(root)
            fixture = _LaneFixture(root)
            fixture.lease()
            connection = sqlite3.connect(str(fixture.manager.register_path))
            try:
                connection.execute(
                    "UPDATE lanes SET expected_model=? WHERE lane_id=?",
                    (author, "WF-1000::helper-gate-fixture"),
                )
                connection.commit()
            finally:
                connection.close()
            return helper_agent_router.admit_request(request, project_root=root)

    def test_sol_authored_lane_is_reviewed_on_the_sonnet_fallback(self) -> None:
        result = self._admit(self.SOL, self._request(self.SONNET))
        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_sonnet_authored_lane_is_reviewed_on_the_opus_primary(self) -> None:
        # Operator rebinding 2026-10-07: QA primary is Claude Opus 5.5.
        result = self._admit(self.SONNET, self._request("anthropic/claude-opus-5-5"))
        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_sol_authored_lane_cannot_be_reviewed_on_sol(self) -> None:
        result = self._admit(self.SOL, self._request(self.SOL))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("must differ" in r for r in result["reasons"]), result["reasons"])

    def test_sonnet_authored_lane_cannot_be_reviewed_on_sonnet(self) -> None:
        result = self._admit(self.SONNET, self._request(self.SONNET))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("must differ" in r for r in result["reasons"]), result["reasons"])

    def test_bare_reviewer_name_cannot_dodge_a_provider_qualified_author(self) -> None:
        # Pre-existing hole: 'gpt-6.1-sol' != 'openai-codex/gpt-6.1-sol' as raw strings.
        for author, reviewer in ((self.SOL, "gpt-6.1-sol"), ("gpt-6.1-sol", self.SOL), (self.SONNET, "claude-sonnet-5-5")):
            with self.subTest(author=author, reviewer=reviewer):
                result = self._admit(author, self._request(reviewer))
                self.assertEqual(result["status"], "rejected", result["reasons"])
                self.assertTrue(any("must differ" in r for r in result["reasons"]), result["reasons"])

    def test_reviewer_model_must_equal_the_route_actually_requested(self) -> None:
        result = self._admit(self.SOL, self._request(self.SONNET, reviewer=self.SOL))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("reviewer_model" in r for r in result["reasons"]), result["reasons"])

    def test_a_model_outside_the_qa_chain_is_still_rejected(self) -> None:
        # Sol is the implementer / senior engineer, no longer a QA route (2026-10-06).
        result = self._admit(self.SONNET, self._request(self.SOL))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("approved binding" in r or "approved route" in r for r in result["reasons"]), result["reasons"])

    def test_fallback_routes_stay_inadmissible_for_non_qa_roles(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _install_registry(root)
            result = helper_agent_router.admit_request(
                _write_request(task_class="analysis", role="architect", model="anthropic/claude-opus-5-5"),
                project_root=root,
            )
        self.assertEqual(result["status"], "rejected")

    def test_qa_fallback_cannot_take_a_write_or_non_review_task(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _install_registry(root)
            result = helper_agent_router.admit_request(
                _write_request(task_class="implementation", role="qa", model=self.SONNET), project_root=root
            )
        self.assertEqual(result["status"], "rejected")


class RepairCycleEscalationTests(unittest.TestCase):
    """Two or more repair cycles on a lane stop further write spawns."""

    def _lane_with_retries(self, root: Path, retry_count: int) -> None:
        fixture = _LaneFixture(root)
        fixture.lease()
        connection = sqlite3.connect(str(fixture.manager.register_path))
        try:
            connection.execute(
                "UPDATE lanes SET retry_count=? WHERE lane_id=?",
                (retry_count, "WF-1000::helper-gate-fixture"),
            )
            connection.commit()
        finally:
            connection.close()

    def test_write_spawn_on_lane_with_two_repair_cycles_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._lane_with_retries(root, 2)
            result = helper_agent_router.admit_request(
                _write_mode_request(), project_root=root
            )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(
            any("escalate" in r for r in result["reasons"]), result["reasons"]
        )

    def test_write_spawn_on_lane_with_one_retry_is_admitted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._lane_with_retries(root, 1)
            result = helper_agent_router.admit_request(
                _write_mode_request(), project_root=root
            )

        self.assertEqual(result["status"], "admitted", result["reasons"])


def _install_registry(root: Path, **role_overrides: dict[str, object]) -> dict[str, object]:
    live = json.loads(
        (PROJECT_ROOT / "state" / "fleet-role-registry.json").read_text(encoding="utf-8")
    )
    for role, override in role_overrides.items():
        live["roles"][role].update(override)
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "fleet-role-registry.json").write_text(json.dumps(live), encoding="utf-8")
    return live


def _set_retry(root: Path, count: int) -> None:
    connection = sqlite3.connect(str(root / "state" / "concurrent-lane-register.sqlite"))
    try:
        connection.execute(
            "UPDATE lanes SET retry_count = ? WHERE lane_id = ?",
            (count, "WF-1000::helper-gate-fixture"),
        )
        connection.commit()
    finally:
        connection.close()


class FleetRoleBindingTests(unittest.TestCase):
    """WF-1200: role -> model binding and implementer -> senior_engineer ladder."""

    def _root(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _write_root(self, retry: int = 0, **overrides: dict[str, object]) -> Path:
        root = self._root()
        fixture = _LaneFixture(root)
        fixture.lease()
        _install_registry(root, **overrides)
        if retry:
            _set_retry(root, retry)
        return root

    def test_live_registry_binds_operator_approved_models(self) -> None:
        live = json.loads(
            (PROJECT_ROOT / "state" / "fleet-role-registry.json").read_text(encoding="utf-8")
        )
        bindings = {r: f"{s['provider']}/{s['model']}" for r, s in live["roles"].items()}
        self.assertEqual(bindings["governor"], "openai-codex/gpt-6.1-sol")
        self.assertEqual(
            live["roles"]["governor"]["approval_ref"],
            "references/efficiency-phase0-reduced-closeout.md#operator-approval",
        )
        # The main primary is Sol; retain the existing approved Astra fallback.
        # Main ownership does not promote the Sol helper roles or allow self-review.
        fallbacks = live["roles"]["governor"]["fallback_providers"]
        self.assertEqual(
            [f"{f['provider']}/{f['model']}" for f in fallbacks],
            ["openai-codex/gpt-6-astra"],
        )
        self.assertEqual(live["roles"]["governor"]["status"], "parent_only")
        # 2026-10-03 operator fleet-fallback change: every role carries one
        # operator-approved fallback; only the researcher primary changed.
        expected_fallbacks = {
            "architect": "anthropic/claude-opus-5-5",
            "implementer": "ollama-cloud/glm-5.3-flash",
            "senior_engineer": "anthropic/claude-sonnet-5-5",
            "qa": "anthropic/claude-sonnet-5-5",
            "researcher": "ollama-cloud/deepseek-v4.1-flash",
        }
        for role, expected in expected_fallbacks.items():
            chain = live["roles"][role].get("fallback_providers", [])
            self.assertEqual([f"{f['provider']}/{f['model']}" for f in chain], [expected], role)
            for item in chain:
                self.assertEqual(item.get("approved_by"), "operator", role)
                self.assertTrue(item.get("approval_ref"), role)
                # a fallback must never be the role's own primary
                self.assertNotEqual(f"{item['provider']}/{item['model']}", bindings[role], role)
        # 2026-10-07 operator decision: permanently bind Opus 5.5 as QA.
        # Sonnet remains the independent fallback for Opus-authored work.
        self.assertEqual(bindings["qa"], "anthropic/claude-opus-5-5")
        self.assertEqual(
            live["roles"]["qa"]["approval_ref"],
            "derived/model-routing/2026-10-07/qa-opus-permanent/operator-approval.md",
        )
        self.assertEqual(bindings["researcher"], "openai-codex/gpt-6-luna")
        self.assertIn(live["roles"]["researcher"]["status"], {"qualified", "qualification_required"})
        if live["roles"]["researcher"]["status"] == "qualification_required":
            self.assertEqual(live["roles"]["researcher"]["requalification"]["previous_model"], "gpt-5.6-luna")
        self.assertNotIn("muse", json.dumps(live).lower())
        self.assertEqual(bindings["architect"], "openai-codex/gpt-6-astra")
        self.assertEqual(bindings["senior_engineer"], "openai-codex/gpt-6.1-sol")
        self.assertEqual(bindings["implementer"], "openai-codex/gpt-6.1-sol")
        self.assertEqual(live["roles"]["implementer"]["status"], "qualification_required")
        self.assertEqual(live["roles"]["governor"]["profile"], "default")
        self.assertEqual(live["roles"]["architect"]["profile"], "architect")
        self.assertEqual(live["roles"]["implementer"]["profile"], "implementer")
        self.assertEqual(live["roles"]["senior_engineer"]["profile"], "seniorengineer")
        self.assertEqual(live["roles"]["qa"]["profile"], "qa")
        self.assertEqual(live["roles"]["researcher"]["profile"], "researcher")
        self.assertEqual(live["roles"]["senior_engineer"]["min_lane_retry_count"], 1)
        # Escalation must be able to absorb every task class the implementer can fail.
        self.assertTrue(
            set(live["roles"]["implementer"]["task_classes"])
            <= set(live["roles"]["senior_engineer"]["task_classes"])
        )

    def test_live_workspace_requires_role_declaration(self) -> None:
        result = helper_agent_router.admit_request(_write_request())
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("must declare role and model" in r for r in result["reasons"]))

    def test_architect_with_bound_model_is_admitted(self) -> None:
        root = self._root()
        _install_registry(root)
        result = helper_agent_router.admit_request(
            _write_request(task_class="analysis", role="architect", model="openai-codex/gpt-6-astra"),
            project_root=root,
        )
        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_role_with_unbound_model_is_rejected(self) -> None:
        root = self._root()
        _install_registry(root)
        result = helper_agent_router.admit_request(
            _write_request(task_class="analysis", role="architect", model="ollama-cloud/deepseek-v4.1-flash"),
            project_root=root,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("not the approved binding" in r for r in result["reasons"]))

    def test_unknown_role_and_parent_only_governor_are_rejected(self) -> None:
        root = self._root()
        _install_registry(root)
        for role, model in (("intern", "x/y"), ("governor", "anthropic/claude-opus-5-5")):
            result = helper_agent_router.admit_request(
                _write_request(task_class="analysis", role=role, model=model), project_root=root
            )
            self.assertEqual(result["status"], "rejected", role)

    def test_architect_cannot_write(self) -> None:
        root = self._write_root()
        result = helper_agent_router.admit_request(
            _write_mode_request(role="architect", model="openai-codex/gpt-6-astra"), project_root=root
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("may not spawn in mode 'write'" in r for r in result["reasons"]))

    def test_unqualified_implementer_write_is_rejected_without_exception(self) -> None:
        root = self._write_root()
        result = helper_agent_router.admit_request(
            _write_mode_request(role="implementer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("qualification_required" in r for r in result["reasons"]))

    def test_implementer_write_admitted_with_operator_lane_exception(self) -> None:
        root = self._write_root()
        path = root / "state" / "fleet-role-registry.json"
        registry = json.loads(path.read_text(encoding="utf-8"))
        registry["lane_exceptions"] = [
            {
                "schema": "helper-lane-exception.v1",
                "kind": "one_time_lane_write",
                "role": "implementer",
                "lane_id": "WF-1000::helper-gate-fixture",
                "task_id": "helper-042",
                "mode": "write",
                "first_attempt_only": True,
                "approved_by": "operator",
            }
        ]
        path.write_text(json.dumps(registry), encoding="utf-8")
        result = helper_agent_router.admit_request(
            _write_mode_request(role="implementer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_implementer_lane_exception_is_bound_to_one_task(self) -> None:
        root = self._write_root()
        path = root / "state" / "fleet-role-registry.json"
        registry = json.loads(path.read_text(encoding="utf-8"))
        registry["lane_exceptions"] = [
            {
                "schema": "helper-lane-exception.v1",
                "kind": "one_time_lane_write",
                "role": "implementer",
                "lane_id": "WF-1000::helper-gate-fixture",
                "task_id": "different-task",
                "mode": "write",
                "first_attempt_only": True,
                "approved_by": "operator",
            }
        ]
        path.write_text(json.dumps(registry), encoding="utf-8")

        result = helper_agent_router.admit_request(
            _write_mode_request(role="implementer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )

        self.assertEqual(result["status"], "rejected", result["reasons"])
        self.assertTrue(any("one-time lane exception" in reason for reason in result["reasons"]))

    def test_qualified_implementer_blocked_after_failed_attempt(self) -> None:
        root = self._write_root(retry=1, implementer={"status": "qualified"})
        result = helper_agent_router.admit_request(
            _write_mode_request(role="implementer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("escalate to senior_engineer" in r for r in result["reasons"]))

    def test_senior_engineer_is_escalation_only(self) -> None:
        root = self._write_root(retry=0)
        result = helper_agent_router.admit_request(
            _write_mode_request(role="senior_engineer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("escalation-only" in r for r in result["reasons"]))

    def test_senior_engineer_admitted_after_implementer_failure(self) -> None:
        root = self._write_root(retry=1)
        result = helper_agent_router.admit_request(
            _write_mode_request(role="senior_engineer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "admitted", result["reasons"])

    def test_repair_cycle_two_blocks_every_role(self) -> None:
        root = self._write_root(retry=2)
        result = helper_agent_router.admit_request(
            _write_mode_request(role="senior_engineer", model="openai-codex/gpt-6.1-sol"),
            project_root=root,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("escalate to the human owner" in r for r in result["reasons"]))

    def test_corrupt_registry_fails_closed(self) -> None:
        root = self._root()
        (root / "state").mkdir()
        (root / "state" / "fleet-role-registry.json").write_text("{not json", encoding="utf-8")
        result = helper_agent_router.admit_request(_write_request(), project_root=root)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("unreadable" in r for r in result["reasons"]))

    def test_spawn_event_records_role_and_model(self) -> None:
        request = _write_request(role="qa", model="openai-codex/gpt-5.6-sol")
        event = helper_agent_router.build_spawn_event(request, {"status": "admitted"})
        self.assertEqual((event["role"], event["model"]), ("qa", "openai-codex/gpt-5.6-sol"))


if __name__ == "__main__":
    unittest.main()