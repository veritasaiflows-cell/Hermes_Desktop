# SMB AI Workflow Diagnostic — offer and preparation contract

Version: 1.0.0. Owner: WF-1006. Scope: internal planning and synthetic training.
Operator direction: reposition process analysis toward AI integration, then align workflows, skills, continuity and routing. This authorizes internal preparation, not external actions, real-data intake, purchases, accounts, deployment or fleet-gate changes.

## Operator-direction provenance

Source: Hermes default-profile session `20261003_121135_1566a31e`, user message
`62961`, timestamp `2026-10-04T05:09:03.259194+00:00`, retrieved from the actual
session record during checkpoint review. Exact operator text:

> Continue to update our workflows, any other skills, continuity notes, router, etc. to ensure it aligns with this posture. Continue to make additional enhacements to prepare for this and suggest additional tools we need to prepare us for it.

The immediately preceding response proposed the AI Workflow Diagnostic posture.
This later direction is the authority for the internal positioning and diagnostic
ownership alignment, including WF-1006 owning scoped demand analysis while
WF-1005 remains optional evidence. It does not remove the real-demand gate.
The 2026-09-26 platform-plan approval applies to its original text; subsequent
positioning edits are attributed to this later direction, not retroactively to
that approval. The separate registration-ratification record cited in the skills
is not the authority for this later offer change. All original implementation,
repair, data-handling and external-action approvals remain separately required.

## Positioning

Help a business identify and test AI automation that reduces repetitive task effort, starting with one workflow and an evidence-backed business case. Respect the owner's operational knowledge. Process mapping/cleanup is diagnostic and enabling work, not the headline standalone service unless specifically requested. AI is a candidate, not a predetermined answer; a non-AI recommendation or NO_GO can be the correct result.

Service sequence: **AI Workflow Diagnostic → bounded AI-assisted pilot → evidence-based expansion/support**. All later stages are conditional; no platform or connector is represented as operational by this document. Do not guarantee job replacement, savings, revenue or an implementation date.

## Ownership and routing

- **WF-1006** owns the AI Workflow Diagnostic, task inventory, feasibility questions, value analysis and pilot specification. It remains route_only; internal/synthetic preparation only. Preserve its stable ID and legacy discovery aliases.
- **WF-1005** remains the optional, separately authorized public Promise Desk. It is not a mandatory funnel step for a prospect explicitly seeking AI automation; it does not become an implementation service.
- **WF-1004** remains Agent Trust Audit for an existing agent system; not the default route for ordinary SMB AI adoption.
- **WF-1100** remains the gated technical foundation/implementation handoff; **WF-1200** retains fleet dispatch, repair-history and independent-review gates. Validated demand can be scoped in WF-1006 (with WF-1005 evidence if relevant); the real-demand requirement is not removed, and no platform phase opens here.
- Source truth: state/active_workflows.json and state/workflow_alias_index.json; synchronize their Markdown mirrors and regenerate index/capsules through workflow_router.py. No routing-code fork or new workflow shell is needed.

Authority remains in GOVERNANCE.md, references/operating-procedures.md and references/wf1100-architecture-decisions.md. Those controls take precedence over this service-positioning contract. Previously accepted technical contracts, failure history and proof packets are not rewritten.

## Diagnostic procedure

1. Establish one desired outcome and one workflow; clarify whether the aim is task effort, capacity, avoided hiring or actual cash cost. Do not equate them.
2. Capture the last occurrence, actor/system/input/output, work volume, touch time, waiting, exceptions and current quality. Label source, estimate, assumption or unknown. Current workspace exercises use fictional data only.
3. Break the workflow into tasks. For each, identify language judgment vs deterministic rules vs human decisions; repetitive does not automatically mean suitable for an LLM.
4. Record input variability, required output fields, error consequences, sensitive-data categories and review burden. Identify where the model must abstain instead of guessing.
5. Ask the stack-assessment owner to identify exact products/versions/plans, supported API/export paths, licensing, scopes, ownership, event identifiers, retry/replay behavior and integration unknowns. Vendor brand alone is not feasibility evidence. Public documentation, even if current, does not establish tenant access.
6. Propose an architecture: trigger → input validation → AI task (if justified) → schema/rule checks → human review → separately approved action → reconciliation/monitoring. Keep customer-supplied text untrusted; it cannot grant action permission.
7. Use smb-process-discovery's baseline/net-effort method. Include review, residual exceptions and maintenance once. Negative or zero benefit stays visible. Use smb-proposal-pilot-design for recurring expense and modeled capacity payback. No savings number without required inputs and provenance.
8. Compare AI-assisted, deterministic-automation and no-change alternatives against the same baseline; document why AI does or does not add value.
9. Build an evaluation plan before implementation: expected fields/evidence, forbidden behavior, difficult/held-out examples, reviewers, quality threshold, exception rate, time-to-review, latency and cost. Thresholds are proposed until approved; fixtures with expected answers are not model test results.
10. Return a disposition and a customer-facing-shaped internal report. A pilot specification needs its own scope, approvals, rollback, support owner, acceptance proof and independent review before implementation.

