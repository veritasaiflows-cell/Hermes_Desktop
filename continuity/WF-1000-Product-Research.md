# Workflow 1000 continuity note

## Objective
Build and harden Workflow A before enabling any new monetization workflows.

## Current state
`Workflow A - Product Research` is implemented in `scripts/product_research_workflow.py`.
Canonical proof exists via `tests/test_product_research_workflow.py` and `tests/test_db.py`.

## Last meaningful progress
- Added phase-0/1/2 workflow scaffolding.
- Added control-plane skeleton files in `state/` and `continuity/`.

## In-scope
- In-place controls for workflow ownership, state, aliases, overrides, and freshness.
- Route command for validation (`workflow_router.py`).

## Out of scope
- External connector implementations (phase 3).
- Campaign spend/launch automation.

## Preflight
- `state/ACTIVE_WORKFLOWS.md` must be valid.
- `state/WORKFLOW_ALIAS_INDEX.md` must contain canonical aliases.
- Overrides must be checked before advancing a blocked workflow.

## Execution posture
- Run preflight checks using `python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index`.

## Acceptance gates
- Route/index freshness checks pass.
- Workflow remains in `active` lifecycle.
- No stale control artifacts when execution decisions are made.

## Exit/closeout checklist
- [ ] Active workflow owner verified.
- [ ] Override state checked.
- [ ] Capsule and index generated and fresh.
- [ ] Control-plane validation checks pass.

## Next pass
- Validate route command in repository checks (`run_checks.py`).
- Promote control-plane surfaces to canonical storage if needed.

## Next safe action
Run:

```bash
python scripts/run_checks.py --skip-smoke
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
```
