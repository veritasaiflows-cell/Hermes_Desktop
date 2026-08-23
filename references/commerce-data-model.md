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
| Margin percentage              | `commerce.margin_percent`                 | percent  |
| Shipping lead time             | `commerce.shipping_days`                  | days     |
| Demand signal                  | `commerce.demand_signal`                 | ratio    |
| Saturation/competition signal   | `commerce.saturation_signal`             | ratio    |
| Campaign return on ad spend     | `commerce.roas`                          | ratio    |
| Customer acquisition cost       | `commerce.cac`                           | currency |
| Conversion rate                | `commerce.conversion_rate`               | ratio    |
| Cost of goods sold             | `commerce.cogs`                          | currency |
| Gross margin                  | `commerce.margin_absolute`                | currency |

All metric rows should include `dimensions_json` with structured join keys,
including at minimum:

- `workflow` (which workflow produced the value)
- `entity_id` (the subject entity row id)
- domain-specific keys (`supplier`, `order_id`, `campaign_id`, etc.)

## 3. Provenance and audit contract

Every commerce write must flow through `canonical.db` methods:

- Insert candidate rows using `db.insert("entities", ..., provenance_id=...)`.
- Write scoring/telemetry with `db.insert("metrics", ..., provenance_id=...)`.
- Emit lifecycle checkpoints via `events` with explicit subject rows.

No direct SQL inserts should bypass this API during phase 1.

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
2. Reporting can recover all actionable candidates by querying:
   `entities` where `entity_type = 'product_candidate'` and `status='candidate'`.
3. Candidate ranking is explainable from associated `commerce.viability_score`.

## 6. Reporting surface

`scripts/top_opportunities_report.py` is the read-only reporting entrypoint for
the ranked opportunity shortlist. It reads `product_candidate` entities and
their `commerce.*` metrics, deduplicates on `(name, supplier)`, filters by a
minimum viability score, and prints a ranked shortlist (text or `--as-json`).
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