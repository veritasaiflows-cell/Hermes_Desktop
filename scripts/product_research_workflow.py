#!/usr/bin/env python3
"""Workflow A: product research ingestion and candidate scoring.

This is the first executable Efficiens workflow scaffold. It ingests a supplier
catalog-style CSV into the canonical layer as `entities` + `metrics` records.

The implementation intentionally stays read-only with respect to external stores.
Only the workspace DB is updated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WORKFLOW_ID = "product_research"
WORKFLOW_RUN_CONFIG_VERSION = "v4"
PRODUCTION_REQUIRED_COLUMNS = {
    "compliance_ip_status",
    "cost_per_unit_usd",
    "duties_usd",
    "evidence_checked_at",
    "evidence_status",
    "estimated_cac_usd",
    "expected_return_cost_usd",
    "inventory_status",
    "margin_basis",
    "moq",
    "packaging_cost_usd",
    "payment_fee_fixed_usd",
    "payment_fee_percent",
    "quality_evidence_status",
    "retail_price_usd",
    "return_policy_url",
    "risk_tags",
    "sample_status",
    "shipping_cost_usd",
    "source_uri",
    "supplier_identity_status",
    "supplier_url",
    "tracking_available",
    "warehouse_country",
}
STRATEGIC_REQUIRED_COLUMNS = {
    "audience_fit_score",
    "audience_id",
    "brand_extension_score",
    "bundle_potential_score",
    "case_study_value_score",
    "category",
    "content_angles",
    "content_demo_score",
    "interest_tags",
    "operator_fit_score",
    "operator_fit_status",
    "problem_space",
    "problem_statement",
    "product_character_tags",
    "thesis_id",
    "why_customer_cares",
    "why_operator_cares",
}
LAUNCH_POLICY_REQUIRED_FIELDS = {
    "bundle_rules",
    "comparable_margin_basis",
    "currency",
    "evidence_max_age_days",
    "excluded_risk_tags",
    "market",
    "margin_basis_values",
    "max_margin_reconciliation_delta_percent",
    "max_moq",
    "max_shipping_days",
    "min_gross_margin_percent",
    "min_pre_ad_contribution_percent",
    "product_character_requirements",
    "qualification_modes",
    "require_tracking",
    "required_inventory_statuses",
    "retail_price_usd",
    "retail_price_basis",
    "sample_budget_usd",
    "sample_shortlist_size",
    "schema",
}
INTEREST_PROFILE_REQUIRED_FIELDS = {
    "approved_problem_spaces",
    "approved_theses",
    "default_excluded_categories",
    "minimum_content_angles",
    "minimum_strategic_fit_score",
    "operator_fit_statuses",
    "primary_audience_ids",
    "profile_id",
    "schema",
    "strategic_fit_weights",
}

from canonical.db import CanonicalDB
from scripts.workflow_runner import WorkflowPreflightError, preflight_workflow
from scripts.runtime_metadata import detect_active_model


@dataclass(frozen=True)
class ProductCandidate:
    """Normalized product research candidate."""

    source_row: int
    name: str
    supplier: str
    source_uri: str
    margin_percent: float
    shipping_days: float
    demand_signal: float
    saturation_signal: float
    cost_per_unit_usd: float | None
    retail_price_usd: float | None
    notes: str | None
    score_primary: float
    score_secondary: float
    score_disagreement: float
    viability_score: float
    split: str = "live"
    business_key: str = ""
    quality_review: dict[str, Any] | None = None
    strategic_review: dict[str, Any] | None = None


def run_product_research(
    catalog_path: str | Path,
    *,
    database_path: str | Path = "canonical/efficiens.db",
    top_n: int = 3,
    min_viability_score: float = 0.0,
    holdout_fraction: float = 0.0,
    adversarial_fraction: float = 0.0,
    launch_policy_path: str | Path | None = None,
    interest_profile_path: str | Path | None = None,
    qualification_mode: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Workflow A and persist a scored set of product candidates.

    Returns a serializable summary payload that identifies candidates and the IDs
    written to canonical storage.
    """

    source_path = Path(catalog_path)
    if top_n <= 0:
        raise ValueError("top_n must be a positive integer")
    if not 0.0 <= min_viability_score <= 1.0:
        raise ValueError("min_viability_score must be between 0.0 and 1.0")
    if not 0.0 <= holdout_fraction <= 1.0:
        raise ValueError("holdout_fraction must be between 0.0 and 1.0")
    if not 0.0 <= adversarial_fraction <= 1.0:
        raise ValueError("adversarial_fraction must be between 0.0 and 1.0")
    if holdout_fraction + adversarial_fraction >= 1.0:
        raise ValueError("holdout_fraction and adversarial_fraction cannot sum to 1.0 or greater")

    launch_policy = _load_launch_policy(launch_policy_path) if launch_policy_path else None
    launch_policy_hash = _file_hash(Path(launch_policy_path)) if launch_policy_path else None
    interest_profile: dict[str, Any] | None = None
    resolved_interest_profile_path: Path | None = None
    interest_profile_hash: str | None = None
    if launch_policy is not None:
        resolved_interest_profile_path = (
            Path(interest_profile_path)
            if interest_profile_path is not None
            else Path(launch_policy_path).with_name("operator-interest-profile.json")
        )
        interest_profile = _load_interest_profile(resolved_interest_profile_path)
        interest_profile_hash = _file_hash(resolved_interest_profile_path)
    elif interest_profile_path is not None:
        raise ValueError("interest_profile_path requires launch_policy_path")
    effective_qualification_mode = qualification_mode or (
        "organic_sample" if launch_policy is not None else "legacy"
    )
    if launch_policy is None and effective_qualification_mode != "legacy":
        raise ValueError("qualification_mode requires launch_policy_path")
    if (
        launch_policy is not None
        and effective_qualification_mode not in launch_policy["qualification_modes"]
    ):
        raise ValueError(
            "qualification_mode must be one of: "
            + ", ".join(sorted(launch_policy["qualification_modes"]))
        )
    shortlist_limit = (
        int(
            launch_policy["qualification_modes"][effective_qualification_mode][
                "max_shortlist_size"
            ]
        )
        if launch_policy is not None
        else top_n
    )
    if launch_policy is not None and top_n > shortlist_limit:
        raise ValueError(
            f"top_n cannot exceed {effective_qualification_mode} max_shortlist_size "
            f"{shortlist_limit}"
        )
    candidates_raw = [
        row
        for row in _iter_catalog(
            source_path,
            holdout_fraction=holdout_fraction,
            adversarial_fraction=adversarial_fraction,
            launch_policy=launch_policy,
            interest_profile=interest_profile,
            qualification_mode=effective_qualification_mode,
        )
    ]
    input_hash = _file_hash(source_path)
    source_ref = str(source_path.resolve())
    candidates, deduped_in_batch = _dedupe_candidates_by_business_key(candidates_raw)

    split_counts = {
        "live": 0,
        "holdout": 0,
        "adversarial": 0,
    }
    for candidate in candidates:
        split_counts[candidate.split] = split_counts.get(candidate.split, 0) + 1

    live_candidates = [candidate for candidate in candidates if candidate.split == "live"]
    eligible = [
        candidate
        for candidate in live_candidates
        if candidate.viability_score >= min_viability_score
        and (candidate.quality_review is None or candidate.quality_review["passed"])
        and (candidate.strategic_review is None or candidate.strategic_review["passed"])
    ]
    eligible.sort(
        key=lambda candidate: (
            candidate.strategic_review["score"] if candidate.strategic_review else -1.0,
            candidate.viability_score,
        ),
        reverse=True,
    )
    selected = eligible[:top_n]
    confidence_scores = [candidate.score_disagreement for candidate in selected]
    if confidence_scores:
        confidence_profile = {
            "abstention_count": sum(1 for score in confidence_scores if score >= 0.25),
            "disagreement_max": max(confidence_scores),
            "disagreement_mean": sum(confidence_scores) / len(confidence_scores),
            "disagreement_p95": _quantile(confidence_scores, 0.95),
        }
    else:
        confidence_profile = {
            "abstention_count": 0,
            "disagreement_max": 0.0,
            "disagreement_mean": 0.0,
            "disagreement_p95": 0.0,
        }
    confidence_profile["evaluation_splits"] = split_counts
    confidence_profile["evaluation_profile"] = _build_evaluation_profile(candidates)

    business_key_dedupe = {
        "incoming_rows": len(candidates_raw),
        "batch_deduped_rows": deduped_in_batch,
        "batch_unique_rows": len(candidates),
        "selected_count": len(selected),
        "existing_reused": 0,
        "new_candidates": 0,
    }
    quality_review = _summarize_quality_reviews(
        candidates,
        launch_policy_path,
        effective_qualification_mode,
    )
    strategic_review = _summarize_strategic_reviews(
        candidates,
        resolved_interest_profile_path,
    )
    qualification_gate_telemetry = _build_qualification_gate_telemetry(
        candidates,
        selected,
        effective_qualification_mode,
        min_viability_score,
        quality_review,
        strategic_review,
    )

    if dry_run:
        source_preflight = _record_source_preflight(
            database_path=database_path,
            source_ref=source_ref,
            source_hash=input_hash,
            input_rows=len(candidates_raw),
            selected_count=len(selected),
            top_n=top_n,
            min_viability_score=min_viability_score,
            holdout_fraction=holdout_fraction,
            adversarial_fraction=adversarial_fraction,
            split_counts=split_counts,
            input_dedupe_summary=business_key_dedupe,
            launch_policy_hash=launch_policy_hash,
            interest_profile_hash=interest_profile_hash,
            qualification_mode=effective_qualification_mode,
        )

        result = {
            "mode": "dry_run",
            "qualification_mode": effective_qualification_mode,
            "source_path": source_ref,
            "selected_count": len(selected),
            "top_n": top_n,
            "min_viability_score": min_viability_score,
            "confidence_profile": confidence_profile,
            "telemetry": {
                "business_key_dedupe": business_key_dedupe,
                "qualification_gates": qualification_gate_telemetry,
            },
            "source_preflight": source_preflight,
            "selected": [
                {
                    "source_row": candidate.source_row,
                    "name": candidate.name,
                    "supplier": candidate.supplier,
                    "split": candidate.split,
                    "viability_score": candidate.viability_score,
                    "commercial_viability_score": candidate.viability_score,
                    "score_primary": candidate.score_primary,
                    "score_secondary": candidate.score_secondary,
                    "score_disagreement": candidate.score_disagreement,
                    **(
                        {
                            "quality_status": candidate.quality_review["status"],
                            "economics": candidate.quality_review["economics"],
                            "quality_warnings": candidate.quality_review["warnings"],
                        }
                        if candidate.quality_review is not None
                        else {}
                    ),
                    **(
                        {"strategic_fit": candidate.strategic_review}
                        if candidate.strategic_review is not None
                        else {}
                    ),
                }
                for candidate in selected
            ],
        }
        if quality_review is not None:
            result["quality_review"] = quality_review
        if strategic_review is not None:
            result["strategic_review"] = strategic_review
        return result

    run_key = _workflow_run_key(
        input_hash=input_hash,
        top_n=top_n,
        min_viability_score=min_viability_score,
        holdout_fraction=holdout_fraction,
        adversarial_fraction=adversarial_fraction,
        launch_policy_hash=launch_policy_hash,
        interest_profile_hash=interest_profile_hash,
        qualification_mode=effective_qualification_mode,
    )
    database_resolved_path = str(Path(database_path).resolve())

    with CanonicalDB(database_path) as db:
        with db.transaction():
            existing = db.connection.execute(
                "SELECT run_id, result_json FROM workflow_runs WHERE run_key = ?",
                (run_key,),
            ).fetchone()
            if existing is not None:
                replayed = json.loads(existing["result_json"])
                replayed_hash = replayed.get("bundle_sha256")
                if replayed_hash is not None and _bundle_result_hash(replayed) != replayed_hash:
                    raise RuntimeError("Stored workflow run result is missing integrity metadata or has been tampered.")
                if replayed_hash is None:
                    replayed["integrity"] = "legacy_unverified"
                has_active_claim = bool(
                    db.get_active_claims(subject_type="workflow_runs", subject_id=existing["run_id"], now=_utc_now())
                )
                replayed["mode"] = "replayed" if has_active_claim else "replayed_stale"
                if not has_active_claim:
                    replayed["freshness"] = {
                        "status": "stale",
                        "reason": "No active provenance claim for matching workflow run key.",
                    }
                return replayed

            source_preflight = _require_source_preflight(
                db=db,
                source_ref=source_ref,
                source_hash=input_hash,
                launch_policy_hash=launch_policy_hash,
                interest_profile_hash=interest_profile_hash,
                qualification_mode=effective_qualification_mode,
            )

            existing_candidates_by_key, existing_candidates_by_base_key = _load_existing_business_key_index(db)
            provenance_id = db.add_provenance(
                source_type="workflow",
                source_ref=f"workflow:product-research:{source_path}",
                source_uri=source_ref,
                content_hash=input_hash,
                notes="Seeded candidates from product research workflow",
                confidence=min(1.0, 0.5 + min(0.5, len(selected) / 20)),
            )

            written_rows = []
            for rank, candidate in enumerate(selected, start=1):
                existing_candidate = _find_existing_candidate(
                    existing_candidates_by_key,
                    existing_candidates_by_base_key,
                    candidate,
                )
                if existing_candidate is not None:
                    business_key_dedupe["existing_reused"] += 1
                    entity_id = existing_candidate["entity_id"]
                    action = "reused"
                    if (
                        candidate.strategic_review is None
                        and candidate.quality_review is None
                    ):
                        written_rows.append(
                            {
                                "action": action,
                                "entity_id": entity_id,
                                "name": candidate.name,
                                "supplier": candidate.supplier,
                                "business_key": candidate.business_key,
                                "split": candidate.split,
                                "viability_score": candidate.viability_score,
                                "commercial_viability_score": candidate.viability_score,
                                "score_primary": candidate.score_primary,
                                "score_secondary": candidate.score_secondary,
                                "score_disagreement": candidate.score_disagreement,
                                "quality_status": None,
                                "economics": None,
                                "strategic_fit": None,
                            }
                        )
                        continue
                else:
                    business_key_dedupe["new_candidates"] += 1
                    entity_id = db.insert(
                        "entities",
                        {
                            "entity_type": "product_candidate",
                            "name": candidate.name,
                            "status": "candidate",
                            "scope": "commerce",
                            "confidence": candidate.viability_score,
                        },
                        provenance_id=provenance_id,
                    )
                    action = "inserted"

                metrics_to_record = [
                    ("commerce.viability_score", candidate.viability_score, "ratio"),
                    ("commerce.viability_score_primary", candidate.score_primary, "ratio"),
                    ("commerce.viability_score_secondary", candidate.score_secondary, "ratio"),
                    ("commerce.viability_score_disagreement", candidate.score_disagreement, "ratio"),
                    ("commerce.margin_percent", candidate.margin_percent, "percent"),
                    ("commerce.shipping_days", candidate.shipping_days, "days"),
                    ("commerce.demand_signal", candidate.demand_signal, "ratio"),
                    ("commerce.saturation_signal", candidate.saturation_signal, "ratio"),
                ]
                if candidate.strategic_review is not None:
                    metrics_to_record.append(
                        (
                            "commerce.strategic_fit_score",
                            candidate.strategic_review["score"],
                            "score_0_100",
                        )
                    )
                    for component_name, component_value in candidate.strategic_review[
                        "components"
                    ].items():
                        metrics_to_record.append(
                            (f"commerce.{component_name}", component_value, "score_0_100")
                        )
                if candidate.quality_review is not None:
                    economics = candidate.quality_review["economics"]
                    economics_metric_specs = {
                        "reported_product_only_margin_percent": (
                            "commerce.product_only_margin_reported",
                            "percent",
                        ),
                        "landed_cost_usd": ("commerce.landed_cost_usd", "USD"),
                        "payment_fee_usd": ("commerce.payment_fee_usd", "USD"),
                        "gross_margin_percent": (
                            "commerce.gross_margin_percent_reconciled",
                            "percent",
                        ),
                        "pre_ad_contribution_usd": (
                            "commerce.pre_ad_contribution_usd",
                            "USD",
                        ),
                        "pre_ad_contribution_percent": (
                            "commerce.pre_ad_contribution_percent",
                            "percent",
                        ),
                        "break_even_cac_usd": ("commerce.break_even_cac_usd", "USD"),
                        "estimated_cac_usd": ("commerce.estimated_cac_usd", "USD"),
                        "post_ad_contribution_usd": (
                            "commerce.post_ad_contribution_usd",
                            "USD",
                        ),
                        "post_ad_contribution_percent": (
                            "commerce.post_ad_contribution_percent",
                            "percent",
                        ),
                    }
                    for economics_key, (metric_name, unit) in economics_metric_specs.items():
                        value = economics.get(economics_key)
                        if value is not None:
                            metrics_to_record.append((metric_name, value, unit))
                for metric_name, value, unit in metrics_to_record:
                    db.insert(
                        "metrics",
                        {
                            "metric_name": metric_name,
                            "metric_value": value,
                            "unit": unit,
                            "dimensions_json": json.dumps(
                                {
                                    "workflow": "product_research",
                                    "source_row": candidate.source_row,
                                    "entity_id": entity_id,
                                    "supplier": candidate.supplier,
                                    "source_uri": candidate.source_uri,
                                    "business_key": candidate.business_key,
                                    "quality_status": (
                                        candidate.quality_review["status"]
                                        if candidate.quality_review is not None
                                        else None
                                    ),
                                    "margin_basis": (
                                        candidate.quality_review.get("margin_basis")
                                        if candidate.quality_review is not None
                                        else None
                                    ),
                                    "thesis_id": (
                                        candidate.strategic_review["thesis_id"]
                                        if candidate.strategic_review is not None
                                        else None
                                    ),
                                    "audience_id": (
                                        candidate.strategic_review["audience_id"]
                                        if candidate.strategic_review is not None
                                        else None
                                    ),
                                },
                                sort_keys=True,
                            ),
                            "provenance_id": provenance_id,
                            "measured_at": _utc_now(),
                        },
                    )

                event_payload = {
                    "workflow": "product_research",
                    "qualification_mode": effective_qualification_mode,
                    "rank": rank,
                    "source_row": candidate.source_row,
                    "split": candidate.split,
                    "viability_score": candidate.viability_score,
                    "score_primary": candidate.score_primary,
                    "score_secondary": candidate.score_secondary,
                    "score_disagreement": candidate.score_disagreement,
                }
                if candidate.notes:
                    event_payload["notes"] = candidate.notes
                if candidate.cost_per_unit_usd is not None:
                    event_payload["cost_per_unit_usd"] = candidate.cost_per_unit_usd
                if candidate.retail_price_usd is not None:
                    event_payload["retail_price_usd"] = candidate.retail_price_usd
                if candidate.quality_review is not None:
                    event_payload["quality_status"] = candidate.quality_review["status"]
                    event_payload["margin_basis"] = candidate.quality_review.get("margin_basis")
                    event_payload["economics"] = candidate.quality_review["economics"]
                    event_payload["quality_warnings"] = candidate.quality_review["warnings"]
                if candidate.strategic_review is not None:
                    event_payload["strategic_fit"] = candidate.strategic_review

                db.insert(
                    "events",
                    {
                        "event_type": "workflow.product_research.candidate_selected",
                        "subject_type": "entities",
                        "subject_id": entity_id,
                        "payload_json": json.dumps(event_payload, sort_keys=True),
                        "provenance_id": provenance_id,
                        "occurred_at": _utc_now(),
                        "recorded_at": _utc_now(),
                    }
                )

                written_rows.append(
                    {
                        "action": action,
                        "entity_id": entity_id,
                        "name": candidate.name,
                        "supplier": candidate.supplier,
                        "viability_score": candidate.viability_score,
                        "commercial_viability_score": candidate.viability_score,
                        "score_primary": candidate.score_primary,
                        "score_secondary": candidate.score_secondary,
                        "score_disagreement": candidate.score_disagreement,
                        "split": candidate.split,
                        "rank": rank,
                        "business_key": candidate.business_key,
                        **(
                            {
                                "quality_status": candidate.quality_review["status"],
                                "economics": candidate.quality_review["economics"],
                                "quality_warnings": candidate.quality_review["warnings"],
                            }
                            if candidate.quality_review is not None
                            else {}
                        ),
                        **(
                            {"strategic_fit": candidate.strategic_review}
                            if candidate.strategic_review is not None
                            else {}
                        ),
                    }
                )

            result = {
                "mode": "write",
                "qualification_mode": effective_qualification_mode,
                "source_path": source_ref,
                "selected_count": len(selected),
                "top_n": top_n,
                "min_viability_score": min_viability_score,
                "confidence_profile": confidence_profile,
                "telemetry": {
                    "business_key_dedupe": business_key_dedupe,
                    "qualification_gates": qualification_gate_telemetry,
                },
                "source_preflight": source_preflight,
                "database_path": database_resolved_path,
                "written": written_rows,
            }
            if quality_review is not None:
                result["quality_review"] = quality_review
            if strategic_review is not None:
                result["strategic_review"] = strategic_review
            result["bundle_sha256"] = _bundle_result_hash(result)
            workflow_run_id = db.insert(
                "workflow_runs",
                {
                    "workflow_id": WORKFLOW_ID,
                    "run_key": run_key,
                    "input_hash": input_hash,
                    "source_uri": source_ref,
                    "status": "completed",
                    "result_json": json.dumps(result, sort_keys=True),
                    "started_at": _utc_now(),
                    "completed_at": _utc_now(),
                    "provenance_id": provenance_id,
                },
            )

            claim_id = db.add_claim(
                "workflow_runs",
                workflow_run_id,
                (
                    "Executed product_research workflow with deterministic scoring "
                    f"and selected {len(written_rows)} candidate(s)"
                ),
                title="product_research_selection",
                source_type="workflow",
                source_artifact_id=workflow_run_id,
                source_locator=source_ref,
                source_hash=input_hash,
                freshness_ttl_seconds=7 * 24 * 60 * 60,
                freshness_rule="ttl_seconds:604800",
                authority_class="system",
                verification_method="workflow_router+product_research.scoring",
                confidence=min(1.0, 0.5 + len(written_rows) / 20),
                provenance_id=provenance_id,
            )
            result["claim_id"] = claim_id

    return result


