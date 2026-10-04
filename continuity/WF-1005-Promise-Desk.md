# Workflow 1005 continuity note

## Objective
Promise desk for local small businesses: extract a business's own public
promises (response-time, hours, pricing, licensing) from its website and public
records, bind each to dated evidence, and measure the gap between what the
business promises and what its published infrastructure shows. The finding
sells the engagement; it does not require the client to use AI.

## Origin
Re-scoped from the WF-1004 Agent Trust Audit after recognizing most target
small businesses have no agent stack to audit. The durable workspace asset —
evidence-bound claims with freshness, plus honest empty/negative baselines from
WF-1000 — applies directly to promises a trade or service business publishes.

## Target niche (initial)
Owner-operated residential trades in the Phoenix East Valley (Mesa first, then
Gilbert / Chandler / Queen Creek). Chosen because:
- measurable public promises exist (24/7, response windows, ROC license numbers,
  flat-rate pricing);
- a broken promise carries observable dollar cost per missed call;
- the owner is reachable and is the decision maker.

Wave-1 candidate list lives in the operator's intake table (10 named
contractors: plumbing ×5, HVAC ×4, dental ×1). Dental is a separate wave —
appointment follow-up, not emergency response — and is out of scope for the
first case study so the before/after stays clean.

## Current state
- effective_status: route_only.
- No implementation script. Control surface only.
- No data collected on any named business yet.
- No paying client engagement exists; demand is unvalidated.
- No operator-selected target business has been approved for examination.

## Out of scope
- Catalog e-commerce scoring (WF-1000 covers that lane; this desk is services,
  not supplier catalogs).
- Building the fix (call handling, scheduling, follow-up automation). The audit
  measures the leak; remediation is a separate, later workflow, not implied.
- Any outreach, external message, or monitoring without operator approval.

## Stop lines
- No external messages, outreach, or publication without operator approval.
- No monitoring of or test contact with a named business without operator
  authorization and a recorded consent decision.
- No implication that a fix exists or is included; deliverable is a measured
  gap report only.
- Avoided cost and lost-revenue figures are labeled estimates, never measured
  or earned revenue.

## Next safe action
Use Promise Desk only when public-promise evidence is the requested deliverable; otherwise route AI Workflow Diagnostic preparation to WF-1006. Named targets and consent decision must be approved before any measurement pass.

Do not collect data on or contact any named business until the operator selects targets and approves. Do not build a Promise Desk implementation script until one buyer conversation has occurred. Scope any requested remediation separately through WF-1006 and the gated WF-1100 platform.

## Separately scoped service path (2026-10-03)
WF-1006 owns AI Workflow Diagnostic preparation; WF-1100 owns the reusable
platform foundation. Both remain route_only with their own records and gates.
Promise Desk is an optional public-evidence service, not a mandatory front door
for AI-seeking buyers or a bundled remediation service. Lead handling, follow-up,
scheduling, invoicing and AI implementation are candidate offers only; no module
is activated here. Posture owner: `references/smb-ai-workflow-diagnostic.md`.
Starting plan: `.hermes/plans/2026-10-03-smb-service-os.md`.
Conversation kit: `derived/wf1100/service-os-2026-10-03/conversation-kit.md`.
