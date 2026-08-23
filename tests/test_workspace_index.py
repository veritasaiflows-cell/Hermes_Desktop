import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import scripts.workspace_index as workspace_index
from scripts.workspace_index import SourceSpec, build_index, search_index


class WorkspaceIndexTests(unittest.TestCase):
    def test_default_index_is_stored_in_the_vector_layer(self):
        self.assertEqual(
            workspace_index.DEFAULT_INDEX_PATH,
            workspace_index.WORKSPACE_ROOT / "vector" / "indexes" / "workspace-index.sqlite",
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
            index_path = Path(directory) / "workspace-index.sqlite"

            summary = build_index(
                index_path,
                [SourceSpec(root, profile="workspace-docs", authority_class="source")],
            )
            results = search_index(index_path, "exact full-text")

            self.assertEqual(summary.indexed_documents, 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].source_path, str(document.resolve()))
            self.assertEqual(results[0].citation, f"{document.resolve()}:L3-L3")
            self.assertEqual(results[0].source_family, "workspace-docs")
            self.assertEqual(results[0].authority_class, "source")
            self.assertEqual(results[0].retrieval_mode, "full_text")
            self.assertEqual(results[0].freshness_state, "fresh")
            self.assertIn("exact full-text search", results[0].excerpt)

    def test_markdown_headings_are_distinct_records_with_absolute_citations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "plan.md"
            document.write_text(
                "# Upgrade plan\n\n"
                "Use exact state for current phase.\n\n"
                "## Acceptance\n\n"
                "Run deterministic tests before advancing.\n\n"
                "## Rationale\n\n"
                "Semantic explanations preserve model-independent context.\n",
                encoding="utf-8",
            )
            index_path = root / "workspace-index.sqlite"

            summary = build_index(index_path, [SourceSpec(document)])
            results = search_index(index_path, "model-independent context")

            self.assertEqual(summary.indexed_documents, 3)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].heading, "Upgrade plan > Rationale")
            self.assertTrue(results[0].section_id)
            self.assertEqual(results[0].line_start, 9)
            self.assertEqual(results[0].line_end, 11)
            self.assertEqual(results[0].citation, f"{document.resolve()}:L11-L11")

    def test_indexing_only_uses_explicitly_approved_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            approved.mkdir()
            (approved / "included.md").write_text("approved evidence", encoding="utf-8")
            (root / "outside.md").write_text("unapproved evidence", encoding="utf-8")
            index_path = root / "workspace-index.sqlite"

            build_index(index_path, [SourceSpec(approved)])

            self.assertEqual(len(search_index(index_path, "approved evidence")), 1)
            self.assertEqual(search_index(index_path, "unapproved evidence"), [])

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
            index_path = root / "workspace-index.sqlite"

            build_index(index_path, [SourceSpec(root)])

            self.assertEqual(len(search_index(index_path, "approved evidence")), 1)
            self.assertEqual(len(search_index(index_path, "private token evidence")), 0)
            self.assertEqual(search_index(index_path, "runtime configuration evidence"), [])

            direct_index_path = root / "direct-index.sqlite"
            summary = build_index(direct_index_path, [SourceSpec(credentials / "note.md")])

            self.assertEqual(summary.indexed_documents, 0)

    def test_indexing_keeps_tmp_parent_paths_when_tmp_is_not_inside_source_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            keep = root / "keep.md"
            keep.write_text("approved tmp parent path", encoding="utf-8")
            nested_tmp = root / "tmp"
            nested_tmp.mkdir()
            (nested_tmp / "skip.md").write_text("nested tmp evidence", encoding="utf-8")
            index_path = root / "workspace-index.sqlite"

            summary = build_index(index_path, [SourceSpec(root)])

            # The test directory itself can live under an OS temp path (often .../tmp),
            # but that should not suppress indexing of approved files.
            self.assertEqual(summary.indexed_documents, 1)
            self.assertEqual(len(search_index(index_path, "approved tmp parent path")), 1)
            self.assertEqual(search_index(index_path, "nested tmp evidence"), [])

    def test_exact_search_requires_the_full_phrase_in_source_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "phrase.md").write_text("exact full-text evidence", encoding="utf-8")
            (root / "separate.md").write_text(
                "exact terms with unrelated full-text evidence", encoding="utf-8"
            )
            index_path = root / "workspace-index.sqlite"
            build_index(index_path, [SourceSpec(root)])

            results = search_index(index_path, "exact full-text", exact=True)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].source_path.endswith("phrase.md"))

    def test_rebuilding_an_approved_directory_replaces_its_prior_documents(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            document = root / "note.md"
            document.write_text("first retrieval policy", encoding="utf-8")
            index_path = Path(directory) / "workspace-index.sqlite"
            source = SourceSpec(root)
            build_index(index_path, [source])
            document.write_text("second retrieval policy", encoding="utf-8")

            build_index(index_path, [source])

            self.assertEqual(search_index(index_path, "first retrieval"), [])
            self.assertEqual(len(search_index(index_path, "second retrieval")), 1)

    def test_rebuilding_source_with_sql_like_characters_preserves_sibling_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            underscored_source = root / "source_a"
            sibling_source = root / "sourceXa"
            underscored_source.mkdir()
            sibling_source.mkdir()
            (underscored_source / "first.md").write_text("first source content", encoding="utf-8")
            sibling = sibling_source / "sibling.md"
            sibling.write_text("sibling source content", encoding="utf-8")
            index_path = root / "workspace-index.sqlite"

            build_index(index_path, [SourceSpec(underscored_source), SourceSpec(sibling_source)])
            (underscored_source / "first.md").write_text("updated source content", encoding="utf-8")
            build_index(index_path, [SourceSpec(underscored_source)])

            results = search_index(index_path, "sibling source content")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].source_path, str(sibling.resolve()))

    def test_missing_index_query_fails_without_creating_a_database(self):
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "missing.sqlite"

            with self.assertRaisesRegex(ValueError, "Workspace index is unavailable"):
                search_index(index_path, "missing")

            self.assertFalse(index_path.exists())

    def test_search_rejects_changed_source_unless_stale_results_are_allowed(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "note.md"
            document.write_text("original retrieval policy", encoding="utf-8")
            index_path = Path(directory) / "workspace-index.sqlite"
            build_index(index_path, [SourceSpec(document)])
            document.write_text("changed retrieval policy", encoding="utf-8")

            self.assertEqual(search_index(index_path, "original retrieval"), [])
            stale = search_index(index_path, "original retrieval", allow_stale=True)

            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0].freshness_state, "stale")
            self.assertIn("source hash changed", stale[0].warnings)

    def test_status_cli_exits_nonzero_for_stale_sources(self):
        with TemporaryDirectory() as directory:
            document = Path(directory) / "note.md"
            document.write_text("original retrieval policy", encoding="utf-8")
            index_path = Path(directory) / "workspace-index.sqlite"
            build_index(index_path, [SourceSpec(document)])
            document.write_text("changed retrieval policy", encoding="utf-8")

            completed = __import__("subprocess").run(
                [
                    "python",
                    "scripts/workspace_index.py",
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

    def test_cli_query_emits_machine_readable_retrieval_packet(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "approved"
            root.mkdir()
            (root / "note.md").write_text("controlled workspace lookup", encoding="utf-8")
            index_path = Path(directory) / "workspace-index.sqlite"
            build_index(index_path, [SourceSpec(root)])

            payload = json.loads(
                __import__("subprocess").check_output(
                    [
                        "python",
                        "scripts/workspace_index.py",
                        "query",
                        "--index",
                        str(index_path),
                        "--query",
                        "workspace lookup",
                    ],
                    text=True,
                    cwd=Path(__file__).resolve().parents[1],
                )
            )

            self.assertEqual(payload["retrieval_mode"], "full_text")
            self.assertEqual(payload["result_count"], 1)
            self.assertEqual(payload["results"][0]["freshness_state"], "fresh")


if __name__ == "__main__":
    unittest.main()
