#!/usr/bin/env python3
"""Generate a ranked, deduplicated opportunity shortlist from canonical data."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


SQL_QUERY = """
WITH entity_metrics AS (
  SELECT
    e.entity_id,
    e.name,
    e.created_at,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.viability_score' THEN m.metric_value END) AS REAL) AS viability,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.margin_percent' THEN m.metric_value END) AS REAL) AS margin_percent,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.shipping_days' THEN m.metric_value END) AS REAL) AS shipping_days,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.demand_signal' THEN m.metric_value END) AS REAL) AS demand_signal,
    CAST(MAX(CASE WHEN m.metric_name = 'commerce.saturation_signal' THEN m.metric_value END) AS REAL) AS saturation_signal,
    COALESCE(json_extract(m.dimensions_json, '$.supplier'), 'Unknown') AS supplier
  FROM entities e
  LEFT JOIN metrics m
    ON e.entity_id = json_extract(m.dimensions_json, '$.entity_id')
  WHERE e.entity_type = 'product_candidate'
    AND COALESCE(e.scope, 'commerce') = 'commerce'
  GROUP BY
    e.entity_id,
    e.name,
    e.created_at
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
  saturation_signal
FROM deduped
WHERE rn = 1
  AND viability >= :min_viability
ORDER BY viability DESC, created_at DESC
LIMIT :top_n;
"""


def generate(db_path: Path, top_n: int, min_viability: float) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON;")

        # Prefer SQL JSON extraction if available
        try:
            rows = con.execute(SQL_QUERY, {"top_n": top_n, "min_viability": min_viability}).fetchall()
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

            candidates = [
                candidate
                for candidate in by_entity.values()
                if candidate["viability"] is not None and candidate["viability"] >= min_viability
            ]

            # Dedupe on (name, supplier)
            best_by_key: dict[tuple[str, str], dict[str, float | str | None]] = {}
            for candidate in sorted(candidates, key=lambda item: item["created_at"] or ""):
                key = (candidate["name"], str(candidate["supplier"]))
                best_by_key[key] = candidate

            deduped = list(best_by_key.values())
            deduped.sort(
                key=lambda item: (item["viability"] if item["viability"] is not None else -1, item["created_at"] or ""),
                reverse=True,
            )
            return deduped[:top_n]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", help="Path to canonical SQLite database")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--min-viability", type=float, default=0.0)
    parser.add_argument("--as-json", action="store_true")
    arguments = parser.parse_args()

    rows = generate(Path(arguments.database), arguments.top_n, arguments.min_viability)
    if arguments.as_json:
        print(json.dumps(rows, indent=2))
        return

    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx:>2d}. {row['name']} | {row['supplier']} | viability={row['viability']} | "
            f"margin={row['margin_percent']} | demand={row['demand_signal']} | "
            f"saturation={row['saturation_signal']} | shipping={row['shipping_days']}"
        )


if __name__ == "__main__":
    main()
