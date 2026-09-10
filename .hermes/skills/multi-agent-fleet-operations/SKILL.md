---
name: multi-agent-fleet-operations
description: "Use when dispatching helper/fleet agents via the gate."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [multi-agent, fleet, admission-gate, helper-agents, lanes, canary, qualification]
    category: workflow
---

# Multi-Agent Fleet Operations

End-to-end procedure for dispatching helper agents in this workspace: admission
gate → lane lease → spawn → untrusted-output verification → verdict recording →
lane closeout.

This skill **routes and sequences**; it does not own the underlying contracts:

- Admission mechanics, toolset allowlists, fail-closed behavior:
  `references/operating-procedures.md` (helper-agent rules) and
  `scripts/helper_agent_router.py`.
- Role authority matrix, promotion/qualification order, open gaps:
  `references/multi-agent-fleet-governance.md`.
- Canary qualification harnesses: `references/researcher-canary-runbook.md`,
  `references/implementer-canary-runbook.md`.
- Lane planning/leasing/closeout: `concurrent-lane-control-plane` skill and
  `scripts/concurrent_lane_manager.py`.
- External CLI agents (Claude Code, Codex, OpenCode): their own skills.

## When to use

- Spawning any helper/subagent that reads or writes workspace surfaces.
- Running a `review`-class spawn against a lane.
- Qualifying or promoting a new bot role (Researcher, Implementer, QA, …).
- Recording review verdicts and qualification evidence.

## Non-scope

- Does not grant authority beyond the admission gate and lane register.
- Does not replace the deterministic gate — never self-certify a spawn.
- Does not cover non-fleet parallelism inside one session (that is ordinary
  tool batching).

## Required inputs

- `task_id`, `task_class`, `phase`, `mode` (`read-only` / `write` / `review`).
- `objective`, exact `scope`, `allowed_toolsets`, `allowed_writes`.
- `max_duration_minutes`, `owner`, and `lane_id` for write mode.
- For `review` class scoped to a lane: `reviews_lane` + `reviewer_model`
  (must differ from the lane's recorded author model).

## Preconditions

- Lane register readable; no overlapping active write lane on the target surfaces.
- Parent job/workflow identity known.
- Current fleet qualification state checked in
  `references/multi-agent-fleet-governance.md` — do not assign a role a task
  class it is not qualified for. (2026-09 snapshot: Researcher qualified;
  Implementer blocked pending round-4 findings; QA/Integrator/Challenger not
  built. Re-check — this snapshot goes stale.)

## Procedure

1. **Classify** the task into a declared `task_class`; pick the cheapest mode
   that covers it (prefer `read-only`; `write` only with a leased lane).
2. **Build** a `helper-agent-request.v1` JSON with the fields above.
3. **Gate it** — mandatory before every spawn:

   ```bash
   python scripts/helper_agent_router.py admit --request <helper-request.json>
   ```

   Parse the **stdout JSON verdict**, not the exit code alone (`--help` exits 0).
   Exit 0 + admit verdict → proceed. Exit 2 → rejection: read `reasons`, fix the
   request, never bypass.
4. **Lease** the lane (write mode): plan → lease → start via
   `scripts/concurrent_lane_manager.py` per `concurrent-lane-control-plane`.
5. **Spawn bounded**: `delegate_task` for quick subtasks; a separate `hermes`
   process (tmux/PTY or `hermes chat -q`) for long missions. Pass objective,
   scope, allowed toolsets/writes, stop conditions, and verification
   requirements explicitly.
6. **Verify output as untrusted**: require evidence — changed-file lists, test
   output, source references. A completion message alone is not success.
7. **Record**: append verdict/proof artifacts; close the lane with
   `complete <lane_id> --proof <artifact>`; run `status --validate`.

## Decision points

- **Gate rejection**: fix the request shape; do not retry unchanged, do not
  hand the task to an ungated path.
- **Lane at ≥2 repair cycles** (`retry_count`): no further write spawns —
  escalate to the human owner.
- **Review spawn**: confirm author/reviewer model diversity before admitting;
  a lane without author attribution fails closed — attribute first.
- **Long vs short**: bounded subtask → `delegate_task`; hours-long autonomous
  mission → spawned `hermes` process; scheduled recurring work → cron job, not
  a spawn.

## Validation

- Gate verdict JSON recorded for every spawn.
- Lane `status --validate` clean after closeout; proof artifacts present.
- Focused tests green for any touched surface; `python scripts/run_checks.py`
  for shared surfaces.
- Qualification claims require: deterministic canary oracle + accepted
  independent review verdict + lane proof + recorded operator approval
  (definition of done in `references/multi-agent-fleet-governance.md`).

## Failure handling

- Exit 2 from the gate is a **rejection with reasons**, including corrupt
  register, malformed request, or internal error (fail-closed backstop). Treat
  as stop-and-fix, never retry blindly.
- Distinguish gate rejection (request/contract problem) from helper failure
  (execution problem) from verification failure (evidence problem).
- Record recurring rejections for fleet-governance review.

## Rollback / recovery

- A mis-dispatched write spawn: stop the lane (`cancelled` with evidence),
  restore via git / curator snapshot, and record the incident in the lane
  register.
- A bad promotion: revert the role's qualification state in
  `references/multi-agent-fleet-governance.md` and record the cause.

## Security and authority

- `terminal`/`execute_code` are never admissible in helper requests; a shell is
  not lane-bounded.
- Never-admissible toolset families (cronjob, computer use, desktop UI, memory,
  skills, delegation, MCP) cannot be renamed past the gate — do not attempt to.
- Operator approval precedes any live-route change from a promotion.

## References

- `references/operating-procedures.md` — admission contract (authoritative).
- `references/multi-agent-fleet-governance.md` — role matrix, qualification order.
- `references/researcher-canary-runbook.md`, `references/implementer-canary-runbook.md`.
- `references/implementation-contract.md`.
- `scripts/helper_agent_router.py`, `scripts/concurrent_lane_manager.py`,
  `scripts/researcher_canary_harness.py`, `scripts/implementer_canary_harness.py`,
  `scripts/implementation_job.py`.

## Change history

- v1.0.0 — Initial fleet-operations skill: dispatch sequence, gate discipline,
  review diversity, repair-cycle escalation, qualification definition of done.
  Extracted from operating procedures and fleet governance into a routable skill
  after the round-1–4 implementer review cycles showed the procedure had no
  skill-level owner.
