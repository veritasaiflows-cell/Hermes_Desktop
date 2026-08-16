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
from typing import Any
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WORKFLOW_ID = "product_research"
WORKFLOW_RUN_CONFIG_VERSION = "v2"

from canonical.db import CanonicalDB
from scripts.workflow_runner import WorkflowPreflightError, preflight_workflow


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

    candidates = [
        row
        for row in _iter_catalog(
            source_path,
            holdout_fraction=holdout_fraction,
            adversarial_fraction=adversarial_fraction,
        )
    ]
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

    if dry_run:
        return {
            "mode": "dry_run",
            "source_path": str(source_path.resolve()),
            "selected_count": len(selected),
            "top_n": top_n,
            "min_viability_score": min_viability_score,
            "confidence_profile": confidence_profile,
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

    input_hash = _file_hash(source_path)
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

            provenance_id = db.add_provenance(
                source_type="workflow",
                source_ref=f"workflow:product-research:{source_path}",
                source_uri=str(source_path.resolve()),
                content_hash=input_hash,
                notes="Seeded candidates from product research workflow",
                confidence=min(1.0, 0.5 + min(0.5, len(selected) / 20)),
            )

            written_rows = []
            for rank, candidate in enumerate(selected, start=1):
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
                        "entity_id": entity_id,
                        "name": candidate.name,
                        "supplier": candidate.supplier,
                        "viability_score": candidate.viability_score,
                        "score_primary": candidate.score_primary,
                        "score_secondary": candidate.score_secondary,
                        "score_disagreement": candidate.score_disagreement,
                        "split": candidate.split,
                        "rank": rank,
                    }
                )

            result = {
                "mode": "write",
                "source_path": str(source_path.resolve()),
                "selected_count": len(selected),
                "top_n": top_n,
                "min_viability_score": min_viability_score,
                "confidence_profile": confidence_profile,
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
                    "source_uri": str(source_path.resolve()),
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
                source_locator=str(source_path.resolve()),
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
        summary = run_product_research(
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
