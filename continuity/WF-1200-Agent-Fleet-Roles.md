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
| qa | `qa` | `openai-codex/gpt-6.1-sol` | admissible | read-only | - | `anthropic/claude-sonnet-5-5` |
| researcher | `researcher` | `openai-codex/gpt-6-luna` | qualified | read-only | - | `ollama-cloud/deepseek-v4.1-flash` |

Governor parent-only fallback chain: `openai-codex/gpt-6-astra`.

Recorded one-time lane exceptions (lane register owns their status):
- `implementer` / `otel-efficiency-impl-2026-10-04`
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

QA rebinding (operator-approved 2026-10-03, `operator-chat-2026-10-03-qa-sol-rebinding`): QA primary is now `openai-codex/gpt-6.1-sol`, fallback `anthropic/claude-sonnet-5-5`, so formal QA is no longer the same model as the Governor session. Review routing: QA primary and the Senior Engineer share gpt-6.1-sol, so a Sol-authored lane is reviewed on the QA fallback (Sonnet) and a Sonnet-authored lane on the primary (Sol). For role `qa` only, the gate admits the primary or a registry-listed fallback and rejects the author's model (names compared normalised). Formal reviews run through `scripts/qa_review_driver.py`, which verifies the model that actually ran. Plain `hermes -p qa chat` calls are not verified. Qualified on 4 of 4 cases with one
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
2. Lane fails QA/acceptance → Governor blocks the failed attempt with evidence
   and plans a scoped successor using `concurrent_lane_manager.py plan --retry 1`,
   preserving parent job and failure lineage, then leases/starts it. There is no
   `retry` subcommand. At cycle 1 the gate refuses Implementer and admits only
   `senior_engineer` (GPT-6.1-Sol) to debug and repair.
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
Verify accepted proof for the separate QA review-routing lane, then operator and primary Governor reconcile fleet readiness and actual Governor binding before WF-1100 repair dispatch. Never reuse consumed Implementer exceptions or allow same-model review. Queued follow-up WF-1200-RS01: role skill provisioning and dispatch verification; bounded scope and acceptance criteria are in the WF-1200 continuity note. Queue approval does not authorize deployment or permission changes.

## Queued follow-up: WF-1200-RS01

Related bounded measurement work (operator-approved 2026-10-04): Governor,
Implementer and QA efficiency OTEL pilot, evidence/specification under
`derived/otel-efficiency/2026-10-04/`, contract `source/hermes-otel-pilot/efficiency.md`.
History: Implementer first attempt failed Governor tests (exception consumed);
Senior Engineer cycle-1 repair failed (2 failed / 1021 passed); Sonnet final QA
returned FAIL; cycle-2 hold stopped helper writes.

Current state (2026-10-04, operator-approved):
- Corrective attempt: Governor-executed only (no helper writes, no exception
  reuse, no repair-history reset), lane `otel-efficiency-corrective-2026-10-04`.
  Fixed side-effect-free health reads, strengthened the dedupe test, real-gate
  runner admission with launch-tools subset check, and loss-aware receipt/read-back.
  Full suite 1061 passed, 59 subtests. Fresh Sonnet QA (actual model verified,
  session `20261004_070650_f85848`): PASS_WITH_NOTES, all 9 scoped items fixed.
- Deployment lane `otel-efficiency-deploy-2026-10-04`: efficiency mode enabled in
  `implementer` and `qa` profiles only (plugin keys + hash-parity files), window
  ends 2026-10-05T14:00:00Z. Live controlled canary: success run accepted and
  injected-failure run rejected; exact collector read-back passed with child
  `hermes.turn`/`hermes.api` spans inside the parent trace. Overhead ≈13 µs per
  hook callback (in-process only). Collector PID/config unchanged.
- Default profile deliberately deferred until the legacy diagnostic pilot closes
  (2026-10-04T20:21:35Z): gated one-shot cron `8e057ead898b` at 13:35 Arizona
  switches it only if the window has passed and closeout job `b1ede4ae3312` ran;
  desktop reload is the operator's step. Read-only expiry reminder cron
  `79bcf6f4911b` at 2026-10-05 07:05 Arizona; no automatic disable or extension.
