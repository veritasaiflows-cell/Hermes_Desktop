# References layer

Authoritative detailed instructions that the lean bootstrap (`AGENTS.md`) points to instead of embedding. Per `GOVERNANCE.md`, the bootstrap routes work; references and skills explain how to perform it.

## Contents

- `operating-procedures.md` — full standard operating loop, workspace memory architecture, learning/telemetry instrumentation, helper-agent rules, workspace change policy, and the failure-handling taxonomy.
- `memory-routing.md` — SQL versus semantic retrieval selection, evidence/freshness rules, approved memory writes, and retrieval observability.
- `graph-memory.md` — durable graph layer: relationship schema, write discipline, traversal commands, and graph route selection.
- `commerce-data-model.md` — Phase 1 commercial entity/metric conventions for monetization workflows.
- `workflow-product-research.md` — phase-2 workflow scaffold: product research ingestion and candidate scoring.
- `workflow-routing-control-plane.md` — routing and ownership control-plane implementation for workflow capsules and stale checks.
- `concurrent-lane-control-plane.md` — collision-safe lane leasing and completion proof protocol.
- `script-index.md` — authoritative routing surface for the `scripts/` layer (purpose, entrypoints, test anchors, owning references).
- `implementer-canary-runbook.md` — bounded, evidence-only canary qualification for a tool-using Implementer Bot (RED baseline, diff/scope/frozen-hash/signature oracle).
- `wiki/` (new): authoritative bootstrap evidence for startup and operational evidence routing.

## Rules

- A reference is the single authoritative owner of the detail it holds. Do not copy this detail back into bootstrap files.
- References must not redefine identity, global safety rules, or authority boundaries — those are owned by the bootstrap. Reference them instead.
- Keep a change-history note at the end of each reference.
