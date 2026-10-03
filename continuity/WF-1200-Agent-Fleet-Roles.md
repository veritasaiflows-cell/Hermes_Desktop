# Workflow 1200 continuity note

## Objective
Role-bound multi-model agent fleet. Every helper spawn declares a `role`; the
admission gate (`scripts/helper_agent_router.py`) checks the role against
`state/fleet-role-registry.json` and binds it to exactly one approved model, so
cheap models do grunt work and expensive models govern, design, and debug.

## Origin
Operator decision 2026-09-26: offload implementation tokens from the governor
model to cheaper coding models, with distinct models per purpose instead of a
single `delegation.model`.

## Role roster

**Authoritative owner: `state/fleet-role-registry.json`.** The table below is generated from it and
verified by A19 (`scripts/check_note_state_drift.py`); it cannot silently disagree with the gate. Hand-written
status, probe, and rebinding commentary follows the generated block.

<!-- BEGIN GENERATED: fleet-role-roster (source: state/fleet-role-registry.json; do not edit by hand) -->

Generated from `state/fleet-role-registry.json` (the owner; the admission gate
enforces the registry, not this table). Regenerate with
`python scripts/fleet_roster_block.py --write`. Do not edit by hand.

| Role | Profile | Binding | Registry status | Modes | Lane retry gate | Fallback chain |
|---|---|---|---|---|---|---|
| governor | `default` | `anthropic/claude-opus-5-5` | parent_only | - | - | `openai-codex/gpt-6-astra` |
| architect | `architect` | `openai-codex/gpt-6-astra` | admissible | read-only | - | `anthropic/claude-opus-5-5` |
| implementer | `implementer` | `ollama-cloud/deepseek-v4.1-flash` | qualification_required | read-only, write | repair cycle <= 0 | `ollama-cloud/glm-5.3-flash` |
| senior_engineer | `seniorengineer` | `openai-codex/gpt-6.1-sol` | admissible | read-only, write | repair cycle >= 1 | `anthropic/claude-sonnet-5-5` |
| qa | `qa` | `anthropic/claude-sonnet-5-5` | admissible | read-only | - | `openai-codex/gpt-6.1-sol` |
| researcher | `researcher` | `openai-codex/gpt-6-luna` | qualified | read-only | - | `ollama-cloud/deepseek-v4.1-flash` |

Governor parent-only fallback chain: `openai-codex/gpt-6-astra`.

Recorded one-time lane exceptions (lane register owns their status):
- `implementer` / `wf1100-p1-client-registry`
- `implementer` / `wf1100-p1-business-schema`
- `implementer` / `wf1100-p1-csv-import`
- `implementer` / `wf1100-p1-demo-datasets`

<!-- END GENERATED: fleet-role-roster -->

Dated probe status (hand-written; sessions and results in the artifact cited below): all five helper
profiles were reachable on 2026-10-03. The architect replied but refused a bare ping, the implementer was
pinged read-only, and the Senior Engineer profile was probed after its
2026-10-03 rebinding.

Probe sessions and results are recorded in
`derived/lane-proofs/fleet-rebinding-2026-10-03/fleet-live-probe-2026-10-03.json`.

Initial explicit-provider smoke test 2026-09-26 (20:06 MST) recorded successful
responses from all four new provider/model routes (sessions
20260926_200559_dcec69, _200612_cecdad, _200621_9dbf5b, _200628_e60021).

## Senior Engineer rebinding (operator-approved 2026-10-03)

The operator dropped OpenRouter `meta/muse-spark-1.3` as a coder. Its route
returned HTTP 403 (account-holder 18+ age confirmation) on every dispatch and
the operator chose not to pursue it. The Senior Engineer is now
`openai-codex/gpt-6.1-sol` on the existing `seniorengineer` profile. Escalation-only
semantics are unchanged: the gate admits it only at lane repair cycle >= 1.
Approval reference: `operator-chat-2026-10-03-fleet-rebinding`.

Live evidence 2026-10-03: `hermes -p seniorengineer chat` answered with no
provider override (session `20261003_120053_985e20`).

Known concentration (corrected 2026-10-03): QA is `anthropic/claude-sonnet-5-5`, the same
model as the Governor session that has been authoring lane code. The gate compares the declared
author model with the reviewer model, so unverified author labels can hide this; treat such
reviews as same-model until the author model is recorded from the session, not typed.

## Per-role fallbacks and Researcher requalification (operator-approved 2026-10-03)

Every helper role now has one fallback in `state/fleet-role-registry.json`
(`roles.<role>.fallback_providers`, shown in the generated roster above) and in that profile's
Hermes config. Approval reference: `operator-chat-2026-10-03-fleet-fallbacks`. The admission gate
binds to each role's primary only; a fallback is a runtime route, never separately admissible.
Live probe 2026-10-03: all ten primary/fallback routes answered. The Researcher fallback
`ollama-cloud/deepseek-v4.1-flash` first failed (no `OLLAMA_API_KEY` in the researcher profile); the
operator approved copying the implementer profile's key into the researcher `.env`, then it answered
from the `researcher` profile. The automatic failover itself is untested for every role.

