# Workflow 1100 continuity note

## Objective
Multi-client small-business services platform: a governed backbone that lets
the operator serve any small business with composable modules (reporting, POS
consolidation, inventory, procurement, CRM) on top of per-client isolation, a
common business data model, and connector adapters.

## Origin
Operator-approved implementation plan 2026-09-26
(`.hermes/plans/2026-09-26-smb-services-platform.md`). This opens the
small-business expansion path anticipated in WF-1005. Current service entry is
WF-1006's AI Workflow Diagnostic; WF-1005 is optional public-promise evidence.
Posture owner: `references/smb-ai-workflow-diagnostic.md`.

## Phase map
1. Foundation: client registry + per-client isolation, common business schema,
   CSV/Excel import adapter, synthetic demo datasets.
2. Reporting module (read-only).
3. POS consolidation (read-only Square / Clover / QuickBooks Online adapters).
4. Inventory, then procurement (reorder points, PO drafts; sends are gated).
5. CRM (customer timeline, follow-up drafts).
Each module is registered as its own WF-11xx workflow only when it is scoped.

## Current state
- effective_status: route_only.
- Phase-1 implementation was attempted under four operator-approved one-time
  Implementer exceptions. All four chats timed out after 900 seconds and all
  acceptance commands failed because their test targets were missing.
- Architect specs (GPT-6-Astra, gate-admitted read-only, session
  20260926_201347_e9ed8b): `derived/wf1100/phase1-architect-specs.md`.
  Governor review 2026-09-26: accepted. Notes: modules live under `platform/`
  with no `__init__.py` (stdlib `platform` wins import resolution); secret IDs
  hex-encode `client_id`; phase 1 is CSV-only (Excel deferred);
  `test_integrated_demo_imports` runs only after all four lanes merge.
- Partial, unaccepted source files exist at `platform/schema/smb_schema.py`,
  `platform/schema/business.sql`, and
  `platform/adapters/csv_import/smb_csv.py`; no required tests or demo dataset
  were produced. These files remain quarantined pending repair and QA.
- No client data exists; phase 1 remains synthetic-only.
- Phase 1 depends on WF-1200 fleet roles for implementation dispatch. The repair
  route is the Senior Engineer (`openai-codex/gpt-6.1-sol`, rebound 2026-10-03,
  verified live); no repair-cycle-1 lane has been opened yet.
- Phases 3-5 stay closed until a real prospect need is validated and scoped
  through WF-1006, optionally informed by authorized WF-1005 evidence.
  Synthetic training and the new positioning do not satisfy this demand gate.

## Current blockers
- All four phase-1 Implementer attempts timed out without required tests or accepted lane proofs.

## Out of scope
- Any live connector credential or write to a client system in phase 1.
- Real client data before a client data-handling policy exists.
- Opening module workflows before their phase is scoped.

## Stop lines
- No real client data or client credentials before a client data-handling policy is approved
- No write, send, or payment action against any external client system without operator and client approval
- Client data never leaves its own client directory or crosses into another client's store

## Next safe action
Reconcile WF-1200 readiness and actual Governor authority, diagnose the shared timeouts, then scope one schema-only repair-cycle-1 canary with acceptance and independent QA before dependent repairs. WF-1006 diagnostic preparation does not open implementation.

Keep the WF-1200 dependency and consumed Implementer history. Do not dispatch repairs while readiness remains blocked, ingest real client data before approved handling policy, or reset retry history. Keep phases 3-5 closed until a real prospect need is validated and scoped through WF-1006, optionally informed by authorized WF-1005 evidence; synthetic diagnostics do not satisfy the demand gate.

Preserve original attempt evidence; cycle 2 stops helper writes. No continuation
is opened by this posture-alignment update.

## Service-OS planning update (2026-10-03)
Astra session `20261003_162044_5a32b5` returned a gate-admitted, zero-tool
blueprint; raw evidence and sources are in
`derived/wf1100/service-os-2026-10-03/`. Accepted for planning only, not platform
implementation. The actual parent runtime is `openai-codex/gpt-6.1-sol`, not the
registry-primary Governor `anthropic/claude-opus-5-5`; no binding changed.

WF-1006 separately scopes the AI Workflow Diagnostic and synthetic training kit.
WF-1005 remains an optional public-promise route. Foundation repairs precede
reporting; phases 3–5 remain real-demand-gated, with WF-1006 service scoping.
No real client data before approved handling policy.

Execution and learning plan: `.hermes/plans/2026-10-03-smb-service-os.md`.
Use the pytest-enabled venv; default `python` has no pytest. Installed lane CLI
has no `retry` command: `plan --job-retry 1` can record a distinct continuation
ID with original parent/scope/failure proof, not a new zero-cycle attempt.
