"""Tests for the approved retrieval source registry and dual-index refresh."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import retrieval_refresh


def _write_manifest(root: Path, payload: dict) -> Path:
    manifest = root / "vector" / "retrieval-sources.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def _seed_sources(root: Path, relatives: tuple[str, ...]) -> None:
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n\nBody for {relative}.\n", encoding="utf-8")


class RetrievalSourceRegistryTests(unittest.TestCase):
    def test_v1_manifest_still_loads_with_default_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md", "references/policy.md"))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v1",
                    "sources": ["AGENTS.md", "references/policy.md"],
                },
            )

            entries = retrieval_refresh.load_source_entries(manifest, project_root=root)

            self.assertEqual(
                [entry.relative_path for entry in entries],
                ["AGENTS.md", "references/policy.md"],
            )
            for entry in entries:
                self.assertEqual(entry.chunking, "markdown_heading")
                self.assertEqual(entry.source_type, "file")
                self.assertIsNone(entry.source_family)
                self.assertIsNone(entry.authority_class)

    def test_v1_path_view_helper_matches_entry_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v1", "sources": ["AGENTS.md"]},
            )

            self.assertEqual(
                retrieval_refresh.load_sources(manifest, project_root=root),
                [(root / "AGENTS.md").resolve()],
            )

    def test_v2_manifest_carries_per_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("GOVERNANCE.md", "state/README.md"))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [
                        {
                            "path": "GOVERNANCE.md",
                            "chunking": "markdown_heading",
                            "authority_class": "governance",
                            "source_family": "bootstrap-docs",
                        },
                        {"path": "state/README.md", "chunking": "document"},
                    ],
                },
            )

            entries = retrieval_refresh.load_source_entries(manifest, project_root=root)

            self.assertEqual(entries[0].authority_class, "governance")
            self.assertEqual(entries[0].source_family, "bootstrap-docs")
            self.assertEqual(entries[0].chunking, "markdown_heading")
            self.assertEqual(entries[1].chunking, "document")
            self.assertIsNone(entries[1].authority_class)

    def test_v2_metadata_flows_into_both_index_specs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("GOVERNANCE.md",))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [
                        {
                            "path": "GOVERNANCE.md",
                            "chunking": "document",
                            "authority_class": "governance",
                            "source_family": "bootstrap-docs",
                            "source_type": "policy",
                        }
                    ],
                },
            )
            entry = retrieval_refresh.load_source_entries(manifest, project_root=root)[0]

            workspace_spec = entry.workspace_spec()
            memory_spec = entry.memory_spec()

            self.assertEqual(workspace_spec.chunking, "document")
            self.assertEqual(workspace_spec.authority_class, "governance")
            self.assertEqual(workspace_spec.profile, "bootstrap-docs")
            self.assertEqual(memory_spec.chunking, "document")
            self.assertEqual(memory_spec.authority_class, "governance")
            self.assertEqual(memory_spec.profile, "bootstrap-docs")
            self.assertEqual(memory_spec.source_type, "policy")

    def test_defaults_differ_per_index_when_metadata_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v1", "sources": ["AGENTS.md"]},
            )
            entry = retrieval_refresh.load_source_entries(manifest, project_root=root)[0]

            self.assertEqual(entry.workspace_spec().profile, "explicit-local-source")
            self.assertEqual(entry.workspace_spec().authority_class, "source")
            self.assertEqual(entry.memory_spec().profile, "explicit-local-memory")
            self.assertEqual(entry.memory_spec().authority_class, "semantic_memory")

    def test_duplicate_sources_are_indexed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [{"path": "AGENTS.md"}, {"path": "AGENTS.md"}],
                },
            )

            entries = retrieval_refresh.load_source_entries(manifest, project_root=root)

            self.assertEqual(len(entries), 1)

    def test_unsupported_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v9", "sources": ["AGENTS.md"]},
            )

            with self.assertRaisesRegex(ValueError, "Unsupported retrieval source"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_v1_schema_rejects_metadata_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v1",
                    "sources": [{"path": "AGENTS.md"}],
                },
            )

            with self.assertRaisesRegex(ValueError, "plain relative paths"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_unsupported_chunking_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [{"path": "AGENTS.md", "chunking": "paragraph"}],
                },
            )

            with self.assertRaisesRegex(ValueError, "Unsupported chunking policy"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_v2_entry_requires_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [{"chunking": "document"}],
                },
            )

            with self.assertRaisesRegex(ValueError, "requires a path"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_v2_rejects_escaping_and_excluded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in ("tmp/query.json", "../outside.md", "secrets/token.md"):
                manifest = _write_manifest(
                    root,
                    {"schema": "retrieval-sources.v2", "sources": [{"path": source}]},
                )
                with self.assertRaisesRegex(ValueError, "approved relative source"):
                    retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_missing_source_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v2", "sources": [{"path": "AGENTS.md"}]},
            )

            with self.assertRaisesRegex(ValueError, "missing"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)

    def test_empty_source_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v2", "sources": []},
            )

            with self.assertRaisesRegex(ValueError, "non-empty sources list"):
                retrieval_refresh.load_source_entries(manifest, project_root=root)


class RetrievalRefreshTests(unittest.TestCase):
    def test_refresh_feeds_identical_source_set_to_both_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md", "references/policy.md"))
            manifest = _write_manifest(
                root,
                {
                    "schema": "retrieval-sources.v2",
                    "sources": [
                        {"path": "AGENTS.md"},
                        {"path": "references/policy.md", "chunking": "document"},
                    ],
                },
            )

            with patch.object(
                retrieval_refresh.workspace_index,
                "build_index",
                return_value=SimpleNamespace(indexed_documents=4, skipped_files=0),
            ) as workspace_build, patch.object(
                retrieval_refresh.vector_memory_index,
                "build_index",
                return_value=SimpleNamespace(
                    indexed_documents=4,
                    skipped_files=0,
                    embedded_documents=4,
                    embedding_errors=0,
                ),
            ) as vector_build:
                summary = retrieval_refresh.refresh_indexes(
                    project_root=root,
                    manifest_path=manifest,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["schema"], "retrieval-refresh.v1")
            self.assertEqual(summary["source_count"], 2)
            workspace_specs = workspace_build.call_args.args[1]
            vector_specs = vector_build.call_args.args[1]
            self.assertEqual(
                [Path(spec.path) for spec in workspace_specs],
                [Path(spec.path) for spec in vector_specs],
            )
            self.assertEqual(
                [Path(spec.path) for spec in workspace_specs],
                [
                    (root / "AGENTS.md").resolve(),
                    (root / "references" / "policy.md").resolve(),
                ],
            )
            self.assertEqual(
                [spec.chunking for spec in workspace_specs],
                ["markdown_heading", "document"],
            )
            self.assertEqual(
                [spec.chunking for spec in vector_specs],
                ["markdown_heading", "document"],
            )

    def test_refresh_writes_both_indexes_into_the_vector_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_sources(root, ("AGENTS.md",))
            manifest = _write_manifest(
                root,
                {"schema": "retrieval-sources.v2", "sources": [{"path": "AGENTS.md"}]},
            )

            with patch.object(
                retrieval_refresh.workspace_index,
                "build_index",
                return_value=SimpleNamespace(indexed_documents=2, skipped_files=0),
            ) as workspace_build, patch.object(
                retrieval_refresh.vector_memory_index,
                "build_index",
                return_value=SimpleNamespace(
                    indexed_documents=2,
                    skipped_files=0,
                    embedded_documents=2,
                    embedding_errors=0,
                ),
            ) as vector_build:
                retrieval_refresh.refresh_indexes(
                    project_root=root,
                    manifest_path=manifest,
                )

            self.assertEqual(
                Path(workspace_build.call_args.args[0]),
                root / "vector" / "indexes" / "workspace-index.sqlite",
            )
            self.assertEqual(
                Path(vector_build.call_args.args[0]),
                root / "vector" / "indexes" / "vector-memory.sqlite",
            )

    def test_workspace_manifest_is_a_valid_v2_registry(self) -> None:
        entries = retrieval_refresh.load_source_entries(
            retrieval_refresh.DEFAULT_MANIFEST,
            project_root=retrieval_refresh.PROJECT_ROOT,
        )

        self.assertGreater(len(entries), 0)
        relatives = [entry.relative_path for entry in entries]
        self.assertEqual(len(relatives), len(set(relatives)))
        self.assertIn("AGENTS.md", relatives)
        for entry in entries:
            self.assertTrue(entry.path.is_file())
            self.assertIn(entry.chunking, {"document", "markdown_heading"})


if __name__ == "__main__":
    unittest.main()
