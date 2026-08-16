# Wiki Change History

- page_type: change_log
- owner: scripts/wiki_bootstrap.py
- status: current
- generated_time: 2026-08-16T00:41:17Z
- source_artifacts:
  - scripts/wiki_bootstrap.py
  - AGENTS.md
  - prompts/hermes-trustworthy-work-operating-model-prompt.md
- source_hashes:
  - scripts/wiki_bootstrap.py: pending
  - AGENTS.md: pending
  - prompts/hermes-trustworthy-work-operating-model-prompt.md: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: AGENTS.md -> references/operating-procedures.md
- warnings: none
- next_action: Append every validated Wiki bootstrap publication and checksum update here.
- source_map:
  - scripts/wiki_bootstrap.py

## Change log

- 2026-08-15: Bootstrapped the universal Wiki structure with required sections and validator.
- 2026-08-15: Added manifest-driven atomic publish and last-known-good preservation.
- 2026-08-15: Implemented run_checks startup wiki validation wiring for baseline sessions.
- 2026-08-16: Aligned change-log freshness_rule daily->weekly (matches sibling pages and publish-event cadence); refreshed generated_time.
