import unittest
import unittest.mock
from tempfile import TemporaryDirectory
from pathlib import Path

import scripts.vector_memory_index as vmi
from scripts.vector_memory_index import (
    SourceSpec,
    build_index,
    clear_query_embedding_cache,
    query_embedding_cache_stats,
    search_index,
)
from scripts import vector_memory_benchmark


class VectorMemoryBenchmarkSmokeTests(unittest.TestCase):
    def test_benchmark_runs_and_reports_expected_keys(self):
        # Tiny deterministic run: exercises full-text + hybrid (mocked) paths.
        report = vector_memory_benchmark.run_benchmark(
            dataset_size=8,
            query_count=6,
            include_real_ollama=False,
        )
        for key in (
            "documents",
            "queries",
            "build_fulltext_s",
            "fulltext_query_ms_mean",
            "hybrid_query_ms_mean",
            "hybrid_query_ms_mean_warm_cache",
            "cache_cold_pass",
            "cache_warm_pass",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["documents"], 8)
        self.assertEqual(report["queries"], 6)
        # Both retrieval paths must return non-zero latency (i.e. they ran).
        self.assertGreater(report["fulltext_query_ms_mean"], 0.0)
        self.assertGreater(report["hybrid_query_ms_mean"], 0.0)

    def test_benchmark_query_vocabulary_matches_corpus(self):
        # Regression guard for the AND-join bug: the queries the harness
        # generates must actually return results against its own corpus.
        report = vector_memory_benchmark.run_benchmark(
            dataset_size=5,
            query_count=3,
            include_real_ollama=False,
        )
        # If queries returned no rows, run_benchmark would have raised.
        self.assertEqual(report["queries"], 3)


class QueryEmbeddingCacheTests(unittest.TestCase):
    def test_repeated_query_embedding_is_cached(self):
        calls = {"n": 0}

        def fake_embedding(text: str, **_: object) -> list[float]:
            calls["n"] += 1
            return [0.1, 0.2, 0.3]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.md").write_text("semantic recall via local embeddings", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            with unittest.mock.patch.object(vmi, "_safe_embedding_for_text", side_effect=fake_embedding):
                build_index(index_path, [SourceSpec(root)])
                # build_index cleared the cache; count only query-side calls.
                clear_query_embedding_cache()
                doc_calls = calls["n"]

                search_index(index_path, "local embeddings", retrieval_mode="semantic", allow_stale=True)
                after_first = calls["n"]
                search_index(index_path, "local embeddings", retrieval_mode="semantic", allow_stale=True)
                after_second = calls["n"]

        # First query embeds once (miss); second query hits the cache (no new call).
        self.assertEqual(after_first - doc_calls, 1)
        self.assertEqual(after_second, after_first)
        stats = query_embedding_cache_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["size"], 1)

    def test_build_index_clears_query_cache(self):
        def fake_embedding(text: str, **_: object) -> list[float]:
            return [0.4, 0.5, 0.6]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.md").write_text("cache reset on reindex", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            with unittest.mock.patch.object(vmi, "_safe_embedding_for_text", side_effect=fake_embedding):
                build_index(index_path, [SourceSpec(root)])
                search_index(index_path, "cache reset", retrieval_mode="semantic", allow_stale=True)
                self.assertGreaterEqual(query_embedding_cache_stats()["size"], 1)
                # Reindex must clear the query cache.
                build_index(index_path, [SourceSpec(root)])
                self.assertEqual(query_embedding_cache_stats()["size"], 0)


if __name__ == "__main__":
    unittest.main()
