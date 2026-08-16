# Workflow A — Product Research (phase-2 scaffold)

## 1) Purpose

Research supplier/product opportunities and materialize top candidates into canonical
state for downstream workflow selection.

## 2) Trigger

Manual execution:

```bash
python scripts/product_research_workflow.py <catalog.csv> --top-n 5
```

## 3) Inputs

A local CSV file with headers:

- `product_name` (required)
- `supplier_name` (required)
- `margin_percent` (required numeric)
- `shipping_days` (required numeric)
- `demand_signal` (required numeric)
- `saturation_signal` (required numeric)
- `cost_per_unit_usd` (optional numeric)
- `retail_price_usd` (optional numeric)
- `notes` (optional text)
- `source_uri` (optional text)

## 4) Outputs

Writes to canonical via `canonical/db.py`:

- `entities` rows with `entity_type='product_candidate'`
- `metrics` rows for
  - `commerce.viability_score`
  - `commerce.margin_percent`
  - `commerce.shipping_days`
  - `commerce.demand_signal`
  - `commerce.saturation_signal`
- `events` row per selected candidate (`workflow.product_research.candidate_selected`)

## 5) Controls and policy

- Read-only workflow from external perspective (local CSV only).
- Deterministic ranking using a weighted score:
  - margin 35%
  - demand 35%
  - shipping 20%
  - inverse saturation 10%
- Writes are gated by `--top-n` and `--min-viability-score`.
- `--dry-run` performs ranking only and writes nothing.

## 6) Failure handling

- Missing required headers -> hard fail with explicit error.
- Invalid numeric values -> hard fail with field-level message.
- Invalid `top_n`/threshold -> hard fail with validation.
- Canonical write failures propagate; run with a short prefix and inspect errors.

## 7) Verification

After run, verify:

1. `selected_count` in returned payload.
2. `entities` contains matching `product_candidate` rows.
3. `metrics` includes viability rows for each written candidate.
4. `events` contains workflow event for each written candidate.

## 8) Next step

Move Workflow A output to scheduling + connector layers in phase-3: supplier freshness
checks and campaign-ready listing content creation.

## Change history

- v1: workflow scaffold for phase-2 execution proof and canonical write-path verification.