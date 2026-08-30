# Workflow 1000 continuity note

## Objective
**Re-scoped 2026-08-30: WF-1000 is a PROOF LAB, not a revenue engine.**

Its original objective was to build and harden Workflow A before enabling new
monetization workflows. That hardening is complete, and the funnel's verdict is
in: 30 real supplier leads across 5 platforms, 0 qualified, $0 spent. The empty
shortlist is the finished result, not an unfinished one.

WF-1000's ongoing value is as the reference implementation and evidence corpus
behind WF-1004 (AI Agent Trust Audit), which monetizes the control plane rather
than the storefront. See `continuity/WF-1004-Agent-Trust-Audit.md`.

Do not loosen launch-policy gates to force a non-empty shortlist. The refusal is
the asset.

## Current state
`Workflow A - Product Research` is implemented in `scripts/product_research_workflow.py`.
Canonical proof exists via `tests/test_product_research_workflow.py` and `tests/test_db.py`.

Phase-3 hardening is complete:
- Business-key dedupe (product + supplier + source_uri) within and across runs.
- Mandatory dry-run preflight per source before any canonical write.
- WF-A summary telemetry (business_key_dedupe, source_preflight, confidence_profile).
- Idempotent run-key replay with bundle integrity hashes.

Interest-alignment hardening is implemented:
- `state/operator-interest-profile.json` makes the AI revenue-systems audience and
  approved physical-product theses explicit, versioned, hashed, and fail-closed.
- Strategic fit is independent from commercial viability and is the primary ranking axis.
- Executable `research`, `organic_sample`, and `paid_launch` modes prevent an
  organic-only candidate from being mislabeled for paid acquisition.
- Product-character requirements, contribution-dollar floors, profitable CAC,
  reason-count telemetry, and production-event-backed reporting are enforced.
- The default opportunity report excludes the five legacy/demo entities; diagnostics
  require `--include-unverified`.

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
- Doba 40L trunk-organizer verification (2026-08-29): rejected before account-gated
  unit-cost access. Exact-title current retail listings are $22.77–$23.99, below the
  $30 launch-policy floor. Doba's current public route is a $0.99 trial, not a permanent
  free tier. Record: `derived/research/wf1000-doba-verification-2026-08-29.md`. $0 spent.
- CJdropshipping/Zendrop and second no-drill cable-management pass (2026-08-29):
  both platforms remain gated supplier leads, but 0 exact offers qualified for
  research, organic sample, or paid launch. The strongest exact lead, TopDawg SKU
  `3239-HG_DeskCableTray_GPCT3729`, was publicly out of stock; Doba withheld unit
  cost, TVCMALL did not prove US stock or <=7-day landed delivery, and the reviewed
  Zendrop pages did not expose an evidence-complete SKU. Record:
  `derived/research/wf1000-cj-zendrop-public-review-2026-08-29.md`. $0 spent.

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
- WF-1000 is now a proof lab. Its evidence corpus feeds WF-1004; see
  `scripts/gate_counterfactual.py` for the measured gated-vs-ungated baseline.
- Keep the Creator Desk Reset shortlist empty unless a named supplier SKU proves the
  complete public evidence contract. Do not repeat broad public searches without a new
  route or a material evidence change.
- If the operator approves account-gated verification, inspect only one named SKU and
  target ZIP with stop lines for exact variant stock, product cost, shipping, delivery,
  return applicability, and compliance before any sample purchase.
- Secondary hypotheses are a Shopify content-capture mount kit and a seller operations
  station kit. Generic pet, automotive, kitchen, garden, and household sweeps remain out.
- Ingest an approved production catalog through dry-run + lane write once a lead
  passes pre-sample gates.
- Emit `derived/research/top-opportunities-*.json` handoff packets.
- Await operator approval for WF-1001/1002/1003 activation.

## Next safe action
Run:

```bash
python scripts/run_checks.py --skip-smoke
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
python scripts/top_opportunities_report.py canonical/efficiens.db --as-json
```
