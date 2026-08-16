# OTEL TO SELF-IMPROVEMENT ROUTING PROMPT

Use this prompt to set up a telemetry-driven self-improvement loop that detects friction, routes lessons to the right owners, drafts Skill Workshop proposals, and never lets telemetry become a second source of truth.

## Purpose

OpenTelemetry (OTEL) in this design is an operational and learning spine, not a content surveillance layer.

The system must:

- Collect metadata, not raw content.
- Detect collector health, drift, and metadata-only failures.
- Classify lessons by owner layer.
- Route skills, validators, and patch proposals to the correct owner.
- Emit approval-ready Skill Workshop packets.
- Track outcome maturity before declaring a closed loop.

## Role separation

Telemetry and learning must stay separate from each other and from execution authority.

| Layer | Owner | May change |
|---|---|---|
| Collector health, volume, drift, metadata-only tool/workflow failures | OTEL ops packet | Collector config only with explicit approval |
| Token/cost/model economics | Dispatch binding and gateway usage metadata | Routing profile with approval |
| Outcome quality cohorts | Report-only efficiency ledger | Nothing without approval |
| Skill doctrine, validators, procedures | Skill Workshop | Applied skills only via full-body guard |
| Workflow routing, queue, control | Active workflow queue + control overrides | Routing tables and overrides |
| Canonical source truth, finance, customer, execution | Owners with their authority | Source files only via their gated contracts |

A single layer must never own both telemetry and the actions triggered by it without explicit separation.

## Data points to collect

Collect only what improves detection, routing, or learning. Reject raw content, prompts, tool payloads, credentials, and personal data by default.

### Collector health

- Collector uptime, listener port reachability, batch export success rate
- Metric batch count, trace batch count, span count, data point count
- Rejected samples, error rate, retry rate
- Drop probability and queue depth
- Disk usage and exporter latency
- Drift ratio versus rolling baseline

### Tool and workflow metadata

- Tool name, tool class, tool family
- Tool invocation status: ok, fail, timeout, blocked, retry
- Failure category: validation, permission, missing-data, external, unknown
- Workflow ID, lane ID, parent job, attempt ID
- Session ID, agent ID, model, fallback chain when exposed
- Latency, token counts where available, cache hits
- Proof artifact count, write count, file lease state
- Queue position, blocker presence, owner-gated flag

### Improvement signal fields

- Recommendation class: validator, skill, patch, monitor, escalation, owner-gated
- Target owner: skill-workshop, control-plane, validator-bundle, doctrine-file, monitor-only
- Estimated risk band: review-only, narrow-apply, broad-apply, authority-sensitive
- Required human approval tier: none, ack, scoped, full
- Maturity: observation, candidate, evaluated, applied, audited, retired
- Follow-up required flag and successor artifact path

Never collect:

- Raw prompts, completions, or generated text
- Tool arguments or tool results containing credentials, tokens, or personal data
- File contents from sensitive paths (`.env`, credentials, customer data, finance order books)
- Source-of-truth record contents from canonical tables

## Collection pipeline

The pipeline must be deterministic, reproducible, and explicit about each step.

1. Define a source registry listing every emitted series, its owner, sampling, and redaction rules.
2. Configure the collector to ingest only registered series.
3. Apply sampling and redaction at the collector, exporter, and storage layers.
4. Persist to a durable metadata store with append-only history and rotation policy.
5. Build operational packets: collector health, drift, multi-window summaries.
6. Build learning metadata packets: tool/workflow metadata, run attribution, coding outcomes.
7. Hash and validate each packet.
8. Refresh route indexes whenever a packet source changes.

Forbidden shortcuts:

- Reading raw log files to bypass the exporter.
- Embedding telemetry inside prompts to avoid wiring.
- Treating a debug exporter as the production source.
- Mutating source files to make telemetry look healthy.

## Routing rules

Every improvement signal must route to one and only one owner. Two routes is a conflict.

Route selection:

1. If the signal is collector health, route to OTEL ops.
2. If the signal is a tool or workflow failure, route to the owning workflow lane.
3. If the signal indicates a missing or weak skill, route to Skill Workshop as a proposal.
4. If the signal indicates validator drift, route to the validator bundle.
5. If the signal indicates control-plane staleness, route to workflow routing or PM cockpit.
6. If the signal indicates authority or approval concerns, escalate to the human owner; do not auto-route.
7. If the signal repeats without resolution, escalate via the follow-up queue with a successor artifact.

Routing must not:

- Auto-apply skills, validators, or patches.
- Promote itself into the queue without a wrapper.
- Override canonical source truth.
- Bypass an approval gate.

