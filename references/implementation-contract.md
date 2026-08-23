# Disciplined implementation-job contract

Authoritative operating contract for long, multi-phase implementation work. It extends the workflow and lane control planes; it does not replace either one.

## Authority split

- `state/active_workflows.json`: workflow intent, status, owner, blockers, and external authority.
- `state/implementation-jobs/*.json`: authoritative implementation job/phase state.
- `state/concurrent-lane-register.sqlite`: execution ownership, leases, collisions, attempts, and lane events.
- `continuity/implementation-jobs/*.md`: stable human plan and rationale, pinned by SHA-256 in the job contract.
- Acceptance receipts embedded in the job contract: executed command evidence and source-fingerprint stability.
- Semantic indexes: recall only; never phase or completion authority.

## Required job fields

Every `implementation-job.v1` contract requires a stable job/workflow ID, objective, owner, status, plan path/hash, current phase, stop lines, and one or more phases.

Each phase requires:

- stable `phase_id`, title, and goal;
- explicit dependency IDs;
- status (`pending`, `active`, `blocked`, `accepted`, or `cancelled`);
- narrow `allowed_writes`;
- acceptance commands encoded as argv arrays, never shell strings;
- proof receipts;
- a bounded next action.

## Lifecycle

```text
validate contract + plan hash
→ phase-start (dependencies accepted; only one active phase)
→ execute work under a leased lane
→ phase-accept (run argv commands; capture bounded output)
→ compare before/after correctness fingerprints
→ accepted or blocked
→ deterministic pickup for the next session
→ close only after every phase is accepted
```

A failed command, timeout, unavailable executable, or source drift blocks the phase. Completion cannot be inferred from prose, an exit-code claim, or a proof path alone.

## Commands

```bash
python scripts/implementation_job.py validate --contract state/implementation-jobs/<job>.json
python scripts/implementation_job.py pickup --contract state/implementation-jobs/<job>.json
python scripts/implementation_job.py phase-start --contract state/implementation-jobs/<job>.json <phase-id>
python scripts/implementation_job.py phase-accept --contract state/implementation-jobs/<job>.json <phase-id>
python scripts/implementation_job.py phase-block --contract state/implementation-jobs/<job>.json <phase-id> --reason "..."
python scripts/implementation_job.py close --contract state/implementation-jobs/<job>.json
```

## Pickup contract

`pickup` returns only the objective, owner, job status, plan pointer, current phase, accepted-phase summaries, stop lines, exact allowed writes, acceptance commands, blocker reason, and next action. Raw historical receipts and session transcripts are excluded to keep context bounded.

A new model should use this order:

1. Read the exact pickup packet.
2. Validate workflow and lane state.
3. Read only the current plan section and required source sections.
4. Use semantic retrieval for rationale only when needed.
5. Open cited sources before acting.

## Efficiency rules

- Keep phase scope small enough for focused tests and review.
- Use deterministic exact state before semantic retrieval.
- Reuse existing workflow, lane, fingerprint, and test infrastructure.
- Store only bounded stdout/stderr tails in receipts.
- Stop acceptance on the first failing command.
- Do not load full chat history when the pickup packet is sufficient.
- Do not add new orchestration infrastructure unless the existing state and lane surfaces cannot represent the requirement.

## Verification

Focused contract tests:

```bash
python -m pytest tests/test_implementation_job.py -q
```

Shared closeout remains:

```bash
python scripts/run_checks.py --skip-smoke
python scripts/concurrent_lane_manager.py validate
```
