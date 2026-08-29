# Workflow 1000 continuity note

## Objective
Build and harden Workflow A before enabling any new monetization workflows.

## Current state
`Workflow A - Product Research` is implemented in `scripts/product_research_workflow.py`.
Canonical proof exists via `tests/test_product_research_workflow.py` and `tests/test_db.py`.

Phase-3 hardening is complete:
- Business-key dedupe (product + supplier + source_uri) within and across runs.
- Mandatory dry-run preflight per source before any canonical write.
- WF-A summary telemetry (business_key_dedupe, source_preflight, confidence_profile).
- Idempotent run-key replay with bundle integrity hashes.

Phase-3 downstream workflows are scaffolded in the active queue as `route_only`:
- WF-1001 Listing Drafts (blocked on storefront connector approval)
- WF-1002 Creative Generation (blocked on ad-channel connector approval)
- WF-1003 Campaign Execution (blocked on connector + spend-cap approval)

## Last meaningful progress
- Phase 1A hardening: dedupe, dry-run preflight enforcement, telemetry, regression tests (13 tests green).
- Phase-3 queue scaffolding: WF-1001/1002/1003 registered with blockers and stop lines.
- TopDawg free-route verification (2026-08-29): all 7 shortlist leads rejected on public
  per-tier cost evidence — 4 home leads fail the 55% margin gate at every membership tier
  (best 48.1%), 3 pet leads sit below the $30 retail floor, 2 of them out of stock.
  Record: `derived/research/wf1000-topdawg-verification-2026-08-29.md`. $0 spent.

## In-scope
- In-place controls for workflow ownership, state, aliases, overrides, and freshness.
- Route command for validation (`workflow_router.py`).
- Canonical candidate writes through leased lanes with dry-run-first policy.

## Out of scope
- External connector implementations (phase 3).
- Campaign spend/launch automation.

## Preflight
- `state/ACTIVE_WORKFLOWS.md` must be valid.
- `state/WORKFLOW_ALIAS_INDEX.md` must contain canonical aliases.
- Overrides must be checked before advancing a blocked workflow.

## Execution posture
- Run preflight checks using `python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index`.
- Canonical writes require: dry-run first, then plan/lease/start lane, then write with `--lane-id`/`--lane-owner`.

## Acceptance gates
- Route/index freshness checks pass.
- Workflow remains in `active` lifecycle.
- No stale control artifacts when execution decisions are made.
- Dedupe and replay checks pass on repeated same-source reruns.

## Exit/closeout checklist
- [x] Active workflow owner verified.
- [x] Override state checked.
- [x] Capsule and index generated and fresh.
- [x] Control-plane validation checks pass.
- [x] Phase-3 downstream workflows scaffolded as route_only.

## Next pass
- TopDawg route is exhausted for the current leads (economics, not missing data).
  Return to the rank-1 Doba 40L trunk-organizer SKU verification (Doba free tier
  exposes unit cost), or sweep TopDawg's broader catalog on operator direction.
- Ingest an approved production catalog through dry-run + lane write once a lead
  passes pre-sample gates.
- Emit `derived/research/top-opportunities-*.json` handoff packets.
- Await operator approval for WF-1001/1002/1003 activation.

## Next safe action
Run:

```bash
python scripts/run_checks.py --skip-smoke
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
```
