# Workflow 1002 continuity note

## Objective
Generate ad creative and copy drafts from WF-1000 top opportunities.

## Current state
`route_only` — scaffolded in the active workflow queue with no implementation script.
Blocked on ad-channel connector approval.

## Blockers
- No approved external connector configuration yet for phase 3.
- No approved ad-channel connector for creative delivery.

## Stop lines
- Override removal before external writes.
- No creative delivery without operator approval.

## In-scope (once activated)
- Consume `derived/research/top-opportunities-*.json` packets from WF-1000.
- Generate creative drafts (headlines, body copy, hooks) as internal artifacts.
- Write drafts to `derived/creatives/` only; no external delivery until connector approval.

## Out of scope
- Ad-channel delivery.
- Any external write.

## Activation gates
- Operator approval recorded.
- Ad-channel connector approved and configured.
- WF-1000 has produced a reviewed top-opportunity packet.

## Next safe action
None — workflow is route_only. Await operator approval.
