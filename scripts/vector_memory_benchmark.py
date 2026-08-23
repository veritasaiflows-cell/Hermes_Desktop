#!/usr/bin/env python3
"""Deterministic benchmark harness for the vector/semantic memory adapters.

Project-local companion to `scripts/vector_memory_index.py`. Measures index
build throughput and query latency for the full-text and hybrid retrieval
paths, plus query-embedding cache effectiveness. Uses a mocked embedding
function by default so numbers are stable and require no external services;
pass --real-ollama for an end-to-end pass against a running local Ollama.

Usage:
    python scripts/vector_memory_benchmark.py --dataset-size 500 --queries 250
    python scripts/vector_memory_benchmark.py --real-ollama --dataset-size 12 --queries 8
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Ensure the project root is importable when run as a script from anywhere.
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.vector_memory_index import (  # noqa: E402
    SourceSpec,
    build_index,
    clear_query_embedding_cache,
    query_embedding_cache_stats,
    search_index,
)


def _percentile(values: list[float], p: int) -> float:
    """Return the ``p``-th percentile of ``values`` (linear interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (p / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    frac = rank - lower
    return ordered[lower] * (1.0 - frac) + ordered[upper] * frac


def _mock_embedding(text: str, **_: object) -> list[float]:
    """Return a deterministic 3-dim embedding derived from character sums."""
    total = sum(ord(ch) for ch in text)
    return [
        float((total % 997) / 997.0),
        float(((total // 997) % 997) / 997.0),
        float(((total // 997 // 997) % 997) / 997.0),
    ]


def _build_dataset(root: Path, size: int) -> None:
    """Write ``size`` synthetic markdown documents into ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(size):
        text = (
            f"vector memory benchmark document {i:04d}. "
            f"topic {i % 20} with phrase seed-{i % 13}.\n"
            f"Extra line {i % 7} for stable ranking spread."
        )
        (root / f"doc_{i:04d}.md").write_text(text, encoding="utf-8")


def _run_queries(index_path: Path, queries: list[str], retrieval_mode: str, *, limit: int = 5) -> list[float]:
    """Time each query against the index and return per-query latencies."""
    timings = []
    for query in queries:
        start = time.perf_counter()
        rows = search_index(
            index_path,
            query,
            limit=limit,
            allow_stale=True,
            retrieval_mode=retrieval_mode,
        )
        if not rows:
            raise RuntimeError(f"No results for query: {query!r}")
        timings.append(time.perf_counter() - start)
    return timings


def run_benchmark(dataset_size: int, query_count: int, include_real_ollama: bool) -> dict[str, object]:
    """Build a synthetic corpus and measure full-text and hybrid query latency."""
    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        approved = workspace / "approved"
        _build_dataset(approved, dataset_size)
        # Queries MUST intersect the corpus vocabulary: non-exact FTS joins terms
        # with AND, so every term must appear in the docs. _build_dataset emits
        # "topic {i % 20}" in every document, so query on that.
        queries = [f"memory topic {i % 20}" for i in range(query_count)]
        index_path = workspace / "vector-memory.sqlite"

        t0 = time.perf_counter()
        fulltext_summary = build_index(index_path, [SourceSpec(approved)], disable_embedding=True)
        fulltext_build = time.perf_counter() - t0
        fulltext_times = _run_queries(index_path, queries, retrieval_mode="full_text")

        t1 = time.perf_counter()
        with patch("scripts.vector_memory_index._safe_embedding_for_text", side_effect=_mock_embedding):
            hybrid_summary = build_index(index_path, [SourceSpec(approved)], disable_embedding=False)
            hybrid_build = time.perf_counter() - t1

            # Cold cache: build_index() cleared the cache, so the first pass over
            # the distinct queries pays the (mocked) embedding cost on each miss.
            clear_query_embedding_cache()
            hybrid_times_cold = _run_queries(index_path, queries, retrieval_mode="hybrid")
            cold_stats = query_embedding_cache_stats()

            # Warm cache: repeat the same queries; embeddings now come from cache.
            hybrid_times_warm = _run_queries(index_path, queries, retrieval_mode="hybrid")
            warm_stats = query_embedding_cache_stats()

        report = {
            "documents": dataset_size,
            "queries": query_count,
            "build_fulltext_s": round(fulltext_build, 4),
            "build_fulltext_docs_per_s": round(fulltext_summary.indexed_documents / max(fulltext_build, 1e-9), 2),
            "fulltext_query_ms_mean": round(statistics.mean(fulltext_times) * 1000, 3),
            "fulltext_query_ms_p50": round(_percentile(fulltext_times, 50) * 1000, 3),
            "fulltext_query_ms_p95": round(_percentile(fulltext_times, 95) * 1000, 3),
            "hybrid_build_s": round(hybrid_build, 4),
            "hybrid_build_docs_per_s": round(hybrid_summary.indexed_documents / max(hybrid_build, 1e-9), 2),
            "hybrid_query_ms_mean": round(statistics.mean(hybrid_times_cold) * 1000, 3),
            "hybrid_query_ms_p50": round(_percentile(hybrid_times_cold, 50) * 1000, 3),
            "hybrid_query_ms_p95": round(_percentile(hybrid_times_cold, 95) * 1000, 3),
            "hybrid_query_ms_mean_warm_cache": round(statistics.mean(hybrid_times_warm) * 1000, 3),
            "hybrid_query_ms_p95_warm_cache": round(_percentile(hybrid_times_warm, 95) * 1000, 3),
            "cache_cold_pass": cold_stats,
            "cache_warm_pass": warm_stats,
            "build_hybrid_embedded_docs": hybrid_summary.embedded_documents,
        }

        if include_real_ollama:
            try:
                t2 = time.perf_counter()
                real_summary = build_index(index_path, [SourceSpec(approved)], disable_embedding=False)
                real_build = time.perf_counter() - t2
                sample_queries = queries[: max(1, len(queries) // 2)]
                clear_query_embedding_cache()
                real_times = _run_queries(index_path, sample_queries, retrieval_mode="hybrid")
                real_times_warm = _run_queries(index_path, sample_queries, retrieval_mode="hybrid")
                report["real_ollama"] = {
                    "enabled": True,
                    "build_s": round(real_build, 4),
                    "embedded_documents": real_summary.embedded_documents,
                    "query_count": len(sample_queries),
                    "query_ms_mean": round(statistics.mean(real_times) * 1000, 3),
                    "query_ms_p95": round(_percentile(real_times, 95) * 1000, 3),
                    "query_ms_mean_warm_cache": round(statistics.mean(real_times_warm) * 1000, 3),
                }
            except Exception as exc:  # noqa: BLE001 - surface the failure in the report
                report["real_ollama"] = {"enabled": False, "error": str(exc)}

        return report


def main() -> None:
    """Parse arguments, run the benchmark, and print (and optionally write) the report."""
    parser = argparse.ArgumentParser(description="Benchmark vector/semantic memory search adapters.")
    parser.add_argument("--dataset-size", type=int, default=500)
    parser.add_argument("--queries", type=int, default=250)
    parser.add_argument("--real-ollama", action="store_true", help="also run a real local Ollama pass")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report to this path")
    args = parser.parse_args()

    payload = run_benchmark(args.dataset_size, args.queries, args.real_ollama)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
