import json
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import patch
from tempfile import TemporaryDirectory
import subprocess
import unittest

import scripts.vector_memory_index as vector_memory_index
from scripts.vector_memory_index import SourceSpec, build_index, get_index_entry, search_index, index_status


class VectorMemoryIndexTests(unittest.TestCase):
    def test_default_index_is_durable_and_query_packet_is_temporary(self):
        self.assertEqual(
            vector_memory_index.DEFAULT_INDEX_PATH,
            vector_memory_index.WORKSPACE_ROOT / "vector" / "indexes" / "vector-memory.sqlite",
        )
        self.assertEqual(
            vector_memory_index.DEFAULT_QUERY_PACKET_PATH,
            vector_memory_index.WORKSPACE_ROOT / "tmp" / "vector-memory-query.json",
        )

    def test_build_and_full_text_search_return_source_grounded_citation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            document = root / "decision.md"
            document.write_text(
                "# Retrieval decision\n\n"
                "Use exact full-text search before semantic retrieval.\n",
                encoding="utf-8",
            )
            index_path = Path(directory) / "vector-memory.sqlite"

            summary = build_index(
                index_path,
                [SourceSpec(root, profile="memory", authority_class="semantic_memory")],
            )
            results = search_index(index_path, "exact full-text")

            self.assertEqual(summary.indexed_documents, 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].source_path, str(document.resolve()))
            self.assertEqual(results[0].citation, f"{document.resolve()}:L3-L3")
            self.assertEqual(results[0].source_family, "memory")
            self.assertEqual(results[0].authority_class, "semantic_memory")
            self.assertEqual(results[0].retrieval_mode, "full_text")
            self.assertEqual(results[0].freshness_state, "fresh")
            self.assertIn("exact full-text search", results[0].excerpt)

    def test_get_index_entry_for_known_source(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "source.md"
            document.write_text("memory retrieval evidence", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"

            build_index(index_path, [SourceSpec(document)])
            record = get_index_entry(index_path, str(document))

            self.assertIsNotNone(record)
            self.assertEqual(record["source_path"], str(document.resolve()))
            self.assertEqual(record["source_family"], "explicit-local-memory")
            self.assertEqual(record["authority_class"], "semantic_memory")
            self.assertEqual(record["bytes"], len("memory retrieval evidence"))

    def test_get_index_entry_is_compatible_with_legacy_schema(self):
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "legacy-memory.sqlite"
            source = Path(directory) / "legacy.md"
            source.write_text("legacy source content", encoding="utf-8")

            source_hash = hashlib.sha256("legacy source content".encode("utf-8")).hexdigest()
            conn = sqlite3.connect(index_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE memory_memories(
                        memory_id INTEGER PRIMARY KEY,
                        source_path TEXT NOT NULL,
                        source_family TEXT NOT NULL,
                        authority_class TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        content TEXT NOT NULL,
                        indexed_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO memory_memories "
                    "(source_path, source_family, authority_class, source_type, source_hash, content, indexed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(source.resolve()),
                        "semantic_memory",
                        "legacy",
                        "file",
                        source_hash,
                        "legacy source content",
                        "2026-08-15T00:00:00Z",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            record = get_index_entry(index_path, str(source))
            self.assertIsNotNone(record)
            self.assertEqual(record["source_path"], str(source.resolve()))
            self.assertEqual(record["source_family"], "semantic_memory")
            self.assertEqual(record["source_type"], "file")
            self.assertNotIn("embedding_model", record)

    def test_indexing_only_uses_explicitly_approved_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            approved.mkdir()
            (approved / "included.md").write_text("approved evidence", encoding="utf-8")
            (root / "outside.md").write_text("unapproved evidence", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            build_index(index_path, [SourceSpec(approved)])

            self.assertEqual(len(search_index(index_path, "approved evidence")), 1)
            self.assertEqual(search_index(index_path, "unapproved evidence"), [])

    def test_oversized_document_is_chunked_and_embedded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            note = root / "long.md"
            # Create a document longer than the embedding chunk limit to exercise
            # the chunking path. The content is split across chunks but is all
            # about the same topic, so semantic search should still rank it.
            paragraph = "semantic retrieval finds notes by meaning rather than exact wording. " * 200
            note.write_text(paragraph, encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"

            def fake_embedding(text: str, **_: object) -> list[float]:
                lower = text.lower()
                # Distinct vectors for topic content vs unrelated content.
                if "semantic retrieval" in lower:
                    return [0.9, 0.1, 0.0]
                return [0.1, 0.1, 0.1]

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                summary = build_index(index_path, [SourceSpec(note)])
                self.assertEqual(summary.indexed_documents, 1)
                self.assertEqual(summary.embedded_documents, 1)
                self.assertEqual(summary.embedding_errors, 0)

                results = search_index(
                    index_path,
                    "semantic retrieval",
                    limit=1,
                    retrieval_mode="semantic",
                )
                self.assertEqual(len(results), 1)
                self.assertTrue(results[0].source_path.endswith("long.md"))
                self.assertGreater(results[0].score, 0.0)

    def test_semantic_search_ranks_heading_section_and_preserves_bounds(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "plan.md"
            note.write_text(
                "# Upgrade\n\n"
                "Use deterministic state.\n\n"
                "## Acceptance\n\n"
                "Run focused tests.\n\n"
                "## Rationale\n\n"
                "Portable handoffs let a different model resume efficiently.\n",
                encoding="utf-8",
            )
            index_path = root / "vector-memory.sqlite"

            def fake_embedding(text: str, **_: object) -> list[float]:
                lower = text.lower()
                if "rationale" in lower or "explain long work" in lower:
                    return [0.0, 1.0]
                return [1.0, 0.0]

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                summary = build_index(index_path, [SourceSpec(note)])
                results = search_index(
                    index_path,
                    "explain long work",
                    limit=1,
                    retrieval_mode="semantic",
                )

            self.assertEqual(summary.indexed_documents, 3)
            self.assertEqual(summary.embedded_documents, 3)
            self.assertEqual(results[0].heading, "Upgrade > Rationale")
            self.assertEqual(results[0].line_start, 9)
            self.assertEqual(results[0].line_end, 11)
            self.assertEqual(results[0].citation, f"{note.resolve()}:L9-L11")

    def test_indexing_excludes_sensitive_and_configuration_directories(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.md").write_text("approved evidence", encoding="utf-8")
            credentials = root / "credentials"
            credentials.mkdir()
            (credentials / "note.md").write_text("private token evidence", encoding="utf-8")
            configuration = root / "config"
            configuration.mkdir()
            (configuration / "note.md").write_text("runtime configuration evidence", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            build_index(index_path, [SourceSpec(root)])

            self.assertEqual(len(search_index(index_path, "approved evidence")), 1)
            self.assertEqual(len(search_index(index_path, "private token evidence")), 0)
            self.assertEqual(len(search_index(index_path, "runtime configuration evidence")), 0)

    def test_exact_search_requires_the_full_phrase_in_source_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "phrase.md").write_text("exact full-text evidence", encoding="utf-8")
            (root / "separate.md").write_text(
                "exact terms with unrelated full-text evidence",
                encoding="utf-8",
            )
            index_path = root / "vector-memory.sqlite"
            build_index(index_path, [SourceSpec(root)])

            results = search_index(index_path, "exact full-text", exact=True)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].source_path.endswith("phrase.md"))

    def test_rebuilding_approved_directory_replaces_its_prior_documents(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            document = root / "note.md"
            document.write_text("first retrieval policy", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"
            source = SourceSpec(root)

            build_index(index_path, [source])
            document.write_text("second retrieval policy", encoding="utf-8")

            build_index(index_path, [source])

            self.assertEqual(search_index(index_path, "first retrieval"), [])
            self.assertEqual(len(search_index(index_path, "second retrieval")), 1)

    def test_search_rejects_changed_source_unless_stale_results_are_allowed(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "note.md"
            document.write_text("original retrieval policy", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"
            build_index(index_path, [SourceSpec(document)])
            document.write_text("changed retrieval policy", encoding="utf-8")

            self.assertEqual(search_index(index_path, "original retrieval"), [])

            stale = search_index(index_path, "original retrieval", allow_stale=True)
            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0].freshness_state, "stale")
            self.assertIn("source hash changed", stale[0].warnings)

    def test_missing_index_query_fails_without_creating_a_database(self):
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "missing.sqlite"

            with self.assertRaisesRegex(ValueError, "Memory index is unavailable"):
                search_index(index_path, "missing")

            self.assertFalse(index_path.exists())

    def test_hybrid_backend_failure_is_labeled_as_full_text_fallback(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("memory routing evidence", encoding="utf-8")
            index = Path(directory) / "memory.sqlite"
            build_index(index, [SourceSpec(note)], embedding_provider="none")
            results = search_index(index, "memory routing", retrieval_mode="hybrid",
                                   embedding_provider="none")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].retrieval_mode, "full_text")
            self.assertIn("semantic unavailable: full_text fallback", results[0].warnings)

    def test_empty_fallback_packet_still_reports_degraded_mode(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("memory routing evidence", encoding="utf-8")
            index = Path(directory) / "memory.sqlite"
            build_index(index, [SourceSpec(note)], embedding_provider="none")
            packet = vector_memory_index.build_query_packet(
                index, "nonexistentzz", retrieval_mode="hybrid", embedding_provider="none",
            )
            self.assertEqual(packet["result_count"], 0)
            self.assertEqual(packet["requested_retrieval_mode"], "hybrid")
            self.assertEqual(packet["retrieval_mode"], "full_text")
            self.assertEqual(packet["status"], "degraded")
            self.assertIn("semantic unavailable: full_text fallback", packet["warnings"])

    def test_hybrid_missing_or_incompatible_embeddings_report_fallback(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("memory routing evidence", encoding="utf-8")
            index = Path(directory) / "memory.sqlite"
            build_index(index, [SourceSpec(note)], embedding_provider="none")
            with patch.object(vector_memory_index, "_build_query_embedding", return_value=[1.0, 0.0]):
                for vector, model in ((None, "nomic-embed-text:latest"), ("[1.0, 0.0]", "other-model"),
                                      ("[1.0, 0.0, 0.0]", "nomic-embed-text:latest"),
                                      ("[NaN, 1.0]", "nomic-embed-text:latest"),
                                      ("[Infinity, 1.0]", "nomic-embed-text:latest"),
                                      ("[0.0, 0.0]", "nomic-embed-text:latest")):
                    with self.subTest(vector=vector, model=model):
                        with closing(sqlite3.connect(index)) as connection:
                            connection.execute(
                                "UPDATE memory_memories SET embedding_vector_json=?, embedding_model=?, embedding_provider='ollama'",
                                (vector, model),
                            )
                            connection.commit()
                        packet = vector_memory_index.build_query_packet(index, "memory", retrieval_mode="hybrid")
                        self.assertEqual(packet["status"], "degraded")
                        self.assertEqual(packet["retrieval_mode"], "full_text")
                        with self.assertRaisesRegex(ValueError, "compatible indexed embeddings"):
                            search_index(index, "memory", retrieval_mode="semantic")

    def test_cli_query_emits_machine_readable_retrieval_packet(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            (root / "note.md").write_text("controlled memory lookup", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"
            packet_path = Path(directory) / "memory-packet.json"

            build_index(index_path, [SourceSpec(root)])

            payload = json.loads(
                subprocess.check_output(
                    [
                        "python",
                        "scripts/vector_memory_index.py",
                        "query",
                        "--index",
                        str(index_path),
                        "--query",
                        "memory lookup",
                        "--packet",
                        str(packet_path),
                    ],
                    text=True,
                    cwd=Path(__file__).resolve().parents[1],
                )
            )

            self.assertEqual(payload["retrieval_mode"], "full_text")
            self.assertEqual(payload["result_count"], 1)
            self.assertEqual(payload["results"][0]["freshness_state"], "fresh")
            self.assertEqual(payload["results"][0]["source_family"], "explicit-local-memory")
            self.assertTrue(packet_path.is_file())
            self.assertEqual(
                json.loads(packet_path.read_text(encoding="utf-8"))["result_count"],
                1,
            )

    def test_memory_search_command_uses_hybrid_adapter_and_local_embedding_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            alpha = root / "alpha.md"
            beta = root / "beta.md"
            alpha.write_text("the system stores local vectors", encoding="utf-8")
            beta.write_text("hybrid retrieval should prefer semantic similarity", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            def fake_embedding(text: str, **_: object) -> list[float]:
                lower = text.lower()
                if "alpha.md" in lower:
                    return [0.1, 0.0, 0.0]
                if "beta.md" in lower:
                    return [0.0, 0.9, 0.0]
                if "hybrid retrieval" in lower:
                    return [0.01, 0.95, 0.0]
                return [0.33, 0.33, 0.33]

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                build_index(index_path, [SourceSpec(root)])

            # force the command path while disabling embeddings so test is fully deterministic
            payload = json.loads(
                subprocess.check_output(
                    [
                        "python",
                        "scripts/vector_memory_index.py",
                        "memory_search",
                        "--index",
                        str(index_path),
                        "--query",
                        "hybrid retrieval",
                        "--disable-embedding",
                        "--packet",
                        str(root / "packet.json"),
                    ],
                    text=True,
                    cwd=Path(__file__).resolve().parents[1],
                )
            )

            # This invocation explicitly disables embeddings: it requested
            # hybrid retrieval but actually performed a lexical fallback.
            self.assertEqual(payload["requested_retrieval_mode"], "hybrid")
            self.assertEqual(payload["retrieval_mode"], "full_text")
            self.assertEqual(payload["status"], "degraded")
            self.assertEqual(payload["result_count"], 1)
            self.assertTrue(payload["results"][0]["source_path"].endswith("beta.md"))

    def test_memory_search_function_and_memory_get_adapter_contract(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "note.md"
            note.write_text("semantic recall is driven by local embeddings", encoding="utf-8")
            index_path = root / "vector-memory.sqlite"

            def fake_embedding(text: str, **_: object) -> list[float]:
                return [0.42, 0.57, 0.12]

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                build_index(index_path, [SourceSpec(note)])
                hits = vector_memory_index.memory_search("local embeddings", index=index_path, limit=1)

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].source_path, str(note.resolve()))
            self.assertEqual(hits[0].retrieval_mode, "hybrid")

            record = vector_memory_index.memory_get(index_path, str(note))
            self.assertEqual(record["source_path"], str(note.resolve()))
            self.assertEqual(record["source_family"], "explicit-local-memory")

    def test_hybrid_search_returns_stale_results_when_requested(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("fresh baseline fact", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"

            def fake_embedding(text: str, **_: object) -> list[float]:
                return [0.5, 0.5, 0.0]

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                build_index(index_path, [SourceSpec(note)])

            note.write_text("the text changed after indexing", encoding="utf-8")

            with patch.object(vector_memory_index, "_safe_embedding_for_text", side_effect=fake_embedding):
                results = search_index(
                    index_path,
                    "fresh baseline",
                    limit=1,
                    retrieval_mode="hybrid",
                    allow_stale=True,
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].freshness_state, "stale")

    def test_index_status_reports_available_and_document_count(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("semantic recall is driven by local embeddings", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"

            build_index(index_path, [SourceSpec(note)])
            summary = index_status(index_path)

            self.assertTrue(summary["available"])
            self.assertEqual(summary["document_count"], 1)
            self.assertEqual(summary["stale_source_count"], 0)
            self.assertEqual(summary["index_path"], str(index_path.resolve()))

    def test_index_status_reports_unavailable_for_missing_index(self):
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "missing.sqlite"
            summary = index_status(index_path)

            self.assertFalse(summary["available"])
            self.assertEqual(summary["document_count"], 0)
            self.assertIsNone(summary["indexed_at"])

    def test_index_status_counts_stale_sources(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("fresh baseline fact", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"

            build_index(index_path, [SourceSpec(note)])
            note.write_text("changed after indexing", encoding="utf-8")
            summary = index_status(index_path)

            self.assertEqual(summary["stale_source_count"], 1)

    def test_status_cli_exits_nonzero_for_stale_sources(self):
        with TemporaryDirectory() as directory:
            note = Path(directory) / "note.md"
            note.write_text("fresh baseline fact", encoding="utf-8")
            index_path = Path(directory) / "vector-memory.sqlite"
            build_index(index_path, [SourceSpec(note)])
            note.write_text("changed after indexing", encoding="utf-8")

            completed = subprocess.run(
                [
                    "python",
                    "scripts/vector_memory_index.py",
                    "status",
                    "--index",
                    str(index_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(json.loads(completed.stdout)["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