The Researcher primary moved to `openai-codex/gpt-6-luna`; status is `qualified` as of 2026-10-03. Canary requalification (new manifests `gpt-6-luna-canary-00x`, evidence in
`derived/model-routing/canaries/gpt-6-luna-2026-10-03/`): 002, 004, 005 passed first attempt; 003
refused on attempt 1 (no JSON, said the request lacked an admitted task request) and passed on the
operator-approved rerun (attempt 1 kept as `...-attempt1`).

Standing rule (from the lane's independent review, 2026-10-03): No QA review of a lane authored by gpt-6.1-sol may run on the QA fallback (it would be the author's own model); reviewer-differs-from-author is checked against the model that actually ran. Qualified on 4 of 4 cases with one
first-attempt miss, so expect occasional refusals on bare prompts.

## Governor fallback (operator-approved 2026-10-03; supersedes 2026-09-30)

Opus 5.5 remains the primary Governor. The operator replaced the secondary
route: the Governor's automatic fallback is now `openai-codex/gpt-6-astra`
(previously GPT-6.1-Sol, approved 2026-09-30). The route is recorded in
`state/fleet-role-registry.json` under `roles.governor.fallback_providers`; the
`default` Hermes profile configures it as its first and only fallback. Approval
reference: `operator-chat-2026-10-03-fleet-rebinding`.

This is a parent-session fallback, not a helper-role promotion. The Governor
remains `parent_only`. The Astra route was probed as a direct endpoint
(session `20261003_115836_9f11b8`) and the fallback chain is configured, but
**automatic failover from Anthropic was not induced and is untested.** Under
failover the parent runs on the same model as the
Architect, so the Architect's read-only specs are not independent of a
fallback Governor's acceptance; the independent QA review remains mandatory.
Earlier evidence for the 2026-09-30 route is retained under
`derived/lane-proofs/governor-secondary-2026-09-30/`; current evidence is under
`derived/lane-proofs/fleet-rebinding-2026-10-03/`.

## Escalation ladder (gate-enforced via lane `retry_count`)
1. Implementer attempts a lane (repair cycle 0). Only implementer may write at cycle 0;
   senior_engineer is refused (escalation-only).
2. Lane fails QA/acceptance → governor runs `concurrent_lane_manager.py retry`
   (cycle 1). The gate now refuses implementer and admits only `senior_engineer`
   (GPT-6.1-Sol) to debug and repair.
3. A second failure (cycle 2) blocks write spawns of every role; escalate to the
   human operator (existing bounded-repair invariant).

## Dispatch recipe (governor)
1. Write `helper-agent-request.v1` JSON with `role` + `model` = registry binding.
2. `python3 scripts/helper_agent_router.py admit --request <file>` → must exit 0.
3. Launch: `hermes -p <profile> chat -Q -q "<spec>"`. Implementer and Senior
   Engineer profiles expose only the file toolset; Governor runs all commands.
4. QA spawn (`role=qa`, `reviews_lane`, `reviewer_model`) before acceptance.

## Current state
- effective_status: route_only.
- Role registry and gate role/model binding are implemented in lane
  `fleet-role-routing-2026-09-26`.
- Role profiles are built and recorded by `profile` in `state/fleet-role-registry.json`.
- Implementer remains unqualified. Its four WF-1100 phase-1 write tasks each have
  a `helper-lane-exception.v1` permission bound to one task, one lane, write mode,
  repair cycle 0, and the operator approval recorded on 2026-09-26.
- The four one-time Implementer exceptions were consumed by the initial
  WF-1100 phase-1 attempts. All four chats timed out after 900 seconds; every
  acceptance command exited 4 because its required test target was missing.
  The lanes are blocked with incident
  `implementer_dispatch_timeout_incomplete_outputs`; proof:
  `derived/lane-proofs/wf1100-phase1-implementer-timeout-2026-09-27.json`.
- Three partial source files were left behind, but no lane is accepted and no
  partial output is trusted.
- Live lane state is owned by the lane register; this note does not restate it.
  Read it with `python scripts/concurrent_lane_manager.py status --validate`.
- The Senior Engineer route is verified live (2026-10-03). No repair-cycle-1
  lane has been opened yet.

## Current blockers
- The four WF-1100 one-time Implementer lane exceptions are consumed and their attempts are blocked.

## Out of scope
- Broadly qualifying Implementer or granting reusable write authority.
- Granting `terminal`/`execute_code` to any helper role.
- Promoting a role without its qualification evidence.

## Stop lines
- No Implementer write spawn without an exact task-and-lane one-time exception
- No reuse of consumed Implementer exceptions and no broad Implementer qualification
- No Senior Engineer dispatch below lane repair cycle 1
- No helper role may approve, merge, or self-review its own work
- No helper spawn with a model other than the role's registry binding

## Next safe action
Governor opens WF-1100 phase-1 repair-cycle-1 lanes, dispatches only
`senior_engineer` through the admission gate, and sends each passing diff to
independent QA. The consumed Implementer exceptions are not reused.
