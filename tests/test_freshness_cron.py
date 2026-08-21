from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import cron_canonical_integrity, retrieval_refresh


class RetrievalRefreshTests(unittest.TestCase):
    def test_manifest_refreshes_both_indexes_from_same_approved_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vector").mkdir()
            for relative in ("AGENTS.md", "references/policy.md"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {relative}\n", encoding="utf-8")
            manifest = root / "vector" / "retrieval-sources.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "retrieval-sources.v1",
                        "sources": ["AGENTS.md", "references/policy.md"],
                    }
                ),
                encoding="utf-8",
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
                summary = retrieval_refresh.refresh_indexes(
                    project_root=root,
                    manifest_path=manifest,
                )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["source_count"], 2)
            workspace_sources = workspace_build.call_args.args[1]
            vector_sources = vector_build.call_args.args[1]
            self.assertEqual(
                [Path(source.path) for source in workspace_sources],
                [root / "AGENTS.md", root / "references" / "policy.md"],
            )
            self.assertEqual(
                [Path(source.path) for source in vector_sources],
                [root / "AGENTS.md", root / "references" / "policy.md"],
            )

    def test_manifest_rejects_tmp_and_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "sources.json"
            for source in ("tmp/query.json", "../outside.md"):
                manifest.write_text(
                    json.dumps({"schema": "retrieval-sources.v1", "sources": [source]}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "approved relative source"):
                    retrieval_refresh.load_sources(manifest, project_root=root)


class CanonicalIntegrityCronTests(unittest.TestCase):
    def test_integrity_ok_returns_zero(self):
        context = unittest.mock.MagicMock()
        context.__enter__.return_value.integrity_check.return_value = "ok"
        with patch.object(cron_canonical_integrity, "CanonicalDB", return_value=context):
            self.assertEqual(cron_canonical_integrity.main(), 0)

    def test_integrity_failure_returns_one(self):
        context = unittest.mock.MagicMock()
        context.__enter__.side_effect = RuntimeError("corrupt")
        with patch.object(cron_canonical_integrity, "CanonicalDB", return_value=context):
            self.assertEqual(cron_canonical_integrity.main(), 1)


if __name__ == "__main__":
    unittest.main()
