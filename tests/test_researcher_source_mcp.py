"""Tests for the fixed-pack, read-only Researcher MCP facade."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import researcher_source_mcp, researcher_task_router  # noqa: E402


class ResearcherSourceMcpTests(unittest.TestCase):
    def _active_pack(self, root: Path) -> Path:
        source_root = root / "source-input"
        (source_root / "docs").mkdir(parents=True)
        (source_root / "docs" / "design.md").write_text(
            "\n".join(
                [
                    "# Design",
                    "The Widget API is read-only.",
                    "Run tests with unittest.",
                    *[f"filler line {number}" for number in range(4, 251)],
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        active_root = root / "active-pack"
        researcher_task_router.stage_request(
            {
                "schema": researcher_task_router.REQUEST_SCHEMA,
                "task_id": "research-043",
                "task_class": "dependency_map",
                "phase": "pre-implementation",
                "mode": "read-only",
                "objective": "Map the Widget API dependency boundary.",
                "source_files": ["docs/design.md"],
                "output_schema": researcher_task_router.ARTIFACT_SCHEMA,
            },
            source_root,
            active_root,
        )
        return active_root

    def test_facade_exposes_only_the_four_read_only_tools(self) -> None:
        schemas = researcher_source_mcp.tool_schemas()

        self.assertEqual(
            set(schemas),
            {"task_contract", "list_sources", "read_source", "search_sources"},
        )
        for schema in schemas.values():
            self.assertFalse(schema.get("additionalProperties", True))
            self.assertNotIn("root", str(schema).lower())
            self.assertNotIn("write", str(schema).lower())

    def test_read_source_cannot_escape_or_exceed_the_fixed_pack(self) -> None:
        with TemporaryDirectory() as directory:
            pack = researcher_source_mcp.load_pack(self._active_pack(Path(directory)))
            contract = researcher_source_mcp.task_contract(pack)
            result = researcher_source_mcp.read_source(
                pack, {"path": "docs/design.md", "line_start": 2, "line_end": 2}
            )

            self.assertEqual(contract["phase"], "pre-implementation")
            self.assertEqual(result["text"], "The Widget API is read-only.")
            with self.assertRaisesRegex(researcher_source_mcp.ResearcherSourceMcpError, "allowlisted"):
                researcher_source_mcp.read_source(
                    pack, {"path": "../outside.txt", "line_start": 1, "line_end": 1}
                )
            with self.assertRaisesRegex(researcher_source_mcp.ResearcherSourceMcpError, "maximum"):
                researcher_source_mcp.read_source(
                    pack, {"path": "docs/design.md", "line_start": 1, "line_end": 250}
                )

    def test_search_is_bounded_to_frozen_sources(self) -> None:
        with TemporaryDirectory() as directory:
            pack = researcher_source_mcp.load_pack(self._active_pack(Path(directory)))
            result = researcher_source_mcp.search_sources(pack, {"query": "widget"})

        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["matches"][0]["path"], "docs/design.md")
        self.assertEqual(result["matches"][0]["line"], 2)

    def test_unknown_argument_is_rejected_before_pack_access(self) -> None:
        with self.assertRaisesRegex(researcher_source_mcp.ResearcherSourceMcpError, "undeclared"):
            researcher_source_mcp.validate_tool_arguments(
                "list_sources", {"workspace_root": "C:/Users/Veritas"}
            )

    def test_missing_pack_returns_inactive_contract_and_rejects_reads(self) -> None:
        with TemporaryDirectory() as directory:
            inactive = researcher_source_mcp.load_pack_or_inactive(Path(directory) / "absent")

            self.assertEqual(researcher_source_mcp.task_contract(inactive)["status"], "no_active_pack")
            with self.assertRaisesRegex(researcher_source_mcp.ResearcherSourceMcpError, "no active"):
                researcher_source_mcp.list_sources(inactive)


if __name__ == "__main__":
    unittest.main()
