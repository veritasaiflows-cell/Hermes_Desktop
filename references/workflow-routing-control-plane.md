# Workflow routing and ownership control plane

This reference defines the concrete control-plane artifacts implemented for routing and
workflow ownership.

## Why this exists

This workspace now needs a stable ownership model before adding more workflows.
This reference captures:

- canonical queue + alias + override sources
- route-index freshness expectations
- workflow capsule contract for output stability
- startup and validation behavior
- safe operational sequence for workflow progression

## Authoritative routing sources

| Source | Purpose | Path |
|---|---|---|
| Active queue | Workflow truth, lifecycle, owners, state | `state/active_workflows.json` |
| Alias index | Human-friendly workflow names -> stable IDs | `state/workflow_alias_index.json` |
| Overrides | Pause/halt/gate controls | `state/workflow-control-overrides.json` |
| Continuity notes | Human-readable resume context per workflow | `continuity/` |
| Dependency graph | Canonical `workflows -> depends_on -> workflows` edges | `canonical/efficiens.db` |
| Vector memory | Optional cited context for a workflow and its graph dependencies | `tmp/vector-memory.sqlite` |
| Route index | Generated freshness artifact | `state/workflow-routing-index.json` |
| Capsules | Generated per-workflow control summary | `state/workflows/WF-<ID>.json` |

Markdown compatibility files remain (`state/ACTIVE_WORKFLOWS.md`, `state/WORKFLOW_ALIAS_INDEX.md`)
as human-readable rendered views. The router prefers the JSON names but reads these
legacy markdown files if needed.

## Command surface

- Build/refresh index and route one workflow:

```bash
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
```

- Build/refresh index and derived capsules:

```bash
python scripts/workflow_router.py --all --answer all --write-index
```

- List available aliases:

```bash
python scripts/workflow_router.py --aliases
```

`--write-capsules` remains supported for explicitness.

- Run phase-0 + routing checks:

```bash
python scripts/run_checks.py
```

## Routing cache

Revalidated routing queries are cached in `canonical/efficiens.db` (`routing_cache`)
for 300 seconds. The cache is keyed by selector, answer mode, validation flag, and
source signatures; control-plane files, the vector index, and the resolved dependency
graph are rechecked before a cached answer is used. Use `--no-cache` to force a fresh computation.

Before meaningful work:

1. queue + alias + continuity + override must be read and validated
2. stale route-index must be detected and surfaced
3. blocked/gated states require owner action before advancing
4. helper output is advisory unless integrated by the owning workflow lane

## Workflow capsule contract

Each generated capsule (`state/workflows/WF-<ID>.json`) includes:

- `workflow_id`, `display_name`, `lifecycle`, `effective_status`
- `implementation_script`: the canonical script entry point
- `commands`: `dry_run` and `write` command templates
- `blockers`, `stop_lines`, `owner_action_required`
- `depends_on` and `dependency_blockers`, resolved from the canonical graph when it is available
- `graph_dependency_blockers`, which makes declaration/graph drift a hard routing blocker
- `recall_context`, up to three cited hybrid-retrieval results scoped by the workflow and its graph dependencies
- `default_resume_command`: the router command to get the next safe action
- `validator_commands`: checks that must pass before the workflow advances

Use these fields instead of parsing free-text `next_action` when selecting
which script to run.

`ACTIVE_WORKFLOWS.md` declares dependency intent and supplies workflow metadata. The
canonical graph is the primary source for dependency traversal; when a declared edge is
absent from the graph, the router returns a `graph_dependency_blockers` item and will
not leave an otherwise active workflow executable. Refresh graph edges with:

```bash
python scripts/graph_backfill.py
python scripts/workflow_router.py --all --answer summary --validate --write-index
```

## Mandatory write preflight

`scripts/workflow_runner.py` is the common, read-only admission gate for a
workflow CLI that can mutate canonical state. It deliberately never refreshes
the index itself. Before a canonical write it requires:

1. a fresh, trusted route index;
2. `effective_status: active`, no blockers, no stop lines, and no pending
   owner action;
3. a running, unexpired lane lease owned by the caller; and
4. every requested write target to fall inside that lane's declared
   `allowed_writes` and outside its `forbidden_writes`.

`scripts/product_research_workflow.py` invokes this gate for non-dry-run CLI
execution. Local `--dry-run` execution remains read-only and does not require
a lease. The underlying Python function is intentionally reserved for isolated
tests and controlled internal harnesses; new workflow CLIs must use the gate.

## Output contract

`workflow_router.py` returns JSON payloads with:

- `routing_index_stale` (boolean)
- `unsafe_to_trust` (boolean)
- `workflow` for single selector routes
- `workflows` list for multi-route operations
- `implementation_script` and `commands` inside each workflow view

Use `routing_index_stale` and `unsafe_to_trust` as hard gates for automation.
Use `implementation_script`/`commands` as the machine-readable instruction for
which script to execute.
