#!/usr/bin/env python3
"""Generate a ranked, deduplicated opportunity shortlist from canonical data.

Reads ``product_candidate`` entities and their ``commerce.*`` metrics from the
canonical SQLite database, deduplicates on ``(name, supplier)``, filters by a
minimum viability score, and prints a ranked shortlist. By default, only rows
with a production-quality WF-1000 selection event are returned; legacy/demo
rows require the explicit ``--include-unverified`` diagnostic flag.

Usage:
    python scripts/top_opportunities_report.py canonical/efficiens.db --top-n 5
    python scripts/top_opportunities_report.py canonical/efficiens.db --as-json

Side effects: read-only. Opens the database with foreign keys enabled and never
writes canonical records.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
from pathlib import Path


PRODUCTION_QUALITY_STATUSES = {
    "sample_candidate",
    "launch_ready",
    "organic_sample_candidate",
    "organic_pilot_ready",
    "paid_launch_ready",
}


SQL_QUERY = """
WITH latest_quality_events AS (
  SELECT
    subject_id AS entity_id,
    json_extract(payload_json, '$.quality_status') AS quality_status,
    json_extract(payload_json, '$.qualification_mode') AS qualification_mode,
    CAST(json_extract(payload_json, '$.strategic_fit.score') AS REAL) AS strategic_fit_score,
    json_extract(payload_json, '$.strategic_fit.thesis_id') AS thesis_id,
    ROW_NUMBER() OVER (
      PARTITION BY subject_id
      ORDER BY COALESCE(datetime(occurred_at), '1970-01-01') DESC,
               COALESCE(datetime(recorded_at), '1970-01-01') DESC,
               rowid DESC
    ) AS quality_rank
  FROM events
  WHERE event_type = 'workflow.product_research.candidate_selected'
), entity_metrics AS (
  SELECT
    e.entity_id,
    e.name,
    e.created_at,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.viability_score' THEN m.metric_value END) AS REAL) AS viability,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.margin_percent' THEN m.metric_value END) AS REAL) AS margin_percent,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.shipping_days' THEN m.metric_value END) AS REAL) AS shipping_days,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.demand_signal' THEN m.metric_value END) AS REAL) AS demand_signal,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.saturation_signal' THEN m.metric_value END) AS REAL) AS saturation_signal,
    COALESCE(json_extract(m.dimensions_json, '$.supplier'), 'Unknown') AS supplier,
    q.quality_status,
    q.qualification_mode,
    q.strategic_fit_score,
    q.thesis_id
  FROM entities e
  LEFT JOIN metrics m
    ON e.entity_id = json_extract(m.dimensions_json, '$.entity_id')
  LEFT JOIN latest_quality_events q
    ON q.entity_id = e.entity_id AND q.quality_rank = 1
  WHERE e.entity_type = 'product_candidate'
    AND COALESCE(e.scope, 'commerce') = 'commerce'
  GROUP BY
    e.entity_id,
    e.name,
    e.created_at,
    q.quality_status,
    q.qualification_mode,
    q.strategic_fit_score,
    q.thesis_id
), deduped AS (
  SELECT
    em.*,
    ROW_NUMBER() OVER (
      PARTITION BY name, supplier
      ORDER BY COALESCE(datetime(created_at), '1970-01-01') DESC
    ) AS rn
  FROM entity_metrics em
)
SELECT
  entity_id,
  name,
  supplier,
  viability,
  margin_percent,
  shipping_days,
  demand_signal,
  saturation_signal,
  quality_status,
  qualification_mode,
  strategic_fit_score,
  thesis_id
FROM deduped
WHERE rn = 1
  AND viability >= :min_viability
  AND (
    :include_unverified = 1
    OR quality_status IN (
      'sample_candidate',
      'launch_ready',
      'organic_sample_candidate',
      'organic_pilot_ready',
      'paid_launch_ready'
    )
  )
ORDER BY
  CASE WHEN :include_unverified = 0 THEN COALESCE(strategic_fit_score, -1.0) ELSE viability END DESC,
  viability DESC,
  created_at DESC
