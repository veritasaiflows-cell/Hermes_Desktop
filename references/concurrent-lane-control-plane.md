# Concurrent lane and write-leasing control plane

This control plane is now implemented by `scripts/concurrent_lane_manager.py` and provides
collision-safe, resumable concurrency for work happening across multiple agents or subprocesses.

## Purpose

- Prevent two workers from editing the same filesets at the same time.
- Record ownership, attempts, and lease timing so handoffs are deterministic.
- Preserve an audit trail of state transitions and proofs of completion.

## Core model

### Parent job

A **parent job** represents the higher-level request (`parent_job_id`) and is bound to a
`workflow_id` + `workstream`.

- `parent_jobs.job_id` is authoritative for that work chunk.
- Multiple lanes can share a parent workflow through separate `lane_id` values.

### Lane

A **lane** is the unit of execution and concurrency control.

- `lane_id` defaults to `<workflow_id>::<workstream>` but can be overridden.
- `allowed_writes`: normalized workspace paths this lane may modify.
- `forbidden_writes`: normalized paths this lane may not touch (policy hardening extension).
- `status`: one of
  - `planned`
  - `leased`
  - `running`
  - `blocked`
  - `cancelled`
  - `complete`
- `lane_mode`
  - `write` (default) can write declared surfaces.
  - `read-only` forbids `allowed_writes`.

## Lease lifecycle (implemented transitions)

- `planned -> leased`
- `leased -> running`
- `running -> blocked`
- `running -> cancelled`
- `running -> complete`
- `planned/leased/blocked -> blocked`
- `planned/leased/running/blocked -> cancelled`
- `blocked -> planned` (manual recovery path)

`running` lanes should always have both `start_at` and active lease metadata.

## Collision policy

At `plan` time and `lease` time, the manager checks all active write lanes
(`planned`, `leased`, `running`) for overlapping `allowed_writes`.
A collision is flagged when:

1. Paths are identical, or
2. One path is a parent/child of another (directory overlap).

Collision events are emitted as hard validation failures and lane transitions are blocked.

### Path rules

- Inputs must be workspace-relative and non-globbed.
- Traversal (`..`) is rejected.
- All paths are normalized before persistence (absolute with canonical separators).
- A few protected policy surfaces are treated as forbidden by default:
  - `.env`
  - `.git/`
  - `AGENTS.md` and `GOVERNANCE.md`
  - `canonical/schema.sql`
  - `credentials/`
  - `config/`
  - active workflow and override control records
  - register DB path.
- Parent jobs cannot mix workflow IDs; a reused parent ID must retain its
  established workflow (and any explicitly supplied parent owner).
- Lease durations must be positive and finite. Expired leased/running lanes are
  hard validation failures, not advisory warnings.

## Audit and durability

All state is stored in SQLite at:

- `state/concurrent-lane-register.sqlite`

It is schema-initialized on first use and writes are revisioned (`meta.revision`).
Every mutation appends an entry to `lane_events`.

## CLI setup

1. Plan a lane before work:
   ```bash
   python scripts/concurrent_lane_manager.py plan \
     --parent-job-id wf-1000-job \
     --workflow-id WF-1000 \
     --workstream research-pass \
     --owner agent-a \
     --allowed-write derived/results.json
   ```
2. Grant a lease before edits:
   ```bash
   python scripts/concurrent_lane_manager.py lease WF-1000::research-pass --owner agent-a
   ```
3. Start execution:
   ```bash
   python scripts/concurrent_lane_manager.py start WF-1000::research-pass
   ```
4. Mark completion with proof:
   ```bash
   python scripts/concurrent_lane_manager.py complete WF-1000::research-pass --proof derived/proof.json
   ```

## Validation and status checks

- `status --validate` prints current lanes and runs consistency checks.
- `validate` prints hard failures, warnings, collisions, and expired leases.
  It also rejects missing parent jobs and lane/parent workflow mismatches.

`validate` returns non-zero on hard failure and is suitable for CI or pre-advance guards.

## Change history

- 2026-08-15: Added first implementation for concurrent lane planning, lease assignment,
  collision checks, and proof-required completion. Tests added at
  `tests/test_concurrent_lane_manager.py` and integrated into `run_checks.py` path.
