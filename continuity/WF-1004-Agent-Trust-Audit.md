# Workflow 1004 continuity note

## Objective
Monetize the WF-1000 control plane as a productized service: apply policy gates,
evidence contracts, staged authority, and freshness harnesses to a client's own
agent stack, and report which of their agents' claims cannot be substantiated.

## Origin
WF-1000 (Product Research) evaluated 30 real supplier leads across 5 platforms
and produced an empty production shortlist at $0 spend. The dropshipping funnel
did not produce revenue; the machine built to run it safely is the asset. WF-1000
is re-scoped as a proof lab and supplies this workflow's reference case study.

## Current state
- Counterfactual baseline implemented: `scripts/gate_counterfactual.py`.
- Regression coverage: `tests/test_gate_counterfactual.py` (14 tests).
- Case study draft: `references/wf1004-agent-trust-audit-case-study.md`.
- Registered in the active queue as `active` with explicit stop lines.

## Measured baseline (2026-08-30)
Reproduce: `python scripts/gate_counterfactual.py`

- Corpus: 30 leads, 5 suppliers, 8 with verified public economics.
- Gated policy (actual): **0 selected**.
- Ungated policy (naive reconstruction): **3 selected** from 10 rankable.
- All 3 naive picks **pass** the 55% margin gate (62.5%, 60.0%, 55.2%).
  They are stopped by retail-floor, inventory, and evidence-completeness gates.
- Gate attribution: 18 of 30 leads (60%) rejected for incomplete evidence rather
  than bad economics.
- Spend: $250 sample budget approved, $0.00 spent.

## Honesty constraints (enforced in code)
- Avoided cost is never reported as earned revenue; asserted by
  `test_spend_is_reported_as_avoided_cost_not_revenue` and
  `test_report_never_claims_revenue`.
- Sales volume is not modelled; only per-unit economics are quantified.
- Leads without public unit economics are named as unquantifiable, never
  assigned assumed values.
- An empty or corrupt evidence corpus raises rather than emitting a hollow
  green baseline.

## Known limitations
- **No revenue has been earned. Demand is unvalidated.** No buyer conversation
  has taken place; pricing is an untested hypothesis and no price appears in any
  artifact.
- The ungated policy is a reconstruction of typical naive-agent behavior, not a
  recording of a real competing system. It is a reasoned baseline, not a
  measurement of a third party.
- "These 3 products would have lost money" is NOT established. The defensible
  claim is that they were bad decisions on the available evidence.
- The corpus is a single domain (US dropshipping) at a single point in time
  (2026-08-29). Generalization to other domains is untested.

## Out of scope
- External messaging, outreach, publication, or advertising.
- Pricing pages, proposals, or client-facing collateral.
- Any spend.

## Stop lines
- No external messages, outreach, or publication without operator approval.
- No pricing or capability claims presented as validated until a real
  engagement exists.
- Avoided cost must never be reported as earned revenue.

## Next safe action
Validate demand before building more tooling. Building is not the bottleneck;
selling is. The recommended next step is operator-led conversations with
prospective buyers, using the case study draft as the artifact.

Verification commands:

```bash
python scripts/gate_counterfactual.py
python -m unittest tests.test_gate_counterfactual -v
python scripts/run_checks.py --skip-smoke
python scripts/workflow_router.py WF-1004 --answer summary --validate
```
