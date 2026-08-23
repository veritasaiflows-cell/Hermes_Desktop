from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from scripts.implementation_job import ImplementationJob, ImplementationJobError


class ImplementationJobTests(unittest.TestCase):
    def _job(self, root: Path) -> ImplementationJob:
        plan = root / "continuity" / "implementation-jobs" / "semantic-upgrade.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# Semantic Upgrade\n\n## P0\nBuild the contract.\n", encoding="utf-8")
        plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
        contract = root / "state" / "implementation-jobs" / "semantic-upgrade.json"
        contract.parent.mkdir(parents=True, exist_ok=True)
        contract.write_text(
            json.dumps(
                {
                    "schema": "implementation-job.v1",
                    "job_id": "semantic-upgrade",
                    "workflow_id": "WF-1000",
                    "objective": "Improve long-work retrieval",
                    "owner": "agent-main",
                    "status": "active",
                    "plan_path": "continuity/implementation-jobs/semantic-upgrade.md",
                    "plan_hash": plan_hash,
                    "current_phase": "P0",
                    "stop_lines": ["Do not skip acceptance"],
                    "phases": [
                        {
                            "phase_id": "P0",
                            "title": "Contract",
                            "goal": "Build the contract",
                            "status": "pending",
                            "depends_on": [],
                            "allowed_writes": ["scripts/implementation_job.py"],
                            "acceptance_commands": [["python", "-c", "print('ok')"]],
                            "proof_receipts": [],
                            "next_action": "Start P0",
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return ImplementationJob(project_root=root, contract_path=contract)

    def _two_phase_job(self, root: Path) -> ImplementationJob:
        job = self._job(root)
        payload = json.loads(job.contract_path.read_text(encoding="utf-8"))
        payload["phases"].append(
            {
                "phase_id": "P1",
                "title": "Retrieval",
                "goal": "Upgrade retrieval",
                "status": "pending",
                "depends_on": ["P0"],
                "allowed_writes": ["scripts/vector_memory_index.py"],
                "acceptance_commands": [["python", "-c", "print('p1')"]],
                "proof_receipts": [],
                "next_action": "Start P1",
            }
        )
        job.contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return job

    def test_valid_contract_produces_bounded_pickup_packet(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))

            validation = job.validate()
            pickup = job.pickup_packet()

            self.assertTrue(validation["ok"])
            self.assertEqual(pickup["schema"], "implementation-pickup.v1")
            self.assertEqual(pickup["job_id"], "semantic-upgrade")
            self.assertEqual(pickup["current_phase"]["phase_id"], "P0")
            self.assertEqual(pickup["current_phase"]["acceptance_commands"], [["python", "-c", "print('ok')"]])
            self.assertNotIn("proof_receipts", pickup["current_phase"])

    def test_plan_hash_drift_fails_validation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)
            (root / "continuity" / "implementation-jobs" / "semantic-upgrade.md").write_text(
                "changed\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ImplementationJobError, "plan hash"):
                job.validate()

    def test_start_phase_persists_active_state(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))

            started = job.start_phase("P0")
            persisted = json.loads(job.contract_path.read_text(encoding="utf-8"))

            self.assertEqual(started["status"], "active")
            self.assertEqual(persisted["current_phase"], "P0")
            self.assertEqual(persisted["phases"][0]["status"], "active")
            self.assertTrue(persisted["phases"][0]["started_at"].endswith("Z"))

    def test_start_phase_rejects_unaccepted_dependency(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._two_phase_job(Path(directory))

            with self.assertRaisesRegex(ImplementationJobError, "dependency P0"):
                job.start_phase("P1")

    def test_accept_phase_executes_commands_and_advances_job(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._two_phase_job(root)
            job.start_phase("P0")

            result = job.accept_phase("P0", timeout_seconds=30)
            persisted = json.loads(job.contract_path.read_text(encoding="utf-8"))
            phase = persisted["phases"][0]

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(phase["status"], "accepted")
            self.assertEqual(phase["proof_receipts"][-1]["status"], "passed")
            self.assertEqual(phase["proof_receipts"][-1]["commands"][0]["exit_code"], 0)
            self.assertEqual(persisted["current_phase"], "P1")
            self.assertEqual(persisted["status"], "active")

    def test_failed_acceptance_blocks_phase(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)
            payload = json.loads(job.contract_path.read_text(encoding="utf-8"))
            payload["phases"][0]["acceptance_commands"] = [["python", "-c", "raise SystemExit(7)"]]
            job.contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            job.start_phase("P0")

            result = job.accept_phase("P0", timeout_seconds=30)
            persisted = json.loads(job.contract_path.read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(persisted["status"], "blocked")
            self.assertEqual(persisted["phases"][0]["status"], "blocked")
            self.assertEqual(persisted["phases"][0]["proof_receipts"][-1]["status"], "failed")

    def test_source_drift_during_acceptance_blocks_phase(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)
            payload = json.loads(job.contract_path.read_text(encoding="utf-8"))
            payload["phases"][0]["acceptance_commands"] = [
                [
                    "python",
                    "-c",
                    "from pathlib import Path; Path('scripts').mkdir(exist_ok=True); Path('scripts/drift.py').write_text('x')",
                ]
            ]
            job.contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            job.start_phase("P0")

            result = job.accept_phase("P0", timeout_seconds=30)
            persisted = json.loads(job.contract_path.read_text(encoding="utf-8"))
            receipt = persisted["phases"][0]["proof_receipts"][-1]

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(receipt["status"], "source_drift")
            self.assertNotEqual(
                receipt["source_snapshot_before"]["source_fingerprint"],
                receipt["source_snapshot_after"]["source_fingerprint"],
            )

    def test_close_requires_all_phases_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))

            with self.assertRaisesRegex(ImplementationJobError, "not accepted"):
                job.close()

    def test_close_marks_fully_accepted_job_complete(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))
            job.start_phase("P0")
            job.accept_phase("P0", timeout_seconds=30)

            result = job.close()
            persisted = json.loads(job.contract_path.read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "complete")
            self.assertEqual(persisted["status"], "complete")
            self.assertTrue(persisted["completed_at"].endswith("Z"))

    def test_validation_rejects_accepted_phase_without_passing_receipt(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))
            payload = json.loads(job.contract_path.read_text(encoding="utf-8"))
            payload["phases"][0]["status"] = "accepted"
            payload["current_phase"] = None
            job.contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            with self.assertRaisesRegex(ImplementationJobError, "passing receipt"):
                job.validate()

    def test_validation_rejects_dependency_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._two_phase_job(Path(directory))
            payload = json.loads(job.contract_path.read_text(encoding="utf-8"))
            payload["phases"][0]["depends_on"] = ["P1"]
            job.contract_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            with self.assertRaisesRegex(ImplementationJobError, "dependency cycle"):
                job.validate()

    def test_block_phase_records_reason_for_pickup(self) -> None:
        with TemporaryDirectory() as directory:
            job = self._job(Path(directory))
            job.start_phase("P0")

            result = job.block_phase("P0", reason="operator review required")
            pickup = job.pickup_packet()

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(pickup["status"], "blocked")
            self.assertEqual(pickup["current_phase"]["blocked_reason"], "operator review required")

    def test_direct_cli_pickup_returns_json_packet(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            job = self._job(root)

            completed = subprocess.run(
                [
                    "python",
                    str(Path(__file__).parents[1] / "scripts" / "implementation_job.py"),
                    "--project-root",
                    str(root),
                    "pickup",
                    "--contract",
                    str(job.contract_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["schema"], "implementation-pickup.v1")


if __name__ == "__main__":
    unittest.main()
