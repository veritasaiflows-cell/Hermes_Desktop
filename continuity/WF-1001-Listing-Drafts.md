# Workflow 1001 continuity note

## Objective
Generate storefront-ready listing drafts from WF-1000 top opportunities.

## Current state
`route_only` — scaffolded in the active workflow queue with no implementation script.
Blocked on storefront connector approval.

## Blockers
- No approved external connector configuration yet for phase 3.
- No approved storefront connector for listing publication.

## Stop lines
- Override removal before external writes.
- No listing publication without operator approval.

## In-scope (once activated)
- Consume `derived/research/top-opportunities-*.json` packets from WF-1000.
- Generate listing drafts (title, description, bullets, attributes) as internal artifacts.
- Write drafts to `derived/listings/` only; no external publication until connector approval.

## Out of scope
- Storefront publication.
- Any external write.

## Activation gates
- Operator approval recorded.
- Storefront connector approved and configured.
- WF-1000 has produced a reviewed top-opportunity packet.

## Next safe action
None — workflow is route_only. Await operator approval.
