# Workspace efficiency — authoritative Phase 0 / 0b / 1 / 2 roadmap

Owner: Governor / Randall. Workflow: **WF-1300**. Job: **workspace-efficiency**.
Origin: efficiency plan in session `20261006_174645_f9feffe8`, assistant message 75362; operator registration/amendment approval recorded in `derived/workspace-efficiency/2026-10-07/registration/operator-approval.md`.

This is the authoritative roadmap for the October efficiency work. The September plan `.hermes/plans/2026-09-22_220000-workspace-efficiency-speed.md` has DIFFERENT phase numbering and is historical, not this plan. This file owns stable scope/rationale; `state/implementation-jobs/workspace-efficiency.json` owns current phase/status/receipts; the workflow queue owns authority and blockers; the lane register owns leases. Do not infer acceptance from a completed lane or this roadmap.

## Fast pickup — before any session or workspace-wide search

1. `python scripts/workflow_router.py efficiency --status` — exact, read-only authority/freshness lookup, without semantic recall.
2. `python scripts/implementation_job.py pickup --contract state/implementation-jobs/workspace-efficiency.json` — bounded current phase, allowed writes, blocker, stop lines, next action and plan pointer.
3. Check the named lane in the existing lane register, then read only the pointed-to phase/source/evidence. Historical efficiency lanes remain under WF-1200; never reset or relabel them.

Aliases include `workspace-efficiency`, `efficiency-roadmap`, `efficiency-phase0`, `efficiency-phase0b`, `efficiency-phase1`, and `efficiency-phase2`. These route this one workstream; they do not claim to enumerate every implementation job in the workspace. For another known workflow, use its exact alias/ID and registered contract. `workflow_router.py --aliases` is the bounded discovery fallback. Broad semantic/session search is only for missing rationale, never current implementation state.

## Phase 0 — reduced warning/tracking qualification

**Current authority:** the operator-approved reduced pivot in `references/efficiency-phase0-reduced-closeout.md#operator-approval` supersedes the former Phase 0 dependency on completing Phase 0b. Implementation remains with main/Sol; one independent Opus review covers Sol's scope/binding/control delta, not Opus's unchanged original warning source. Preserve the historical split closeout, original failures and corrective author provenance.

**Scope:** qualify the existing informational aged-uncommitted-work warning and exact WF-1300/job pickup, reconcile the permanent main binding and current owner records. No new warning-source repair, hook replacement or publication-system implementation. Existing warning source must match the historically reviewed hashes. Ordinary shared-working-tree regression is the stated verification level; it does not establish an immutable candidate, verified partial checkpoint or tested-equals-committed guarantee.

**Acceptance:** fresh focused tests and the ordinary `scripts/run_checks.py` gate under unchanged limits, exact-source/evidence verification, verified actual independent Opus identity and a nonblocking substantive scoped verdict, followed by a real fingerprinted acceptance receipt through `implementation_job.py`. A failed gate or HIGH/MEDIUM stops this bounded attempt. Qualification and administrative closeout are accepted; the later operator checkpoint/push approval is narrowly recorded in `references/efficiency-phase0-checkpoint.md`. Acceptance of this reduced phase does not close the whole job or authorize Phase 0b/1/2.

## Phase 0b — immutable-tree gate design and eventual implementation

**Current disposition: explicitly deferred, not accepted or deployed.** The reduced pivot allocates no new B diagnostic/repair/review budget. A's internal materializer source core and accepted design/preparation are retained; B's FAIL, seven unresolved MEDIUM, consumed review, retry 2 and source remain preserved. The historical authorized slice below records prior acceptance, not permission to resume component work. Reopening Phase 0b requires a separately budgeted operator decision and fresh scoped execution authority.

**Current authorized slice:** preparation and the narrow 20-obligation cron-count correction are ACCEPTED after the single renewed Opus 5.5 delta review (session `20261007_154800_8fe044`, no HIGH/MEDIUM), actual-model/frozen-input verification and the passing full shared-workspace gate. Acceptance proof: `derived/efficiency-phase0b/2026-10-07/count-review/final-proof.md`. Earlier rejected packets, timeout and retry 2 remain history, not current approval blockers. The permanent Opus QA binding is separately approved and verified, with Sonnet fallback unchanged. Actual binding-test delta is three methods across two files. Existing implementation approval permits only newly scoped component/qualification lanes; no H collection, materialized performance, controller implementation acceptance or deployment is claimed. All 20 static obligations remain NOT_PROVEN_QUIESCENT; live consumers and effects remain unqualified.

**Design acceptance:** actual independent reviewer identity, frozen-packet/hash verification, explicit N1/N2 dispositions and no remaining HIGH/MEDIUM; separate Governor reconciliation. This accepts planning only, NOT implementation readiness, original code, a bootstrap invocation or deployment.

