# Open Gaps and Debt Register

- page_type: gap_register
- owner: references/operating-procedures.md
- status: current
- generated_time: 2026-10-08T03:24:21Z
- source_artifacts:
  - references/operating-procedures.md
  - references/concurrent-lane-control-plane.md
  - scripts/concurrent_lane_manager.py
- source_hashes:
  - references/operating-procedures.md: pending
  - references/concurrent-lane-control-plane.md: pending
  - scripts/concurrent_lane_manager.py: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: references/workflow-routing-control-plane.md
- warnings: includes known technical debt
- next_action: Prioritize connector approval and spend-cap policy before phase-3 workflow activation.
- source_map:
  - references/operating-procedures.md
  - references/concurrent-lane-control-plane.md

## Known gaps

- Workflow rerun idempotency. (RESOLVED 2026-08-16: business-key dedupe + run-key replay + dry-run preflight enforced in WF-1000.)
- Source-map and route-schema strictness.
- Provenance and freshness enforcement for some workflow stages.
- Retrieval bootstrap and external connector readiness. (Phase-3 workflows WF-1001/1002/1003 scaffolded as route_only; blocked on connector approval.)
- Open recommendations from prompt remain:
  - Separate evaluator identity from the producer/editor pipeline. (PARTIAL 2026-08-16: primary/secondary scorer disagreement + per-split evaluation profile now captured in confidence_profile.)
  - Add holdout/adversarial eval suites before broad rollout. (RESOLVED 2026-08-16: evaluation_profile added to product_research_workflow.py with drift detection.)
  - Add shadow/canary promotion policy with explicit rollback proofs.
  - Add replay-time drift alerts for all critical claim tables. (RESOLVED 2026-08-16: cron_claim_drift_check.py monitors expiring claims, tampered workflow runs, and stale replays.)
  - Add automated validators for schema drift, lane-register integrity, and cron-registration targets. (RESOLVED 2026-08-16: test_schema_current.py, test_lane_register_integrity.py, cron_registration_validator.py + test; wired into run_checks and A2 health gate.)
- Wiki publish deadlock: RESOLVED 2026-08-30. `scripts/wiki_bootstrap.py publish` refused stale candidate pages before it could regenerate their `generated_time`, so a page that aged past its freshness window could not be refreshed by its own owner command (worked around manually on 2026-08-23 under lane `wiki-freshness-review-2026-08-23`). A `reattest` action now closes the loop: it refreshes `generated_time` only for pages whose sole outstanding issue is `stale_freshness` and whose declared source artifacts still hash to their published manifest values, and refuses everything else with explicit reasons. Regression coverage in `tests/test_wiki_bootstrap.py` includes refusal on genuine source drift.
- Acceptance-command environment drift: contract phases invoking bare `python -m pytest` fail under the implementation-job executor because `sys.executable` resolves to a uv-managed interpreter without pytest. Mitigated 2026-08-23 by pinning `uv run --with pytest` in phases P1-P4 of `state/implementation-jobs/semantic-memory-long-work-upgrade.json`; no general guard prevents a future phase from reintroducing the bare form.
