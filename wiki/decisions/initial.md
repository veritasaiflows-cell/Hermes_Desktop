# Review-only Decisions

- page_type: decision_map
- owner: references/operating-procedures.md
- status: current
- generated_time: 2026-10-01T02:11:17Z
- source_artifacts:
  - references/operating-procedures.md
  - tests/test_run_checks.py
  - scripts/run_checks.py
- source_hashes:
  - references/operating-procedures.md: pending
  - tests/test_run_checks.py: pending
  - scripts/run_checks.py: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: scripts/run_checks.py -> AGENTS.md -> canonical/db.py
- warnings: none
- next_action: Maintain this page as a review checkpoint after control-plane changes.
- source_map:
  - references/operating-procedures.md
  - scripts/run_checks.py

## Active decisions

1. Keep startup and routing checks deterministic and replayable.
2. Treat stale control-plane artifacts as explicit blockers.
3. Use write lanes and preflight gates before enabling mutable writes.
4. Treat Wiki checks as startup-required evidence and refuse unverified startup.
