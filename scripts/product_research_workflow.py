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
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WORKFLOW_ID = "product_research"
WORKFLOW_RUN_CONFIG_VERSION = "v2"

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


def run_product_research(
    catalog_path: str | Path,
    *,
    database_path: str | Path = "canonical/efficiens.db",
    top_n: int = 5,
    min_viability_score: float = 0.0,
    holdout_fraction: float = 0.0,
    adversarial_fraction: float = 0.0,
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

    candidates_raw = [
        row
        for row in _iter_catalog(
            source_path,
            holdout_fraction=holdout_fraction,
            adversarial_fraction=adversarial_fraction,
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
        candidate for candidate in live_candidates if candidate.viability_score >= min_viability_score
    ]
    eligible.sort(key=lambda candidate: candidate.viability_score, reverse=True)
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
        )

        return {
            "mode": "dry_run",
            "source_path": source_ref,
            "selected_count": len(selected),
            "top_n": top_n,
            "min_viability_score": min_viability_score,
            "confidence_profile": confidence_profile,
            "telemetry": {
                "business_key_dedupe": business_key_dedupe,
            },
            "source_preflight": source_preflight,
            "selected": [
                {
                    "source_row": candidate.source_row,
                    "name": candidate.name,
                    "supplier": candidate.supplier,
                    "split": candidate.split,
                    "viability_score": candidate.viability_score,
                    "score_primary": candidate.score_primary,
                    "score_secondary": candidate.score_secondary,
                    "score_disagreement": candidate.score_disagreement,
                }
                for candidate in selected
            ],
        }

    run_key = _workflow_run_key(
        input_hash=input_hash,
        top_n=top_n,
        min_viability_score=min_viability_score,
        holdout_fraction=holdout_fraction,
        adversarial_fraction=adversarial_fraction,
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
                    written_rows.append(
                        {
                            "action": "reused",
                            "entity_id": existing_candidate["entity_id"],
                            "name": candidate.name,
                            "supplier": candidate.supplier,
                            "viability_score": candidate.viability_score,
                            "score_primary": candidate.score_primary,
                            "score_secondary": candidate.score_secondary,
                            "score_disagreement": candidate.score_disagreement,
                            "split": candidate.split,
                            "rank": rank,
                            "business_key": candidate.business_key,
                        }
                    )
                    continue

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
                                },
                                sort_keys=True,
                            ),
                            "provenance_id": provenance_id,
                            "measured_at": _utc_now(),
                        },
                    )

                event_payload = {
                    "workflow": "product_research",
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
                        "action": "inserted",
                        "entity_id": entity_id,
                        "name": candidate.name,
                        "supplier": candidate.supplier,
                        "viability_score": candidate.viability_score,
                        "score_primary": candidate.score_primary,
                        "score_secondary": candidate.score_secondary,
                        "score_disagreement": candidate.score_disagreement,
                        "split": candidate.split,
                        "rank": rank,
                        "business_key": candidate.business_key,
                    }
                )

            result = {
                "mode": "write",
                "source_path": source_ref,
                "selected_count": len(selected),
                "top_n": top_n,
                "min_viability_score": min_viability_score,
                "confidence_profile": confidence_profile,
                "telemetry": {
                    "business_key_dedupe": business_key_dedupe,
                },
                "source_preflight": source_preflight,
                "database_path": database_resolved_path,
                "written": written_rows,
            }
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
    top_n: int = 5,
    min_viability_score: float = 0.0,
    holdout_fraction: float = 0.0,
    adversarial_fraction: float = 0.0,
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


def _require_source_preflight(*, db: CanonicalDB, source_ref: str, source_hash: str) -> dict[str, Any]:
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
) -> list[ProductCandidate]:
    if not path.exists():
        raise FileNotFoundError(f"Catalog path does not exist: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"product_name", "supplier_name", "margin_percent", "shipping_days", "demand_signal", "saturation_signal"}
        missing = required.difference(reader.fieldnames or set())
        if missing:
            raise ValueError(f"Catalog is missing required columns: {', '.join(sorted(missing))}")

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
            )


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
    parser.add_argument("--top-n", type=int, default=5, help="Max candidates to persist")
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
            dry_run=arguments.dry_run,
        )
    except (FileNotFoundError, OSError, ValueError, KeyError, WorkflowPreflightError) as error:
        raise SystemExit(f"product-research-workflow: {error}") from error

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
