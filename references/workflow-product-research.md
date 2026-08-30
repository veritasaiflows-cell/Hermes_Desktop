# Workflow A — Product Research

## 1) Purpose

Research supplier/product opportunities and materialize top candidates into canonical
state for downstream workflow selection.

## 2) Trigger

Legacy/demo scoring (diagnostic only; never appears in the default opportunity report):

```bash
python scripts/product_research_workflow.py <catalog.csv> --top-n 5
```

Interest-aligned research dry run:

```bash
python scripts/product_research_workflow.py <catalog.csv> \
  --launch-policy state/commerce-launch-policy.json \
  --interest-profile state/operator-interest-profile.json \
  --qualification-mode research \
  --top-n 10 \
  --dry-run
```

Organic-sample and paid-launch evaluations use `--qualification-mode organic_sample`
or `paid_launch`. Canonical writes require the exact same catalog, policy, interest
profile, qualification mode, and scoring configuration to have passed a dry run,
plus an active WF-1000 write lane. Research and shortlist creation do not authorize
ordering, publication, advertising, or external messages.

## 3) Inputs

A local CSV file with these base headers:

- `product_name` (required)
- `supplier_name` (required)
- `margin_percent` (required numeric)
- `shipping_days` (required numeric)
- `demand_signal` (required numeric)
- `saturation_signal` (required numeric)
- `cost_per_unit_usd` (optional numeric in legacy mode)
- `retail_price_usd` (optional numeric in legacy mode)
- `notes` (optional text)
- `source_uri` (optional text)

When `--launch-policy` is supplied, an interest profile is mandatory. The default
path is `operator-interest-profile.json` beside the launch-policy file. Every mode
requires these strategic fields:

- Thesis and audience: `thesis_id`, `audience_id`, `problem_space`, `category`
- Operator context: `problem_statement`, `why_operator_cares`, `why_customer_cares`,
  `interest_tags`, `operator_fit_status`
- Content proof: `content_angles` (pipe-delimited; at least five)
- Independent fit components: `audience_fit_score`, `operator_fit_score`,
  `content_demo_score`, `brand_extension_score`, `bundle_potential_score`,
  `case_study_value_score`
- Product character evidence: `product_character_tags`

`research` mode requires only the base and strategic fields. `organic_sample` and
`paid_launch` additionally require these production columns:

- Evidence: `source_uri`, `supplier_url`, `evidence_status`, `evidence_checked_at`,
  `supplier_identity_status`, `return_policy_url`
- Fulfillment: `warehouse_country`, `inventory_status`, `tracking_available`, `moq`
- Economics: `cost_per_unit_usd`, `retail_price_usd`, `margin_basis`, `shipping_cost_usd`,
  `packaging_cost_usd`, `duties_usd`, `payment_fee_percent`,
  `payment_fee_fixed_usd`, `expected_return_cost_usd`, `estimated_cac_usd`
- Risk and review: `risk_tags`, `compliance_ip_status`, `quality_evidence_status`,
  `sample_status`

`evidence_status` and `supplier_identity_status` must be `verified`;
`compliance_ip_status` must be `cleared`. `quality_evidence_status` is
`sample_pending` or `sample_passed` and must agree with `sample_status`. `risk_tags`
must contain reviewed tags or the explicit marker `none`. `estimated_cac_usd` may be
`unknown` for organic evaluation; `paid_launch` requires numeric CAC that preserves
the configured post-ad contribution. `margin_basis` is `landed_cost`,
`product_cost_only`, or `supplier_claim_unknown`; only `landed_cost` claims are
reconciled against WF-1000's computed landed margin. Empty values remain failures
instead of being inferred.

## 4) Outputs

Writes to canonical via `canonical/db.py`:

- `entities` rows with `entity_type='product_candidate'`
- `metrics` rows for
  - `commerce.viability_score`
  - `commerce.strategic_fit_score` and six component scores (interest-profile mode)
  - `commerce.margin_percent`
  - `commerce.shipping_days`
  - `commerce.demand_signal`
  - `commerce.saturation_signal`
  - `commerce.landed_cost_usd` (production mode)
  - `commerce.payment_fee_usd` (production mode)
  - `commerce.gross_margin_percent_reconciled` (production mode)
  - `commerce.pre_ad_contribution_usd` (production mode)
  - `commerce.pre_ad_contribution_percent` (production mode)
  - `commerce.break_even_cac_usd` (production mode)
  - `commerce.estimated_cac_usd` when a numeric estimate is supplied
  - `commerce.post_ad_contribution_usd` and percent when CAC is numeric
