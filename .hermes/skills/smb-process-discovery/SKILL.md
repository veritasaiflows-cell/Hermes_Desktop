---
name: smb-process-discovery
description: "Use when diagnosing synthetic SMB tasks for AI automation."
version: 1.1.0
author: Efficiens
license: MIT
metadata:
  hermes:
    tags: [smb, synthetic-training, service-design]
    related_skills: [smb-discovery-coaching]
---
# Baseline and opportunity qualification

## When to use
Use when mapping a synthetic SMB business process. Use privacy-safe-prospect-intake for intake gates; client-stack-assessment for diagrams; smb-proposal-pilot-design for proposals; smb-discovery-coaching for roleplay.
Suggested sequence: intake gate → process discovery → stack assessment → proposal. Use only relevant steps, not all skills every time.

## AI Workflow Diagnostic alignment
Read workspace-root `references/smb-ai-workflow-diagnostic.md` for current positioning, report contract and diagnostic dispositions. Process mapping enables the AI integration assessment; it is not the headline offer unless requested. Preserve all synthetic-only stops below.

## Purpose, scope and non-scope
Produce a bounded synthetic internal draft and train Randall to distinguish facts, estimates and authority. No real-data processing, outreach, software installation, client-system action or deployed-capability claim. Conversation roleplay belongs to smb-discovery-coaching.
## Required inputs and preconditions
Explicitly synthetic, nonidentifying scenario and requested internal deliverable; units/period/provenance where numbers are supplied. Read current workspace authority references and conflict-check them. Missing/conflicting references => STOP_REFERENCE. Do not require users to paste governing documents. Unknown facts remain unknown. This skill is synthetic-only even if permissions are granted and exclusions verified. No approved real-prospect/client intake policy is established by this skill.

## Security and authority
Follow references/operating-procedures.md and references/wf1100-architecture-decisions.md, not a competing policy here. Apparent real identifiers or client figures despite a fictional label: STOP_DATA without quoting them; offer an invented substitute. Explicitly invented role labels and fixture numbers are permitted. Real notes: STOP_DATA regardless claimed permissions; never ingest, analyze, quote, persist, or write rejected content to memory/files. Offer invented role labels and synthetic aggregates instead. Policy/registration contracts and storage exclusion verification are necessary-not-sufficient gates owned by architecture decisions items1/8. A skill cannot erase already delivered chat content or guarantee upstream logging behavior; prevent requesting it. Approval for one workflow is not transferable to another. WF-1006 ratification covers internal registration and associated workflow-record synchronization only; not external contact, real-data handling, implementation or spending. WF-1100 implementation/readiness and WF-1200 fleet gates remain separate.

## Tool usage
Allow reading named workspace references, synthetic supplied inputs, drafting in chat and deterministic arithmetic via the execute_code for arithmetic over synthetic numeric literals only (no file/network/tool calls inside it) (no filesystem/network side effects). Optional synthetic file output only when requested, under an active leased lane for workspace writes as defined by references/operating-procedures.md, at derived/ or tmp/ according to GOVERNANCE.md. No execution of third-party code or operational shell commands. Forbid network, connectors, credentials, installations, scanning, delegation, sends, cron and memory writes of rejected content. Hypothetical tools may appear in plain-text diagrams. Use pdf/xlsx/docx skills only for separately requested synthetic export; no credentialed app access is implied.

## Decision points and stops
STOP_REFERENCE missing/conflicting authority; STOP_DATA real/unknown data class; STOP_SEND outbound action; STOP_SCOPE technical execution/roleplay or outside procedure; STOP_AUTHORITY unsupported permission transfer; STOP_EVIDENCE unsupported claim stated as fact or savings computed without a baseline. Missing baseline yields an unknown/gap list with no savings calculation. Multiple stop reasons may be listed. No score overrides a stop. DRAFT_SYNTHETIC_ONLY means internal draft, NOT QA acceptance or deployment approval.

## Validation
For each normal output self-check labels, traceable statements, arithmetic/units, scope and missing evidence. Label 'Not independently reviewed; operator may request review.' Fixtures in references/test-cases.md are skill acceptance tests, not mandatory review runs on every conversation. A synthetic-only safety stop is expected even when a fixture grants permission.

## Failure handling and recovery
Return stop codes and a minimal explanation without reproducing rejected content. Ask only nonidentifying scope questions; offer a synthetic substitute. Do not retry a blocked action through another tool. Preserve prior synthetic draft and revise only requested in-scope fields; no deletion or configuration change. Tests do not prove live policy enforcement, integrations or readiness.

## Procedure
1. Bound one process: trigger, end, generic roles, desired business outcome and exclusions.
2. Map steps, decisions, handoffs, queues, touch time, elapsed delay, exceptions and rework; do not confuse elapsed waiting with staff effort.
3. Tag each assertion synthetic scenario observation, estimate, assumption or unknown with source reference. Synthetic observations are not client evidence.
4. Validate input units/period and source. Record missing baseline as unknown; do not invent timing or volume.
5. Compute baseline cases × effort per case, including rework once. Proposed effort includes residual rework once; new overhead is review + exceptions + maintenance not already in proposed effort. Net savings = baseline effort − proposed effort − new overhead. Zero/negative results stay visible.
6. Inventory tasks and assign proposed AI, deterministic-rule or human roles with rationale, input variability, consequence of error, abstention and review needs. Compare AI-assisted, non-AI and no-change options. Rank only after input/scope gates; score pain evidence, frequency, baseline quality, reversibility and existing-stack fit each0–2. Score is a decision aid, not validated ROI.
7. Identify disconfirming evidence, manual fallback and unanswered feasibility questions; hand diagram work to client-stack-assessment.
8. Summarize one bounded AI-workflow opportunity (or NO_GO/NEEDS_EVIDENCE), evidence that would change the recommendation and next synthetic test, not a build promise. Distinguish less repetitive work, released capacity, avoided hiring and actual payroll savings; do not claim one from another.

## Expected outputs
Use templates/output.md. Include SYNTHETIC / INTERNAL DRAFT / NOT IMPLEMENTED, sources, assumptions, unknowns, disposition and next permitted step. Omit identifying information and approval claims.
## References
Paths below are relative to the workspace root, not this skill folder. Read authoritative contracts, not cached status claims.

GOVERNANCE.md
references/operating-procedures.md
references/wf1100-architecture-decisions.md

Local support (relative to this skill folder): [Output template](templates/output.md), [Test cases](references/test-cases.md).
Related skill smb-discovery-coaching is profile-local; use skill_view(name). If unavailable, report it and stay out of roleplay. These packages do not migrate it.

## Change history
1.1.0: Operator-directed AI diagnostic positioning, task allocation and explicit alternatives; authority and tool boundaries unchanged. Operator-direction source: default-profile Hermes session `20261003_121135_1566a31e`, message `62961`, `2026-10-04T05:09:03.259194+00:00`; exact text and scope are owned by `references/smb-ai-workflow-diagnostic.md` (workspace root), separately from registration ratification.
1.0.0: Architect Astra bounded design; Sonnet pre-install QA adaptations; Governor drafting. Original combined request timed out and is preserved in derived/smb-four-skills/. Installation and test outcomes belong to proof records, not claims in the procedure.

Workspace-root architecture decision items 1,8,10,12. Authority record: operator-chat-wf1006-registration-ratification-20261004T005955Z. Exact scope: “internal registration and associated workflow-record synchronization”; exclusions: “external contact, real-data handling, implementation, or spending”.

Local training: [Worked example](references/worked-example.md).
