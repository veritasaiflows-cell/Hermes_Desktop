#!/usr/bin/env python3
"""Model-routing evidence report (read-only).

Aggregates profile-local turn telemetry into a per-model routing profile so a
future smart-model-routing policy can be built on measured behaviour instead of
assumption. This script NEVER changes runtime model/provider settings and never
writes to the telemetry database; it only reads and emits a derived report.

Cost/latency comparisons between models are confounded by workload: a model that
happens to receive heavier turns will look slower. Every rate here is therefore
normalised per-request or per-1k-input-tokens where possible, and raw counts are
retained so a reader can judge sample adequacy. Rows below MIN_SAMPLES are
reported under "insufficient_evidence" rather than ranked.

Usage:
    python scripts/model_routing_telemetry.py                # human summary
    python scripts/model_routing_telemetry.py --as-json      # machine readable
    python scripts/model_routing_telemetry.py --window-days 7
    python scripts/model_routing_telemetry.py --write        # persist derived report
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = PROJECT_ROOT / "derived" / "model-routing" / "latest.json"
SCHEMA = "model-routing-telemetry.v1"

# Below this many turns a model's averages are noise, not signal.
MIN_SAMPLES = 5


def default_turn_database() -> Path:
    """Resolve the profile-local turn-metrics database without assuming a drive layout."""
    override = os.environ.get("HERMES_TURN_METRICS_DB")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes" / "telemetry" / "turn-metrics.sqlite"
    return Path.home() / ".hermes" / "telemetry" / "turn-metrics.sqlite"


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Return column names so the report degrades instead of crashing on older schemas."""
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row[1]) for row in rows}


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """Divide without inventing a value when the denominator is absent."""
    if not denominator:
        return None
    return numerator / denominator


def collect_rows(
    database_path: Path,
    *,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    """Read per-model aggregates from the turn telemetry database (read-only)."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Turn telemetry database not found: {database_path}")

    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        columns = _table_columns(connection, "turn_metrics")
        if not columns:
            raise RuntimeError("turn_metrics table is missing or unreadable")

        where = ""
        params: list[Any] = []
        if window_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            where = "WHERE started_at >= ?"
            params.append(cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))

        query = f"""
            SELECT
                model,
                provider,
                COUNT(*) AS turns,
                SUM(api_request_count) AS requests,
                SUM(api_error_count) AS api_errors,
                SUM(retry_count) AS retries,
                SUM(tool_call_count) AS tool_calls,
                SUM(tool_error_count) AS tool_errors,
                SUM(tool_round_count) AS rounds,
                AVG(duration_ms) AS avg_turn_ms,
                AVG(api_duration_ms) AS avg_api_ms,
                AVG(approx_input_tokens) AS avg_input_tokens,
                SUM(CASE WHEN outcome = 'complete' THEN 1 ELSE 0 END) AS completed
            FROM turn_metrics
            {where}
            GROUP BY model, provider
            ORDER BY turns DESC
        """
        rows = connection.execute(query, params).fetchall()
    finally:
        # Explicit close: on Windows a lingering handle blocks temp-dir cleanup,
        # and `with sqlite3.connect(...)` manages the transaction, NOT the handle.
        connection.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        turns = int(row["turns"] or 0)
        requests = int(row["requests"] or 0)
        errors = int(row["api_errors"] or 0)
        avg_input = float(row["avg_input_tokens"] or 0.0)
        avg_turn_ms = float(row["avg_turn_ms"] or 0.0)

        # Normalise latency by workload so heavier-tasked models are not unfairly ranked.
        ms_per_1k_input = _safe_ratio(avg_turn_ms, avg_input / 1000.0)

        results.append(
            {
                "model": row["model"],
                "provider": row["provider"],
                "turns": turns,
                "requests": requests,
                "api_errors": errors,
                "retries": int(row["retries"] or 0),
                "error_rate_percent": round(100.0 * errors / requests, 2) if requests else None,
                "completion_rate_percent": round(100.0 * int(row["completed"] or 0) / turns, 1)
                if turns
                else None,
                "avg_turn_seconds": round(avg_turn_ms / 1000.0, 1),
                "avg_api_seconds": round(float(row["avg_api_ms"] or 0.0) / 1000.0, 1),
                "avg_input_tokens": int(avg_input),
                "avg_tool_calls": round(_safe_ratio(float(row["tool_calls"] or 0), turns) or 0.0, 1),
                "avg_rounds": round(_safe_ratio(float(row["rounds"] or 0), turns) or 0.0, 1),
                "tool_errors": int(row["tool_errors"] or 0),
                "ms_per_1k_input_tokens": round(ms_per_1k_input, 1)
                if ms_per_1k_input is not None
                else None,
                "sufficient_evidence": turns >= MIN_SAMPLES,
            }
        )
    return results


def build_report(
    database_path: Path,
    *,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Assemble the routing-evidence report, separating ranked rows from thin samples."""
    rows = collect_rows(database_path, window_days=window_days)
    ranked = [row for row in rows if row["sufficient_evidence"]]
    thin = [row for row in rows if not row["sufficient_evidence"]]

    # Rank only where evidence is adequate; workload-normalised latency first.
    ranked_by_reliability = sorted(
        ranked,
        key=lambda row: (row["error_rate_percent"] if row["error_rate_percent"] is not None else 1e9),
    )
    ranked_by_normalised_latency = sorted(
        (row for row in ranked if row["ms_per_1k_input_tokens"] is not None),
        key=lambda row: row["ms_per_1k_input_tokens"],
    )

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_database": str(database_path),
        "window_days": window_days,
        "min_samples_for_ranking": MIN_SAMPLES,
        "model_count": len(rows),
        "models": rows,
        "most_reliable": [
            {
                "model": row["model"],
                "provider": row["provider"],
                "error_rate_percent": row["error_rate_percent"],
                "turns": row["turns"],
            }
            for row in ranked_by_reliability[:5]
        ],
        "fastest_per_1k_input": [
            {
                "model": row["model"],
                "provider": row["provider"],
                "ms_per_1k_input_tokens": row["ms_per_1k_input_tokens"],
                "turns": row["turns"],
            }
            for row in ranked_by_normalised_latency[:5]
        ],
        "insufficient_evidence": [
            {"model": row["model"], "provider": row["provider"], "turns": row["turns"]}
            for row in thin
        ],
        "caveats": [
            "Latency across models is workload-confounded; prefer ms_per_1k_input_tokens.",
            "Rows below min_samples_for_ranking are listed but never ranked.",
            "Error rate is per API request, not per turn.",
            "This report is descriptive evidence only; it applies no routing policy.",
        ],
    }


