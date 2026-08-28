# AI Dropshipping Controlled-DTC Monetization Plan

> **For Hermes:** Use the relevant implementation, workflow-control, and security skills task-by-task. Do not activate storefront publication, supplier ordering, ad delivery, payment collection, or spend without explicit operator approval.

**Goal:** Turn the existing research-only commerce scaffold into a controlled, evidence-backed direct-to-consumer (DTC) product-validation system that can monetize one verified product at a time while preserving human approval over every external write and every dollar of spend.

**Architecture:** The implementation remains a staged pipeline: verified supplier data → WF-1000 product research and unit-economics gate → internal listing and creative artifacts → internal campaign plan → explicitly approved storefront/ad connectors → order and campaign telemetry → scale/pause decisions. Existing canonical `entities`, `metrics`, `events`, `workflow_runs`, and `claims` remain the initial system of record; dedicated commerce tables are deferred until the generic model proves insufficient.

**Tech Stack:** Existing Python 3.11 standard-library workspace, SQLite canonical store, `CanonicalDB`, `workflow_router.py`, deterministic JSON/CSV artifacts, Windows Credential Manager through `scripts/secrets_helper.py`, and a future narrowly scoped storefront adapter. No runtime third-party dependency is currently present.

---

## Current verified context

- **WF-1000 Product Research is the only active workflow.** It has dry-run-first preflight, business-key dedupe, replay protection, provenance, and canonical write controls (`scripts/product_research_workflow.py`).
- **WF-1001 Listing Drafts, WF-1002 Creative Generation, and WF-1003 Campaign Execution are scaffolded only.** They have no implementation scripts and are `route_only` until connector, operator-approval, and spend-control gates are satisfied.
- The current control plane is fresh and reports no overrides. WF-1001 and WF-1002 each have two external-connector blockers; WF-1003 also requires an approved spend-cap policy.
- The existing `catalog-aug-2026.csv` is **not production supplier evidence**: its source URLs are `example.com` placeholders. It can validate the pipeline but cannot justify a real purchase, listing, or ad spend.
- Current shortlist from the canonical DB is illustrative only:

| Rank | Candidate | Viability | Supplier | Listed retail / unit cost | Pre-fulfillment price-cost spread |
|---:|---|---:|---|---:|---:|
| 1 | Hydra Bluetooth Headset | 0.6712 | Supplier North | $49.99 / $18.00 | $31.99 |
| 2 | Aero Tumbler | 0.6624 | Supplier X | $24.99 / $9.50 | $15.49 |
| 3 | Wireless Charging Pad | 0.6381 | Supplier X | $19.99 / $7.50 | $12.49 |
| 4 | Mini Projector | 0.6235 | Supplier North | $89.99 / $32.00 | $57.99 |

The listed price-cost spreads exclude shipping, packaging, payment processing, taxes, duties, refunds/chargebacks, customer support, and customer-acquisition cost (CAC). They are not profit forecasts. The catalog's `margin_percent` values also do not consistently equal the simple price-cost spread percentages, so metric semantics must be reconciled before launch selection.

---

## Monetization route

### Primary revenue model: one-product DTC direct fulfillment

1. A customer purchases a verified product on the storefront at an approved retail price.
2. The supplier fulfills the order under an agreed service level.
3. The business retains only the contribution remaining after all variable costs: product/fulfillment cost, shipping, payment fees, customer support, refunds/chargebacks, taxes/duties where applicable, and CAC.
4. We scale only products and acquisition channels whose observed contribution remains positive after those costs—not products that merely look attractive in supplier-sheet margin fields.

### Secondary upside, only after positive unit economics

- Bundles, quantity breaks, order bumps, and post-purchase cross-sells can raise average order value.
- Email/SMS retention and replenishment sequences can reduce dependence on paid acquisition for products with repeat-purchase potential.
- These are experiments, not assumed revenue streams; they must be separately tracked and approved.

### Operating principle

AI reduces research, content-production, and analysis time. It does **not** make a product sell automatically and it does **not** receive authority to publish, spend, place supplier orders, or alter price without operator approval.

---

## Implementation milestones

### Milestone 0 — Define launch policy and verify real supplier evidence

**Objective:** Replace demo inputs with decision-grade evidence and write the business constraints before automation can act.

**Files:**
- Create: `state/commerce-launch-policy.json`
- Modify: `references/commerce-data-model.md`
- Modify: `references/workflow-product-research.md`
- Modify: `scripts/product_research_workflow.py`
- Modify: `tests/test_product_research_workflow.py`
- Create: `tests/test_commerce_launch_policy.py`

