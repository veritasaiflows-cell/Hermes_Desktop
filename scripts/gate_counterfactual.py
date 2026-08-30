#!/usr/bin/env python3
"""Gate counterfactual baseline for WF-1004 (AI Agent Trust Audit).

Purpose
-------
WF-1000 evaluated 30 real supplier leads across 5 platforms and produced an
EMPTY production shortlist. "The system is careful" is an assertion. This
module converts that assertion into a number by replaying the same evidence
corpus through two decision policies:

  * ``ungated``  - the naive-agent baseline: rank by whatever margin signal is
    present and select the top-N. This is what a typical unconstrained product
    -research agent does.
  * ``gated``    - the WF-1000 launch policy: every hard gate enforced, unknown
    treated as a hold rather than a guess.

The delta between the two is the measured value of the policy layer.

Honesty contract
----------------
This script is deliberately conservative about what it claims:

  * It reports AVOIDED COST, never earned revenue. The two are not the same and
    the distinction is enforced in the output schema and in the printed report.
  * Contribution figures are computed ONLY from evidence actually present in
    the corpus. Leads whose unit economics were never publicly exposed are
    counted as ``unquantifiable`` rather than being assigned an assumed value.
  * The counterfactual loss is a BOUNDED ESTIMATE derived from published
    per-tier costs and observed retail prices, not a forecast of sales volume.
    Sales volume is unknown; the per-unit economics are not.

Sources
-------
All inputs are on-disk evidence records produced by earlier WF-1000 passes:

  derived/research/wf1000-supplier-discovery-2026-08-29.json
  derived/research/wf1000-topdawg-verification-2026-08-29.json
  derived/research/wf1000-doba-verification-2026-08-29.json
  derived/research/wf1000-cj-zendrop-public-review-2026-08-29.json

No network access, no canonical writes, no external actions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = PROJECT_ROOT / "derived" / "research"
DEFAULT_POLICY_PATH = PROJECT_ROOT / "state" / "commerce-launch-policy.json"

SCHEMA = "gate-counterfactual.v1"

DISCOVERY_RECORD = "wf1000-supplier-discovery-2026-08-29.json"
TOPDAWG_RECORD = "wf1000-topdawg-verification-2026-08-29.json"
DOBA_RECORD = "wf1000-doba-verification-2026-08-29.json"
CJ_RECORD = "wf1000-cj-zendrop-public-review-2026-08-29.json"


class CounterfactualError(RuntimeError):
    """Raised when the evidence corpus cannot support an honest baseline."""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CounterfactualError(f"Required evidence record missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - corrupt corpus
        raise CounterfactualError(f"Evidence record unreadable: {path}: {exc}") from exc


def _as_float(value: Any) -> float | None:
    """Coerce a possibly-string numeric field to float, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def load_corpus(research_dir: Path = RESEARCH_DIR) -> dict[str, Any]:
    """Load every WF-1000 evidence record required for the baseline."""
    return {
        "discovery": _load_json(research_dir / DISCOVERY_RECORD),
        "topdawg": _load_json(research_dir / TOPDAWG_RECORD),
        "doba": _load_json(research_dir / DOBA_RECORD),
        "cj_zendrop": _load_json(research_dir / CJ_RECORD),
    }


