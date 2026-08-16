# Workflow 1003 continuity note

## Objective
Plan and execute ad campaigns with spend controls from approved WF-1000 opportunities,
WF-1001 listings, and WF-1002 creative.

## Current state
`route_only` — scaffolded in the active workflow queue with no implementation script.
Blocked on connector approval and spend-cap policy.

## Blockers
- No approved external connector configuration yet for phase 3.
- No approved ad-channel connector for campaign execution.
- No spend-cap policy approved for campaign budgets.

## Stop lines
- Override removal before external writes.
- No campaign spend without operator approval.

## In-scope (once activated)
- Consume WF-1000/1001/1002 artifacts.
- Plan campaign structure, budgets, and bid strategy as internal artifacts.
- Write plans to `derived/campaigns/` only; no external execution until connector approval.

## Out of scope
- Ad spend execution.
- Any external write.

## Activation gates
- Operator approval recorded.
- Ad-channel connector approved and configured.
- Spend-cap policy approved.
- WF-1001 and WF-1002 outputs reviewed.

## Next safe action
None — workflow is route_only. Await operator approval.
