# Efficiens Workspace Workflow-Implementation Readiness Audit

- **Audit date:** 2026-08-15
- **Scope:** workspace architecture, scripts, canonical state, workflow control plane, concurrency control, tests, procedures, retrieval, observability, and operational readiness
- **Authority:** derived audit artifact; cited source files and current tool output remain authoritative
- **Decision:** **Conditional GO** for local, read-only/reversible workflow implementation; **NO-GO** for unattended external or financially consequential execution

## 1. Rating

| Dimension | Score / 10 | Assessment |
|---|---:|---|
| Governance and procedures | 8.5 | Strong single-owner model, safety boundaries, response contract, and operating loop |
| Canonical substrate and data safety | 7.5 | Strong transactional record API, provenance, history, and integrity checks; workflow-level transactions and idempotency are missing |
| Workflow routing and control plane | 6.0 | Stable IDs, aliases, queue, overrides, continuity, capsules, and hash freshness exist; controls are advisory and SLA/state schemas are weakly enforced |
| Test and verification harness | 7.0 | 31 tests pass and source compiles; no CI, dependency manifest, coverage, lint, type checks, or isolated default smoke command |
| Executable workflow capability | 5.0 | One real local CSV-to-canonical workflow works; no reusable workflow runtime/template, scheduler, connectors, or atomic rerun model |
| Concurrent-lane/process control | 4.5 | Durable leases, collisions, transitions, and proof fields exist; important invariants and actual filesystem enforcement are missing |
| Retrieval and observability | 3.0 | Full-text index code exists, but the default index, vector/graph adapters, source registry, telemetry writer, and populated stores do not |
| Deployment and integrations | 2.0 | No packaging, CI, scheduler, connector layer, environment contract, deployment target, or external-system acceptance path |

**Weighted implementation-readiness score: 6.2/10.**

Operational interpretations:

- **Local read-only or reversible workflow implementation:** 7/10 — proceed with guardrails.
- **Write-heavy or concurrent workflow implementation:** 5/10 — harden transaction and lane invariants first.
- **Unattended/external/financial workflow execution:** 3/10 — do not enable yet.

## 2. Verified current capabilities

### Governance and architecture

- `AGENTS.md:7-10` points to a single-owner governance model.
- `AGENTS.md:29-47` defines explicit authority and safety boundaries.
- `AGENTS.md:65-92` defines startup and routing behavior.
- `GOVERNANCE.md:51-76` defines authoritative ownership and duplicate-resolution rules.
- `references/operating-procedures.md:14-76` defines the full orient → classify → route → plan → execute → verify → record → respond loop.
- Layer roles are explicitly documented under `source/`, `canonical/`, `graph/`, `vector/`, `derived/`, and `telemetry/`.

### Canonical storage

- `canonical/schema.sql` defines 11 operational/audit tables, including provenance, entities, tasks, events, metrics, freshness, validation, version history, and run metrics.
- `canonical/db.py:33-50` enables foreign keys, busy timeout, WAL, schema initialization, and integrity checks.
- `canonical/db.py:153-295` performs audited updates under `BEGIN IMMEDIATE` with rollback, prior-state snapshots, and update events.
- Read-only inspection of `canonical/efficiens.db` returned `integrity_check=ok` and no foreign-key violations.
- Current data counts: 5 provenance, 5 entities, 25 metrics, 5 events; tasks, decisions, preferences, source freshness, validation results, version history, and run metrics are empty.

### Workflow control plane

- `state/ACTIVE_WORKFLOWS.md` is the declared authoritative queue with one stable workflow, `WF-1000`.
- Alias, override, continuity, generated capsule, and route-index surfaces exist.
- Live validation returned `routing_index_stale=false` and `unsafe_to_trust=false`.
- Effective state is `monitor_only`, with two blockers and an operator override preventing progression beyond approved local behavior.
- The concurrent lane register passed SQLite integrity and foreign-key checks. It currently has zero jobs, lanes, or events.

### Executable behavior and tests

- `scripts/product_research_workflow.py` parses and scores a local supplier CSV, supports `--dry-run`, and writes candidate entities, metrics, provenance, and events.
- `scripts/workspace_index.py` implements explicit-source FTS5 indexing, source hashes, citations, and stale-result rejection.
- `scripts/workflow_router.py` resolves stable workflow IDs/aliases and validates route-index source fingerprints.
- `scripts/concurrent_lane_manager.py` implements a durable SQLite lane ledger, collision checks, leases, transitions, and proof fields.
- `python -m unittest discover -s tests -v`: **31 tests passed**.
- `python scripts/run_checks.py --skip-smoke`: **31 tests passed**.
- `python -m compileall -q canonical scripts tests`: **passed**.
- A phase-0 smoke run against a temporary database returned positive entity, metric, and event deltas.