## Diagnostic dispositions (not workflow activation states)

- **NEEDS_EVIDENCE**: material baseline or integration facts missing; identify exactly what must be learned, and do not manufacture a ROI.
- **CANDIDATE_FOR_SCOPING**: plausible task with an explicit risk/value hypothesis; still not permission to build.
- **NO_GO**: costs/risks/negative net benefit defeat the case, or no acceptable access/review path exists.
- **PILOT_PROPOSED**: bounded internal specification assembled; unresolved approvals stay listed. Never relabel this as approved, implemented or accepted.

Apply safety/data gates before ranking. A weighted score cannot override refusal or a missing approval.

## Prospect-facing deliverable structure

The future report contains: objective; confirmed current workflow; task-level AI/rule/human allocation; proposed workflow; feasible/unknown integration paths; baseline and conditional economics; alternatives; risk and data boundaries; evaluation plan; proposed pilot scope; next decision. Keep learner scores and internal readiness checklists out of it.

Use .hermes/skills/smb-proposal-pilot-design/templates/ai-diagnostic-report.md as the reusable report template. Internal preparation packet: artifacts/smb-readiness/ai-workflow-diagnostic/. Historical practice workbook and PDF remain evidence/training materials, not revised claims of client capability.

## Quote-intake worked case

Scenario evidence: emailed request → details in Excel → estimator pricing → quote emailed back; missing details cause a customer follow-up, waiting and update. Volume, active effort, email vendor, Excel hosting/version and estimator handoff are UNKNOWN. Phone requests and multiple-times-daily volume were learner assumptions, not scenario facts.

Candidate: AI drafts structured details from a request and missing-information questions; deterministic checks validate required fields; a person verifies before anything moves forward. Estimator retains pricing, staff approve customer communications. No live mailbox, API, automatic send or approved spreadsheet write exists.

Current disposition: NEEDS_EVIDENCE. A synthetic learning dataset can test the evaluation contract, not prove integration or business benefit. Independently invented numerical calculator examples must be labeled separately; never attribute them to this conversation.

## Acceptance and preparation checklist

Before a proposed real engagement: approved prospect/client handling and storage exclusions; named scope and budget; supported stack evidence; representative permitted evaluation data; baselines; meaningful acceptance/error thresholds; human decision points; failure/rollback/support/retention plan; scoped implementation and reviewer lanes; exact author/reviewer provenance. Current posture changes satisfy none of those external or platform gates automatically.

For this preparation package: router aliases resolve to WF-1006; legacy aliases still work; status/blockers/dependencies/stop lines preserved; templates distinguish expected versus actual results; links resolve; test-case inventory is complete; illustrative economics are recomputed with tools and retain negative outcomes; scoped and repository checks run with truthful limitations.

## Tool selection

Prefer existing tools first: Hermes coaching, Excel/PDF/document skills, deterministic calculations, Git and pytest. Learn one automation runner only after a stack-fit decision. Power Automate is a candidate for Microsoft-centric environments; n8n is a candidate for cross-system workflows. Neither is selected/installed/authorized here. Promptfoo is optional when repeated prompt/model evaluation exceeds a simple fixture-and-pytest workflow. Local evaluation runners still transmit inputs to configured remote providers unless explicitly local.

Do not add CRM, vector databases, paid observability, broad connector bundles or voice-call agents without a diagnosed requirement. Never place a client's credentials or records in this shared training repository.

## Change history

1.0.0: Operator-directed AI diagnostic positioning; route/data/fleet boundaries retained. Deterministic verification belongs in derived/smb-ai-diagnostic-alignment/, not a claim of independent design review or deployed capability.
