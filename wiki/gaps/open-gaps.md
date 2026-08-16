# Open Gaps and Debt Register

- page_type: gap_register
- owner: prompts/workspace-workflow-readiness-audit-2026-08-15.md
- status: current
- generated_time: 2026-08-15T00:40:00Z
- source_artifacts:
  - prompts/workspace-workflow-readiness-audit-2026-08-15.md
  - references/concurrent-lane-control-plane.md
  - scripts/concurrent_lane_manager.py
- source_hashes:
  - prompts/workspace-workflow-readiness-audit-2026-08-15.md: pending
  - references/concurrent-lane-control-plane.md: pending
  - scripts/concurrent_lane_manager.py: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: references/workflow-routing-control-plane.md
- warnings: includes known technical debt
- next_action: Prioritize lane and atomicity hardening before concurrent production writes.
- source_map:
  - prompts/workspace-workflow-readiness-audit-2026-08-15.md
  - references/concurrent-lane-control-plane.md

## Known gaps

- Workflow rerun idempotency.
- Source-map and route-schema strictness.
- Provenance and freshness enforcement for some workflow stages.
- Retrieval bootstrap and external connector readiness.
- Open recommendations from prompt remain:
  - Separate evaluator identity from the producer/editor pipeline.
  - Add holdout/adversarial eval suites before broad rollout.
  - Add shadow/canary promotion policy with explicit rollback proofs.
  - Add replay-time drift alerts for all critical claim tables.