**Work:**
1. Record the operator-approved target market, return policy, permitted product categories, launch channel, fulfillment geography, maximum test spend, daily spend cap, stop-loss rule, and required human approvals in `state/commerce-launch-policy.json`.
2. Extend supplier-input validation to require or explicitly mark unknown: supplier identity, source timestamp, inventory availability, fulfillment/shipping cost, delivery estimate, return/chargeback exposure, and product compliance/claim risks.
3. Add a single explicit unit-economics record per candidate rather than treating `margin_percent` as a decision-ready profit value. Preserve source data and record a validation failure when source margin and derived cost/retail math use incompatible semantics.
4. Use a real, authorized supplier feed or manually verified catalog. Run WF-1000 dry-run, review its result, then use the existing leased-lane write process only after the source is approved.

**Acceptance:** A reviewed candidate packet includes provenance, source freshness, total variable-cost assumptions, fulfillment conditions, and an operator-visible reason for selection or rejection.

---

### Milestone 1 — Build internal-only listing drafts (WF-1001)

**Objective:** Transform reviewed WF-1000 packets into high-quality product-page drafts without publishing anything externally.

**Files:**
- Create: `scripts/listing_drafts_workflow.py`
- Create: `tests/test_listing_drafts_workflow.py`
- Create: `derived/listings/.gitkeep` if the directory does not yet exist
- Modify: `state/workflows/WF-1001.json` only when the implementation and its tests are ready; do not remove its external-write stop lines
- Modify: `continuity/WF-1001-Listing-Drafts.md`

**Work:**
1. Accept only a reviewed `derived/research/top-opportunities-*.json` handoff packet.
2. Generate versioned internal artifacts containing title, description, feature bullets, technical attributes, shipping/returns disclosure placeholders, FAQs, and a list of claims requiring human substantiation.
3. Validate that no unverified performance, medical, safety, or comparative claim is emitted as fact.
4. Keep output in `derived/listings/`; do not call storefront APIs.

**Acceptance:** Tests prove input provenance validation, deterministic versioning, claim-flagging behavior, and no network/external-write path.

---

### Milestone 2 — Build internal creative generation (WF-1002)

**Objective:** Produce reviewable acquisition creative variants tied to an approved product and listing draft.

**Files:**
- Create: `scripts/creative_generation_workflow.py`
- Create: `tests/test_creative_generation_workflow.py`
- Create: `derived/creatives/.gitkeep` if the directory does not yet exist
- Modify: `state/workflows/WF-1002.json` only after internal-only acceptance
- Modify: `continuity/WF-1002-Creative-Generation.md`

**Work:**
1. Create structured creative briefs, hooks, headlines, body copy, landing-page angles, and disclosure/claim review flags.
2. Version every artifact by candidate ID and listing version so performance can later be attributed to a specific message.
3. Treat image/video asset generation and rights clearance as separate approval gates; do not assume generated assets are publishable.
4. Keep output in `derived/creatives/`; do not deliver to an ad platform.

**Acceptance:** Tests prove traceability to the candidate/listing, no unsupported claims, idempotent re-runs, and no external delivery.

---

### Milestone 3 — Build internal campaign planning and economics checks (WF-1003)

**Objective:** Produce a bounded test plan that is measurable before any paid campaign exists.

**Files:**
- Create: `scripts/campaign_planning_workflow.py`
- Create: `tests/test_campaign_planning_workflow.py`
- Create: `derived/campaigns/.gitkeep` if the directory does not yet exist
- Modify: `references/commerce-data-model.md`
- Modify: `state/workflows/WF-1003.json` only after internal-only acceptance
- Modify: `continuity/WF-1003-Campaign-Execution.md`

**Work:**
1. Consume reviewed research, listing, creative, and launch-policy artifacts.
2. Produce a campaign plan containing audience hypothesis, creative variants, landing destination, approved budget ceiling, duration, evaluation window, stop conditions, and manual approval checkpoint.
3. Define canonical measurements for `commerce.cogs`, `commerce.margin_absolute`, `commerce.cac`, `commerce.conversion_rate`, and `commerce.roas`, using enough dimensions to connect product, creative, campaign, order, and source data.
4. Make the plan refuse to produce an executable launch manifest when unit-cost inputs or the operator-approved spend cap are absent.

**Acceptance:** Tests prove a campaign plan cannot be marked launch-ready without all upstream artifacts, a spend cap, and a positive pre-launch contribution calculation based on stated assumptions.

---

### Milestone 4 — Add a storefront adapter with a strict dry-run/live boundary

**Objective:** Implement an auditable storefront integration, initially for one approved store, without granting default publication authority.

**Files:**
- Create: `scripts/connectors/__init__.py`
- Create: `scripts/connectors/shopify_storefront.py`
- Create: `scripts/connectors/storefront_contract.py`
- Create: `tests/test_shopify_storefront.py`
- Modify: `scripts/secrets_helper.py` only if validated non-secret configuration fields are missing
- Modify: `tests/test_secrets_helper.py` if the secret contract changes
- Modify: `references/commerce-data-model.md`

