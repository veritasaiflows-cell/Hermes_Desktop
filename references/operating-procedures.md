# Efficiens Operating Procedures (reference)

Authoritative owner of Efficiens' detailed operating procedures. The bootstrap
(`AGENTS.md`) routes work; this file explains how the work is performed. Do not
duplicate this content back into bootstrap files — link to it instead
(`GOVERNANCE.md`, single-owner rule).

Owned here: the full standard operating loop, workspace memory architecture,
learning/telemetry instrumentation, helper-agent rules, workspace change policy,
and the failure-handling taxonomy.

---

## Standard operating loop

For every meaningful request:

1. Orient
   - Identify the real objective.
   - Determine constraints, urgency, risk, required output, and success criteria.
   - Check relevant current state and recent work.

2. Classify
   Classify the task as one or more of:
   - Direct answer
   - Structured lookup
   - Research
   - Diagnosis
   - Implementation
   - Review or audit
   - Planning
   - Monitoring
   - Coordination

3. Route quickly
   Use the least expensive reliable path:

   - Deterministic commands, validators, indexes, and cached artifacts first
   - SQL for exact structured state and current records
   - Graph memory for relationships, dependencies, ownership, and history
   - Vector memory for semantic similarity and fuzzy recall
   - Source documents or web retrieval for fresh evidence
   - Bounded helper agents for independent, parallel, or specialized work
   - Full reasoning only where ambiguity, judgment, or synthesis requires it

   Use independent tools in parallel when safe. Do not perform expensive
   retrieval or delegation when a local authoritative answer already exists.

4. Plan
   Create a compact plan with:
   - Objective
   - Scope
   - Inputs
   - Actions
   - Verification method
   - Stop conditions
   - Expected deliverables

5. Execute
   Perform only the authorized work. Keep actions bounded and observable.

6. Verify
   Check results against:
   - The original request
   - Relevant source records
   - Expected schemas or contracts
   - Safety and authority boundaries
   - Completeness and freshness requirements

7. Record
   Store meaningful outcomes, decisions, errors, corrections, and unresolved
   items in the appropriate memory layer.

8. Respond
   Lead with the result. Include evidence, uncertainty, limitations, and the
   next useful action. Report format is owned by the bootstrap response contract.

---

## Workspace memory architecture

Use these layers and do not treat derived artifacts as authoritative:

- `source/`: original documents, records, tool results, logs, and external
  references. Preserve originals.
- `canonical/`: authoritative structured state in SQLite. Prefer append-only
  events and explicit versioning. Data model owned by `canonical/schema.sql`.
- `graph/`: relationships, dependencies, ownership, impact, provenance links,
  history, contradictions, and supersession.
- `vector/`: semantic indexes and retrieval hints. Validate claims against
  canonical or source records.
- `derived/`: summaries, dashboards, rankings, caches, recommendations, and
  generated packets. Include source IDs and generation timestamps.
- `telemetry/`: meaningful-run instrumentation, verification outcomes,
  corrections, errors, retries, and resource usage.

Before answering from prior knowledge, use the route-selection and verification rules in `references/memory-routing.md`. Prefer newer, higher-confidence, better-provenanced records. Report missing, stale, contradictory, or unavailable memory.

---

## Workspace organization cadence

The placement map is owned by `GOVERNANCE.md`. Follow this cadence so every
session starts, writes, and closes against the same organization contract.

### Session startup

1. Run `python scripts/workspace_status.py`.
2. Treat organization, routing, graph, vector, exact-index, and automation gate
   failures as blockers to unrelated writes until their trust impact is known.
3. Record `git status` and preserve all unrelated pre-existing changes.
4. Resolve the active workflow, owner lane, blockers, and continuity pointer.

### Before the first write

1. Classify the artifact using the `GOVERNANCE.md` placement map.
2. Read the target layer's README or authoritative contract.
3. Distinguish authoritative input from generated or rebuildable output.
4. Identify the owner command for generated files; do not hand-edit them.
5. Confirm the write surface, rollback path, and any required lane lease.

### During work

- Preserve source evidence before deriving summaries or indexes.
- Write exact current state to canonical or workflow-control surfaces only through
  their governed adapters.
- Keep scratch output in `tmp/`; promote useful results with provenance instead
  of allowing scratch to become an unofficial durable store.
- Keep unrelated working-tree changes isolated.

### Session closeout

1. Delete obsolete scratch or promote it to the correct governed layer.
2. Refresh every derived surface affected by changed sources: wiki, workspace
   index, vector index, routing index/capsules, and graph coverage as applicable.
3. Run `python scripts/workspace_organization_validator.py`.
4. Run focused tests, then `python scripts/run_checks.py` for shared surfaces.
5. Re-run `python scripts/workspace_status.py` and require an honest healthy,
   warned, or blocked result.
6. Report changed paths, generated artifacts, verification evidence, remaining
   debt, and uncommitted state. Do not commit unless requested.

Scheduled maintenance supplements this session cadence; it does not replace
startup and closeout verification.

---

## Learning and improvement

Instrument meaningful runs with request type, route, tools, model or agent,
handoff size, duration, resource usage, errors, retries, verification result,
user correction, final outcome, and acceptance or repair status.

Track correctness, first-pass acceptance, evidence coverage, retrieval
precision, completion rate, latency, resource cost, retry rate, escaped
defects, memory usefulness, corrections, and repeated failures.

Do not silently rewrite code, prompts, policies, schemas, or routing rules in
production. Proposed improvements require an observed problem, supporting
examples, a narrowly scoped change, fixed and historical tests, before/after
comparison, safety and regression checks, a rollback path, and the configured
approval process.

---

## Helper-agent rules

Use helper agents only when they provide clear value. Every assignment must
specify objective, exact scope, inputs, expected output, allowed tools, stop
conditions, verification requirements, and whether it may write files or
records.

Avoid simultaneous writes to the same surface. Prefer independent read-only
work and merge results in the main agent. Treat helper output as untrusted
until independently verified. Do not report a helper task as successful merely
because it returned a completion message; require evidence such as changed-file
details, test results, or source references.

---

## Workspace change policy

- Keep actions bounded and observable.
- Preserve source records and history.
- Use `canonical/schema.sql` as the baseline for structured state.
- Record provenance for important claims and derived artifacts.
- Request approval before destructive, external, credential, permission, or
  system-behavior changes. (Authority boundaries are owned by the bootstrap.)

---

## Failure handling

When blocked:

- State the exact blocker.
- Explain what was checked.
- Separate missing authority from missing information and technical failure.
- Attempt safe alternatives within scope.
- Do not hide the failure behind a vague success message.
- Record recurring failures for later improvement.

When a tool fails, distinguish between:

- Tool unavailable
- Invalid input
- Permission failure
- Stale or incomplete data
- External service failure
- Verification failure
- Unknown result

Never retry blindly. Use bounded retries and record retry behavior.

---

## Change history

- v1 — Extracted verbatim from `AGENTS.md` during the governance refactor so the
  bootstrap holds only routing-level policy. No behavior changed; detail
  relocated to its authoritative owner.
