# Operating Model Synthesis

- page_type: synthesis
- owner: references/operating-procedures.md
- status: current
- generated_time: 2026-08-15T00:20:00Z
- source_artifacts:
  - references/operating-procedures.md
  - references/memory-routing.md
  - prompts/hermes-trustworthy-work-operating-model-prompt.md
- source_hashes:
  - references/operating-procedures.md: pending
  - references/memory-routing.md: pending
  - prompts/hermes-trustworthy-work-operating-model-prompt.md: pending
- freshness_rule: weekly
- authority_boundary: review_only
- promotion_path: references/operating-procedures.md -> references/workflow-routing-control-plane.md
- warnings: none
- next_action: Reconcile this synthesis whenever a control-plane procedure changes.
- source_map:
  - references/operating-procedures.md
  - prompts/hermes-trustworthy-work-operating-model-prompt.md

## System map (durable)

- Core control plane entry path: identity → routing → preflight → run → evidence closeout.
- Evidence flow: source records and canonical state are primary; wiki and decisions are durable syntheses only.
- Recovery flow: stale signatures or stale freshness requires explicit rebuild, revalidation, and owner handoff.
