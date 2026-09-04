# Multi-agent fleet governance and qualification order

Authoritative owner for the workspace's bot-fleet role matrix, per-role admission
contracts, and the qualification order for promoting new bots. The deterministic
admission mechanics are owned by `references/operating-procedures.md`
(`scripts/helper_agent_router.py`); the canary harnesses are owned by
`references/researcher-canary-runbook.md` and
`references/implementer-canary-runbook.md`. This reference does not grant
authority beyond those controls.

## Current fleet state (2026-09-04)

| Role | Status | Evidence |
|---|---|---|
| Researcher | **qualified** (read-only, 5 case classes) | `references/model-routing-evidence.md` → Researcher Bot qualification; lane `researcher-class-promotion-2026-09-01` |
| Implementer | **blocked** — round-3 review rejected the v3 oracle (forged-PASS vectors: snapshot-in-reach, pyc-mirror poisoning, substring walk exclusions; plus validate-fixtures traceback and orphan-bytecode false rejection) | `derived/model-routing/canaries/implementer-2026-09-03/review-verdict-round3.json`, `lane-proof.json` (v4) |
| Integrator | **not built** — no admission contract, merge proof, or qualification case | gap recorded below |
| QA | **not built** — no deterministic verdict oracle | gap recorded below |
| Challenger | **not built** — risk-trigger contract undefined | gap recorded below |
| Documentarian | **covered by existing class** — `documentation` task class in `ALLOWED_TASK_CLASSES` with lane-bounded writes | `scripts/helper_agent_router.py` |

## Role authority matrix (workspace-enforced)

| Role | Admission shape | Write authority | May approve own work? |
|---|---|---|---|
| Researcher | read-only, `pre-implementation`, declared class | none | no |
| Implementer | write, `implementation`, leased covering lane | lane surfaces only, `write_file`/`patch` toolsets (never `terminal`) | no |
| Integrator | write, `implementation`, leased lane covering the shared integration surface | sole shared-branch writer | no self-QA waiver |
| QA | read-only, `review` class, `reviews_lane` + `reviewer_model` declared | test artifacts only | no |
| Challenger | read-only, `review` class, risk-triggered invocation | none | no |

Gate-enforced invariants (all fail closed in `admit_request`):

1. **Author/reviewer diversity:** a `review`-class spawn scoped to a lane must
   declare `reviewer_model`, and it must differ from the lane's recorded author
   model (`expected_model`); a reviewed lane without author attribution cannot
   be reviewed by an unknown relationship.
2. **Bounded repair loop:** a lane at ≥2 recorded repair cycles
   (`retry_count`) admits no further write spawns — the parent must escalate to
   the human owner.
3. **No shell on live lanes:** `terminal`/`execute_code` are rejected in every
   mode; a canary sandbox terminal is a different trust domain (see
   `references/implementer-canary-runbook.md`, terminal-toolset split).

## Qualification order (next bots)

Promote one role at a time; each promotion requires its own deterministic
oracle plus an accepted independent review verdict. Order is chosen by risk and
by dependency:

1. **Implementer** — finish the in-flight loop: round-3 independent review of
   the hardened v3 oracle; only an accepted verdict unblocks the first live
   candidate run. Until then Implementer stays sandbox-only.
2. **QA (next)** — read-only verdict role with a deterministic oracle: given a
   frozen diff + acceptance-command output, produce a `qa-verdict.v1`
   (pass/fail + defects with `path:line` citations), verified by an exact
   fixture oracle in the same style as the researcher canaries. Admission
   always uses `reviews_lane` + `reviewer_model` (diversity gate above).
3. **Integrator (after QA)** — sole shared-branch writer. Needs: an
   `integration` admission shape, a merge-proof contract (the lane's
   `--proof` artifacts must include the merge commit + deterministic gate
   output), and a qualification case where merging two disjoint lanes' work is
   verified against collision rules.
4. **Challenger (last, risk-triggered)** — read-only counterexample role;
   invoke only on explicit triggers (author/reviewer disagreement, security
   review, architecture change), never as a routine reviewer.

## Definition of done for each promotion

- Deterministic canary oracle for the role's bounded task class, with its own
  fixture pack and fail-closed CLI (mirror
  `scripts/researcher_canary_harness.py` / `scripts/implementer_canary_harness.py`).
- One accepted independent review verdict through the corresponding
  `review`-class admission (diversity gate applies to it too).
- A lane with proof artifacts recording the qualification, closed via
  `concurrent_lane_manager.py complete --proof`.
- Human operator approval recorded before any live-route change.

## Open gaps (honest register)

- No Integrator admission shape or merge-proof contract exists yet.
- No QA verdict oracle or fixture pack exists yet.
- Challenger risk-trigger set is undefined (which disagreement classes fire a
  challenger review).
- Implementer round-3 review outcome is pending; no accepted verdict exists as
  of this writing.

## Change history

- 2026-09-04 — Established from the fleet review; author/reviewer diversity
  gate and 2-repair-cycle escalation stop implemented in
  `scripts/helper_agent_router.py` (TDD, 94 tests green); terminal-split intent
  documented in `references/implementer-canary-runbook.md`.