**Later execution:** clean committed development baseline, explicit scoped leases, trusted controller packaging, real-policy discovery, fixture and operational-state isolation, strict raw-blob fidelity, coverage-preserving bootstrap contract, containment/integration tests, measured performance within unchanged 360/420-second limits, and separately approved promotion/publication. New source is not inherited from rejected Opus/Sol work. Every item is future evidence, not a present capability.

The previous 393.74-second pytest / 398.86-second supervisor run is diagnostic only and exceeded the 360-second suite cap. No new timing benefit is claimed by this roadmap.

## Phase 1 — reduce tokens, tool calls and rediscovery

Status: scoped backlog; execution-ready subplans, independent planning acceptance and runtime/config authority still required. Phase 0 approvals are not blanket Phase 1 authority.

- **P1-0 — Started-work registration and exact pickup.** Immediate, operator-approved registration slice: place this existing work in WF-1300 with a hash-pinned implementation contract, phase/evidence links and existing fast-router aliases. Acceptance: aliases resolve deterministically; pickup exposes current phase, blockers and next action; no source/search/history scan is needed; old workflow rows and author/repair history are preserved. Prevention: register future multi-phase work BEFORE implementation. Backfill other identified jobs only under a separately scoped inventory, not an unbounded workspace scan. A universal cross-job dashboard is NOT built here.
- **P1-1 — Reuse the governed-change path.** Start with `implementation_job.py`, `workflow_router.py`, lane manager and existing commit/proof commands. Identify a measured missing operation before proposing a wrapper; do not add a second orchestrator. Acceptance: a bounded canary uses existing admission, ownership, test, review and checkpoint gates without manual state editing or weakened failures.
- **P1-2 — Consolidate procedural ownership.** Map overlaps among lane/commit/fleet, refresh and readiness skills; retain one authoritative procedure and pointers. Acceptance: representative tasks retain every approval/stop/test gate and load fewer distinct procedural documents; record before/after counts. No broad skill rewrite is authorized now.
- **P1-3 — Bound retrieval and session overhead.** Exact workflow/job lookup first; reuse already loaded, unpruned evidence; targeted source reads and batched independent calls; shorter task-focused sessions. Inspect compression settings only after measuring context loss, with separate configuration approval. Acceptance: matched sample counts for tool calls, repeated skill loads, loaded characters and elapsed time, without omitted evidence or premature completion.

**Entry/exit:** rebaseline a small set of real main tasks and corrected telemetry, choose one bottleneck, freeze a bounded spec and independent review boundary, run matched before/after canaries, preserve stop lines and rollback. No unsupported percentage/latency target is asserted. Phase completion requires reviewed scoped changes, actual execution evidence and an explicit operator disposition of dependencies.

## Phase 2 — reduce noise and keep measuring

Status: scoped backlog, dependent on accepted Phase 1 measurement and a separately approved execution plan.

- **P2-1 — Actionable alerts.** Examine repeated lane-expiry and Graphify warnings; distinguish active risk, stale derived data, known blocked work and duplicate alerts. Acceptance: each remaining alert names owner/action/evidence; controlled stale/expired cases still alert. No silent suppression of a hard gate.
- **P2-2 — Bound external waits and retrieval fallbacks.** Reuse existing browser timeout/recovery and exact-first retrieval procedures. Acceptance: declared retry/wait budgets, surfaced timeout/degraded outcomes and no fabricated fallback results; matched examples preserve successful recovery.
- **P2-3 — Weekly efficiency scorecard.** Reuse existing telemetry, not a new collector. Report turn/task duration, failed commits, test reruns, uncommitted-work age, repeated document/skill loads and exact-route usage with denominator, collection window, missing-data notes and corrected cohort exclusions. Acceptance: reproducible source-linked calculations and one real baseline report. Scheduling/cron changes require separate approval.

## Stop lines and completion rules

The reduced-scope implementation approval authorizes warning/tracking qualification and the explicitly named default-profile model keys and SOUL binding sentence only. The later "proceed to commit and push" narrowly supersedes the pinned-HEAD/no-publication restriction for the accepted Phase 0 checkpoint and four already-local ancestors; `references/efficiency-phase0-checkpoint.md` owns that exception and its real verification requirements. No rejected source, hook installation, controller promotion, policy-profile confirmation, general profile/configuration change, deadline increase, helper writes, qualification change, Phase 0b resumption, force push or general Phase 1/2 execution is granted. Stop on unresolved HIGH/MEDIUM at a review boundary; no automatic further correction. Historical failed lanes are not reopened or reset. Any newly required protected write or scope expansion returns to the operator.

The job contract owns the reduced Phase 0 qualification state and receipt. Phase 0b implementation remains blocked/deferred on outstanding component/isolation/coverage/performance qualification, not the resolved count finding; Phase 1 and Phase 2 remain blocked pending their separate scope decisions. The accepted design receipt is immutable. No full Phase 0b implementation or deployment acceptance and no fabricated historical receipts are backfilled.
