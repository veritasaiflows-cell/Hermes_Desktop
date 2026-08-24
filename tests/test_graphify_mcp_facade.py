"""Regression tests for the fixed-project Graphify MCP facade."""
from __future__ import annotations

import unittest


class GraphifyMcpFacadeTests(unittest.TestCase):
    def test_facade_exposes_only_closed_allowlisted_schemas(self) -> None:
        from scripts import cron_graphify_mcp_contract, graphify_mcp_facade
        from scripts.graphify_mcp_benchmark import ALLOWED_OPERATIONS

        schemas = graphify_mcp_facade.tool_schemas()

        self.assertEqual(set(schemas), ALLOWED_OPERATIONS)
        self.assertEqual(cron_graphify_mcp_contract.schema_contract_issues(schemas), [])
        for schema in schemas.values():
            self.assertNotIn("project_path", str(schema))
            self.assertFalse(schema.get("additionalProperties", True))

    def test_facade_rejects_project_path_before_upstream_dispatch(self) -> None:
        from scripts import graphify_mcp_facade

        with self.assertRaisesRegex(graphify_mcp_facade.GraphifyMcpFacadeError, "undeclared"):
            graphify_mcp_facade.validate_tool_arguments(
                "graph_stats",
                {"project_path": "C:/outside"},
            )


if __name__ == "__main__":
    unittest.main()