**Work:**
1. Keep credentials out of source control. Use `scripts/secrets_helper.py` presence checks and Windows Credential Manager; do not place tokens in JSON artifacts, plans, prompts, or logs.
2. Implement read-only connectivity and schema checks first using mocked HTTP responses in tests.
3. Require a reviewed listing manifest, explicit per-product operator approval, idempotency keys, and a named `--confirm-live` style action before any create/update operation is possible.
4. Record every proposed and completed connector action in canonical events with product/listing IDs and operator approval evidence.
5. Reject destructive or broad update scopes by default. Product publication must be one product at a time during the pilot.

**Acceptance:** All connector tests run against fakes; dry-run emits an exact request manifest without network mutation; live mode fails closed if any credential, approval, product, policy, or idempotency condition is missing.

---

### Milestone 5 — Operator-approved pilot launch

**Objective:** Validate a single product and a single acquisition channel with a deliberately small, approved budget.

**Preconditions:**
- A real supplier, price, inventory position, fulfillment SLA, returns policy, and compliance review are verified.
- One reviewed candidate, listing, and creative set is approved.
- Storefront connector and ad-channel connector are explicitly approved and configured.
- An operator has approved the spend cap, daily cap, stop-loss, owner, and rollback procedure.
- WF-1001, WF-1002, and WF-1003 remain human-gated even after their internal implementation is complete.

**Work:**
1. Publish one product only after a review of the dry-run manifest.
2. Launch only the manually approved campaign plan; never add unreviewed audiences, creatives, or budget increases automatically.
3. Capture order, fulfillment, refund, campaign-spend, and attribution data with source timestamps and idempotent keys.
4. Pause the campaign immediately when the agreed stop-loss condition is met, supplier inventory/SLA changes, or data provenance becomes stale.

**Acceptance:** The pilot yields traceable observations for spend, orders, conversion, CAC, COGS, refunds, and contribution—not merely clicks or a positive supplier-sheet margin.

---

### Milestone 6 — Scale only proven economics

**Objective:** Convert pilot evidence into a repeatable operating loop rather than scaling a hypothesis.

**Work:**
1. Compare observed contribution after all variable costs against the operator-approved profitability rule.
2. Identify whether a result is caused by product, price, creative, audience, channel, or fulfillment performance before changing more than one factor.
3. Increment spend only within an explicitly approved increase schedule and keep a rollback/kill switch.
4. Add bundles, post-purchase offers, and retention flows only after the core product has positive verified contribution and compliant fulfillment.
5. Re-run supplier freshness and quality checks before each scale increment.

**Acceptance:** Scale decisions cite canonical evidence and remain reversible. A weak or negative result is a valid outcome: pause it, record why, and return to WF-1000 research rather than spending more to "win back" sunk cost.

---

## Verification plan

### Engineering checks

Run focused tests as each workflow/connector is added:

```bash
python -m unittest tests/test_product_research_workflow.py -v
python -m unittest tests/test_listing_drafts_workflow.py -v
python -m unittest tests/test_creative_generation_workflow.py -v
python -m unittest tests/test_campaign_planning_workflow.py -v
python -m unittest tests/test_shopify_storefront.py -v
python scripts/run_checks.py --skip-smoke
```

Validate workflow state and routing before any canonical or external action:

```bash
python scripts/workflow_router.py WF-1000 --answer summary --validate
python scripts/workflow_router.py WF-1001 --answer summary --validate
python scripts/workflow_router.py WF-1002 --answer summary --validate
python scripts/workflow_router.py WF-1003 --answer summary --validate
```

### Business launch checks

- Supplier identities and operational terms are real, current, and approved.
- Product claims, product safety, privacy, consumer disclosures, taxes, and returns obligations receive human/legal review appropriate to the launch market.
- The decision packet reconciles all cost and margin fields.
- The store and ad channel pass dry-run checks before any live request.
- Written operator approval specifies the product, price, channel, amount, duration, stop-loss, and responsible owner.

---

## Risks and open decisions

1. **The current catalog is demonstration data.** No candidate can be treated as a real commercial opportunity until real supplier evidence replaces the `example.com` rows.
2. **Supplier-sheet margin is not profit.** Shipping, fees, taxes, returns, chargebacks, and CAC can eliminate apparent margin.
3. **No storefront connector is configured.** `scripts/secrets_helper.py verify shopify` currently reports 0 of 6 required fields present, and the workflows authoritatively forbid external publication.
4. **No ad channel or spend cap is approved.** Paid acquisition must remain internal-planning-only until the operator chooses a channel and caps the downside.
5. **Regulatory/compliance obligations vary by product and market.** Product selection must screen claims, safety, intellectual-property risk, consumer protection, privacy, and taxes before launch.
6. **Do not commit or publish regenerable artifacts automatically.** The workspace has unrelated local changes; commits stay outside this plan unless separately requested and their scope is reviewed.

---

## Recommended immediate next action

Do not activate WF-1001, WF-1002, or WF-1003 yet. First provide or approve a real supplier catalog and a launch-policy decision (market, product category, storefront/channel choice, and maximum test spend). Then run the existing WF-1000 dry-run and review the resulting packet before any connector implementation or external configuration begins.