## 3. Material gaps and risks

### P0 — Fix before write-heavy or concurrent workflow expansion

1. **Workflow writes are not atomic.**
   - `product_research_workflow.py:94-180` performs many independently committed `CanonicalDB.insert()` calls.
   - An injected failure produced partial state: 1 provenance, 1 entity, 2 metrics, and 0 events.
   - Required fix: a workflow-run transaction/unit-of-work API with rollback of the whole candidate batch.

2. **Workflow reruns are not idempotent.**
   - Re-running the same input doubled records: 2 provenance, 2 entities, 10 metrics, and 2 events.
   - There is no stable workflow-run ID, source content hash, candidate natural key, or deduplication policy.
   - Required fix: stable run identity, source hash, unique business keys, and explicit resume/replay semantics.

3. **Control-plane state is advisory, not enforced by workflow entrypoints.**
   - `WF-1000` is effectively `monitor_only`, but `product_research_workflow.py` does not call the router/preflight and can still write canonical state.
   - `workflow_router.py:624-707` reports state but does not gate a shared execution runner.
   - Required fix: one enforced preflight wrapper that every workflow uses before any write.

4. **Lane invariants are incomplete.**
   - A reused parent job can own lanes with different workflow IDs; the validator still returned `ok=true`.
   - A negative lease duration is accepted; the validator reported an expired lease but still returned `ok=true` with no hard failure.
   - `scripts/concurrent_lane_manager.py:42-48` contradicts `references/concurrent-lane-control-plane.md:40-49` about blocked/cancelled recovery transitions.
   - Completion proof checks only existence at transition time; later validation does not verify proof existence, hash, or acceptance-command result.
   - The lane ledger detects declared collisions but does not technically prevent a worker from writing outside `allowed_writes`.
   - Default forbidden surfaces omit `.git/`, governance files, canonical schema/state, and other high-impact workspace control files; a probe accepted a declared `.git/config` write surface.
   - Required fix: validate parent/child identity, positive durations, expired-lease hard failures/reclamation, consistent transition semantics, hashed proofs, acceptance execution, and an enforcement hook or explicit advisory label.

5. **The default harness command mutates persistent state.**
   - `scripts/run_checks.py:107-109` defaults smoke writes to `canonical/efficiens.db`.
   - `scripts/run_checks.py:75-100` also writes routing index/capsules.
   - Repeated default runs add duplicate canonical records.
   - Required fix: default to a temporary database and temporary route artifacts; make persistent smoke an explicit opt-in.

### P1 — Fix before scaling beyond the first local workflow

6. **Freshness SLA is documented but not enforced.**
   - Queue/capsule declare `freshness_sla=daily`, but `workflow_router.py:469-574` checks only source fingerprints and alias count, not age.
   - Queue/alias `generated_at` values are not validated.

7. **Router schemas permit unsafe defaults.**
   - Active entries require only `workflow_id` and `display_name` (`workflow_router.py:145-156`).
   - Missing owner, approval owner, authority boundary, lifecycle, next action, or stop lines silently receive defaults.
   - Alias targets and queue aliases are not comprehensively cross-validated.
   - `routing_index_stale=false` is returned when validation is not requested, even though freshness was not checked.
   - `--write-index` does not create an index unless `--validate` first detects staleness, and it does not regenerate an already-fresh index, contrary to the flag/help wording.

8. **Retrieval is coded but not operational.**
   - Querying the default workspace index failed because `tmp/workspace-index.sqlite` did not exist.
   - The query path opens SQLite in create mode, creating an empty DB on a missing-index lookup; the audit-created file was removed.
   - Index refresh deletion uses an unescaped SQL `LIKE` path prefix (`workspace_index.py:209-223`). A temporary probe showed that rebuilding a source path containing `_` deleted an indexed sibling path because `_` was interpreted as a SQL wildcard.
   - `references/memory-routing.md:80-83` names `scripts/vector_memory_index.py`, but that adapter does not exist.
   - `source/`, `graph/`, `vector/`, and `telemetry/` each contain only a README.

9. **Observability is schema-only.**
   - `run_metrics`, `source_freshness`, and `validation_results` all contain zero rows.
   - No workflow-run recorder, structured logger, correction loop, or startup health command populates these surfaces.

