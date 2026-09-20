# Wiki Change History

- page_type: change_log
- owner: scripts/wiki_bootstrap.py
- status: current
- generated_time: 2026-09-20T19:45:50Z
- source_artifacts:
  - scripts/wiki_bootstrap.py
  - AGENTS.md
  - references/operating-procedures.md
- source_hashes:
  - scripts/wiki_bootstrap.py: pending
  - AGENTS.md: pending
  - references/operating-procedures.md: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: AGENTS.md -> references/operating-procedures.md
- warnings: none
- next_action: Append every validated Wiki bootstrap publication and checksum update here.
- source_map:
  - scripts/wiki_bootstrap.py
  - AGENTS.md

## Change log

- 2026-08-15: Bootstrapped the universal Wiki structure with required sections and validator.
- 2026-08-15: Added manifest-driven atomic publish and last-known-good preservation.
- 2026-08-15: Implemented run_checks startup wiki validation wiring for baseline sessions.
- 2026-08-16: Aligned change-log freshness_rule daily->weekly (matches sibling pages and publish-event cadence); refreshed generated_time.
- 2026-08-16: Phase-3 completion for WF-A: business-key dedupe, dry-run preflight enforcement, summary telemetry, and regression tests landed in scripts/product_research_workflow.py. Scaffolded WF-1001/1002/1003 as route_only in the active workflow queue with connector blockers and stop lines. Ingested source/catalog-aug-2026.csv through dry-run + leased-lane write; verified idempotent replay.
- 2026-08-16: P0-P5 workspace improvement sweep: wiki sources realigned after prompt cleanup and manifest published fresh; cron jobs registered directly against repo scripts; holdout/adversarial evaluation_profile added to product_research_workflow.py; claim-drift monitor (cron_claim_drift_check.py) and hourly cron added; pytest installed and run_checks.py prefers pytest with unittest fallback.
- 2026-08-16: Validator suite added: schema-current (test_schema_current.py), lane-register-integrity (test_lane_register_integrity.py), and cron-registration (cron_registration_validator.py + test_cron_registration_validator.py) checks run on every test invocation; cron-registration validator also wired into A2 health check every 4 hours.
- 2026-08-16: A9 claim-drift cron wrapper fixed: created a9_claim_drift_check.py in Hermes profile scripts directory, updated cron job to use wrapper pattern, and adjusted cron_registration_validator to expect wrapper-based A9.
- 2026-08-23: Implementation-job P1 (section indexing and retrieval registry v2) accepted with receipt 9684cd7d-9eb4-4ccd-9616-dbc712f8251c. Section-level chunking landed in scripts/workspace_index.py and scripts/vector_memory_index.py; scripts/retrieval_refresh.py gained a retrieval-sources.v2 registry with per-source chunking, source_family, authority_class, and source_type while remaining v1-compatible; tests/test_retrieval_refresh.py added. Focused acceptance: 49 passed.
- 2026-08-23: Correctness proof restored after the write lane closed. cron_test_gate.py returned TEST GATE OK with 330 tests, 0 failures, at commit 5cc35be with matching pre/post source fingerprints.
- 2026-08-23: Retrieval indexes rebuilt from the v2 manifest: 16 approved sources now yield 144 sections in both workspace-index.sqlite and vector-memory.sqlite, with stale_source_count 0 on each.
- 2026-08-23: Graphify refreshed and rebaselined (1341 nodes, 2972 edges, 79 communities); graphify_freshness.py reports fresh with no issues.
- 2026-08-23: This page and wiki/gaps/open-gaps.md were manually refreshed under lane wiki-freshness-review-2026-08-23 because publish refuses stale candidates before regenerating them; the deadlock is recorded as an open gap.
- 2026-08-30: Wiki publish deadlock closed in code. scripts/wiki_bootstrap.py gained a `reattest` action that refreshes generated_time ONLY for pages whose sole issue is stale_freshness and whose declared source artifacts still hash to their published manifest values; pages with real source drift, missing markers, forbidden authority language, or unproven `pending` hashes are refused with reasons. Re-attestation asserts "sources re-verified unchanged", never "content regenerated". Five regression tests added in tests/test_wiki_bootstrap.py, including refusal on genuine source drift and CRLF preservation. This replaces the 2026-08-23 manual refresh workaround; the open gap is resolved.
- 2026-09-20: A1 wiki regen wrapper (scripts/cron_wiki_regen.py) now chains the reattest action before publish. All six required pages aged past the weekly freshness window from 2026-09-01 onward and publish refused stale candidates, so A1 failed on 6 consecutive daily runs; nothing in the daily path invoked reattest. The wrapper now runs `wiki_bootstrap.py reattest` first (refreshing generated_time for time-only-stale pages whose sources re-verify) and then publishes; refusal of a genuinely drifted page still flows into publish, which reports it and exits non-zero. Contract tests updated in tests/test_cron_wrappers.py (reattest-before-publish order, refusal passthrough, reattest timeout fails closed). Closed under lane wiki-reattest-a1-2026-09-20.
