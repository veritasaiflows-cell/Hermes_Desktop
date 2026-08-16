from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import concurrent_lane_manager


class LaneRegisterIntegrityTests(unittest.TestCase):
    """Verify the concurrent lane register remains consistent."""

    def _temporary_register(self):
        directory = TemporaryDirectory()
        register_path = Path(directory.name) / "lanes.sqlite"
        manager = concurrent_lane_manager.ConcurrentLaneManager(
            project_root=Path(__file__).resolve().parents[1],
            register_path=register_path,
        )
        return manager, directory

    def test_empty_register_has_no_active_overlaps(self):
        manager, directory = self._temporary_register()
        try:
            result = manager.validate()
            self.assertTrue(result["ok"])
            self.assertEqual(result["active_lanes"], 0)
            self.assertEqual(result["collisions"], [])
        finally:
            directory.cleanup()

    def test_overlapping_active_lanes_are_detected(self):
        manager, directory = self._temporary_register()
        try:
            lane_a = manager.plan_lane(
                parent_job_id="job-1",
                workflow_id="WF-1000",
                workstream="stream-a",
                owner="agent-a",
                lane_mode="write",
                allowed_writes=["scripts/"],
            )["lane_id"]
            manager.lease_lane(lane_a, owner="agent-a", duration_minutes=10)
            manager.start_lane(lane_a)

            with self.assertRaisesRegex(Exception, "Collision detected while planning"):
                manager.plan_lane(
                    parent_job_id="job-2",
                    workflow_id="WF-1000",
                    workstream="stream-b",
                    owner="agent-b",
                    lane_mode="write",
                    allowed_writes=["scripts/sub/"],
                )
        finally:
            directory.cleanup()

    def test_non_overlapping_active_lanes_are_valid(self):
        manager, directory = self._temporary_register()
        try:
            lane_a = manager.plan_lane(
                parent_job_id="job-1",
                workflow_id="WF-1000",
                workstream="stream-a",
                owner="agent-a",
                lane_mode="write",
                allowed_writes=["scripts/"],
            )["lane_id"]
            manager.lease_lane(lane_a, owner="agent-a", duration_minutes=10)
            manager.start_lane(lane_a)

            lane_b = manager.plan_lane(
                parent_job_id="job-2",
                workflow_id="WF-1000",
                workstream="stream-b",
                owner="agent-b",
                lane_mode="write",
                allowed_writes=["wiki/"],
            )["lane_id"]
            manager.lease_lane(lane_b, owner="agent-b", duration_minutes=10)
            manager.start_lane(lane_b)

            result = manager.validate()
            self.assertTrue(result["ok"])
            self.assertEqual(result["collisions"], [])
        finally:
            directory.cleanup()

    def test_completed_lane_does_not_collide(self):
        manager, directory = self._temporary_register()
        try:
            proof_path = manager.project_root / "temp-proof-drift.txt"
            proof_path.write_text("done")
            lane_a = manager.plan_lane(
                parent_job_id="job-1",
                workflow_id="WF-1000",
                workstream="stream-a",
                owner="agent-a",
                lane_mode="write",
                allowed_writes=["scripts/"],
            )["lane_id"]
            manager.lease_lane(lane_a, owner="agent-a", duration_minutes=10)
            manager.start_lane(lane_a)
            try:
                manager.complete_lane(lane_a, proof_artifacts=[str(proof_path)])

                lane_b = manager.plan_lane(
                    parent_job_id="job-2",
                    workflow_id="WF-1000",
                    workstream="stream-b",
                    owner="agent-b",
                    lane_mode="write",
                    allowed_writes=["scripts/sub/"],
                )["lane_id"]
                manager.lease_lane(lane_b, owner="agent-b", duration_minutes=10)
                manager.start_lane(lane_b)

                result = manager.validate()
                self.assertTrue(result["ok"])
            finally:
                proof_path.unlink(missing_ok=True)
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
