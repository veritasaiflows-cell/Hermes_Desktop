---
name: privacy-safe-prospect-intake
description: "Use when checking synthetic prospect intake boundaries."
version: 1.1.0
author: Efficiens
license: MIT
metadata:
  hermes:
    tags: [smb, synthetic-training, service-design]
    related_skills: [smb-discovery-coaching]
---
# Minimal intake and permission-gap checklist

## When to use
Use when checking synthetic prospect intake boundaries. Use smb-process-discovery for detailed process mapping/calculations; smb-discovery-coaching for roleplay; this skill owns the shared approval-checklist template.
Suggested sequence: intake gate → process discovery → stack assessment → proposal. Use only relevant steps, not all skills every time.

## AI Workflow Diagnostic alignment
Read workspace-root `references/smb-ai-workflow-diagnostic.md`. Capture the invented buyer's automation objective, one repetitive task and intended benefit category; diagnostic interest is not access permission. Ask about data categories and approval gaps, not sample real emails or records. Route accepted synthetic inputs to the existing discovery/stack/proposal skills.

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
1. Determine data class from the operator explicit statement before analysis; do not request sample real records.
2. Confirm supplied description explicitly invented/nonidentifying and objective internal training; unknown class blocks intake.
3. Load current authoritative workspace boundaries; permission and exclusion evidence alone never lift synthetic-only scope.
4. Use templates/approval-checklist.md to record proposed requirements, evidence reference or missing, and open question. Include policy, data, location, access, retention, permission tier and vendor/model permission. Never approve them.
5. Capture only invented scenario label, stated purpose, generic role, minimal friction and unknowns. Do not expand into CRM or identify a business.
6. Record supplied estimates with units/period as unverified; do not calculate discovery ROI here.
7. Choose DRAFT_SYNTHETIC_ONLY or a stop code. Actual outreach/registration/write is outside this procedure.
8. Offer a synthetic substitute on rejection; neither quote rejected material nor ask identifying follow-ups.

## Expected outputs
Use templates/output.md. Include SYNTHETIC / INTERNAL DRAFT / NOT IMPLEMENTED, sources, assumptions, unknowns, disposition and next permitted step. Omit identifying information and approval claims.
## References
Paths below are relative to the workspace root, not this skill folder. Read authoritative contracts, not cached status claims.

GOVERNANCE.md
references/operating-procedures.md
references/wf1100-architecture-decisions.md

Local support (relative to this skill folder): [Output template](templates/output.md), [Test cases](references/test-cases.md). [Approval checklist](templates/approval-checklist.md).
Related skill smb-discovery-coaching is profile-local; use skill_view(name). If unavailable, report it and stay out of roleplay. These packages do not migrate it.

## Change history
1.1.0: AI diagnostic intake framing; no change to synthetic-only refusal or approval-checklist ownership. Operator-direction source: default-profile Hermes session `20261003_121135_1566a31e`, message `62961`, `2026-10-04T05:09:03.259194+00:00`; exact text and scope are owned by `references/smb-ai-workflow-diagnostic.md` (workspace root), separately from registration ratification.
1.0.0: Architect Astra bounded design; Sonnet pre-install QA adaptations; Governor drafting. Original combined request timed out and is preserved in derived/smb-four-skills/. Installation and test outcomes belong to proof records, not claims in the procedure.


Workspace-root architecture decision items 1,8,10. Authority record: operator-chat-wf1006-registration-ratification-20261004T005955Z. Exact scope: “internal registration and associated workflow-record synchronization”; exclusions: “external contact, real-data handling, implementation, or spending”.

Local training: [Worked example](references/worked-example.md).