def render_text(report: dict[str, Any]) -> str:
    """Render a compact operator-readable summary."""
    lines = [
        f"MODEL ROUTING TELEMETRY {report['generated_at']}",
        f"  models={report['model_count']} window_days={report['window_days']} "
        f"min_samples={report['min_samples_for_ranking']}",
        "",
        f"  {'model':<28} {'provider':<14} {'turns':>5} {'err%':>6} {'turn_s':>8} "
        f"{'in_tok':>8} {'ms/1k':>8}",
    ]
    for row in report["models"]:
        marker = " " if row["sufficient_evidence"] else "~"
        error_rate = "n/a" if row["error_rate_percent"] is None else f"{row['error_rate_percent']:.1f}"
        normalised = (
            "n/a" if row["ms_per_1k_input_tokens"] is None else f"{row['ms_per_1k_input_tokens']:.1f}"
        )
        lines.append(
            f"{marker} {row['model']:<28} {row['provider']:<14} {row['turns']:>5} "
            f"{error_rate:>6} {row['avg_turn_seconds']:>8} {row['avg_input_tokens']:>8} "
            f"{normalised:>8}"
        )
    lines.append("")
    lines.append("  ~ = below min_samples, listed but not ranked")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="turn-metrics SQLite path (default: profile-local telemetry database)",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="restrict to the trailing N days (default: all recorded turns)",
    )
    parser.add_argument("--as-json", action="store_true", help="emit the raw JSON report")
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"persist the report to {DEFAULT_REPORT_PATH.relative_to(PROJECT_ROOT).as_posix()}",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="override the derived report destination used by --write",
    )
    return parser.parse_args()


def main() -> int:
    """Emit the routing-evidence report; exit 1 only on a real collection failure."""
    args = _parse_args()
    database_path = args.database or default_turn_database()
    try:
        report = build_report(database_path, window_days=args.window_days)
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError) as exc:
        print(f"MODEL ROUTING TELEMETRY FAIL reason={type(exc).__name__} detail={exc}")
        return 1

    if args.write:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True) if args.as_json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
