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
| Researcher | **qualified** on `gpt-6-luna` as of 2026-10-03 (canary 4/4; case 003 refused on attempt 1 and passed on the operator-approved rerun, so it is not a clean sweep) | `references/model-routing-evidence.md` → Researcher Bot qualification; lane `researcher-class-promotion-2026-09-01` |
| Implementer | **blocked** — round-4 independent review rejected the current oracle; parent probes confirmed false PASS from a hidden `.git` payload and early process exit. No real candidate run or live promotion. | `derived/model-routing/canaries/implementer-2026-09-03/review-verdict-round4.json`, `round4-probes.json`, `lane-proof.json` (v5) |
| Integrator | **not built** — no admission contract, merge proof, or qualification case | gap recorded below |
| QA | **not built** — no deterministic verdict oracle | gap recorded below |
| Challenger | **not built** — risk-trigger contract undefined | gap recorded below |
| Documentarian | **covered by existing class** — `documentation` task class in `ALLOWED_TASK_CLASSES` with lane-bounded writes | `scripts/helper_agent_router.py` |

### Model bindings (WF-1200, operator-approved 2026-09-26)

Source of truth: `state/fleet-role-registry.json` (gate-enforced; one approved
`provider/model` per helper role; a request with any other model is rejected).
The Governor remains `parent_only`: its primary binding and operator-approved
secondary routes are recorded under `roles.governor`, with secondaries in
`fallback_providers`. These entries are parent-session metadata, not additional
helper bindings. The `default` profile's Hermes `fallback_providers` controls
runtime failover; no Governor may be spawned as a helper.

| Role / profile | Binding | Registry status | Ladder |
|---|---|---|---|
| Governor / `default` | `anthropic/claude-opus-5-5` (fallback `openai-codex/gpt-6-astra`, parent-only) | parent_only (never spawned) | approves, dispatches, accepts |
| Architect / `architect` | `openai-codex/gpt-6-astra` (fallback `anthropic/claude-opus-5-5`) | admissible, read-only | specs + lane decomposition |
| Implementer / `implementer` | `ollama-cloud/deepseek-v4.1-flash` (fallback `ollama-cloud/glm-5.3-flash`) | qualification_required (write needs canary pass or an exact one-time lane exception) | repair cycle 0 only |
| Senior Engineer / `seniorengineer` | `openai-codex/gpt-6.1-sol` (fallback `anthropic/claude-sonnet-5-5`) | admissible, escalation-only | repair cycle ≥ 1 (after implementer failure) |
| QA / `qa` | `openai-codex/gpt-6.1-sol` (fallback `anthropic/claude-sonnet-5-5`) | admissible, read-only review | must differ from lane author |
| Researcher / `researcher` | `openai-codex/gpt-6-luna` (fallback `ollama-cloud/deepseek-v4.1-flash`) | qualified on 2026-10-03 (canary 4/4; case 003 refused on attempt 1, passed on rerun) | unchanged |

Escalation: implementer attempt fails acceptance → `retry` the lane (cycle 1)
→ only `senior_engineer` is admitted → a second failure (cycle 2) blocks every
role and routes to the human owner. Runbook: `continuity/WF-1200-Agent-Fleet-Roles.md`.

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
4. **One-time exception shape:** an unqualified Implementer write requires a
   `helper-lane-exception.v1` entry with kind `one_time_lane_write`, exact role,
   lane, task, write mode, `first_attempt_only=true`, and operator approval.
   Lane retry limits and terminal status prevent promotion into reusable authority.

## Fleet fallbacks (operator-approved 2026-10-03)

