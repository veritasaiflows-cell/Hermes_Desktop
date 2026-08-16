# Wiki Change History

- page_type: change_log
- owner: scripts/wiki_bootstrap.py
- status: current
- generated_time: 2026-08-16T00:41:17Z
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
