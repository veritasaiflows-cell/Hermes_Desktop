# Commerce data model (Phase 1)

This is the first domain model for AI drop-shipping and online marketing workflows.
It is implemented initially on top of existing canonical tables using typed
`entity_type` values and canonical metrics.

## 1. Canonical entities currently in use

The following `entities` rows represent commerce concepts for phase 1:

- `product_candidate` — screened product hypothesis.
- `product` — approved listing-ready product (future state).
- `supplier` — source/vendor of catalog or inventory data.
- `store` — e-commerce destination for publication.
- `campaign` — ad campaign run and tracked for outcomes.
- `creative` — ad or listing creative variant.
- `customer` — named or hashed prospect/customer record.
- `order` — transactional sale unit pulled from storefronts.

Recommended required fields (by convention when entity type applies):

- `name`: human-readable identifier.
- `status`: `candidate`, `active`, `paused`, `archived`.
- `scope`: `commerce`.
- `confidence`: model confidence for computed decisions.
- `provenance_id`: source citation.

## 2. Phase 1 canonical metric convention

Use `metrics` for commercial scoring and monitoring until dedicated commerce tables
are added.

| Metric                         | Suggested `metric_name`                  | Unit     |
| ------------------------------ | ---------------------------------------- | -------- |
| Candidate viability score       | `commerce.viability_score`               | ratio    |
| Strategic fit score             | `commerce.strategic_fit_score`            | score_0_100 |
| Audience fit                    | `commerce.audience_fit_score`             | score_0_100 |
| Operator fit                    | `commerce.operator_fit_score`             | score_0_100 |
| Content demonstration fit       | `commerce.content_demo_score`             | score_0_100 |
| Brand extension fit             | `commerce.brand_extension_score`          | score_0_100 |
| Bundle potential                | `commerce.bundle_potential_score`          | score_0_100 |
| Case-study value                | `commerce.case_study_value_score`          | score_0_100 |
| Margin percentage              | `commerce.margin_percent`                 | percent  |
| Shipping lead time             | `commerce.shipping_days`                  | days     |
| Demand signal                  | `commerce.demand_signal`                 | ratio    |
| Saturation/competition signal   | `commerce.saturation_signal`             | ratio    |
| Campaign return on ad spend     | `commerce.roas`                          | ratio    |
| Customer acquisition cost       | `commerce.cac`                           | currency |
| Conversion rate                | `commerce.conversion_rate`               | ratio    |
| Cost of goods sold             | `commerce.cogs`                          | currency |
| Gross margin                  | `commerce.margin_absolute`                | currency |
| Reconciled gross margin       | `commerce.gross_margin_percent_reconciled` | percent  |
| Landed product cost           | `commerce.landed_cost_usd`                 | currency |
| Payment fee                   | `commerce.payment_fee_usd`                 | currency |
| Pre-ad contribution           | `commerce.pre_ad_contribution_usd`         | currency |
| Pre-ad contribution margin    | `commerce.pre_ad_contribution_percent`     | percent  |
| Break-even acquisition cost   | `commerce.break_even_cac_usd`               | currency |
| Estimated acquisition cost    | `commerce.estimated_cac_usd`                | currency |
| Post-ad contribution          | `commerce.post_ad_contribution_usd`         | currency |
| Post-ad contribution margin   | `commerce.post_ad_contribution_percent`     | percent  |

All metric rows should include `dimensions_json` with structured join keys,
including at minimum:

- `workflow` (which workflow produced the value)
- `entity_id` (the subject entity row id)
- domain-specific keys (`supplier`, `order_id`, `campaign_id`, etc.)

WF-1000 interest-aligned metrics also include `thesis_id`, `audience_id`,
`qualification_mode`, `quality_status`, and `margin_basis` dimensions.

## 3. Provenance and audit contract

Every commerce write must flow through `canonical.db` methods:

- Insert candidate rows using `db.insert("entities", ..., provenance_id=...)`.
- Write scoring/telemetry with `db.insert("metrics", ..., provenance_id=...)`.
- Emit lifecycle checkpoints via `events` with explicit subject rows.

No direct SQL inserts should bypass this API during phase 1.

For WF-1000 interest-aligned candidates, the selection event payload also carries:

- `qualification_mode`: `research`, `organic_sample`, or `paid_launch`.
- `strategic_fit`: approved thesis/audience identifiers, independent component scores,
  aggregate score, content angles, and product-character evidence.
- `quality_status`: `research_candidate`, `organic_sample_candidate`,
  `organic_pilot_ready`, or `paid_launch_ready`.
- `quality_warnings`: non-blocking explicit unknowns such as pre-sample CAC.
- `economics`: calculated landed cost, payment fee, reconciled gross margin, and
  pre-ad contribution/break-even CAC values, plus estimated CAC when supplied.

`retail_price_usd` is the customer-facing offer total. A one-supplier bundle may
contain sub-$30 components, but its offer total and aggregate landed costs must pass
every gate. Supplier-reported margin is reconciled only when
`margin_basis=landed_cost`; product-only and unknown-basis claims remain advisory.

`rejected` is a review outcome and is not written as a selected candidate.
`organic_pilot_ready` requires a passed sample but cannot authorize paid acquisition.
`paid_launch_ready` additionally requires numeric estimated CAC that leaves the
configured positive post-ad contribution; catalog evidence alone can at most produce
`organic_sample_candidate`.

## 4. Relationship map (logical)

- `supplier` → one-to-many `product_candidate` / `product`
- `product` → one-to-many `order`
- `store` → one-to-many `campaign`, `order`
- `campaign` → one-to-many `creative`
- `order` → one-to-one `customer`

Until dedicated relationship tables are added, track these joins in `event.payload_json`
and `metrics.dimensions_json` until a stable relation model is introduced.

## 5. Phase 1 milestone outcomes

1. Workflow A can write/read `product_candidate` entities with provenance and
   viability metrics.
2. Reporting recovers actionable candidates only when a current selection event has
   a production quality state; entity type alone is insufficient.
3. Candidate ranking exposes strategic fit independently from commercial viability.

## 6. Reporting surface

`scripts/top_opportunities_report.py` is the read-only reporting entrypoint for
the ranked opportunity shortlist. It reads `product_candidate` entities, selection
events, and `commerce.*` metrics; deduplicates on `(name, supplier)`; requires a
production-quality event by default; and prints a ranked shortlist (text or
`--as-json`). `--include-unverified` is a diagnostic-only escape hatch for legacy rows.
It never writes canonical records; see `references/script-index.md` for the
full script inventory.

## 7. Future migration targets (optional)

When needed for scale, add dedicated tables:

- `commerce_suppliers`, `commerce_products`, `commerce_orders`, `commerce_campaigns`,
  with stable foreign keys back to `entities`.
- Optional graph links (`graph/*`) for campaign-funnel reasoning.

This keeps the existing foundation intact while unblocking first revenue workflows.

## Change history

- v1: initial phase-1 commerce model for Workflow A and canonical score telemetry.
- v1.1: documented the `top_opportunities_report.py` reporting surface and linked
  the script index.
- v1.2: documented WF-1000 production economics and sample-gated quality states.
- v1.3: added strategic-fit metrics, staged quality states, event-backed production
  reporting, and positive post-ad contribution semantics.