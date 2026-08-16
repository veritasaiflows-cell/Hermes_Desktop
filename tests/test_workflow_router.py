import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import workflow_router, workflow_runner
from scripts.concurrent_lane_manager import ConcurrentLaneManager



def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Workflow control surface\n\n```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )


def _build_control_plane_with_workflows(root: Path, workflows: list[dict], aliases: dict[str, str]) -> None:
    _write_markdown_json(
        root / "state" / "ACTIVE_WORKFLOWS.md",
        {
            "schema": "active-workflows.v1",
            "generated_by": "workflow-router-tests",
            "workflows": workflows,
        },
    )

    _write_markdown_json(
        root / "state" / "WORKFLOW_ALIAS_INDEX.md",
        {
            "schema": "workflow-alias-index.v1",
            "aliases": aliases,
        },
    )


def _build_control_plane(root: Path) -> None:
    _build_control_plane_with_workflows(
        root,
        workflows=[
            {
                "workflow_id": "WF-1000",
                "display_name": "Workflow A - Product Research",
                "tier": "P1",
                "priority": "high",
                "lifecycle": "active",
                "readiness": "active",
                "effective_status": "active",
                "state_description": "Test workflow state",
                "next_action": "Run workflow smoke.",
                "authoritative_next_action": "Run workflow smoke with review.",
                "helper_safe": True,
                "owner_action_required": False,
                "proof_artifact": "continuity/WF-1000-Product-Research.md",
                "freshness_sla": "daily",
                "blockers": ["Pending connector approval"],
                "stop_lines": ["Pause external writes"],
            }
        ],
        aliases={
            "workflow-a": "WF-1000",
            "product-research": "WF-1000",
        },
    )

    _write_json(
        root / "state" / "workflow-control-overrides.json",
        [
            {
                "workflow_id": "WF-1000",
                "status": "monitor_only",
                "set_by": "tests",
                "set_at": "2026-08-15T00:00:00Z",
                "reason": "Blocked for review",
                "resume_condition": "Resume after connector approval.",
                "replacement_next_action": "Manual resume step required.",
            }
        ],
    )

    (root / "continuity/WF-1000-Product-Research.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "continuity/WF-1000-Product-Research.md").write_text(
        "# continuity test\n\nManual check required.\n",
        encoding="utf-8",
    )


class WorkflowRouterTests(unittest.TestCase):
    def test_route_with_alias_and_validation_generates_capsules(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"

            result = workflow_router.route_workflows(
                selector="product-research",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=True,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertFalse(result["routing_index_stale"])
            self.assertFalse(result["unsafe_to_trust"])
            self.assertEqual(result["routing_freshness"]["status"], "fresh")
            self.assertEqual(result["routing_index_schema"], workflow_router.ROUTER_SCHEMA)
            self.assertEqual(result["workflow"]["workflow_id"], "WF-1000")
            self.assertEqual(result["workflow"]["effective_status"], "monitor_only")

            capsule = root / "state" / "workflows" / "WF-1000.json"
            self.assertTrue(capsule.exists())
            payload = json.loads(capsule.read_text(encoding="utf-8"))
            self.assertEqual(payload["workflow_id"], "WF-1000")
            self.assertEqual(payload["effective_status"], "monitor_only")

    def test_write_index_auto_regenerates_capsules(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"

            capsule_path = root / "state" / "workflows" / "WF-1000.json"
            if capsule_path.exists():
                capsule_path.unlink()

            result = workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertFalse(result["routing_index_stale"])
            self.assertTrue(capsule_path.exists())
            payload = json.loads(capsule_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["workflow_id"], "WF-1000")
            self.assertEqual(payload["effective_status"], "monitor_only")

    def test_validate_detects_source_schema_drift(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"

            workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            alias_index_path = root / "state" / "WORKFLOW_ALIAS_INDEX.md"
            alias_payload = workflow_router._load_json_surface(alias_index_path)
            alias_payload["schema"] = "workflow-alias-index.v2"
            alias_index_path.write_text(
                "# Workflow alias index (routing surface)\n\n```json\n"
                + json.dumps(alias_payload, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )

            stale = workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=False,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertTrue(stale["routing_index_stale"])
            self.assertTrue(stale["unsafe_to_trust"])
            self.assertEqual(stale["routing_freshness"]["status"], "stale")
            self.assertEqual(stale["routing_source_schema"]["alias_index"], "workflow-alias-index.v2")
            self.assertIn("Alias source schema changed since index creation.", stale["message"])

    def test_dependency_stalled_workflow_switches_to_monitor_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane_with_workflows(
                root,
                workflows=[
                    {
                        "workflow_id": "WF-1000",
                        "display_name": "Workflow A - Product Research",
                        "effective_status": "monitor_only",
                        "owner_action_required": False,
                        "proof_artifact": "continuity/WF-1000-Product-Research.md",
                        "blockers": ["Downstream gate pending"],
                        "stop_lines": [],
                    },
                    {
                        "workflow_id": "WF-2000",
                        "display_name": "Workflow B - Dependent",
                        "effective_status": "active",
                        "depends_on": ["WF-1000"],
                        "owner_action_required": False,
                        "proof_artifact": "continuity/WF-2000-Dependent.md",
                        "blockers": [],
                        "stop_lines": [],
                    },
                ],
                aliases={
                    "workflow-b": "WF-2000",
                    "product-b": "WF-2000",
                },
            )

            continuity_root = root / "continuity"
            continuity_root.mkdir(parents=True, exist_ok=True)
            (continuity_root / "WF-1000-Product-Research.md").write_text(
                "# continuity test\n",
                encoding="utf-8",
            )
            (continuity_root / "WF-2000-Dependent.md").write_text(
                "# continuity test\n",
                encoding="utf-8",
            )

            result = workflow_router.route_workflows(
                selector="WF-2000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=root / "tmp" / "workflow-routing-index.json",
                project_root=root,
                state_dir=root / "state",
            )

            self.assertEqual(result["workflow"]["workflow_id"], "WF-2000")
            self.assertEqual(result["workflow"]["effective_status"], "monitor_only")
            self.assertEqual(result["workflow"]["depends_on"], ["WF-1000"])
            self.assertTrue(any("Dependency WF-1000" in item for item in result["workflow"]["blockers"]))

    def test_dependency_graph_change_triggers_stale_validation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"

            workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            payload = json.loads(index_path.read_text(encoding="utf-8"))
            payload["dependency_graph"]["WF-1000"] = ["WF-0001"]
            index_path.write_text(
                json.dumps(payload, sort_keys=True, indent=2),
                encoding="utf-8",
            )

            stale = workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=False,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertTrue(stale["routing_index_stale"])
            self.assertTrue(stale["unsafe_to_trust"])
            self.assertIn("dependency_graph", stale["changed_sources"][0]["path"])
            self.assertIn("Dependency graph changed since index creation.", stale["message"])

    def test_validate_requires_fresh_index_or_returns_stale(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"

            stale = workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=False,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertTrue(stale["routing_index_stale"])
            self.assertTrue(stale["unsafe_to_trust"])
            self.assertEqual(stale["required_refresh_command"],
                             "python scripts/workflow_router.py --write-index --answer summary")

            refreshed = workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            self.assertFalse(refreshed["routing_index_stale"])

    def test_unknown_selector_raises(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)

            with self.assertRaises(KeyError):
                workflow_router.route_workflows(
                    selector="workflow-b",
                    answer="summary",
                    validate=False,
                    write_index=False,
                    write_capsules=False,
                    project_root=root,
                    state_dir=root / "state",
                )

    def test_write_preflight_blocks_monitor_only_workflow_but_allows_dry_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            index_path = root / "tmp" / "workflow-routing-index.json"
            workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            dry_run = workflow_runner.preflight_workflow(
                "WF-1000",
                write=False,
                project_root=root,
                state_dir=root / "state",
                index_path=index_path,
            )
            self.assertEqual(dry_run["effective_status"], "monitor_only")

            with self.assertRaisesRegex(workflow_runner.WorkflowPreflightError, "monitor_only"):
                workflow_runner.preflight_workflow(
                    "WF-1000",
                    write=True,
                    project_root=root,
                    state_dir=root / "state",
                    index_path=index_path,
                )

    def test_write_preflight_requires_a_running_scoped_lane_lease(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            _write_markdown_json(
                root / "state" / "ACTIVE_WORKFLOWS.md",
                {
                    "schema": "active-workflows.v1",
                    "workflows": [
                        {
                            "workflow_id": "WF-1000",
                            "display_name": "Workflow A - Product Research",
                            "lifecycle": "active",
                            "effective_status": "active",
                            "owner_action_required": False,
                            "blockers": [],
                            "stop_lines": [],
                        }
                    ],
                },
            )
            _write_json(root / "state" / "workflow-control-overrides.json", [])
            index_path = root / "tmp" / "workflow-routing-index.json"
            workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )
            register_path = root / "state" / "concurrent-lane-register.sqlite"
            manager = ConcurrentLaneManager(project_root=root, register_path=register_path)
            manager.plan_lane(
                parent_job_id="job-write",
                workflow_id="WF-1000",
                workstream="product-research",
                owner="agent-main",
                lane_id="WF-1000::product-research",
                lane_mode="write",
                allowed_writes=["canonical/efficiens.db"],
            )
            manager.lease_lane("WF-1000::product-research", owner="agent-main", duration_minutes=5)
            manager.set_status("WF-1000::product-research", "running", actor="agent-main")

            preflight = workflow_runner.preflight_workflow(
                "WF-1000",
                write=True,
                lane_id="WF-1000::product-research",
                lane_owner="agent-main",
                write_targets=["canonical/efficiens.db"],
                project_root=root,
                state_dir=root / "state",
                index_path=index_path,
                lane_register_path=register_path,
            )

            self.assertEqual(preflight["preflight_mode"], "write")
            self.assertEqual(preflight["lane_id"], "WF-1000::product-research")

    def test_write_preflight_rejects_read_only_lane_mode(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _build_control_plane(root)
            _write_markdown_json(
                root / "state" / "ACTIVE_WORKFLOWS.md",
                {
                    "schema": "active-workflows.v1",
                    "generated_by": "workflow-router-tests",
                    "workflows": [
                        {
                            "workflow_id": "WF-1000",
                            "display_name": "Workflow A - Product Research",
                            "effective_status": "active",
                            "owner_action_required": False,
                            "blockers": [],
                            "stop_lines": [],
                        }
                    ],
                },
            )
            _write_markdown_json(
                root / "state" / "WORKFLOW_ALIAS_INDEX.md",
                {
                    "schema": "workflow-alias-index.v1",
                    "aliases": {
                        "workflow-a": "WF-1000",
                        "product-research": "WF-1000",
                    },
                },
            )
            _write_json(root / "state" / "workflow-control-overrides.json", [])

            index_path = root / "tmp" / "workflow-routing-index.json"
            workflow_router.route_workflows(
                selector="WF-1000",
                answer="summary",
                validate=True,
                write_index=True,
                write_capsules=False,
                index_path=index_path,
                project_root=root,
                state_dir=root / "state",
            )

            register_path = root / "state" / "concurrent-lane-register.sqlite"
            manager = ConcurrentLaneManager(project_root=root, register_path=register_path)
            manager.plan_lane(
                parent_job_id="job-read-only",
                workflow_id="WF-1000",
                workstream="product-research",
                owner="agent-main",
                lane_id="WF-1000::product-research-read-only",
                lane_mode="read-only",
            )
            manager.lease_lane("WF-1000::product-research-read-only", owner="agent-main", duration_minutes=5)
            manager.set_status("WF-1000::product-research-read-only", "running", actor="agent-main")

            with self.assertRaisesRegex(
                workflow_runner.WorkflowPreflightError,
                "read[- ]only",
            ):
                workflow_runner.preflight_workflow(
                    "WF-1000",
                    write=True,
                    lane_id="WF-1000::product-research-read-only",
                    lane_owner="agent-main",
                    write_targets=["canonical/efficiens.db"],
                    project_root=root,
                    state_dir=root / "state",
                    index_path=index_path,
                    lane_register_path=register_path,
                )


if __name__ == "__main__":
    unittest.main()
