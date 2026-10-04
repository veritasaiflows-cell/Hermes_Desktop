# AI Workflow Opportunity Report — fictional quote intake
SYNTHETIC / INTERNAL DRAFT / NOT IMPLEMENTED
Not independently reviewed. Diagnostic disposition: **NEEDS_EVIDENCE**.

## Recommendation
Investigate whether AI-assisted extraction from varied email text can reduce retyping and missing-information effort, while rules check completeness and people retain pricing, exceptions and communication approval. This is a candidate to investigate, not a validated saving or ready integration.

## What the scenario actually establishes
Source: fictional coaching scenario, preserved in `artifacts/smb-readiness/Quote updated.xlsx` (Discovery worksheet and Process map).
- A request arrives by email; details are copied to Excel.
- An estimator prices the quote; staff email the quote back.
- Missing information causes a follow-up, waiting, an update and another estimator handoff.
- UNKNOWN: weekly volume, active minutes, rework frequency, waiting duration, exact email product, workbook hosting/version and estimator handoff mechanism.
- NOT CONFIRMED: telephone requests and “multiple times a day.” Those were learner assumptions.
No request is made for real customer emails, credentials or client records.

## Task assessment
| Task | Candidate allocation | Why / limit |
|---|---|---|
| Read varied request text and draft fields | AI candidate | Language variability may help justify AI; require exact source support and abstention |
| Check required fields and field types | Deterministic rules | Required-field list must first be agreed; a model cannot invent the business policy |
| Draft missing-information question | AI or fixed template | Compare a template first; employee approves any future customer message |
| Detect a replayed request | Deterministic event-ID check | Proposed control, not built; identity and retry semantics unknown |
| Resolve conflicting details | Human | Never silently choose a quantity or price |
| Price the quote | Estimator | Not part of the proposed AI scope |
| Authorize handoff and customer communication | Human | No automatic sends or live writes in this preparation |

Proposed path: request → validate input → AI draft → rules/evidence checks → employee review → separately approved handoff. Exact integration and fallback mechanisms are unresolved; existing manual work stays the fallback.

## Integration evidence
| Edge | What is known | What must be checked |
|---|---|---|
| Email → extraction input | Email in the fictional process | Product/plan, approved read scope, data handling, supported trigger, message ID |
| Draft → workbook | Excel in the fictional process | Local vs hosted workbook, format/table, supported write method, locking, deduplication, approval |
| Workbook → estimator | Estimator role exists | Actual handoff channel, record ownership, receipt and correction behavior |
No connector edge is verified. Excel alone does not establish Microsoft 365, Outlook or OneDrive.

## Business case
Net effort and price justification: UNKNOWN until volume, active baseline, proposed review burden and operating costs are supplied or measured. Do not apply the separate economics exercise to this fictional owner.
Compare AI extraction, a structured request form with rules, and the unchanged workflow. A form may be simpler if customers will use it; AI may be useful if varied email remains the preferred intake. Neither choice is established.

## Proposed evaluation, not an approved pilot
Use the eight invented teaching cases in evaluation-cases.json to discuss complete, missing, ambiguous, corrected, repeated, malicious, empty and malformed inputs. Before any real pilot, add representative permitted held-out data and approved sample/thresholds. All case executions remain NOT_RUN.
Measure correctness/evidence, abstention, review/correction effort, exception rate, elapsed latency and total cost. Do not infer production reliability from a small teaching set.
Human approval is required for extracted details, ambiguous requests, prices, external messages and any future system write. Stop for invented critical facts, unapproved action, data-boundary failure or negative net benefit.

## Next questions for synthetic practice
1. What happened in the last request, and how many minutes were active work rather than waiting?
2. Which fields are truly required before estimating, and who decides?
3. How many requests occur in the same period, and how trustworthy is that count?
4. What does staff have to check before trusting a draft?
5. What exact email/workbook environment is involved, and how is the estimator notified?

Next safe step: rehearse these questions with an invented owner, then revise the report's evidence gaps. No outreach, accounts, paid tools or integration build is included.