- Open low findings and non-claims: launch-tools check is trivial while `bot_room`
  resolves to zero tools; native home branch untested; only controlled-cohort
  traces; no efficiency, cost or quality claim; no bot qualification.
Evidence (git-ignored): `derived/otel-efficiency/2026-10-04/{corrective,deploy}/`.
Lane register owns live status. This does not start or authorize the queued
skill-provisioning item below.

## Checkpoint provenance clarification — 2026-10-04

The historical `fleet-review-route-2026-10-03` lane remains blocked and is not
accepted by the checkpoint. The former route-specific prohibition on a Sol-authored
lane using the QA fallback predates the QA rebinding (Sol primary, Sonnet fallback)
and is no longer an accurate route-label rule. Its invariant is preserved and made
explicit in the queue and mirrored capsule: no QA review unless session-verified
reviewer model differs from session-verified author model. Registry approval and
all other stop lines remain required; this is not a qualification or permission
change and does not clear WF-1100. The WF-1200-RS01 queue item and its original
operator queueing authority are recorded in the bounded item below. The review
packet now includes this existing note; checkpointing does not start that item.

**Title:** Role skill provisioning and dispatch verification.
**Status:** Queued; not started. Owner: Governor (`agent-main`).
**Authority:** Operator explicitly requested queueing this bounded item in the current chat; this is backlog registration only, not implementation, profile deployment, or permission approval.

### Objective and bounded scope

Reduce repeated manual procedure instructions while preserving role boundaries. Cover only the existing Architect, Implementer, Senior Engineer, QA and Researcher profiles and their governed dispatch paths.

- Inventory installed, discoverable, permitted and actually loaded skills separately, including profile-local and trusted workspace sources.
- Define a minimal approved role-to-skill manifest with one authoritative source per procedure, source/version or hash provenance, and an explicit context budget. Do not preload the whole library.
- Design automatic read-only delivery of the selected skills through the real dispatch path. Distinguish profile-launched jobs from in-process delegation; neither may silently inherit Governor authority.
- Reconcile the current prohibition on helper skill tools before proposing any permission change. Prefer Governor-resolved, bounded content delivery where it meets the requirement; do not bypass the admission gate.
- Prepare a bounded synthetic before/after canary using identical role tasks and verified actual model identities. Reuse accepted measurement tooling where available rather than building a separate telemetry subsystem.

### Acceptance criteria for future execution

1. Each covered role has an explicit minimal skill manifest and deterministic missing, stale or disallowed-skill failure behavior.
2. Dispatch evidence records which approved skill sources and versions actually reached each job, not merely which files exist or what a bot claims to have read.
3. Positive and negative canaries verify skill use, role/tool restrictions, client isolation and rejection of unapproved skill access or mutation; existing admission, lease, repair-cycle and independent-review gates remain intact.
4. Before/after results report task correctness, repeated instruction volume, token use where observable, tool calls, retries and elapsed time. Missing metrics stay unknown; no speed or cost improvement is claimed without measurement.
5. Governor-run focused tests and independent QA by a different actual model pass before any rollout acceptance; retain rollback instructions and exact changed-profile surfaces.

### Sequence and stop lines

- First reconcile existing WF-1200 review-routing readiness and obtain a scoped design/implementation decision. This item does not supersede current blockers or authorize WF-1100 repairs.
- Before future writes, obtain the appropriate lane lease and explicit approval for exact profile/configuration or permission changes; any Implementer exception remains separately task-and-lane-specific.
- No profile, toolset, credential, model, role-qualification or runtime changes are authorized by queueing. No helper dispatch, client data, external messages, new telemetry capture or paid canary is started by this registration.
- Do not replace or rewrite existing broad skill libraries merely to satisfy an inventory count. Curate only the approved role set after the loading path is verified.
- Next action for this item: prepare the read-only inventory and proposed role manifests for Governor/operator scope review, without deploying them.
