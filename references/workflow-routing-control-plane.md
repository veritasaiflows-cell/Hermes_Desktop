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
| Active queue | Workflow truth, lifecycle, owners, state | `state/ACTIVE_WORKFLOWS.md` |
| Alias index | Human-friendly workflow names -> stable IDs | `state/WORKFLOW_ALIAS_INDEX.md` |
| Overrides | Pause/halt/gate controls | `state/workflow-control-overrides.json` |
| Continuity notes | Human-readable resume context per workflow | `continuity/` |
| Route index | Generated freshness artifact | `tmp/workflow-routing-index.json` |
| Capsules | Generated per-workflow control summary | `state/workflows/WF-<ID>.json` |

Legacy compatibility files remain (`state/active_workflows.json`, `state/workflow_alias_index.json`).
The router prefers the uppercase names but reads these legacy files if needed.

## Command surface

- Build/refresh index and route one workflow:

```bash
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
```

- Build/refresh index and derived capsules:

```bash
python scripts/workflow_router.py --all --answer all --write-index
```

`--write-capsules` remains supported for explicitness.

- Run phase-0 + routing checks:

```bash
python scripts/run_checks.py
```

## Guardrails

Before meaningful work:

1. queue + alias + continuity + override must be read and validated
2. stale route-index must be detected and surfaced
3. blocked/gated states require owner action before advancing
4. helper output is advisory unless integrated by the owning workflow lane

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

Use `routing_index_stale` and `unsafe_to_trust` as hard gates for automation.