Every role now carries one registry-recorded fallback (`fallback_providers`), mirrored into that
profile's Hermes config. The registry owns the facts; the WF-1200 roster block is generated from it.
The admission gate binds a request to the role's primary only, so a fallback is a runtime route and
is never separately admissible. QA primary is `gpt-6.1-sol` (operator, 2026-10-03), a lane authored by gpt-6.1-sol (the Senior Engineer) is reviewed on the QA fallback `claude-sonnet-5-5`, and a lane authored by Sonnet (the Governor) on the QA primary `gpt-6.1-sol`. For role `qa` ONLY, the gate admits the primary or any registry-listed fallback, and rejects the lane author's model; author and reviewer names are compared normalised (`gpt-6.1-sol` equals `openai-codex/gpt-6.1-sol`). Run formal reviews through `scripts/qa_review_driver.py`, which pins the route and verifies the model that ACTUALLY ran from the session record, failing closed when it is unknown, differs from the pinned route, or equals the author. The gate alone checks only the declared `reviewer_model`; reviewer-differs-from-author is checked against the model that actually ran. Under automatic failover the model that runs differs from the
registry primary: the reviewer-differs-from-author rule must be checked against the model that
actually ran. Researcher requalification: primary changed `gpt-5.6-luna` to `gpt-6-luna`; evidence in
`derived/model-routing/canaries/gpt-6-luna-2026-10-03/`. Researcher fallback
`ollama-cloud/deepseek-v4.1-flash` initially failed live (no `OLLAMA_API_KEY` in that profile); on
2026-10-03 the operator approved copying the implementer profile's existing key into the researcher
profile's `.env`, and a live probe from `researcher` then answered. Rotate that key in each profile's `.env`. Canary runs need an empty fallback chain: clear it, run, restore.

## Qualification order (next bots)

Promote one role at a time; each promotion requires its own deterministic
oracle plus an accepted independent review verdict. Order is chosen by risk and
by dependency:

1. **Implementer** — round-4 review is rejected. Before another candidate run,
   resolve OS confinement and independent acceptance integrity, then manifest
   and signature enforcement; require deterministic attack replay and an
   accepted independent review. A disposable directory is not a sandbox.
   Do not promote or run the real candidate on the current host runner.
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
- Implementer round-4 review is rejected (review validator exit 2): no OS
  confinement, forgeable GREEN output, unchecked execution-time mutations,
  executable top-level `.git` payloads, inconsistent manifest validation, and
  incomplete signature checks. The parent reproduced the GREEN and `.git`
  bypasses in disposable fixtures. The runbook now removes the false
  confinement claim; implementation remains blocked pending root-cause repair.

## Change history

- 2026-10-03 — Operator rebound Senior Engineer to `openai-codex/gpt-6.1-sol`
  (OpenRouter Muse Spark 1.3 dropped; it was blocked by a provider age gate) and
  replaced the Governor's parent-only fallback with `openai-codex/gpt-6-astra`.
  Opus 5.5 stays primary; repair-cycle limits, consumed Implementer exceptions,
  and the gate's reviewer-differs-from-author rule are unchanged. Owner:
  `state/fleet-role-registry.json`; approval ref
  `operator-chat-2026-10-03-fleet-rebinding`.

- 2026-09-30 — Operator approved GPT-6.1-Sol as the secondary Governor route
  (superseded 2026-10-03 by GPT-6-Astra).
  Opus 5.5 stays primary; parent-only authority and helper bindings are unchanged.
  Approval and fallback metadata are owned by `state/fleet-role-registry.json`.

- 2026-09-26 — Built role profiles (`default`, `architect`, `implementer`,
  `seniorengineer`, `qa`; existing `researcher`) and added task-and-lane-bound
  `helper-lane-exception.v1` enforcement for the four WF-1100 phase-1 tasks.

- 2026-09-26 — WF-1200: role → model registry (`state/fleet-role-registry.json`)
  enforced by `_check_role_binding` in `scripts/helper_agent_router.py`;
  added Architect (GPT-6-Astra) and escalation-only Senior Engineer
  (Muse Spark 1.3); Opus 5.5 named governor. Implementer write stays gated.

- 2026-09-04 — Established from the fleet review; author/reviewer diversity
  gate and 2-repair-cycle escalation stop implemented in
  `scripts/helper_agent_router.py` (TDD, 94 tests green); terminal-split intent
  documented in `references/implementer-canary-runbook.md`.