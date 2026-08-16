from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import os

from scripts.concurrent_lane_manager import ConcurrentLaneManager, LaneManagerError


class ConcurrentLaneManagerTests(unittest.TestCase):
    def _make_manager(self, temp_root: Path) -> ConcurrentLaneManager:
        register = temp_root / "state" / "concurrent-lane-register.sqlite"
        return ConcurrentLaneManager(project_root=temp_root, register_path=register)

    def test_plan_and_lease_and_complete_with_proof(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            planned = manager.plan_lane(
                parent_job_id="job-product-research",
                workflow_id="WF-1000",
                workstream="research-pass",
                owner="agent-a",
                lane_id="WF-1000::research-pass",
                lane_mode="write",
                allowed_writes=["derived/output-a.json"],
            )
            self.assertEqual(planned["status"], "planned")

            leased = manager.lease_lane("WF-1000::research-pass", owner="agent-a", duration_minutes=30)
            self.assertEqual(leased["status"], "leased")
            self.assertIsNotNone(leased["lease_expires_at"])

            started = manager.start_lane("WF-1000::research-pass", actor="agent-a")
            self.assertEqual(started["status"], "running")

            proof = root / "derived" / "proof.txt"
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text("proof: done\n")

            completed = manager.complete_lane(
                "WF-1000::research-pass",
                actor="agent-a",
                proof_artifacts=[str(proof)],
            )
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(
                completed["proof_artifacts"],
                [os.path.normcase(str((root / "derived" / "proof.txt").resolve()))],
            )

    def test_collision_prevents_overlapping_allowed_writes(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            manager.plan_lane(
                parent_job_id="job-a",
                workflow_id="WF-1000",
                workstream="a",
                owner="agent-a",
                lane_id="WF-1000::a",
                lane_mode="write",
                allowed_writes=["derived/shared/"],
            )

            with self.assertRaises(LaneManagerError):
                manager.plan_lane(
                    parent_job_id="job-b",
                    workflow_id="WF-1000",
                    workstream="b",
                    owner="agent-b",
                    lane_id="WF-1000::b",
                    lane_mode="write",
                    allowed_writes=["derived/shared/report.json"],
                )

    def test_plan_rejects_high_impact_workspace_control_paths(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            with self.assertRaisesRegex(LaneManagerError, "forbidden"):
                manager.plan_lane(
                    parent_job_id="job-control-path",
                    workflow_id="WF-1000",
                    workstream="control-path",
                    owner="agent-a",
                    lane_id="WF-1000::control-path",
                    lane_mode="write",
                    allowed_writes=[".git/config"],
                )

    def test_parent_job_cannot_mix_workflow_ids(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)
            manager.plan_lane(
                parent_job_id="job-shared",
                workflow_id="WF-1000",
                workstream="first",
                owner="agent-a",
                lane_id="WF-1000::first",
                lane_mode="write",
                allowed_writes=["derived/first.txt"],
            )

            with self.assertRaisesRegex(LaneManagerError, "workflow_id"):
                manager.plan_lane(
                    parent_job_id="job-shared",
                    workflow_id="WF-2000",
                    workstream="second",
                    owner="agent-b",
                    lane_id="WF-2000::second",
                    lane_mode="write",
                    allowed_writes=["derived/second.txt"],
                )

    def test_lease_rejects_non_positive_duration(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)
            manager.plan_lane(
                parent_job_id="job-duration",
                workflow_id="WF-1000",
                workstream="duration",
                owner="agent-a",
                lane_id="WF-1000::duration",
                lane_mode="write",
                allowed_writes=["derived/duration.txt"],
            )

            with self.assertRaisesRegex(LaneManagerError, "positive"):
                manager.lease_lane("WF-1000::duration", owner="agent-a", duration_minutes=0)

    def test_parent_child_collision_is_blocked(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            manager.plan_lane(
                parent_job_id="job-parent",
                workflow_id="WF-1000",
                workstream="parent",
                owner="agent-a",
                lane_id="WF-1000::parent",
                lane_mode="write",
                allowed_writes=["derived/tree"],
            )

            with self.assertRaises(LaneManagerError):
                manager.plan_lane(
                    parent_job_id="job-child",
                    workflow_id="WF-1000",
                    workstream="child",
                    owner="agent-b",
                    lane_id="WF-1000::child",
                    lane_mode="write",
                    allowed_writes=["derived/tree/leaf.txt"],
                )

    def test_complete_requires_proof_artifacts(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            manager.plan_lane(
                parent_job_id="job-proof",
                workflow_id="WF-1000",
                workstream="proof",
                owner="agent-a",
                lane_id="WF-1000::proof",
                lane_mode="write",
                allowed_writes=["derived/needs-proof.txt"],
            )
            manager.lease_lane("WF-1000::proof", owner="agent-a", duration_minutes=120)
            manager.start_lane("WF-1000::proof", actor="agent-a")

            with self.assertRaises(LaneManagerError):
                manager.complete_lane("WF-1000::proof", actor="agent-a")

    def test_validate_flags_expired_lease(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manager = self._make_manager(root)

            manager.plan_lane(
                parent_job_id="job-expire",
                workflow_id="WF-1000",
                workstream="expire",
                owner="agent-a",
                lane_id="WF-1000::expire",
                lane_mode="write",
                allowed_writes=["derived/expired.txt"],
            )
            manager.lease_lane("WF-1000::expire", owner="agent-a", duration_minutes=1)
            with manager._connect() as connection:
                connection.execute(
                    "UPDATE lanes SET lease_expires_at = '2000-01-01T00:00:00Z' "
                    "WHERE lane_id = ?",
                    ("WF-1000::expire",),
                )

            validation = manager.validate()
            self.assertTrue(validation["expired_leases"])
            self.assertTrue(any(item["lane_id"] == "WF-1000::expire" for item in validation["expired_leases"]))
            self.assertFalse(validation["ok"])
            self.assertTrue(
                any(item["code"] == "expired_lease" for item in validation["hard_failures"])
            )


if __name__ == "__main__":
    unittest.main()
