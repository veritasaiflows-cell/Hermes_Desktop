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

Before answering from prior knowledge, follow `references/memory-routing.md`, the authoritative owner of route selection, source authority, freshness and source-bound reuse. Report missing, stale, contradictory, or unavailable memory.

---

## Workspace organization cadence

The placement map is owned by `GOVERNANCE.md`. Follow this cadence so every
session starts, writes, and closes against the same organization contract.

### Session startup

1. Run `python scripts/workspace_status.py --fast`.
2. Treat organization, routing, graph, vector, exact-index, and automation gate
   failures as blockers to unrelated writes until their trust impact is known.
   (Fast mode covers organization, routing, wiki, and lane_register; stale
   vector/workspace indexes warn rather than fail since source-direct
   fallback exists — missing or corrupt indexes still fail hard.)
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
6. Classify each durable write as tracked workspace source, profile-local state,
   Git-ignored derived output, or external system state.
7. Apply the standing local checkpoint authority below to significant verified
   tracked workspace source. Report changed paths, generated artifacts,
   verification evidence, remaining debt, and the final checkpoint disposition.

### Standing local checkpoint authority

The operator has granted standing approval for scope-limited **local** checkpoint
commits after coherent, significant workspace updates pass their focused checks
and repository gate. A significant update changes behavior, governance, schema,
workflow state, or a workspace-owned skill contract; trivial edits and
regenerable derived output do not require one commit per file or command.

- Inspect the live branch, status, diff, recent history, and active write lanes
  before staging.
- Stage only reviewed in-scope paths. Never absorb unrelated pre-existing or
  concurrently written changes merely to make the tree clean.
- Use the repository's commit-subject convention and verify the resulting commit
  plus remaining status. Never push under this standing authority.
- If a gate, active lane, missing scope evidence, or unresolved mixed ownership
  prevents a safe commit, return `checkpoint_pending` with exact paths, proof,
  proposed subject, and blocker instead of leaving the durability state implicit.
- Profile-local skill changes are not covered by workspace Git: use Curator
  backup and mutation-ledger evidence. Workspace-specific skills that require Git
  history belong under trusted `.hermes/skills/` as the single authoritative
  editable source; do not keep a competing profile-local copy.

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

Every helper/subagent spawn must pass the deterministic admission gate before
the spawn is issued:

```bash
python scripts/helper_agent_router.py admit --request <helper-request.json>
```

Build a `helper-agent-request.v1` (task_id, task_class, phase, mode,
objective, scope, allowed_toolsets, allowed_writes, max_duration_minutes,
owner, and lane_id for write mode), run the gate, and spawn only on exit 0.
For `review`-class requests scoped to a lane, `reviews_lane` and
`reviewer_model` are additionally required together (and rejected on any other
task class): the reviewer model must differ from the reviewed lane's recorded
author model, and a reviewed lane without author attribution fails closed.
Write spawns against a lane already carrying two or more recorded repair
cycles are rejected — escalate to the human owner instead. Role authority
matrix and qualification order: `references/multi-agent-fleet-governance.md`.
The gate rejects: undeclared task classes; `read-only` requests that declare
write surfaces, a lane, or any toolset outside the read-only allowlist
(`read_file(s)`, `search_files`, `web_search`, `web_extract`); `write`
requests that declare any toolset outside the write-mode allowlist (the
read-only set plus `write_file` and `patch` — `terminal` and `execute_code`
are never admissible because a shell is not lane-bounded, and unknown names
fail closed in both modes); `write` requests without a leased or running
write-mode lane whose unexpired lease, owner, and normalized allowed-write
surfaces cover every requested path (the surface-itself match ignores
trailing separators; a stored file surface covers only itself, and a
directory surface covers itself and its children); path traversal, globs,
absolute paths — including UNC (`\\server\share`, `//server`) and
drive-relative (`C:foo`) forms, whatever their separators or leading
whitespace — duplicate allowed-writes entries (separator variants included),
and root-anchored forbidden surfaces; and never-admissible toolset families
(cronjob, computer use, desktop UI, project, memory, skills/skill,
delegation, MCP and its setup/install forms), matched case-insensitively by
family — separators ignored — so naming variants (`computer-use`,
`cron-job`, `delegate_task`, `memory_search`, `skill_manage`,
`setup_mcp`, `mcp__server__tool`) cannot bypass the boundary. Every
rejection — including a corrupt lane register, malformed lane row,
unparseable lease, unreadable or non-UTF-8 request, hostile path bytes,
malformed CLI usage, or any internal error — is a JSON verdict with exit 2;
the gate never crashes with exit 1 (a fail-closed backstop converts any
unexpected exception into an exit-2 JSON rejection). The lane register is
opened by plain filesystem path (never a SQLite URI), so `--project-root`
values containing URI metacharacters cannot misbind the connection.
Note that `--help` exits 0 with plain text: a parent must parse the stdout
JSON verdict, not trust the exit code alone, before spawning. Admission is a
deterministic
labeling and lane-coverage contract, not a sandbox: it does not constrain a
child session at runtime, and the parent remains responsible for spawning only after the
gate passes and for treating helper output as untrusted.

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