10. **Development and release controls are absent.**
    - No `pyproject.toml`, requirements file, package metadata, CI configuration, pre-commit config, lint/type configuration, coverage tool, Makefile, or Dockerfile was found.
    - `pygount` and `coverage` are not installed in the current environment.
    - `run_checks.run_tests()` discovers `tests` relative to the process working directory, not `PROJECT_ROOT`; an empty-CWD probe ran 0 tests and still returned success.
    - The working tree has 8 modified and 15 untracked paths; most new control-plane code and tests are not yet versioned.

### P2 — Resolve as part of operationalization

11. **Documentation has stale or conflicting claims.**
    - `canonical/README.md:44` says the persistent database contains no records; direct inspection found 5 entities and 25 metrics.
    - Workflow and lane transition documentation does not fully match code.
    - The workflow procedure is a reference file, not a formal executable Hermes skill meeting the full `GOVERNANCE.md:101-126` skill contract.

12. **No external or unattended runtime exists.**
    - There is no scheduler, queue worker, connector abstraction, secrets/config contract, rate-limit/retry framework, external-write approval handshake, rollback compensator, or end-to-end connector acceptance test.
    - No Shopify/supplier/ad/email/customer-support integrations exist in the workspace.

13. **No schema migration or backup/restore procedure exists.**
    - `schema.sql` is a baseline, and constructors apply it automatically, but there is no schema version table, migration runner, backup command, restore drill, or recovery-point objective.
    - A temporary legacy-schema probe retained only its original `entities(entity_id, name)` columns after `CanonicalDB` initialization; `CREATE TABLE IF NOT EXISTS` did not migrate required columns.

## 4. Go / no-go decision

### GO now

Proceed with implementation of:

- local source ingestion,
- read-only research,
- deterministic analysis/scoring,
- dry-run artifact generation,
- human-reviewed canonical writes after enforced preflight,
- single-lane workflows with temporary/sandbox state.

### NO-GO now

Do not enable:

- unattended canonical batch writes,
- concurrent workers relying on lane leases as hard enforcement,
- publishing, spend changes, customer messages, orders, or fulfillment actions,
- external connectors using credentials,
- automatic retry/replay of write workflows,
- production decisions based on graph/vector/telemetry layers.

## 5. Recommended hardening sequence

1. **Make workflow execution atomic and idempotent.** Add `workflow_runs`, stable input hashes, unique run/candidate keys, transactional batch APIs, and rollback tests.
2. **Add a mandatory workflow runner/preflight.** It must resolve workflow ID, enforce effective status/override/approval, validate freshness, acquire a lane lease for writes, and record the run before calling workflow logic.
3. **Repair lane invariants.** Parent-child identity, positive leases, expired lease hard-fail/reclaim, transition consistency, proof hashes, acceptance-command verification, and clear advisory-vs-enforced semantics.
4. **Isolate the harness.** Make temporary DB/index/capsules the default; provide a separately named persistent canary command.
5. **Add operational telemetry.** Populate `run_metrics`, `validation_results`, and `source_freshness` on every meaningful run and preflight.
6. **Establish a reproducible dev gate.** Add `pyproject.toml`, pinned dev dependencies, lint/type/coverage commands, and CI running unit, invariant, smoke, and migration checks.
7. **Operationalize retrieval.** Define source profiles/registry, build the default FTS index, then add vector/graph only when a grounded use case exists.
8. **Add connectors last.** Require sandbox credentials, approval tokens, rate limits, idempotency keys, compensating actions, and end-to-end canary tests before any external mutation.

## 6. Minimum acceptance gate for the next workflow

A second workflow should not advance from `route_only` until all of these are true:

- [ ] Stable workflow ID, owner, human approval owner, lifecycle, stop lines, continuity, and aliases validate against a strict schema.
- [ ] Effective status and overrides are enforced by the executable runner.
- [ ] Freshness SLA is age-checked, not only hash-checked.
- [ ] The run has a stable ID and source/content hash.
- [ ] Repeat execution is idempotent or explicitly versioned.
- [ ] All canonical writes commit or roll back as one unit.
- [ ] Write lane lease is positive, fresh, collision-free, and identity-consistent.
- [ ] Tests include rollback, replay, stale-control, expired-lease, and proof-loss cases.
- [ ] Smoke runs use isolated temporary state by default.
- [ ] Run metrics, validation result, and closeout proof are recorded.
- [ ] No external action occurs without an explicit approved connector gate and compensating recovery path.