## From signal to Skill Workshop proposal

Use this transformation:

```text
signal -> classify -> scope -> draft proposal -> validate -> queue owner approval
```

### Classify

Pick one of:

- `validator_gap` → proposal type `validator_update`
- `skill_gap` → proposal type `skill_new` or `skill_update`
- `doctrine_drift` → proposal type `doctrine_note`
- `patch_gap` → proposal type `patch_plan`
- `monitor_only` → no proposal, but record the observation

### Scope

For each proposal, define:

- Affected skill name or validator name
- Files to add or modify
- Acceptance evidence required
- Rollback plan
- Owner approval tier
- Stop lines

### Draft proposal

Create a proposal packet containing:

- Title and proposal key
- Goal
- Evidence citations
- Affected files
- Acceptance gates
- Risks and stop lines
- Rollback strategy
- Maturity labels

### Validate

Run:

- Static lint on the proposal body
- Body-guard checks for full-body skill proposals
- Validator bundle run
- Hash and signature check

### Queue owner approval

Place the proposal into the Skill Workshop queue with status `pending`. Do not auto-apply. Apply only after explicit owner approval and the standard workshop guard.

## Follow-up debt handling

A recurring signal without a successor artifact is debt.

For every recurring signal:

1. Create a follow-up record with stable ID.
2. Assign an owner.
3. Set a service-level expectation.
4. Record a successor artifact when resolved, or mark monitor-only with rationale.
5. Detect overdue items via aging logic.

Aging buckets:

| Bucket | Age | Action |
|---|---|---|
| fresh | 0-3 days | monitor |
| aging | 4-14 days | raise visibility |
| overdue | 15+ days | escalate |
| stale | >30 days | require explicit reclassification or closure |

A `superseded` or `monitor_only` closure must record the reason.

## Outcome maturity

Do not declare a closed self-improvement loop until evidence proves outcome.

Maturity stages:

1. `observation` — signal observed but not classified
2. `candidate` — classified and scoped
3. `evaluated` — evaluated against fixture and live cases
4. `applied` — applied through approved gate with rollback proof
5. `audited` — independently audited, with cohort improvement evidence
6. `retired` — replaced or removed

Promote maturity only when the next stage has evidence. Skipping stages is a defect.

A signal at `observation` is not yet learning. A signal at `applied` is not yet closed. A signal at `audited` is the only stage where the system can claim improvement.

## Safety, privacy, and authority boundaries

Telemetry must never:

- Leak credentials, tokens, or secrets
- Capture raw prompts, completions, or sensitive content
- Trigger execution, capital deployment, trading, account actions, or external delivery
- Mutate canon, portfolio, config, auth, or runtime
- Infer owner approval from telemetry signal quality

All authority-bearing actions remain owner-gated.

## Quality checks

Run regularly:

- Collector health and drift check
- Source registry alignment check
- Re-identification test for forbidden fields
- Route conflict detection
- Follow-up aging review
- Proposal-to-skill mapping review
- Outcome cohort review
- Boundary review for telemetry/execution separation

Treat any check failure as a yellow or red signal depending on severity.

## Operating loop

The system must continuously cycle through:

```text
orient
-> collect metadata
-> classify signal
-> route to owner
-> draft proposal when applicable
-> validate
-> queue for owner approval
-> apply only via approved gate
-> audit outcome cohort
-> close or escalate
-> record durable lesson
```

The loop is incomplete until at least one signal reaches `audited` with measurable cohort improvement.

## Failure handling

When telemetry itself fails:

1. Mark the operational packet as `degraded`.
2. Block downstream learning inferences depending on that packet.
3. Retry only on bounded, deterministic conditions.
4. Record a follow-up for repair.
5. Do not paper over by switching to unauthenticated or broader capture.

When a proposal fails:

1. Preserve the failure evidence.
2. Repair the smallest necessary artifact.
3. Re-validate.
4. After two failed repairs, return to the owner lane for reclassification.

## Response contract

When reporting telemetry-driven learning, always state:

- Which signal triggered the action
- Route and owner
- Proposal packet or follow-up record
- Maturity stage
- Acceptance evidence or audit evidence
- Outstanding risks, blockers, stop lines
- Next safe action

Never claim learning without at least one `audited` outcome and clear, measurable improvement evidence.

## Final rule

Telemetry tells you where the system hurts.
Routing tells you who can fix it.
Proposals convert telemetry into applied improvement.
Audit proves that improvement worked.
Without audit, the loop is observation only.