def run_product_research_with_telemetry(
    catalog_path: str | Path,
    *,
    database_path: str | Path = "canonical/efficiens.db",
    top_n: int = 3,
    min_viability_score: float = 0.0,
    holdout_fraction: float = 0.0,
    adversarial_fraction: float = 0.0,
    launch_policy_path: str | Path | None = None,
    interest_profile_path: str | Path | None = None,
    qualification_mode: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Workflow A and also persist a run_metrics telemetry row.

    This is a thin wrapper around `run_product_research` that opens a separate
    connection for telemetry so it never interferes with the main workflow
    transaction.
    """
    started_at = _utc_now()
    run_start = time.perf_counter_ns()
    result = run_product_research(
        catalog_path,
        database_path=database_path,
        top_n=top_n,
        min_viability_score=min_viability_score,
        holdout_fraction=holdout_fraction,
        adversarial_fraction=adversarial_fraction,
        launch_policy_path=launch_policy_path,
        interest_profile_path=interest_profile_path,
        qualification_mode=qualification_mode,
        dry_run=dry_run,
    )
    duration_ms = (time.perf_counter_ns() - run_start) // 1_000_000
    with CanonicalDB(database_path) as db:
        _record_workflow_run_telemetry(
            db,
            workflow_id=WORKFLOW_ID,
            run_key=_workflow_run_key(
                input_hash=_file_hash(Path(catalog_path)),
                top_n=top_n,
                min_viability_score=min_viability_score,
                holdout_fraction=holdout_fraction,
                adversarial_fraction=adversarial_fraction,
                launch_policy_hash=(
                    _file_hash(Path(launch_policy_path)) if launch_policy_path else None
                ),
                interest_profile_hash=(
                    _file_hash(
                        Path(interest_profile_path)
                        if interest_profile_path is not None
                        else Path(launch_policy_path).with_name("operator-interest-profile.json")
                    )
                    if launch_policy_path
                    else None
                ),
                qualification_mode=(
                    qualification_mode
                    or ("organic_sample" if launch_policy_path else "legacy")
                ),
            ),
            source_ref=str(Path(catalog_path).resolve()),
            result=result,
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=duration_ms,
            dry_run=dry_run,
            replayed=result.get("mode", "").startswith("replayed"),
        )
    return result


def _record_workflow_run_telemetry(
    db: CanonicalDB,
    *,
    workflow_id: str,
    run_key: str,
    source_ref: str,
    result: dict[str, Any],
    started_at: str,
    completed_at: str,
    duration_ms: int,
    dry_run: bool,
    replayed: bool,
) -> str:
    """Persist a metadata-only run_metrics row for a workflow run.

    Captures summary telemetry (dedupe, confidence profile, preflight) without
    raw candidate details so the row stays small and privacy-safe.
    """
    telemetry = result.get("telemetry", {})
    business_key_dedupe = telemetry.get("business_key_dedupe", {})
    qualification_gates = telemetry.get("qualification_gates", {})
    confidence_profile = result.get("confidence_profile", {})
    evaluation_profile = confidence_profile.get("evaluation_profile", {})
    source_preflight = result.get("source_preflight", {})
    selected_count = result.get("selected_count", 0)

    errors: list[str] = []
    if evaluation_profile.get("drift_flag"):
        errors.append("drift_flag")
    if source_preflight.get("status") != "passed":
        errors.append("source_preflight_failed")

    resource_usage = {
        "workflow_id": workflow_id,
        "run_key": run_key,
        "dry_run": dry_run,
        "replayed": replayed,
        "selected_count": selected_count,
        "incoming_rows": business_key_dedupe.get("incoming_rows", 0),
        "batch_unique_rows": business_key_dedupe.get("batch_unique_rows", 0),
        "existing_reused": business_key_dedupe.get("existing_reused", 0),
        "new_candidates": business_key_dedupe.get("new_candidates", 0),
        "qualification_mode": qualification_gates.get("qualification_mode", "legacy"),
        "commercial_eligible_count": qualification_gates.get("commercial_eligible_count", 0),
        "quality_eligible_count": qualification_gates.get("quality_eligible_count", 0),
        "strategic_eligible_count": qualification_gates.get("strategic_eligible_count", 0),
        "joint_eligible_count": qualification_gates.get("joint_eligible_count", 0),
        "quality_rejection_reason_counts": qualification_gates.get(
            "quality_rejection_reason_counts", {}
        ),
        "strategic_rejection_reason_counts": qualification_gates.get(
            "strategic_rejection_reason_counts", {}
        ),
        "rejections_by_thesis": qualification_gates.get("rejections_by_thesis", {}),
        "rejections_by_supplier": qualification_gates.get("rejections_by_supplier", {}),
        "disagreement_mean": confidence_profile.get("disagreement_mean", 0.0),
        "abstention_count": confidence_profile.get("abstention_count", 0),
    }

    return db.record_run(
        request_type="product_research",
        route_selected=workflow_id,
        tools_json=["product_research_workflow", "canonical_db"],
        model_or_agent=detect_active_model(),
        input_size=business_key_dedupe.get("incoming_rows", 0),
        handoff_size=selected_count,
        duration_ms=duration_ms,
        resource_usage_json=resource_usage,
        errors_json=errors,
        retries=0,
        verification_result="pass" if not errors else "fail",
        final_outcome="accepted" if not errors else "rejected",
        acceptance_status="accepted" if not errors else "rejected",
        started_at=started_at,
        completed_at=completed_at,
    )





def _build_evaluation_profile(candidates: list[ProductCandidate]) -> dict[str, Any]:
    """Compute per-split viability statistics and a simple drift flag.

    The profile compares live, holdout, and adversarial splits so operators can
    spot distribution shifts before trusting the selected live candidates.
    """
    by_split: dict[str, list[ProductCandidate]] = {"live": [], "holdout": [], "adversarial": []}
    for candidate in candidates:
        by_split.setdefault(candidate.split, []).append(candidate)

    def _split_stats(split_candidates: list[ProductCandidate]) -> dict[str, float | int]:
        if not split_candidates:
            return {
                "sample_count": 0,
                "mean_viability": 0.0,
                "mean_disagreement": 0.0,
                "min_viability": 0.0,
                "max_viability": 0.0,
            }
        viabilities = [c.viability_score for c in split_candidates]
        disagreements = [c.score_disagreement for c in split_candidates]
        return {
            "sample_count": len(split_candidates),
            "mean_viability": round(sum(viabilities) / len(viabilities), 4),
            "mean_disagreement": round(sum(disagreements) / len(disagreements), 4),
            "min_viability": round(min(viabilities), 4),
            "max_viability": round(max(viabilities), 4),
        }

    profile = {
        "live": _split_stats(by_split.get("live", [])),
        "holdout": _split_stats(by_split.get("holdout", [])),
        "adversarial": _split_stats(by_split.get("adversarial", [])),
    }

    # Drift detection: flag if live mean viability differs from holdout/adversarial
    # by more than a simple threshold relative to the overall mean.
    live_mean = profile["live"]["mean_viability"]
    drift_flag = False
    drift_details: list[str] = []
    threshold = 0.05
    for split in ("holdout", "adversarial"):
        split_mean = profile[split]["mean_viability"]
        if profile[split]["sample_count"] == 0:
            continue
        if abs(live_mean - split_mean) > threshold:
            drift_flag = True
            drift_details.append(
                f"{split} mean viability {split_mean} differs from live {live_mean} by more than {threshold}"
            )

    profile["drift_flag"] = drift_flag
    profile["drift_details"] = drift_details
    return profile


def _normalize_business_key_value(value: str) -> str:
    return " ".join((value or "").strip().lower().split())

def _build_business_key(*, name: str, supplier: str, source_uri: str | None = None) -> str:
    parts = [
        _normalize_business_key_value(name),
        _normalize_business_key_value(supplier),
    ]
    if source_uri is not None:
        normalized_source_uri = _normalize_business_key_value(source_uri)
        if normalized_source_uri:
            parts.append(normalized_source_uri)
    return "|".join(parts)


def _dedupe_candidates_by_business_key(
    candidates: list[ProductCandidate],
) -> tuple[list[ProductCandidate], int]:
    deduped: dict[str, ProductCandidate] = {}
    for candidate in candidates:
        existing = deduped.get(candidate.business_key)
        if existing is None or candidate.viability_score > existing.viability_score:
            deduped[candidate.business_key] = candidate
    return list(deduped.values()), len(candidates) - len(deduped)


def _load_existing_business_key_index(db: CanonicalDB) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    by_business_key: dict[str, dict[str, str]] = {}
    by_business_key_without_source: dict[str, dict[str, str]] = {}

    rows = db.connection.execute(
        "SELECT entity_id, name FROM entities WHERE entity_type = ?",
        ("product_candidate",),
    ).fetchall()

    for row in rows:
        metric = db.connection.execute(
            "SELECT metric_value, dimensions_json FROM metrics "
            "WHERE metric_name = 'commerce.viability_score' AND json_extract(dimensions_json, '$.entity_id') = ? "
            "ORDER BY measured_at DESC LIMIT 1",
            (row["entity_id"],),
        ).fetchone()
        if metric is None:
            continue
        try:
            dimensions = json.loads(metric["dimensions_json"] or "{}")
        except json.JSONDecodeError:
            continue

        supplier = str(dimensions.get("supplier") or "")
        source_uri = str(dimensions.get("source_uri") or "")

        with_source = _build_business_key(
            name=row["name"],
            supplier=supplier,
            source_uri=source_uri,
        )
        without_source = _build_business_key(name=row["name"], supplier=supplier, source_uri=None)
        payload = {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "supplier": supplier,
            "source_uri": source_uri,
            "viability_score": metric["metric_value"],
        }
        by_business_key[with_source] = payload
        by_business_key_without_source.setdefault(without_source, payload)

    return by_business_key, by_business_key_without_source


def _find_existing_candidate(
    by_business_key: dict[str, dict[str, str]],
    by_business_key_without_source: dict[str, dict[str, str]],
    candidate: ProductCandidate,
) -> dict[str, str] | None:
    direct_hit = by_business_key.get(candidate.business_key)
    if direct_hit is not None:
        return direct_hit

    fallback_key = _build_business_key(name=candidate.name, supplier=candidate.supplier, source_uri=None)
    return by_business_key_without_source.get(fallback_key)


def _record_source_preflight(
    *,
    database_path: str | Path,
    source_ref: str,
    source_hash: str,
    input_rows: int,
    selected_count: int,
    top_n: int,
    min_viability_score: float,
    holdout_fraction: float,
    adversarial_fraction: float,
    split_counts: dict[str, int],
    input_dedupe_summary: dict[str, int],
    launch_policy_hash: str | None,
    interest_profile_hash: str | None,
    qualification_mode: str,
) -> dict[str, Any]:
    check_result: dict[str, Any] = {
        "status": "passed",
        "mode": "dry_run",
        "source_hash": source_hash,
        "input_rows": input_rows,
        "selected_count": selected_count,
        "top_n": top_n,
        "min_viability_score": min_viability_score,
        "holdout_fraction": holdout_fraction,
        "adversarial_fraction": adversarial_fraction,
        "split_counts": split_counts,
        "business_key_dedupe": input_dedupe_summary,
        "launch_policy_hash": launch_policy_hash,
        "interest_profile_hash": interest_profile_hash,
        "qualification_mode": qualification_mode,
    }
    checked_at = _utc_now()
    payload = json.dumps(check_result, sort_keys=True)

    with CanonicalDB(database_path) as db:
        existing = db.connection.execute(
            "SELECT 1 FROM source_freshness WHERE source_ref = ?",
            (source_ref,),
        ).fetchone()
        if existing is None:
            db.connection.execute(
                "INSERT INTO source_freshness (source_ref, last_checked_at, source_timestamp, freshness_status, check_result, provenance_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_ref, checked_at, checked_at, "passed", payload, None),
            )
        else:
            db.connection.execute(
                "UPDATE source_freshness "
                "SET last_checked_at = ?, source_timestamp = ?, freshness_status = ?, check_result = ? "
                "WHERE source_ref = ?",
                (checked_at, checked_at, "passed", payload, source_ref),
            )
        db.connection.commit()

    return {
        "source_ref": source_ref,
        "status": "passed",
        "checked_at": checked_at,
        "source_hash": source_hash,
        "check_result": check_result,
    }


def _require_source_preflight(
    *,
    db: CanonicalDB,
    source_ref: str,
    source_hash: str,
    launch_policy_hash: str | None,
    interest_profile_hash: str | None,
    qualification_mode: str,
) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT source_ref, last_checked_at, freshness_status, check_result "
        "FROM source_freshness WHERE source_ref = ?",
        (source_ref,),
    ).fetchone()

    if row is None:
        raise WorkflowPreflightError(
            "No dry-run preflight record found for this source. "
            "Run the same catalog with --dry-run before writing canonical results."
        )

    if row["freshness_status"] != "passed":
        raise WorkflowPreflightError(
            "Source preflight did not pass for this source. "
            "Rerun the catalog with --dry-run and then retry this write."
        )

    try:
        check_result = json.loads(row["check_result"] or "{}") if row["check_result"] is not None else {}
    except json.JSONDecodeError:
        check_result = {}

    if check_result.get("source_hash") != source_hash:
        raise WorkflowPreflightError(
            "Source content changed since the last dry-run. "
            "Rerun the catalog with --dry-run before writing canonical results."
        )

    if check_result.get("launch_policy_hash") != launch_policy_hash:
        raise WorkflowPreflightError(
            "Launch policy changed since the last dry-run. "
            "Rerun the catalog with --dry-run before writing canonical results."
        )

    if check_result.get("interest_profile_hash") != interest_profile_hash:
        raise WorkflowPreflightError(
            "Interest profile changed since the last dry-run. "
            "Rerun the catalog with --dry-run before writing canonical results."
        )

    if check_result.get("qualification_mode") != qualification_mode:
        raise WorkflowPreflightError(
            "Qualification mode changed since the last dry-run. "
            "Rerun the catalog with --dry-run before writing canonical results."
        )

    return {
        "source_ref": row["source_ref"],
        "status": row["freshness_status"],
        "checked_at": row["last_checked_at"],
        "source_hash": source_hash,
        "check_result": check_result,
    }


def _iter_catalog(
    path: Path,
    *,
    holdout_fraction: float,
    adversarial_fraction: float,
    launch_policy: dict[str, Any] | None = None,
    interest_profile: dict[str, Any] | None = None,
    qualification_mode: str = "legacy",
) -> list[ProductCandidate]:
    if not path.exists():
        raise FileNotFoundError(f"Catalog path does not exist: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"product_name", "supplier_name", "margin_percent", "shipping_days", "demand_signal", "saturation_signal"}
        missing = required.difference(reader.fieldnames or set())
        if missing:
            raise ValueError(f"Catalog is missing required columns: {', '.join(sorted(missing))}")
        if launch_policy is not None and qualification_mode != "research":
            missing_production = PRODUCTION_REQUIRED_COLUMNS.difference(reader.fieldnames or set())
            if missing_production:
                raise ValueError(
                    "Catalog is missing production columns: "
                    + ", ".join(sorted(missing_production))
                )
        if interest_profile is not None:
            missing_strategic = STRATEGIC_REQUIRED_COLUMNS.difference(reader.fieldnames or set())
            if missing_strategic:
                raise ValueError(
                    "Catalog is missing strategic-fit columns: "
                    + ", ".join(sorted(missing_strategic))
                )

        for row_number, row in enumerate(reader, start=2):
            name = (row.get("product_name") or "").strip()
            if not name:
                continue
            supplier = (row.get("supplier_name") or "Unknown supplier").strip()
            margin = _to_float(row.get("margin_percent"), 0.0, "margin_percent")
            shipping_days = _to_float(row.get("shipping_days"), 0.0, "shipping_days")
            demand = _to_float(row.get("demand_signal"), 0.0, "demand_signal")
            saturation = _to_float(row.get("saturation_signal"), 0.0, "saturation_signal")
            source_uri = (row.get("source_uri") or "").strip() or str(path)
            score_primary = _score_row_primary(
                margin=margin,
                shipping_days=shipping_days,
                demand=demand,
                saturation=saturation,
            )
            score_secondary = _score_row_secondary(
                margin=margin,
                shipping_days=shipping_days,
                demand=demand,
                saturation=saturation,
            )
            yield ProductCandidate(
                source_row=row_number,
                name=name,
                supplier=supplier,
                source_uri=source_uri,
                margin_percent=margin,
                shipping_days=shipping_days,
                demand_signal=demand,
                saturation_signal=saturation,
                cost_per_unit_usd=_to_optional_float(row.get("cost_per_unit_usd")),
                retail_price_usd=_to_optional_float(row.get("retail_price_usd")),
                notes=(row.get("notes") or "").strip() or None,
                score_primary=score_primary,
                score_secondary=score_secondary,
                score_disagreement=_candidate_score_disagreement(
                    score_primary=score_primary,
                    score_secondary=score_secondary,
                ),
                viability_score=_blend_evaluator_scores(
                    score_primary=score_primary,
                    score_secondary=score_secondary,
                ),
                split=_assign_candidate_split(
                    source_uri=source_uri,
                    source_row=row_number,
                    product_name=name,
                    supplier=supplier,
                    holdout_fraction=holdout_fraction,
                    adversarial_fraction=adversarial_fraction,
                ),
                business_key=_build_business_key(
                    name=name,
                    supplier=supplier,
                    source_uri=source_uri,
                ),
                quality_review=(
                    _evaluate_qualification_row(row, launch_policy, qualification_mode)
                    if launch_policy is not None
                    else None
                ),
                strategic_review=(
                    _evaluate_interest_profile_row(row, interest_profile)
                    if interest_profile is not None
                    else None
                ),
            )


def _load_launch_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path)
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid launch policy JSON: {policy_path}") from exc
    if payload.get("schema") != "commerce-launch-policy.v1":
        raise ValueError("Launch policy schema must be commerce-launch-policy.v1")
    missing = LAUNCH_POLICY_REQUIRED_FIELDS.difference(payload)
    if missing:
        raise ValueError(f"Launch policy missing required fields: {', '.join(sorted(missing))}")
    retail_range = payload["retail_price_usd"]
    if not isinstance(retail_range, dict) or not {"min", "max"}.issubset(retail_range):
        raise ValueError("Launch policy retail_price_usd must define min and max")
    if float(retail_range["min"]) <= 0 or float(retail_range["max"]) < float(retail_range["min"]):
        raise ValueError("Launch policy retail_price_usd range is invalid")
    positive_fields = {
        "evidence_max_age_days",
        "max_moq",
        "max_shipping_days",
        "sample_budget_usd",
        "sample_shortlist_size",
    }
    invalid_positive = [field for field in positive_fields if float(payload[field]) <= 0]
    if invalid_positive:
        raise ValueError(
            "Launch policy fields must be positive: " + ", ".join(sorted(invalid_positive))
        )
    product_characters = payload["product_character_requirements"]
    if not isinstance(product_characters, list) or not product_characters:
        raise ValueError("Launch policy product_character_requirements must be non-empty")
    modes = payload["qualification_modes"]
    required_modes = {"research", "organic_sample", "paid_launch"}
    if not isinstance(modes, dict) or not required_modes.issubset(modes):
        raise ValueError(
            "Launch policy qualification_modes must define research, organic_sample, and paid_launch"
        )
    for mode_name in required_modes:
        if int(modes[mode_name].get("max_shortlist_size", 0)) <= 0:
            raise ValueError(f"Launch policy {mode_name} max_shortlist_size must be positive")
    margin_basis_values = payload["margin_basis_values"]
    if not isinstance(margin_basis_values, list) or not margin_basis_values:
        raise ValueError("Launch policy margin_basis_values must be non-empty")
    if payload["comparable_margin_basis"] not in margin_basis_values:
        raise ValueError("Launch policy comparable_margin_basis must be an allowed basis")
    if payload["retail_price_basis"] != "offer_total":
        raise ValueError("Launch policy retail_price_basis must be offer_total")
    bundle_rules = payload["bundle_rules"]
    if not isinstance(bundle_rules, dict) or int(
        bundle_rules.get("minimum_component_count", 0)
    ) < 2:
        raise ValueError("Launch policy bundle_rules minimum_component_count must be at least 2")
    return payload


def _load_interest_profile(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid interest profile JSON: {profile_path}") from exc
    if payload.get("schema") != "commerce-interest-profile.v1":
        raise ValueError("Interest profile schema must be commerce-interest-profile.v1")
    missing = INTEREST_PROFILE_REQUIRED_FIELDS.difference(payload)
    if missing:
        raise ValueError(f"Interest profile missing required fields: {', '.join(sorted(missing))}")
    if not 0.0 <= float(payload["minimum_strategic_fit_score"]) <= 100.0:
        raise ValueError("Interest profile minimum_strategic_fit_score must be between 0 and 100")
    if int(payload["minimum_content_angles"]) <= 0:
        raise ValueError("Interest profile minimum_content_angles must be positive")
    weights = payload["strategic_fit_weights"]
    expected_components = {
        "audience_fit_score",
        "operator_fit_score",
        "content_demo_score",
        "brand_extension_score",
        "bundle_potential_score",
        "case_study_value_score",
    }
    if not isinstance(weights, dict) or set(weights) != expected_components:
        raise ValueError(
            "Interest profile strategic_fit_weights must define: "
            + ", ".join(sorted(expected_components))
        )
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
        raise ValueError("Interest profile strategic_fit_weights must sum to 1.0")
    theses = payload["approved_theses"]
    if not isinstance(theses, list) or not theses:
        raise ValueError("Interest profile approved_theses must be a non-empty list")
    thesis_ids = [str(thesis.get("thesis_id") or "").strip() for thesis in theses]
    if any(not thesis_id for thesis_id in thesis_ids) or len(thesis_ids) != len(set(thesis_ids)):
        raise ValueError("Interest profile thesis_id values must be non-empty and unique")
    return payload


def _evaluate_interest_profile_row(
    row: dict[str, str | None],
    profile: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    thesis_id = (row.get("thesis_id") or "").strip().lower()
    audience_id = (row.get("audience_id") or "").strip().lower()
    problem_space = (row.get("problem_space") or "").strip().lower()
    problem_statement = (row.get("problem_statement") or "").strip()
    why_operator_cares = (row.get("why_operator_cares") or "").strip()
    why_customer_cares = (row.get("why_customer_cares") or "").strip()
    category = (row.get("category") or "").strip().lower()
    operator_fit_status = (row.get("operator_fit_status") or "").strip().lower()
    interest_tags = _parse_pipe_values(row.get("interest_tags"))
    content_angles = _parse_pipe_values(row.get("content_angles"))

    theses = {
        str(thesis["thesis_id"]).strip().lower(): thesis
        for thesis in profile["approved_theses"]
    }
    thesis = theses.get(thesis_id)
    if thesis is None:
        reasons.append("outside_operator_thesis")

    excluded_categories = {
        str(value).strip().lower() for value in profile["default_excluded_categories"]
    }
    if category in excluded_categories:
        reasons.append("category_excluded_by_default")

    primary_audiences = {
        str(value).strip().lower() for value in profile["primary_audience_ids"]
    }
    if audience_id not in primary_audiences:
        reasons.append("audience_outside_profile")
    approved_problem_spaces = {
        str(value).strip().lower() for value in profile["approved_problem_spaces"]
    }
    if problem_space not in approved_problem_spaces:
        reasons.append("problem_space_outside_profile")

    if thesis is not None:
        thesis_audiences = {
            str(value).strip().lower() for value in thesis.get("audience_ids", [])
        }
        thesis_problem_spaces = {
            str(value).strip().lower() for value in thesis.get("problem_spaces", [])
        }
        thesis_interest_tags = {
            str(value).strip().lower() for value in thesis.get("interest_tags", [])
        }
        if audience_id not in thesis_audiences:
            reasons.append("audience_outside_thesis")
        if problem_space not in thesis_problem_spaces:
            reasons.append("problem_space_outside_thesis")
        if not interest_tags.intersection(thesis_interest_tags):
            reasons.append("interest_tags_outside_thesis")

    approved_operator_statuses = {
        str(value).strip().lower() for value in profile["operator_fit_statuses"]
    }
    if operator_fit_status not in approved_operator_statuses:
        reasons.append("operator_fit_not_approved")
    if not problem_statement:
        reasons.append("problem_statement_missing")
    if not why_operator_cares:
        reasons.append("why_operator_cares_missing")
    if not why_customer_cares:
        reasons.append("why_customer_cares_missing")
    if len(content_angles) < int(profile["minimum_content_angles"]):
        reasons.append("insufficient_content_angles")

    components: dict[str, float] = {}
    for field in profile["strategic_fit_weights"]:
        value = _to_optional_float(row.get(field))
        if value is None or not 0.0 <= value <= 100.0:
            reasons.append(f"invalid_strategic_component:{field}")
        else:
            components[field] = value

    score = 0.0
    if len(components) == len(profile["strategic_fit_weights"]):
        score = round(
            sum(
                components[field] * float(weight)
                for field, weight in profile["strategic_fit_weights"].items()
            ),
            2,
        )
        if score < float(profile["minimum_strategic_fit_score"]):
            reasons.append("strategic_fit_below_profile")

    return {
        "passed": not reasons,
        "score": score,
        "minimum_score": float(profile["minimum_strategic_fit_score"]),
        "profile_id": profile["profile_id"],
        "thesis_id": thesis_id,
        "audience_id": audience_id,
        "problem_space": problem_space,
        "problem_statement": problem_statement,
        "why_operator_cares": why_operator_cares,
        "why_customer_cares": why_customer_cares,
        "category": category,
        "interest_tags": sorted(interest_tags),
        "content_angles": sorted(content_angles),
        "operator_fit_status": operator_fit_status,
        "components": components,
        "reasons": reasons,
    }


def _product_character_reasons(
    row: dict[str, str | None],
    policy: dict[str, Any],
) -> list[str]:
    provided = _parse_pipe_values(row.get("product_character_tags"))
    required = {
        str(value).strip().lower() for value in policy["product_character_requirements"]
    }
    return [f"product_character_missing:{tag}" for tag in sorted(required - provided)]


def _evaluate_qualification_row(
    row: dict[str, str | None],
    policy: dict[str, Any],
    qualification_mode: str,
) -> dict[str, Any]:
    if qualification_mode != "research":
        return _evaluate_launch_policy_row(row, policy, qualification_mode)

    reasons = _product_character_reasons(row, policy)
    warnings = ["production_evidence_not_required_in_research"]
    declared_margin = _to_optional_float(row.get("margin_percent"))
    minimum_margin = float(
        policy["qualification_modes"]["research"]["min_product_only_margin_percent"]
    )
    if declared_margin is None:
        reasons.append("missing_margin_percent")
    elif declared_margin < minimum_margin:
        reasons.append("product_only_margin_below_research_mode")
    return {
        "passed": not reasons,
        "status": (
            policy["qualification_modes"]["research"]["quality_status"]
            if not reasons
            else "rejected"
        ),
        "qualification_mode": qualification_mode,
        "reasons": reasons,
        "warnings": warnings,
        "economics": {
            "reported_product_only_margin_percent": declared_margin,
        },
    }


def _evaluate_launch_policy_row(
    row: dict[str, str | None],
    policy: dict[str, Any],
    qualification_mode: str,
) -> dict[str, Any]:
    mode_config = policy["qualification_modes"][qualification_mode]
    reasons: list[str] = _product_character_reasons(row, policy)
    warnings: list[str] = []
    margin_basis = (row.get("margin_basis") or "").strip().lower()
    allowed_margin_bases = {
        str(value).strip().lower() for value in policy["margin_basis_values"]
    }
    if margin_basis not in allowed_margin_bases:
        reasons.append("margin_basis_not_approved")
    if (row.get("evidence_status") or "").strip().lower() != "verified":
        reasons.append("evidence_status_not_verified")
    if _is_placeholder_or_invalid_url(row.get("source_uri")):
        reasons.append("placeholder_product_url")
    if _is_placeholder_or_invalid_url(row.get("supplier_url")):
        reasons.append("placeholder_supplier_url")
    if (row.get("supplier_identity_status") or "").strip().lower() != "verified":
        reasons.append("supplier_identity_not_verified")
    if _is_placeholder_or_invalid_url(row.get("return_policy_url")):
        reasons.append("return_policy_url_invalid")
    if (row.get("compliance_ip_status") or "").strip().lower() != "cleared":
        reasons.append("compliance_ip_not_cleared")
    quality_evidence_status = (row.get("quality_evidence_status") or "").strip().lower()
    if quality_evidence_status not in {
        "sample_pending",
        "sample_passed",
    }:
        reasons.append("quality_evidence_status_unknown")

    evidence_checked_at = (row.get("evidence_checked_at") or "").strip()
    try:
        checked_at = datetime.fromisoformat(evidence_checked_at.replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if checked_at > now:
            reasons.append("evidence_timestamp_in_future")
        elif (now - checked_at).total_seconds() > float(policy["evidence_max_age_days"]) * 86400:
            reasons.append("evidence_stale")
    except ValueError:
        reasons.append("invalid_evidence_checked_at")

    cost = _to_optional_float(row.get("cost_per_unit_usd"))
    retail = _to_optional_float(row.get("retail_price_usd"))
    shipping = _to_optional_float(row.get("shipping_cost_usd"))
    packaging = _to_optional_float(row.get("packaging_cost_usd"))
    duties = _to_optional_float(row.get("duties_usd"))
    payment_fee_percent = _to_optional_float(row.get("payment_fee_percent"))
    payment_fee_fixed = _to_optional_float(row.get("payment_fee_fixed_usd"))
    expected_return_cost = _to_optional_float(row.get("expected_return_cost_usd"))
    economics_inputs = {
        "cost_per_unit_usd": cost,
        "retail_price_usd": retail,
        "shipping_cost_usd": shipping,
        "packaging_cost_usd": packaging,
        "duties_usd": duties,
        "payment_fee_percent": payment_fee_percent,
        "payment_fee_fixed_usd": payment_fee_fixed,
        "expected_return_cost_usd": expected_return_cost,
    }
    for field, value in economics_inputs.items():
        if value is None:
            reasons.append(f"missing_{field}")
        elif value < 0:
            reasons.append(f"negative_{field}")

    retail_range = policy["retail_price_usd"]
    if retail is not None and not (
        float(retail_range["min"]) <= retail <= float(retail_range["max"])
    ):
        reasons.append("retail_price_outside_policy")

    shipping_days = _to_optional_float(row.get("shipping_days"))
    if shipping_days is None:
        reasons.append("missing_shipping_days")
    elif shipping_days > float(policy["max_shipping_days"]):
        reasons.append("shipping_days_above_policy")

    warehouse_country = (row.get("warehouse_country") or "").strip().upper()
    if warehouse_country != str(policy["market"]).upper():
        reasons.append("warehouse_country_outside_market")

    inventory_status = (row.get("inventory_status") or "").strip().lower()
    approved_inventory = {
        str(status).strip().lower() for status in policy["required_inventory_statuses"]
    }
    if inventory_status not in approved_inventory:
        reasons.append("inventory_status_not_approved")

    tracking_available = _to_optional_bool(row.get("tracking_available"))
    if policy.get("require_tracking") and tracking_available is not True:
        reasons.append("tracking_required")

    moq = _to_optional_float(row.get("moq"))
    if moq is None:
        reasons.append("missing_moq")
    elif moq > float(policy["max_moq"]):
        reasons.append("moq_above_policy")

    risk_tags = {
        value.strip().lower()
        for value in (row.get("risk_tags") or "").replace(",", "|").split("|")
        if value.strip()
    }
    if not risk_tags:
        reasons.append("risk_review_not_explicit")
    excluded_risk_tags = {
        str(value).strip().lower() for value in policy["excluded_risk_tags"]
    }
    for risk_tag in sorted(risk_tags.intersection(excluded_risk_tags)):
        reasons.append(f"excluded_risk_tag:{risk_tag}")

    estimated_cac_text = (row.get("estimated_cac_usd") or "").strip().lower()
    estimated_cac: float | None = None
    if estimated_cac_text in {"", "unknown"}:
        warnings.append("estimated_cac_unknown")
    else:
        estimated_cac = _to_optional_float(estimated_cac_text)
        if estimated_cac is None or estimated_cac < 0:
            reasons.append("invalid_estimated_cac_usd")

    economics: dict[str, Any] = {}
    economics_ready = (
        retail is not None
        and retail > 0
        and all(value is not None and value >= 0 for value in economics_inputs.values())
    )
    if economics_ready:
        landed_cost = float(cost + shipping + packaging + duties)
        payment_fee = float((retail * payment_fee_percent / 100.0) + payment_fee_fixed)
        gross_margin_percent = ((retail - landed_cost) / retail) * 100.0
        pre_ad_contribution = retail - landed_cost - payment_fee - expected_return_cost
        pre_ad_contribution_percent = (pre_ad_contribution / retail) * 100.0
        economics = {
            "landed_cost_usd": round(landed_cost, 2),
            "payment_fee_usd": round(payment_fee, 2),
            "gross_margin_percent": round(gross_margin_percent, 2),
            "pre_ad_contribution_usd": round(pre_ad_contribution, 2),
            "pre_ad_contribution_percent": round(pre_ad_contribution_percent, 2),
            "break_even_cac_usd": round(pre_ad_contribution, 2),
        }
        if estimated_cac is not None:
            post_ad_contribution = pre_ad_contribution - estimated_cac
            post_ad_contribution_percent = (post_ad_contribution / retail) * 100.0
            economics["estimated_cac_usd"] = round(estimated_cac, 2)
            economics["post_ad_contribution_usd"] = round(post_ad_contribution, 2)
            economics["post_ad_contribution_percent"] = round(
                post_ad_contribution_percent,
                2,
            )
        declared_margin = _to_optional_float(row.get("margin_percent"))
        if declared_margin is None:
            reasons.append("missing_margin_percent")
        elif margin_basis == str(policy["comparable_margin_basis"]).strip().lower():
            if abs(declared_margin - gross_margin_percent) > float(
                policy["max_margin_reconciliation_delta_percent"]
            ):
                reasons.append("margin_claim_not_reconciled")
        elif margin_basis in allowed_margin_bases:
            warnings.append(f"reported_margin_not_comparable:{margin_basis}")
        if gross_margin_percent < float(mode_config["min_gross_margin_percent"]):
            reasons.append("gross_margin_below_mode")
        if pre_ad_contribution_percent < float(
            mode_config["min_pre_ad_contribution_percent"]
        ):
            reasons.append("pre_ad_contribution_percent_below_mode")
        minimum_pre_ad_usd = mode_config.get("min_pre_ad_contribution_usd")
        if minimum_pre_ad_usd is not None and pre_ad_contribution < float(minimum_pre_ad_usd):
            reasons.append("pre_ad_contribution_usd_below_mode")
        if qualification_mode == "paid_launch" and estimated_cac is not None:
            if post_ad_contribution_percent < float(
                mode_config["min_post_ad_contribution_percent"]
            ):
                reasons.append("post_ad_contribution_percent_below_mode")

    sample_status = (row.get("sample_status") or "pending").strip().lower()
    if sample_status == "failed":
        reasons.append("sample_failed")
    elif sample_status not in {"pending", "passed"}:
        reasons.append("invalid_sample_status")
    elif (
        (sample_status == "passed" and quality_evidence_status != "sample_passed")
        or (sample_status == "pending" and quality_evidence_status != "sample_pending")
    ):
        reasons.append("sample_quality_evidence_mismatch")

    if qualification_mode == "paid_launch":
        if sample_status != "passed":
            reasons.append("sample_required_for_paid_launch")
        if estimated_cac is None:
            reasons.append("estimated_cac_required_for_paid_launch")
        status = mode_config["quality_status"]
    else:
        status = (
            mode_config["sample_passed_status"]
            if sample_status == "passed"
            else mode_config["sample_pending_status"]
        )
        if estimated_cac is not None and economics.get("post_ad_contribution_usd", 0.0) < 0:
            warnings.append("estimated_cac_would_be_unprofitable")

    if reasons:
        status = "rejected"
    return {
        "passed": not reasons,
        "status": status,
        "qualification_mode": qualification_mode,
        "margin_basis": margin_basis,
        "reasons": reasons,
        "warnings": warnings,
        "economics": economics,
    }


def _is_placeholder_or_invalid_url(value: str | None) -> bool:
    text = (value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return True
    host = parsed.hostname.lower().rstrip(".")
    reserved_hosts = {"localhost", "example.com", "example.net", "example.org"}
    if host in reserved_hosts or any(host.endswith(f".{item}") for item in reserved_hosts):
        return True
    return host.endswith((".example", ".invalid", ".localhost", ".test"))


def _to_optional_bool(value: str | None) -> bool | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_pipe_values(value: str | None) -> set[str]:
    return {
        item.strip().lower()
        for item in (value or "").replace(",", "|").split("|")
        if item.strip()
    }


def _rejection_reason_counts(rejected: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rejected:
        for reason in item["reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _summarize_quality_reviews(
    candidates: list[ProductCandidate],
    launch_policy_path: str | Path | None,
    qualification_mode: str,
) -> dict[str, Any] | None:
    if launch_policy_path is None:
        return None
    rejected = [
        {
            "source_row": candidate.source_row,
            "name": candidate.name,
            "supplier": candidate.supplier,
            "status": candidate.quality_review["status"],
            "reasons": candidate.quality_review["reasons"],
        }
        for candidate in candidates
        if candidate.quality_review is not None and not candidate.quality_review["passed"]
    ]
    return {
        "mode": "launch_policy",
        "qualification_mode": qualification_mode,
        "policy_path": str(Path(launch_policy_path).resolve()),
        "eligible_count": len(candidates) - len(rejected),
        "rejected_count": len(rejected),
        "reason_counts": _rejection_reason_counts(rejected),
        "rejected": rejected,
    }


def _summarize_strategic_reviews(
    candidates: list[ProductCandidate],
    interest_profile_path: str | Path | None,
) -> dict[str, Any] | None:
    if interest_profile_path is None:
        return None
    rejected = [
        {
            "source_row": candidate.source_row,
            "name": candidate.name,
            "supplier": candidate.supplier,
            "score": candidate.strategic_review["score"],
            "thesis_id": candidate.strategic_review["thesis_id"],
            "reasons": candidate.strategic_review["reasons"],
        }
        for candidate in candidates
        if candidate.strategic_review is not None and not candidate.strategic_review["passed"]
    ]
    return {
        "mode": "operator_interest_profile",
        "profile_path": str(Path(interest_profile_path).resolve()),
        "eligible_count": len(candidates) - len(rejected),
        "rejected_count": len(rejected),
        "reason_counts": _rejection_reason_counts(rejected),
        "rejected": rejected,
    }


def _build_qualification_gate_telemetry(
    candidates: list[ProductCandidate],
    selected: list[ProductCandidate],
    qualification_mode: str,
    min_viability_score: float,
    quality_review: dict[str, Any] | None,
    strategic_review: dict[str, Any] | None,
) -> dict[str, Any]:
    live_candidates = [candidate for candidate in candidates if candidate.split == "live"]
    commercial_eligible = [
        candidate
        for candidate in live_candidates
        if candidate.viability_score >= min_viability_score
    ]
    quality_eligible = [
        candidate
        for candidate in live_candidates
        if candidate.quality_review is None or candidate.quality_review["passed"]
    ]
    strategic_eligible = [
        candidate
        for candidate in live_candidates
        if candidate.strategic_review is None or candidate.strategic_review["passed"]
    ]
    joint_eligible = [
        candidate
        for candidate in commercial_eligible
        if (candidate.quality_review is None or candidate.quality_review["passed"])
        and (candidate.strategic_review is None or candidate.strategic_review["passed"])
    ]

    rejections_by_thesis: dict[str, int] = {}
    rejections_by_supplier: dict[str, int] = {}
    for candidate in live_candidates:
        rejected = (
            candidate.viability_score < min_viability_score
            or (candidate.quality_review is not None and not candidate.quality_review["passed"])
            or (
                candidate.strategic_review is not None
                and not candidate.strategic_review["passed"]
            )
        )
        if not rejected:
            continue
        thesis_id = (
            candidate.strategic_review["thesis_id"]
            if candidate.strategic_review is not None
            else "unclassified"
        )
        rejections_by_thesis[thesis_id] = rejections_by_thesis.get(thesis_id, 0) + 1
        rejections_by_supplier[candidate.supplier] = (
            rejections_by_supplier.get(candidate.supplier, 0) + 1
        )

    return {
        "qualification_mode": qualification_mode,
        "live_input_count": len(live_candidates),
        "commercial_eligible_count": len(commercial_eligible),
        "quality_eligible_count": len(quality_eligible),
        "strategic_eligible_count": len(strategic_eligible),
        "joint_eligible_count": len(joint_eligible),
        "selected_count": len(selected),
        "quality_rejection_reason_counts": (
            quality_review.get("reason_counts", {}) if quality_review is not None else {}
        ),
        "strategic_rejection_reason_counts": (
            strategic_review.get("reason_counts", {}) if strategic_review is not None else {}
        ),
        "rejections_by_thesis": dict(sorted(rejections_by_thesis.items())),
        "rejections_by_supplier": dict(sorted(rejections_by_supplier.items())),
    }


def _to_float(value: str | None, default: float, field: str) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid value for {field}: {value!r}") from exc


def _to_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Invalid optional numeric value: {value!r}") from exc


def _normalize_percent(value: float) -> float:
    return max(0.0, min(1.0, value / 100.0))


def _score_row_primary(*, margin: float, shipping_days: float, demand: float, saturation: float) -> float:
    # Input expectations are 0-100 percentages (or equivalent).
    demand = _normalize_percent(demand)
    saturation = _normalize_percent(saturation)
    margin = _normalize_percent(margin)
    # Faster shipping is better: 0 days = best, >=14 days = worst within score.
    shipping = max(0.0, min(1.0, 1.0 - (shipping_days / 14.0)))

    return round((0.35 * margin) + (0.35 * demand) + (0.2 * shipping) + (0.1 * (1.0 - saturation)), 4)


def _score_row_secondary(*, margin: float, shipping_days: float, demand: float, saturation: float) -> float:
    demand = _normalize_percent(demand)
    saturation = _normalize_percent(saturation)
    margin = _normalize_percent(margin)
    shipping = max(0.0, min(1.0, 1.0 - (shipping_days / 18.0)))

    return round((0.25 * margin) + (0.4 * demand) + (0.25 * shipping) + (0.1 * (1.0 - saturation)), 4)


def _candidate_score_disagreement(*, score_primary: float, score_secondary: float) -> float:
    return round(abs(score_primary - score_secondary), 4)


def _blend_evaluator_scores(*, score_primary: float, score_secondary: float) -> float:
    return round((0.6 * score_primary) + (0.4 * score_secondary), 4)


def _assign_candidate_split(
    *,
    source_uri: str,
    source_row: int,
    product_name: str,
    supplier: str,
    holdout_fraction: float,
    adversarial_fraction: float,
) -> str:
    if holdout_fraction <= 0.0 and adversarial_fraction <= 0.0:
        return "live"

    digest = hashlib.sha256(
        f"{source_uri}:{source_row}:{product_name}:{supplier}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:12], 16) / float(16**12)
    if bucket < adversarial_fraction:
        return "adversarial"
    if bucket < adversarial_fraction + holdout_fraction:
        return "holdout"
    return "live"


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be between 0 and 1")
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    left = int(position)
    right = min(left + 1, len(sorted_values) - 1)
    if left == right:
        return sorted_values[left]
    weight = position - left
    return sorted_values[left] + weight * (sorted_values[right] - sorted_values[left])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workflow_run_key(
    *,
    input_hash: str,
    top_n: int,
    min_viability_score: float,
    holdout_fraction: float,
    adversarial_fraction: float,
    launch_policy_hash: str | None = None,
    interest_profile_hash: str | None = None,
    qualification_mode: str = "legacy",
) -> str:
    payload = {
        "workflow_id": WORKFLOW_ID,
        "config_version": WORKFLOW_RUN_CONFIG_VERSION,
        "input_hash": input_hash,
        "top_n": top_n,
        "min_viability_score": min_viability_score,
        "holdout_fraction": round(holdout_fraction, 6),
        "adversarial_fraction": round(adversarial_fraction, 6),
        "workflow_version": WORKFLOW_RUN_CONFIG_VERSION,
        "launch_policy_hash": launch_policy_hash,
        "interest_profile_hash": interest_profile_hash,
        "qualification_mode": qualification_mode,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _bundle_result_hash(result: dict[str, Any]) -> str:
    payload = dict(result)
    payload.pop("bundle_sha256", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", help="CSV catalog of supplier products")
    parser.add_argument("--database", default="canonical/efficiens.db", help="Canonical database path")
    parser.add_argument("--top-n", type=int, default=3, help="Max candidates to persist")
    parser.add_argument(
        "--min-viability-score",
        type=float,
        default=0.0,
        help="Minimum viability score threshold [0-1]",
    )
    parser.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.0,
        help="Deterministic holdout fraction for offline confidence checks",
    )
    parser.add_argument(
        "--adversarial-fraction",
        type=float,
        default=0.0,
        help="Deterministic adversarial test fraction for offline checks",
    )
    parser.add_argument(
        "--launch-policy",
        help="Path to a commerce-launch-policy.v1 JSON file; enables fail-closed production gates",
    )
    parser.add_argument(
        "--interest-profile",
        help=(
            "Path to a commerce-interest-profile.v1 JSON file; defaults to "
            "operator-interest-profile.json beside --launch-policy"
        ),
    )
    parser.add_argument(
        "--qualification-mode",
        choices=("research", "organic_sample", "paid_launch"),
        help="Qualification stage; defaults to organic_sample with a launch policy",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run analysis and print results without writing to canonical DB",
    )
    parser.add_argument(
        "--lane-id",
        help="Running concurrent-lane ID required for canonical writes",
    )
    parser.add_argument(
        "--lane-owner",
        help="Owner recorded on the running lane lease required for canonical writes",
    )
    parser.add_argument(
        "--lane-register",
        default="state/concurrent-lane-register.sqlite",
        help="Lane-register path used to validate write authority",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    try:
        if not arguments.dry_run:
            preflight_workflow(
                "WF-1000",
                write=True,
                lane_id=arguments.lane_id,
                lane_owner=arguments.lane_owner,
                write_targets=(arguments.database,),
                lane_register_path=Path(arguments.lane_register),
            )
        summary = run_product_research_with_telemetry(
            arguments.catalog,
            database_path=arguments.database,
            top_n=arguments.top_n,
            min_viability_score=arguments.min_viability_score,
            holdout_fraction=arguments.holdout_fraction,
            adversarial_fraction=arguments.adversarial_fraction,
            launch_policy_path=arguments.launch_policy,
            interest_profile_path=arguments.interest_profile,
            qualification_mode=arguments.qualification_mode,
            dry_run=arguments.dry_run,
        )
    except (FileNotFoundError, OSError, ValueError, KeyError, WorkflowPreflightError) as error:
        raise SystemExit(f"product-research-workflow: {error}") from error

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
