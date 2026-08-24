# Graph memory layer — routing and write contract

Authoritative owner of the durable graph layer for this workspace. It governs
when to use graph retrieval, how edges are written, and how the graph stays
consistent with canonical state.

## Layer purpose

The graph stores **asserted relationships** between canonical records:
dependencies, ownership, impact, provenance links, history, contradictions, and
supersession. It answers relationship-shaped questions:

- "What depends on X?"
- "Who owns Y?"
- "What did Z affect?"
- "How are A and B connected?"

The graph explains relationships. It is **not authoritative** when it conflicts
with canonical SQL or primary source evidence. Edges are recall aids with
provenance, not proof.

## Relationship to other layers

| Layer | Question shape | Mechanism |
|---|---|---|
| `canonical/` (SQL) | "What is the exact state of X?" | Deterministic lookup — authoritative |
| `graph/` (this layer) | "How is X connected to Y?" | Typed edges, BFS traversal |
| `vector/` (semantic) | "What is similar to X?" | Embedding similarity |
| `graphify-out/` (derived) | "What code relates to what?" | LLM-inferred, disposable, gitignored |

Graphify is a **derived routing layer** for codebase questions. It is not the
durable graph: most edges are AST- or LLM-extracted, and the workspace adds only
deterministic static gate-dispatch edges with exact source paths and lines. None
are canonical assertions. The durable graph stores the relationships the agent
asserts with provenance and confidence.

Graphify freshness is checked separately with
`python scripts/graphify_freshness.py`. The gate compares the refreshed graph
and manifest against a content-hash baseline, checks every current Python module
under `canonical/`, `scripts/`, and `tests/` has graph source coverage, and
honors Graphify's semantic `needs_update` marker. It requires all code roots,
rejects linked/reparse-pointed artifact and source paths, refuses incomplete code
coverage, and publishes the local baseline atomically. After a successful code refresh:

```bash
graphify update .
python scripts/graphify_gate_edges.py
graphify diagnose multigraph --graph graphify-out/graph.json --json
python scripts/graphify_freshness.py --write-baseline
python scripts/graphify_freshness.py
```

`graphify_gate_edges.py` derives `runs_gate` edges from the literal
`DEFAULT_GATES` contract in `scripts/workspace_status.py`. The freshness gate
refuses a new baseline when that evidence is missing or stale, so a Graphify
shortest path can expose a cited dispatch edge without turning Graphify into an
authority.

A stale Graphify artifact is a routing warning, not a failure of the durable
asserted graph. Route to direct source inspection until the artifact is fresh.

## Data model

The `relationships` table in `canonical/schema.sql` is the durable graph store.
Each edge:

- `rel_id` — stable relationship identifier
- `subject_type` / `subject_id` — the source record (canonical table + ID)
- `predicate` — relationship type (e.g. `owns`, `depends_on`, `contradicts`, `supersedes`)
- `object_type` / `object_id` — the target record
- `status` — `active`, `superseded`, or `invalidated`
- `confidence` — 0.0–1.0
- `valid_from` / `valid_until` — validity interval
- `superseded_by` — replacement edge
- `provenance_id` — required provenance reference
- `created_at` / `updated_at`

## Write discipline (anti-duplication)

Edges **link** canonical records by identifier; they never re-state facts. The
fact lives in canonical; the edge lives in graph. No overlap is possible when
the adapter refuses free-text edges.

Every `add_edge` requires:

1. Canonical subject and object identifiers (table + ID).
2. A provenance reference (automatic via `CanonicalDB.insert`).
3. A predicate from the workspace vocabulary.

Do not write the same fact independently into multiple memory layers unless one
record clearly points to the other (`references/memory-routing.md`).

## Executable commands

The adapter is `scripts/graph_memory.py`:

```bash
# Assert an edge (provenance-gated).
python scripts/graph_memory.py add_edge \
  --subject-type entities --subject-id <entity-id> \
  --predicate owns \
  --object-type tasks --object-id <task-id> \
  --confidence 0.9 --source-ref "session-2026-08-17"

# List active edges from a record.
python scripts/graph_memory.py neighbors \
  --subject-type entities --subject-id <entity-id>

# Shortest active path between two records (BFS).
python scripts/graph_memory.py path \
  --start-type entities --start-id <a> \
  --end-type tasks --end-id <b> --max-depth 8

# Reverse traversal: what depends on a record.
python scripts/graph_memory.py affected \
  --object-type entities --object-id <entity-id> --max-depth 2

# Orphan/contradiction sweep (exit 1 on issues).
python scripts/graph_memory.py validate
```

## Route selection

Use the graph route when the request is about **relationships**:

- "What depends on X?" → `affected`
- "How are A and B connected?" → `path`
- "What does X own / affect / contradict?" → `neighbors`

Routing order remains: SQL → graph → vector. SQL finds exact structure; graph
finds relationships; vector finds meaning. The source record establishes truth.

## Edge production

Edges are produced two ways:

1. **Workflow emission (asserted at write time).** `product_research_workflow.py`
   asserts edges inside its canonical write transaction:
   - `entities -> has_metric -> metrics` for every metric written
   - `workflow_runs -> produced -> entities` for every candidate inserted
   Edges commit or roll back atomically with the records they link. The run
   summary reports `graph.edges_asserted`.

2. **Backfill (one-time, idempotent).** `scripts/graph_backfill.py` materializes
   the same vocabulary for records written before the graph layer existed. It
   reuses existing active triples and never duplicates. Safe to re-run.

## Validation and freshness

- `validate` sweeps for orphan edges (subject/object record missing) and
  duplicate active triples. It is wired into the A2 health gate
  (`scripts/cron_health_check.py`).
- A10 (`scripts/cron_graph_freshness.py`, hourly at :15) runs the integrity
  sweep plus a coverage check: canonical records that should have edges but do
  not (e.g. `product_candidate` entities without `has_metric`, workflow runs
  without `produced` edges). Coverage gaps alert with the remediation command
  (`python scripts/graph_backfill.py`).
- Expired edges (`valid_until` in the past) are excluded from traversal.
- Superseded edges are excluded from traversal but preserved for history.
- Important claims derived from graph traversal must be verified against the
  canonical record before being reported.

## Change history

- v1 — Installed the durable graph layer: `relationships` table, `CanonicalDB`
  graph methods, `scripts/graph_memory.py` adapter, A2 health-gate wiring, and
  this routing contract.
- v1.1 — Workflow edge emission (`has_metric`, `produced`), idempotent backfill
  script, and A10 graph-freshness cron (integrity + coverage drift).
