#!/usr/bin/env python3
"""Mandatory, read-only control-plane preflight for workflow entry points.

Workflow implementations call :func:`preflight_workflow` before every canonical
write. The preflight refuses stale route state, non-active effective status,
blockers, owner-only gates, stop lines, and invalid lane leases/write surfaces.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable

from scripts import workflow_router


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
DEFAULT_INDEX_PATH = PROJECT_ROOT / "tmp" / "workflow-routing-index.json"
DEFAULT_LANE_REGISTER = DEFAULT_STATE_DIR / "concurrent-lane-register.sqlite"


class WorkflowPreflightError(RuntimeError):
    """Raised when a workflow invocation is not safe to start."""


def preflight_workflow(
    selector: str,
    *,
    write: bool,
    lane_id: str | None = None,
    lane_owner: str | None = None,
    write_targets: Iterable[str | Path] = (),
    project_root: Path = PROJECT_ROOT,
    state_dir: Path = DEFAULT_STATE_DIR,
    index_path: Path = DEFAULT_INDEX_PATH,
    lane_register_path: Path = DEFAULT_LANE_REGISTER,
) -> dict[str, Any]:
    """Validate routing state and, for writes, a current scoped lane lease.

    The operation intentionally does not regenerate any control-plane artifact.
    A caller must refresh routing state through the explicit router command before
    a write can be admitted.
    """

    route = workflow_router.route_workflows(
        selector=selector,
        answer="all",
        validate=True,
        write_index=False,
        write_capsules=False,
        project_root=project_root,
        state_dir=state_dir,
        index_path=index_path,
    )
    if route.get("unsafe_to_trust") or route.get("routing_index_stale"):
        raise WorkflowPreflightError(
            "Workflow routing state is stale or unsafe to trust; refresh it before execution."
        )

    capsule = route.get("workflow")
    if not isinstance(capsule, dict):
        raise WorkflowPreflightError("Workflow routing did not return one workflow capsule.")

    if not write:
        return {**capsule, "preflight_mode": "dry_run"}

    effective_status = capsule.get("effective_status")
    if effective_status != "active":
        raise WorkflowPreflightError(
            f"Workflow {capsule['workflow_id']} is {effective_status!r}; canonical writes require 'active'."
        )
    if capsule.get("owner_action_required"):
        raise WorkflowPreflightError(
            f"Workflow {capsule['workflow_id']} requires an owner action before canonical writes."
        )
    if capsule.get("blockers"):
        raise WorkflowPreflightError(
            f"Workflow {capsule['workflow_id']} has active blockers; canonical writes are refused."
        )
    if capsule.get("stop_lines"):
        raise WorkflowPreflightError(
            f"Workflow {capsule['workflow_id']} has active stop lines; canonical writes are refused."
        )

    target_list = tuple(str(target) for target in write_targets)
    _validate_lane_lease(
        workflow_id=str(capsule["workflow_id"]),
        lane_id=lane_id,
        lane_owner=lane_owner,
        write_targets=target_list,
        project_root=project_root,
        lane_register_path=lane_register_path,
    )
    return {
        **capsule,
        "preflight_mode": "write",
        "lane_id": lane_id,
        "write_targets": list(target_list),
    }


def _validate_lane_lease(
    *,
    workflow_id: str,
    lane_id: str | None,
    lane_owner: str | None,
    write_targets: tuple[str, ...],
    project_root: Path,
    lane_register_path: Path,
) -> None:
    if not lane_id:
        raise WorkflowPreflightError("Canonical writes require a lane_id with an active running lease.")
    if not lane_owner:
        raise WorkflowPreflightError("Canonical writes require the leased lane_owner.")
    if not write_targets:
        raise WorkflowPreflightError("Canonical writes require explicit write_targets for lease verification.")

    register = lane_register_path if lane_register_path.is_absolute() else project_root / lane_register_path
    if not register.is_file():
        raise WorkflowPreflightError(f"Lane register is unavailable: {register}")

    try:
        connection = sqlite3.connect(f"file:{register.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM lanes WHERE lane_id = ?", (lane_id,)).fetchone()
    except sqlite3.Error as error:
        raise WorkflowPreflightError(f"Lane register cannot be read: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    if row is None:
        raise WorkflowPreflightError(f"No lane registered with lane_id {lane_id!r}.")
    if row["workflow_id"] != workflow_id:
        raise WorkflowPreflightError("Lane workflow_id does not match the selected workflow.")
    if row["owner"] != lane_owner:
        raise WorkflowPreflightError("Lane owner does not match the active lease owner.")
    if row["status"] != "running":
        raise WorkflowPreflightError("Lane must be in running status before canonical writes.")
    if row["lane_mode"] == "read-only":
        raise WorkflowPreflightError(
            "Lane is read-only; canonical writes are not allowed from this lane."
        )

    expires_at = _parse_utc(row["lease_expires_at"])
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        raise WorkflowPreflightError("Lane lease is absent, invalid, or expired.")

    allowed_writes = _json_path_list(row["allowed_writes_json"], "allowed_writes_json")
    forbidden_writes = _json_path_list(row["forbidden_writes_json"], "forbidden_writes_json")
    for target in write_targets:
        if any(_surface_contains(surface, target, project_root) for surface in forbidden_writes):
            raise WorkflowPreflightError(f"Write target {target!r} is forbidden by the lane lease.")
        if not any(_surface_contains(surface, target, project_root) for surface in allowed_writes):
            raise WorkflowPreflightError(
                f"Write target {target!r} is outside the lane's declared write surface."
            )


def _json_path_list(raw_value: str, field_name: str) -> list[str]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise WorkflowPreflightError(f"Invalid {field_name} in lane register.") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowPreflightError(f"Invalid {field_name} in lane register.")
    return value


def _surface_contains(surface: str, target: str, project_root: Path) -> bool:
    root = project_root.resolve()
    surface_path = _relative_workspace_path(surface, root)
    target_path = _relative_workspace_path(target, root)
    return target_path == surface_path or target_path.startswith(surface_path.rstrip("/") + "/")


def _relative_workspace_path(value: str, project_root: Path) -> str:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError as error:
        raise WorkflowPreflightError(f"Path is outside the workspace: {value!r}") from error


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