LIMIT :top_n;
"""


def generate(
    db_path: Path,
    top_n: int,
    min_viability: float,
    *,
    include_unverified: bool = False,
) -> list[dict[str, object]]:
    """Return the top ``top_n`` deduplicated candidates at or above ``min_viability``.

    Prefers a single SQL query with JSON extraction; falls back to explicit
    Python-side JSON parsing when the SQLite build lacks JSON1 support.
    """
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON;")

        # Prefer SQL JSON extraction if available
        try:
            rows = con.execute(
                SQL_QUERY,
                {
                    "top_n": top_n,
                    "min_viability": min_viability,
                    "include_unverified": int(include_unverified),
                },
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            # Fallback: explicit JSON parsing in Python (schema-agnostic)
            raw_entities = con.execute(
                "SELECT entity_id, name, created_at, scope, status FROM entities WHERE entity_type='product_candidate'"
            ).fetchall()
            metric_rows = con.execute(
                "SELECT metric_name, metric_value, dimensions_json FROM metrics"
            ).fetchall()

            by_entity: dict[str, dict[str, float | str | None]] = {}
            for row in raw_entities:
                by_entity[row["entity_id"]] = {
                    "entity_id": row["entity_id"],
                    "name": row["name"],
                    "created_at": row["created_at"],
                    "supplier": "Unknown",
                    "viability": None,
                    "margin_percent": None,
                    "shipping_days": None,
                    "demand_signal": None,
                    "saturation_signal": None,
                    "quality_status": None,
                    "qualification_mode": None,
                    "strategic_fit_score": None,
                    "thesis_id": None,
                }

            for metric_name, metric_value, dimensions_json in metric_rows:
                if dimensions_json is None:
                    continue
                try:
                    dims = json.loads(dimensions_json)
                except (TypeError, json.JSONDecodeError):
                    continue
                entity_id = dims.get("entity_id")
                if entity_id is None or entity_id not in by_entity:
                    continue
                if metric_name in {
                    "commerce.viability_score",
                    "commerce.margin_percent",
                    "commerce.shipping_days",
                    "commerce.demand_signal",
                    "commerce.saturation_signal",
                }:
                    by_entity[entity_id][metric_name.split(".")[-1]] = metric_value
                if dims.get("supplier"):
                    by_entity[entity_id]["supplier"] = dims.get("supplier", "Unknown")

            quality_rows = con.execute(
                "SELECT rowid, subject_id, payload_json, occurred_at, recorded_at "
                "FROM events WHERE event_type = ? ORDER BY occurred_at, recorded_at, rowid",
                ("workflow.product_research.candidate_selected",),
            ).fetchall()
            for event in quality_rows:
                entity_id = event["subject_id"]
                if entity_id not in by_entity:
                    continue
                try:
                    payload = json.loads(event["payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                strategic_fit = payload.get("strategic_fit") or {}
                by_entity[entity_id]["quality_status"] = payload.get("quality_status")
                by_entity[entity_id]["qualification_mode"] = payload.get("qualification_mode")
                by_entity[entity_id]["strategic_fit_score"] = strategic_fit.get("score")
                by_entity[entity_id]["thesis_id"] = strategic_fit.get("thesis_id")

            candidates = [
                candidate
                for candidate in by_entity.values()
                if candidate["viability"] is not None and candidate["viability"] >= min_viability
                and (
                    include_unverified
                    or candidate["quality_status"] in PRODUCTION_QUALITY_STATUSES
                )
            ]

            # Dedupe on (name, supplier)
            best_by_key: dict[tuple[str, str], dict[str, float | str | None]] = {}
            for candidate in sorted(candidates, key=lambda item: item["created_at"] or ""):
                key = (candidate["name"], str(candidate["supplier"]))
                best_by_key[key] = candidate

            deduped = list(best_by_key.values())
            if include_unverified:
                deduped.sort(
                    key=lambda item: (
                        item["viability"] if item["viability"] is not None else -1,
                        item["created_at"] or "",
                    ),
                    reverse=True,
                )
            else:
                deduped.sort(
                    key=lambda item: (
                        item["strategic_fit_score"]
                        if item["strategic_fit_score"] is not None
                        else -1,
                        item["viability"] if item["viability"] is not None else -1,
                        item["created_at"] or "",
                    ),
                    reverse=True,
                )
            return deduped[:top_n]


def main() -> None:
    """Parse arguments, generate the shortlist, and print it (text or JSON)."""
    parser = argparse.ArgumentParser(
        description="Ranked, deduplicated opportunity shortlist from canonical data.",
        epilog="Read-only: never writes canonical records.",
    )
    parser.add_argument("database", help="Path to canonical SQLite database")
    parser.add_argument("--top-n", type=int, default=5, help="Number of candidates to return (default 5)")
    parser.add_argument("--min-viability", type=float, default=0.0, help="Minimum viability score filter (default 0.0)")
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="Include legacy/demo rows without production quality evidence",
    )
    parser.add_argument("--as-json", action="store_true", help="Emit JSON instead of a text table")
    arguments = parser.parse_args()

    rows = generate(
        Path(arguments.database),
        arguments.top_n,
        arguments.min_viability,
        include_unverified=arguments.include_unverified,
    )
    if arguments.as_json:
        print(json.dumps(rows, indent=2))
        return

    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx:>2d}. {row['name']} | {row['supplier']} | viability={row['viability']} | "
            f"strategic_fit={row['strategic_fit_score']} | thesis={row['thesis_id']} | "
            f"mode={row['qualification_mode']} | quality={row['quality_status']} | "
            f"margin={row['margin_percent']} | demand={row['demand_signal']} | "
            f"saturation={row['saturation_signal']} | shipping={row['shipping_days']}"
        )


if __name__ == "__main__":
    main()