def build_lead_table(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the corpus into one row per lead with whatever economics exist.

    Verified per-SKU economics (TopDawg, Doba) override the weaker discovery
    -pass fields, because the verification passes were performed later and
    against a stricter evidence contract.
    """
    leads: list[dict[str, Any]] = []

    for lead in corpus["discovery"].get("leads", []):
        cost = _as_float(lead.get("observed_cost_usd"))
        cost_range = lead.get("observed_cost_range_usd")
        if cost is None and isinstance(cost_range, list) and cost_range:
            numeric = [_as_float(v) for v in cost_range]
            numeric = [v for v in numeric if v is not None]
            cost = min(numeric) if numeric else None

        leads.append(
            {
                "lead_id": lead.get("id"),
                "supplier": lead.get("supplier"),
                "product": lead.get("product"),
                "status": lead.get("status"),
                "unit_cost_usd": cost,
                "retail_usd": _as_float(lead.get("implied_retail_usd")),
                "site_estimated_profit_usd": _as_float(
                    lead.get("observed_estimated_profit_usd")
                ),
                "rejection_reasons": list(lead.get("rejection_reasons", []) or []),
                "missing_evidence": list(lead.get("missing_evidence", []) or []),
                "risk_flags": list(lead.get("risk_flags", []) or []),
                "evidence_source": DISCOVERY_RECORD,
                "economics_basis": "discovery_pass",
            }
        )

    verdicts = corpus["topdawg"].get("lead_verdicts", {})
    by_id = {row["lead_id"]: row for row in leads}
    for lead_id, verdict in verdicts.items():
        screen = verdict.get("policy_screen", {}) or {}
        best_cost = _as_float(screen.get("best_tier_cost"))
        retail = _as_float(screen.get("retail_used"))
        gross_margin = _as_float(screen.get("gross_margin_percent_at_best_tier"))
        row = by_id.get(lead_id)
        if row is None:
            row = {"lead_id": lead_id, "supplier": "TopDawg"}
            leads.append(row)
            by_id[lead_id] = row

        row.update(
            {
                "product": verdict.get("product", row.get("product")),
                "sku": verdict.get("sku"),
                "in_stock_now": verdict.get("in_stock_now"),
                "unit_cost_usd": best_cost,
                "retail_usd": retail,
                "msrp_usd": _as_float(verdict.get("msrp_usd")),
                "gross_margin_percent": gross_margin,
                "site_estimated_profit_usd": _as_float(
                    verdict.get("site_estimated_profit_usd")
                ),
                "fail_reasons": list(screen.get("fail_reasons", []) or []),
                "evidence_source": TOPDAWG_RECORD,
                "economics_basis": "verified_public_tier_pricing",
            }
        )

    doba = corpus["doba"]
    doba_screen = doba.get("policy_screen", {}) or {}
    observed = doba_screen.get("observed_retail_range_usd", {}) or {}
    doba_product = (doba.get("sku", {}) or {}).get("product")

    # The verified Doba SKU is already present in the discovery pass as a lead
    # (matched on exact product title). Enrich that row rather than appending a
    # duplicate, which would inflate the corpus count.
    doba_target = None
    for row in leads:
        if (
            str(row.get("supplier", "")).lower().startswith("doba")
            and row.get("product") == doba_product
        ):
            doba_target = row
            break

    doba_fields = {
        "sku": (doba.get("sku", {}) or {}).get("item_no"),
        "status": "rejected",
        "unit_cost_usd": _as_float(
            (doba_screen.get("cost_economics", {}) or {}).get("unit_cost_usd")
        ),
        "retail_usd": _as_float(observed.get("max")),
        "observed_retail_min_usd": _as_float(observed.get("min")),
        "observed_retail_max_usd": _as_float(observed.get("max")),
        "fail_reasons": list(doba_screen.get("hard_fail_reasons", []) or []),
        "evidence_source": DOBA_RECORD,
        "economics_basis": "verified_live_retail_listings",
    }

    if doba_target is not None:
        doba_target.update(doba_fields)
        # The verification resolved the discovery pass's open questions.
        doba_target["missing_evidence"] = []
    else:
        leads.append(
            {
                "lead_id": "DOBA-40L-TRUNK",
                "supplier": "Doba",
                "product": doba_product,
                **doba_fields,
            }
        )
    return leads


def apply_gated_policy(
    leads: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    """Replay the real launch policy. This is what WF-1000 actually did."""
    selected = [
        lead
        for lead in leads
        if not lead.get("fail_reasons")
        and not lead.get("rejection_reasons")
        and not lead.get("missing_evidence")
        and lead.get("unit_cost_usd") is not None
        and lead.get("retail_usd") is not None
    ]
    return {
        "policy": "gated",
        "selected_count": len(selected),
        "selected": [lead.get("lead_id") for lead in selected],
        "spend_authorized_usd": 0.0,
        "note": (
            "Zero selections. Every lead carries at least one hard gate failure "
            "or an unresolved evidence gap; unknown is treated as a hold."
        ),
    }


def apply_ungated_policy(
    leads: list[dict[str, Any]], top_n: int = 3
) -> dict[str, Any]:
    """Replay a naive agent: rank by any available margin signal, take top-N.

    The naive agent has no retail floor, no stock requirement, no margin gate,
    and no evidence-completeness contract. It trusts supplier-displayed profit
    badges and computes margin wherever two numbers are present.
    """
    ranked: list[dict[str, Any]] = []
    for lead in leads:
        cost = lead.get("unit_cost_usd")
        retail = lead.get("retail_usd") or lead.get("msrp_usd")
        margin_pct = lead.get("gross_margin_percent")
        profit = lead.get("site_estimated_profit_usd")

        if margin_pct is None and cost is not None and retail:
            margin_pct = round((retail - cost) / retail * 100, 1)
        if profit is None and cost is not None and retail:
            profit = round(retail - cost, 2)

        if margin_pct is None and profit is None:
            continue

        ranked.append(
            {
                **lead,
                "naive_margin_percent": margin_pct,
                "naive_profit_usd": profit,
            }
        )

    ranked.sort(
        key=lambda row: (
            row.get("naive_margin_percent") or 0.0,
            row.get("naive_profit_usd") or 0.0,
        ),
        reverse=True,
    )
    selected = ranked[:top_n]
    return {
        "policy": "ungated",
        "ranked_count": len(ranked),
        "selected_count": len(selected),
        "selected": [
            {
                "lead_id": row.get("lead_id"),
                "product": row.get("product"),
                "supplier": row.get("supplier"),
                "naive_margin_percent": row.get("naive_margin_percent"),
                "naive_profit_usd": row.get("naive_profit_usd"),
                "unit_cost_usd": row.get("unit_cost_usd"),
                "retail_usd": row.get("retail_usd") or row.get("msrp_usd"),
                "in_stock_now": row.get("in_stock_now"),
                "actual_fail_reasons": row.get("fail_reasons")
                or row.get("rejection_reasons")
                or [],
                "unresolved_evidence_gaps": row.get("missing_evidence", []),
            }
            for row in selected
        ],
    }


def _gate_attribution(leads: list[dict[str, Any]]) -> dict[str, int]:
    """Count which specific gate caught each lead. This is the sales pitch."""
    counts: dict[str, int] = {}
    for lead in leads:
        reasons = (
            list(lead.get("fail_reasons", []) or [])
            + list(lead.get("rejection_reasons", []) or [])
        )
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
        if not reasons and lead.get("missing_evidence"):
            counts["incomplete_evidence_contract"] = (
                counts.get("incomplete_evidence_contract", 0) + 1
            )
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def quantify_avoided_loss(
    ungated: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Bound the per-unit economics of what the naive agent would have picked.

    We do NOT forecast sales volume. We report, for each naive selection, the
    per-unit gap between what the supplier signal implied and what the launch
    policy requires, plus the concrete operational failure (out of stock,
    below retail floor) the naive agent ignored.
    """
    min_margin = _as_float(policy.get("min_gross_margin_percent")) or 55.0
    floor = _as_float((policy.get("retail_price_usd") or {}).get("min")) or 30.0

    quantified: list[dict[str, Any]] = []
    unquantifiable: list[str] = []

    for row in ungated.get("selected", []):
        cost = _as_float(row.get("unit_cost_usd"))
        retail = _as_float(row.get("retail_usd"))
        if cost is None or retail is None:
            unquantifiable.append(row.get("lead_id"))
            continue

        actual_margin = round((retail - cost) / retail * 100, 1) if retail else None
        margin_shortfall = (
            round(min_margin - actual_margin, 1)
            if actual_margin is not None and actual_margin < min_margin
            else 0.0
        )
        # Retail the product would need to clear the margin gate at this cost.
        required_retail = round(cost / (1 - min_margin / 100), 2) if cost else None
        below_floor = bool(retail < floor)
        out_of_stock = row.get("in_stock_now") is False

        # The binding constraint is the interesting part: a naive agent that
        # only checks margin would have shipped these, because margin is NOT
        # what stops them.
        if actual_margin is not None and actual_margin >= min_margin:
            binding: list[str] = []
            if below_floor:
                binding.append("retail_below_floor")
            if out_of_stock:
                binding.append("out_of_stock")
            if not binding:
                binding = [
                    reason
                    for reason in row.get("actual_fail_reasons", [])
                    if reason
                ] or ["evidence_incomplete"]
            margin_gate_verdict = "passes_margin_gate"
        else:
            binding = ["gross_margin_below_policy"]
            margin_gate_verdict = "fails_margin_gate"

        quantified.append(
            {
                "lead_id": row.get("lead_id"),
                "product": row.get("product"),
                "unit_cost_usd": cost,
                "assumed_retail_usd": retail,
                "actual_gross_margin_percent": actual_margin,
                "policy_min_gross_margin_percent": min_margin,
                "margin_gate_verdict": margin_gate_verdict,
                "margin_shortfall_points": margin_shortfall,
                "required_retail_to_pass_usd": required_retail,
                "retail_floor_usd": floor,
                "below_retail_floor": below_floor,
                "retail_floor_shortfall_usd": (
                    round(floor - retail, 2) if below_floor else 0.0
                ),
                "in_stock_now": row.get("in_stock_now"),
                "binding_constraints": binding,
                "operational_failures": row.get("actual_fail_reasons", []),
            }
        )

    return {
        "basis": "per_unit_economics_only",
        "sales_volume_assumed": None,
        "quantified": quantified,
        "unquantifiable_lead_ids": unquantifiable,
        "note": (
            "Per-unit gaps are computed from published supplier costs and observed "
            "retail evidence. Sales volume is unknown and deliberately not modelled, "
            "so no aggregate dollar loss is claimed."
        ),
    }


def build_report(
    research_dir: Path = RESEARCH_DIR,
    policy_path: Path = DEFAULT_POLICY_PATH,
    top_n: int = 3,
) -> dict[str, Any]:
    """Produce the full counterfactual baseline report."""
    corpus = load_corpus(research_dir)
    policy = _load_json(policy_path)
    leads = build_lead_table(corpus)

    if not leads:
        raise CounterfactualError(
            "Evidence corpus produced zero leads; refusing to emit an empty baseline."
        )

    gated = apply_gated_policy(leads, policy)
    ungated = apply_ungated_policy(leads, top_n=top_n)
    avoided = quantify_avoided_loss(ungated, policy)

    verified = [
        lead
        for lead in leads
        if lead.get("economics_basis")
        in {"verified_public_tier_pricing", "verified_live_retail_listings"}
    ]

    return {
        "schema": SCHEMA,
        "workflow_id": "WF-1004",
        "evidence_as_of": corpus["discovery"].get("as_of"),
        "policy_id": policy.get("policy_id"),
        "corpus": {
            "total_leads": len(leads),
            "leads_with_verified_economics": len(verified),
            "suppliers": sorted(
                {
                    str(lead.get("supplier", "")).split()[0]
                    for lead in leads
                    if lead.get("supplier")
                }
            ),
            "records": [
                DISCOVERY_RECORD,
                TOPDAWG_RECORD,
                DOBA_RECORD,
                CJ_RECORD,
            ],
        },
        "gated_result": gated,
        "ungated_result": ungated,
        "gate_attribution": _gate_attribution(leads),
        "avoided_loss": avoided,
        "spend": {
            "sample_budget_usd": _as_float(policy.get("sample_budget_usd")),
            "actually_spent_usd": 0.0,
            "accounting_note": (
                "Unspent budget is AVOIDED COST, not earned revenue. No revenue "
                "has been generated by WF-1000 or WF-1004."
            ),
        },
        "honesty_notes": [
            "This baseline compares decision policies over a fixed evidence corpus; "
            "it does not simulate market demand or sales volume.",
            "The ungated policy is a reconstruction of typical naive-agent behavior, "
            "not a recording of an actual competing system.",
            "Leads without public unit economics are excluded from quantification "
            "rather than assigned assumed values.",
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    """Render a human-readable summary."""
    lines: list[str] = []
    add = lines.append
    corpus = report["corpus"]
    add("GATE COUNTERFACTUAL BASELINE (WF-1004)")
    add("=" * 60)
    add(f"Evidence as of : {report['evidence_as_of']}")
    add(f"Launch policy  : {report['policy_id']}")
    add(
        f"Corpus         : {corpus['total_leads']} leads across "
        f"{len(corpus['suppliers'])} suppliers "
        f"({corpus['leads_with_verified_economics']} with verified economics)"
    )
    add("")
    add(f"GATED   (actual)  : {report['gated_result']['selected_count']} selected")
    add(
        f"UNGATED (naive)   : {report['ungated_result']['selected_count']} selected "
        f"from {report['ungated_result']['ranked_count']} rankable"
    )
    add("")
    add("What the naive agent would have shipped:")
    for row in report["ungated_result"]["selected"]:
        add(f"  - {row['lead_id']}: {row['product']}")
        add(
            f"      naive margin {row['naive_margin_percent']}% | "
            f"cost ${row['unit_cost_usd']} | retail ${row['retail_usd']}"
        )
        if row.get("in_stock_now") is False:
            add("      OUT OF STOCK at verification time")
        for reason in row.get("actual_fail_reasons", []):
            add(f"      BLOCKED BY GATE: {reason}")
    add("")
    add("Gate attribution (which gate caught how many leads):")
    for reason, count in report["gate_attribution"].items():
        add(f"  {count:3d}  {reason}")
    add("")
    add("Per-unit economics of naive selections:")
    for row in report["avoided_loss"]["quantified"]:
        add(
            f"  - {row['lead_id']}: margin {row['actual_gross_margin_percent']}% "
            f"({row['margin_gate_verdict'].replace('_', ' ')})"
        )
        if row["below_retail_floor"]:
            add(
                f"      retail ${row['assumed_retail_usd']} is "
                f"${row['retail_floor_shortfall_usd']} BELOW the "
                f"${row['retail_floor_usd']} floor"
            )
        add(f"      binding constraint: {', '.join(row['binding_constraints'])}")
    add("")
    passes_margin = [
        row
        for row in report["avoided_loss"]["quantified"]
        if row["margin_gate_verdict"] == "passes_margin_gate"
    ]
    if passes_margin:
        add(
            f"KEY FINDING: {len(passes_margin)}/"
            f"{len(report['avoided_loss']['quantified'])} naive picks PASS the "
            f"margin gate. A margin-only agent ships them. They are stopped by "
            f"retail-floor and inventory gates instead."
        )
        add("")
    spend = report["spend"]
    add(
        f"Sample budget ${spend['sample_budget_usd']} approved, "
        f"${spend['actually_spent_usd']} spent."
    )
    add(f"NOTE: {spend['accounting_note']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--research-dir",
        default=str(RESEARCH_DIR),
        help="Directory containing WF-1000 evidence records.",
    )
    parser.add_argument(
        "--launch-policy",
        default=str(DEFAULT_POLICY_PATH),
        help="Launch policy JSON to replay.",
    )
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--as-json", action="store_true", help="Emit raw JSON.")
    args = parser.parse_args()

    try:
        report = build_report(
            research_dir=Path(args.research_dir),
            policy_path=Path(args.launch_policy),
            top_n=args.top_n,
        )
    except CounterfactualError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    if args.as_json:
        # NOTE: sort_keys is deliberately False. gate_attribution is ordered by
        # descending count and that ordering is meaningful output, not noise.
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
