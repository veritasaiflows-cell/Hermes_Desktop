# SMB Services Platform — implementation plan (WF-1100)

Status: **operator-approved 2026-09-26**. Governing workflow: WF-1100.
Delegation model: WF-1200 (`state/fleet-role-registry.json`).

## Current precedence — AI diagnostic preparation
`references/wf1100-architecture-decisions.md` governs current technical readiness,
repair sequencing and approval gates; implementation remains NOT READY.
`references/smb-ai-workflow-diagnostic.md` governs the operator-directed offer
alignment: WF-1006 is the AI Workflow Diagnostic, WF-1005 an optional evidence
service. Historic role names/first-attempt instructions below are not a new
dispatch authorization. Original accepted technical contracts and failed-attempt
proofs remain preserved; scope and approval gates remain separately required.
This alignment's authority is
user message `62961` in Hermes session `20261003_121135_1566a31e` at
`2026-10-04T05:09:03.259194+00:00`, quoted in the offer contract. The 2026-09-26
approval applies to the original plan, not these later positioning edits.

## Goal
One governed backbone that can serve any small business (trades, café, retail,
services) by composing capability modules — reporting, POS consolidation,
inventory, procurement, CRM — on shared foundations, instead of building a
separate product per client.

## Architecture (7 layers)
1. **Client isolation** — `clients/<client_id>/` per tenant: own SQLite DB,
   own `client.json` profile (industry, systems, modules enabled, approval
   contacts), own secrets namespace `CLIENT_<ID>_<CONNECTOR>_<FIELD>`.
   `state/client-registry.json` lists tenants; registration is gated.
2. **Common business data model** — `platform/schema/business.sql`: customers,
   vendors, items, locations, stock_levels, sales_orders + lines, purchase_orders
   + lines, invoices, payments, interactions. Every row carries provenance
   (`source_system`, `source_id`, `imported_at`, `import_batch_id`).
3. **Connector adapters** — one contract: `pull() -> records in common model`,
   `verify() -> reconciliation report`; permission tiers read → draft → write.
   First adapter: CSV/Excel (works with every client). Then Square, Clover,
   QuickBooks Online (read-only first).
4. **Capability modules** — each consumes only the common model; enabled per
   client in `client.json`. Reporting → POS consolidation → inventory →
   procurement → CRM.
5. **Intake & scoping** — WF-1006 AI Workflow Diagnostic → task/stack/value
   assessment → scoped pilot; WF-1005 Promise Desk provides optional public evidence.
6. **Client governance** — per-client approvals (PO sends, customer messages,
   price changes), audit trail, data-handling policy. Any external write or send
   needs operator + client approval.
7. **Demo sandbox** — synthetic tenants `demo-trades`, `demo-cafe`,
   `demo-retail` so every module can be demoed with zero real client data.

## Phases and acceptance
| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 Foundation | client registry + isolation, common schema, CSV import adapter, 3 demo datasets | pytest green; import of each demo dataset reconciles row counts; cross-tenant read test fails closed |
| 2 Reporting | weekly sales / top items / AR aging / margin reports from common model | reports reproducible from demo data; numbers tie to source CSV totals |
| 3 POS consolidation | read-only Square/Clover/QBO adapters → common model; dedupe across sources | `verify()` reconciliation report per source; no write scopes requested |
| 4 Inventory → Procurement | stock levels, reorder points, PO **drafts** | PO send path absent/gated; drafts require approval record |
| 5 CRM | customer timeline, follow-up drafts | no outbound message without approval |

Demand gate: before phases 3–5, validate and scope a real prospect need through
WF-1006, optionally informed by authorized WF-1005 evidence. Synthetic training
does not satisfy the gate. Order of 3–5 may be re-sequenced by validated demand.

## Phase-1 lane split (parallel, non-overlapping write surfaces)
| Lane | Allowed writes | Role |
|---|---|---|
| p1-client-registry | `platform/clients/`, `tests/platform/test_client_registry.py` | implementer |
| p1-business-schema | `platform/schema/`, `tests/platform/test_business_schema.py` | implementer |
| p1-csv-import | `platform/adapters/csv_import/`, `tests/platform/test_csv_import.py` | implementer |
| p1-demo-datasets | `platform/demo/` | implementer |

Flow per lane: architect (GPT-6-Astra) writes spec → governor (Opus 5.5)
approves and leases lane → implementer (DeepSeek v4.1 Flash) writes →
governor runs pytest → QA (GPT-5.6-Sol) reviews → on failure `retry` →
senior engineer (Muse Spark 1.3) repairs → second failure → human.

## Open decisions (operator)
1. Implementer write route: repair + pass the implementer canary, **or** record
   scoped `lane_exceptions` for the four phase-1 lanes.
2. Create per-role Hermes profiles (`architect`, `implementer`,
   `senior-engineer`, `qa`) vs. explicit `--provider/-m` flags per launch.
3. Client data-handling policy (required before any real client data).
