#!/usr/bin/env python3
"""Governed, resumable implementation-job contracts and pickup packets."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.workspace_fingerprint import correctness_snapshot

CONTRACT_SCHEMA = "implementation-job.v1"
PICKUP_SCHEMA = "implementation-pickup.v1"
VALID_JOB_STATUSES = {"active", "blocked", "ready_to_close", "complete", "cancelled"}
VALID_PHASE_STATUSES = {"pending", "active", "blocked", "accepted", "cancelled"}


class ImplementationJobError(RuntimeError):
    """Raised when a job contract violates its execution invariants."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ImplementationJob:
    """Read and mutate one machine-readable implementation contract."""

    def __init__(self, *, project_root: Path, contract_path: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.contract_path = Path(contract_path).resolve()
        self._assert_within_workspace(self.contract_path, "contract")

    def _assert_within_workspace(self, path: Path, label: str) -> Path:
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise ImplementationJobError(f"{label} path is outside workspace: {path}") from exc
        return path

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.contract_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ImplementationJobError(f"contract is missing: {self.contract_path}") from exc
        except json.JSONDecodeError as exc:
            raise ImplementationJobError(f"contract is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ImplementationJobError("contract root must be an object")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _utc_now()
        temporary = self.contract_path.with_name(f".{self.contract_path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.contract_path)

    @staticmethod
    def _phase(payload: dict[str, Any], phase_id: str) -> dict[str, Any]:
        phase = next(
            (candidate for candidate in payload["phases"] if candidate["phase_id"] == phase_id),
            None,
        )
        if phase is None:
            raise ImplementationJobError(f"unknown phase: {phase_id}")
        return phase

    def validate(self) -> dict[str, Any]:
        """Fail closed on malformed, stale, or internally inconsistent contracts."""
        payload = self._load()
        if payload.get("schema") != CONTRACT_SCHEMA:
            raise ImplementationJobError("unsupported implementation contract schema")
        for field in ("job_id", "workflow_id", "objective", "owner", "plan_path", "plan_hash"):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise ImplementationJobError(f"contract requires non-empty {field}")
        if payload.get("status") not in VALID_JOB_STATUSES:
            raise ImplementationJobError("contract has invalid job status")

        plan_relative = Path(payload["plan_path"])
        if plan_relative.is_absolute() or ".." in plan_relative.parts:
            raise ImplementationJobError("plan path must be workspace-relative")
        plan_path = self._assert_within_workspace((self.project_root / plan_relative).resolve(), "plan")
        try:
            actual_plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise ImplementationJobError(f"plan is missing: {plan_path}") from exc
        if actual_plan_hash != payload["plan_hash"]:
            raise ImplementationJobError("plan hash does not match the governed plan")

        phases = payload.get("phases")
        if not isinstance(phases, list) or not phases:
            raise ImplementationJobError("contract requires at least one phase")
        phase_ids: list[str] = []
        for phase in phases:
            if not isinstance(phase, dict):
                raise ImplementationJobError("each phase must be an object")
            phase_id = phase.get("phase_id")
            if not isinstance(phase_id, str) or not phase_id.strip():
                raise ImplementationJobError("each phase requires phase_id")
            if phase_id in phase_ids:
                raise ImplementationJobError(f"duplicate phase_id: {phase_id}")
            phase_ids.append(phase_id)
            if phase.get("status") not in VALID_PHASE_STATUSES:
                raise ImplementationJobError(f"phase {phase_id} has invalid status")
            commands = phase.get("acceptance_commands")
            if not isinstance(commands, list) or not commands:
                raise ImplementationJobError(f"phase {phase_id} requires acceptance_commands")
            for command in commands:
                if not isinstance(command, list) or not command or not all(
                    isinstance(part, str) and part for part in command
                ):
                    raise ImplementationJobError(
                        f"phase {phase_id} acceptance commands must be argv arrays"
                    )
            if not isinstance(phase.get("allowed_writes"), list) or not phase["allowed_writes"]:
                raise ImplementationJobError(f"phase {phase_id} requires allowed_writes")
            if not isinstance(phase.get("depends_on", []), list):
                raise ImplementationJobError(f"phase {phase_id} depends_on must be a list")
            if not isinstance(phase.get("proof_receipts", []), list):
                raise ImplementationJobError(f"phase {phase_id} proof_receipts must be a list")
            if phase["status"] == "accepted":
                passing_receipts = [
                    receipt
                    for receipt in phase.get("proof_receipts", [])
                    if isinstance(receipt, dict)
                    and receipt.get("status") == "passed"
                    and isinstance(receipt.get("source_snapshot_before"), dict)
                    and isinstance(receipt.get("source_snapshot_after"), dict)
                    and receipt["source_snapshot_before"].get("source_fingerprint")
                    == receipt["source_snapshot_after"].get("source_fingerprint")
                    and receipt["source_snapshot_before"].get("source_file_count")
                    == receipt["source_snapshot_after"].get("source_file_count")
                ]
                if not passing_receipts:
                    raise ImplementationJobError(
                        f"accepted phase {phase_id} requires a passing receipt"
                    )

        phase_id_set = set(phase_ids)
        for phase in phases:
            phase_id = phase["phase_id"]
            for dependency in phase.get("depends_on", []):
                if dependency not in phase_id_set or dependency == phase_id:
                    raise ImplementationJobError(
                        f"phase {phase_id} has invalid dependency: {dependency}"
                    )
        self._validate_dependency_graph(phases)

        current_phase = payload.get("current_phase")
        if current_phase is not None and current_phase not in phase_id_set:
            raise ImplementationJobError("current_phase does not identify a known phase")
        active = [phase["phase_id"] for phase in phases if phase["status"] == "active"]
        if len(active) > 1:
            raise ImplementationJobError("only one phase may be active")
        if active and current_phase != active[0]:
            raise ImplementationJobError("current_phase must identify the active phase")

        return {
            "ok": True,
            "schema": "implementation-job-validation.v1",
            "job_id": payload["job_id"],
            "status": payload["status"],
            "phase_count": len(phases),
            "current_phase": current_phase,
            "plan_path": str(plan_path),
            "plan_hash": actual_plan_hash,
        }

    @staticmethod
    def _validate_dependency_graph(phases: list[dict[str, Any]]) -> None:
        dependencies = {
            phase["phase_id"]: list(phase.get("depends_on", [])) for phase in phases
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(phase_id: str) -> None:
            if phase_id in visiting:
                raise ImplementationJobError("phase dependency cycle detected")
            if phase_id in visited:
                return
            visiting.add(phase_id)
            for dependency in dependencies[phase_id]:
                visit(dependency)
            visiting.remove(phase_id)
            visited.add(phase_id)

        for phase_id in dependencies:
            visit(phase_id)

    def pickup_packet(self) -> dict[str, Any]:
        """Return bounded resume context without raw receipts or historical logs."""
        self.validate()
        payload = self._load()
        current_id = payload.get("current_phase")
        current = next(
            (phase for phase in payload["phases"] if phase["phase_id"] == current_id),
            None,
        )
        accepted = [
            {
                "phase_id": phase["phase_id"],
                "title": phase.get("title", phase["phase_id"]),
                "receipt_count": len(phase.get("proof_receipts", [])),
            }
            for phase in payload["phases"]
            if phase["status"] == "accepted"
        ]
        current_packet = None
        if current is not None:
            current_packet = {
                key: current.get(key)
                for key in (
                    "phase_id",
                    "title",
                    "goal",
                    "status",
                    "depends_on",
                    "allowed_writes",
                    "acceptance_commands",
                    "next_action",
                    "blocked_reason",
                )
            }
        return {
            "schema": PICKUP_SCHEMA,
            "job_id": payload["job_id"],
            "workflow_id": payload["workflow_id"],
            "objective": payload["objective"],
            "owner": payload["owner"],
            "status": payload["status"],
            "plan_path": payload["plan_path"],
            "current_phase": current_packet,
            "accepted_phases": accepted,
            "stop_lines": list(payload.get("stop_lines", [])),
            "pickup_command": (
                f"python scripts/implementation_job.py pickup --contract "
                f"{self.contract_path.relative_to(self.project_root).as_posix()}"
            ),
        }

    def start_phase(self, phase_id: str) -> dict[str, Any]:
        """Start one dependency-ready phase and persist it atomically."""
        self.validate()
        payload = self._load()
        if payload["status"] not in {"active", "blocked"}:
            raise ImplementationJobError(
                f"job status {payload['status']} does not allow phase start"
            )
        phase = self._phase(payload, phase_id)
        if phase["status"] not in {"pending", "blocked"}:
            raise ImplementationJobError(
                f"phase {phase_id} cannot start from status {phase['status']}"
            )
        active = [candidate["phase_id"] for candidate in payload["phases"] if candidate["status"] == "active"]
        if active:
            raise ImplementationJobError(f"phase {active[0]} is already active")
        by_id = {candidate["phase_id"]: candidate for candidate in payload["phases"]}
        for dependency in phase.get("depends_on", []):
            if by_id[dependency]["status"] != "accepted":
                raise ImplementationJobError(
                    f"phase {phase_id} dependency {dependency} is not accepted"
                )
        phase["status"] = "active"
        phase["started_at"] = _utc_now()
        phase.pop("blocked_reason", None)
        payload["status"] = "active"
        payload["current_phase"] = phase_id
        self._write(payload)
        return {
            "schema": "implementation-phase-transition.v1",
            "job_id": payload["job_id"],
            "phase_id": phase_id,
            "status": "active",
            "started_at": phase["started_at"],
        }

    def accept_phase(self, phase_id: str, *, timeout_seconds: float = 600.0) -> dict[str, Any]:
        """Execute phase acceptance commands and persist a fingerprinted receipt."""
        if timeout_seconds <= 0:
            raise ImplementationJobError("timeout_seconds must be positive")
        self.validate()
        payload = self._load()
        phase = self._phase(payload, phase_id)
        if phase["status"] != "active" or payload.get("current_phase") != phase_id:
            raise ImplementationJobError(f"phase {phase_id} is not the active phase")

        started_at = _utc_now()
        snapshot_before = correctness_snapshot(self.project_root)
        command_receipts: list[dict[str, Any]] = []
        commands_passed = True
        for command in phase["acceptance_commands"]:
            command_started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    shell=False,
                )
                exit_code: int | None = completed.returncode
                stdout_tail = completed.stdout[-4000:]
                stderr_tail = completed.stderr[-4000:]
                outcome = "passed" if completed.returncode == 0 else "failed"
            except subprocess.TimeoutExpired as exc:
                exit_code = None
                stdout_tail = (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else ""
                stderr_tail = (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else ""
                outcome = "timeout"
            except OSError as exc:
                exit_code = None
                stdout_tail = ""
                stderr_tail = f"{type(exc).__name__}: {exc}"[-4000:]
                outcome = "error"
            duration_ms = int((time.perf_counter() - command_started) * 1000)
            command_receipts.append(
                {
                    "argv": command,
                    "exit_code": exit_code,
                    "status": outcome,
                    "duration_ms": duration_ms,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                }
            )
            if outcome != "passed":
                commands_passed = False
                break

        snapshot_after = correctness_snapshot(self.project_root)
        source_unchanged = (
            snapshot_before["source_fingerprint"] == snapshot_after["source_fingerprint"]
            and snapshot_before["source_file_count"] == snapshot_after["source_file_count"]
        )
        if not commands_passed:
            receipt_status = "failed"
        elif not source_unchanged:
            receipt_status = "source_drift"
        else:
            receipt_status = "passed"

        receipt = {
            "schema": "implementation-acceptance-receipt.v1",
            "receipt_id": str(uuid.uuid4()),
            "phase_id": phase_id,
            "status": receipt_status,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "source_snapshot_before": snapshot_before,
            "source_snapshot_after": snapshot_after,
            "commands": command_receipts,
        }
        phase.setdefault("proof_receipts", []).append(receipt)

        if receipt_status == "passed":
            phase["status"] = "accepted"
            phase["accepted_at"] = receipt["completed_at"]
            phase.pop("blocked_reason", None)
            next_phase = self._next_ready_phase(payload)
            payload["current_phase"] = next_phase["phase_id"] if next_phase else None
            payload["status"] = "active" if next_phase else "ready_to_close"
            transition_status = "accepted"
        else:
            phase["status"] = "blocked"
            phase["blocked_reason"] = receipt_status
            payload["status"] = "blocked"
            payload["current_phase"] = phase_id
            transition_status = "blocked"

        self._write(payload)
        return {
            "schema": "implementation-phase-transition.v1",
            "job_id": payload["job_id"],
            "phase_id": phase_id,
            "status": transition_status,
            "receipt_status": receipt_status,
            "receipt_id": receipt["receipt_id"],
            "next_phase": payload.get("current_phase"),
        }

    @staticmethod
    def _next_ready_phase(payload: dict[str, Any]) -> dict[str, Any] | None:
        by_id = {phase["phase_id"]: phase for phase in payload["phases"]}
        for phase in payload["phases"]:
            if phase["status"] != "pending":
                continue
            if all(by_id[dependency]["status"] == "accepted" for dependency in phase.get("depends_on", [])):
                return phase
        return None

    def block_phase(self, phase_id: str, *, reason: str) -> dict[str, Any]:
        """Block the active phase with an explicit pickup reason."""
        if not reason.strip():
            raise ImplementationJobError("blocked phase requires a reason")
        self.validate()
        payload = self._load()
        phase = self._phase(payload, phase_id)
        if phase["status"] != "active" or payload.get("current_phase") != phase_id:
            raise ImplementationJobError(f"phase {phase_id} is not the active phase")
        phase["status"] = "blocked"
        phase["blocked_reason"] = reason.strip()
        phase["blocked_at"] = _utc_now()
        payload["status"] = "blocked"
        self._write(payload)
        return {
            "schema": "implementation-phase-transition.v1",
            "job_id": payload["job_id"],
            "phase_id": phase_id,
            "status": "blocked",
            "reason": phase["blocked_reason"],
        }

    def close(self) -> dict[str, Any]:
        """Close a job only after every phase has accepted proof."""
        self.validate()
        payload = self._load()
        incomplete = [
            phase["phase_id"]
            for phase in payload["phases"]
            if phase["status"] != "accepted"
        ]
        if incomplete:
            raise ImplementationJobError(
                f"phases are not accepted: {', '.join(incomplete)}"
            )
        payload["status"] = "complete"
        payload["current_phase"] = None
        payload["completed_at"] = _utc_now()
        self._write(payload)
        return {
            "schema": "implementation-job-transition.v1",
            "job_id": payload["job_id"],
            "status": "complete",
            "completed_at": payload["completed_at"],
        }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_contract(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--contract", type=Path, required=True)

    for name in ("validate", "status", "pickup", "close"):
        add_contract(subparsers.add_parser(name))

    start = subparsers.add_parser("phase-start")
    add_contract(start)
    start.add_argument("phase_id")

    accept = subparsers.add_parser("phase-accept")
    add_contract(accept)
    accept.add_argument("phase_id")
    accept.add_argument("--timeout-seconds", type=float, default=600.0)

    block = subparsers.add_parser("phase-block")
    add_contract(block)
    block.add_argument("phase_id")
    block.add_argument("--reason", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    try:
        job = ImplementationJob(
            project_root=arguments.project_root,
            contract_path=arguments.contract,
        )
        if arguments.command == "validate":
            result = job.validate()
        elif arguments.command == "status":
            job.validate()
            result = job._load()
        elif arguments.command == "pickup":
            result = job.pickup_packet()
        elif arguments.command == "phase-start":
            result = job.start_phase(arguments.phase_id)
        elif arguments.command == "phase-accept":
            result = job.accept_phase(
                arguments.phase_id,
                timeout_seconds=arguments.timeout_seconds,
            )
        elif arguments.command == "phase-block":
            result = job.block_phase(arguments.phase_id, reason=arguments.reason)
        elif arguments.command == "close":
            result = job.close()
        else:  # pragma: no cover - argparse owns command selection
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        if arguments.command == "phase-accept" and result.get("status") == "blocked":
            return 2
        return 0
    except ImplementationJobError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