- `events` row per selected candidate (`workflow.product_research.candidate_selected`)

Interest-aligned events and result rows include `qualification_mode`, `strategic_fit`,
`quality_status`, `quality_warnings`, and mode-appropriate `economics`.
Allowed selected states are:

- `research_candidate`: strategic fit, product-character, and lightweight research
  margin gates pass; production evidence remains incomplete.
- `organic_sample_candidate`: hard supplier/safety/fulfillment gates and organic
  economics pass; physical sample is pending.
- `organic_pilot_ready`: organic gates pass and the sample passed; paid acquisition
  remains prohibited.
- `paid_launch_ready`: paid gates pass, the sample passed, and numeric CAC preserves
  at least the configured post-ad contribution.
- `rejected`: one or more gates failed; never selected or written as a new candidate.

## 5) Controls and policy

- Read-only workflow from external perspective (local CSV only).
- Commercial viability remains deterministic:
  - margin 35%
  - demand 35%
  - shipping 20%
  - inverse saturation 10%
- Strategic fit is calculated independently from audience, operator, content-demo,
  brand-extension, bundle-potential, and case-study components. It is a hard gate and the primary ranking axis;
  commercial viability is the secondary axis. High demand cannot compensate for an
  off-thesis product.
- Mode caps are 10 research candidates, 3 organic sample candidates, and 1 paid launch.
- `--launch-policy` fails closed on stale/unverified evidence, placeholder URLs,
  retail range, US warehouse, inventory, tracking, MOQ, delivery time, economics,
  excluded risk tags, and sample status.
- Supplier margin claims are reconciled only when `margin_basis=landed_cost`. The
  policy rejects comparable claims outside tolerance and labels non-comparable
  supplier claims advisory.
- `retail_price_usd` is the total offer price. Sub-$30 bundle components are allowed
  only inside a coherent one-supplier bundle whose total offer passes every gate.
- Landed cost = product cost + shipping + packaging + duties.
- Payment fee = retail price × payment fee percent + fixed payment fee.
- Pre-ad contribution = retail price − landed cost − payment fee − expected return cost.
- Organic sample mode permits a 45% landed gross margin only when both 35% and $20
  pre-ad contribution floors pass. Paid launch retains 55% landed gross margin and
  requires at least 10% post-ad contribution.
- Unknown CAC is an explicit organic warning and a hard paid-launch failure.
- `--dry-run` writes only source-preflight and run-metrics control records; it does
  not write product-candidate entities, candidate metrics, or selection events.
- The dry-run record includes catalog, launch-policy, interest-profile, and
  qualification-mode identity. Changing any one requires a new dry run.
- Result and persisted run telemetry include stage counts, reason counts, and
  rejections grouped by thesis and supplier.

## 6) Failure handling

- Missing required headers -> hard fail with explicit error.
- Invalid numeric values -> hard fail with field-level message.
- Invalid `top_n`/threshold -> hard fail with validation.
- Invalid/missing production columns or policy fields -> hard fail with explicit error.
- Placeholder, stale, unknown, or policy-violating product rows -> rejected with
  machine-readable reasons and excluded from selection.
- Canonical write failures propagate; run with a short prefix and inspect errors.

## 7) Verification

After run, verify:

1. `selected_count` in returned payload.
2. `entities` contains matching `product_candidate` rows.
3. `metrics` includes viability rows for each written candidate.
4. `events` contains workflow event for each written candidate.
5. Interest-aligned rows expose thesis, audience, strategic-fit decomposition,
   qualification mode, quality status, and applicable economics.
6. No row may be `organic_pilot_ready` or `paid_launch_ready` before a passed sample.
7. `scripts/top_opportunities_report.py` returns production-evidenced rows only by
   default; use `--include-unverified` only for legacy/demo diagnostics.

## 8) Current operator policy

`state/operator-interest-profile.json` is the approved audience/thesis contract.
`state/commerce-launch-policy.json` contains the US conservative-DTC safety,
fulfillment, economics, stage, and $250 sample-budget controls. Both files are
operator policy—not supplier evidence. Each product still requires current evidence.

## Change history

- v1: workflow scaffold for phase-2 execution proof and canonical write-path verification.
- v2: production launch policy, evidence/economics/risk gates, quality states,
  policy-hash preflight, and persisted quality metrics.
- v3: operator-interest profile, independent strategic-fit axis, executable product
  characters, research/organic/paid stages, contribution-dollar and profitable-CAC
  gates, rejection telemetry, and production-only reporting.