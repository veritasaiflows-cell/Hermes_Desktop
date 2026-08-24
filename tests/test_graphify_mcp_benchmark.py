"""Tests for the immutable Graphify MCP benchmark corpus and harness."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts import graphify_mcp_benchmark


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "graphify_mcp_cases.json"


class GraphifyMcpCorpusTests(unittest.TestCase):
    def test_load_cases_returns_the_pre_registered_eighteen_case_corpus(self) -> None:
        cases = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)

        self.assertEqual(len(cases), 18)
        self.assertEqual(
            {case.category for case in cases},
            {"topology_navigation", "symbol_relation", "boundary"},
        )
        self.assertEqual(
            {category: sum(case.category == category for case in cases)
             for category in {case.category for case in cases}},
            {
                "topology_navigation": 6,
                "symbol_relation": 6,
                "boundary": 6,
            },
        )
        self.assertEqual(len({case.case_id for case in cases}), 18)
        self.assertTrue(all(case.fallback_policy == "source_fallback" for case in cases))
        self.assertEqual({case.operation for case in cases}, graphify_mcp_benchmark.ALLOWED_OPERATIONS)


class DirectSourceRouteTests(unittest.TestCase):
    def test_direct_source_route_finds_and_verifies_pre_registered_evidence(self) -> None:
        case = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)[0]

        result = graphify_mcp_benchmark.run_direct_source_case(case, PROJECT_ROOT)

        self.assertEqual(result.route, "direct_source")
        self.assertEqual(result.case_id, case.case_id)
        self.assertTrue(result.source_verified)
        self.assertIn(case.source_path, result.matched_sources)
        self.assertIn(case.proof_pattern, result.evidence_excerpt)
        self.assertGreater(result.elapsed_ms, 0.0)


class RawTransportTests(unittest.TestCase):
    def test_default_server_command_is_version_pinned_to_the_workspace_graph(self) -> None:
        command = graphify_mcp_benchmark.default_mcp_server_command(PROJECT_ROOT)

        self.assertEqual(command[:4], ("uvx", "--from", "graphifyy[mcp]==0.9.45", "graphify-mcp"))
        self.assertEqual(command[4], str((PROJECT_ROOT / "graphify-out" / "graph.json").resolve()))

    def test_transport_runner_invokes_only_the_case_operation_and_records_output(self) -> None:
        case = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)[0]
        calls: list[tuple[str, dict[str, object]]] = []

        def invoke(operation: str, arguments: dict[str, object]) -> dict[str, object]:
            calls.append((operation, arguments))
            return {"content": [{"type": "text", "text": "graph response"}]}

        results = graphify_mcp_benchmark.run_transport_cases(
            [case],
            invoke,
            repetitions=3,
        )

        self.assertEqual(len(results), 3)
        self.assertEqual([result.route for result in results], ["raw_mcp"] * 3)
        self.assertTrue(all(result.error is None for result in results))
        self.assertTrue(all("graph response" in result.evidence_excerpt for result in results))
        self.assertEqual([operation for operation, _ in calls], [case.operation] * 3)
        self.assertTrue(all(arguments == case.mcp_arguments for _, arguments in calls))

    def test_transport_runner_records_a_protocol_reported_tool_error(self) -> None:
        case = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)[0]

        results = graphify_mcp_benchmark.run_transport_cases(
            [case],
            lambda _operation, _arguments: {
                "is_error": True,
                "content": [{"type": "text", "text": "graph unavailable"}],
            },
            repetitions=1,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].error, "MCP tool error: graph unavailable")
        self.assertIn("graph unavailable", results[0].evidence_excerpt)

    def test_cold_transport_retains_a_server_startup_failure_with_timing(self) -> None:
        case = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)[0]

        run = graphify_mcp_benchmark.run_raw_mcp_cold_cases(
            [case],
            PROJECT_ROOT,
            command=("graphify-mcp-command-that-does-not-exist",),
        )

        self.assertEqual(len(run.results), 1)
        self.assertIsNotNone(run.results[0].error)
        self.assertGreater(run.results[0].elapsed_ms, 0.0)

    def test_warm_transport_retains_a_server_startup_failure_for_every_case(self) -> None:
        case = graphify_mcp_benchmark.load_cases(CASE_PATH, PROJECT_ROOT)[0]

        run = graphify_mcp_benchmark.run_raw_mcp_warm_cases(
            [case],
            PROJECT_ROOT,
            repetitions=2,
            command=("graphify-mcp-command-that-does-not-exist",),
        )

        self.assertEqual(len(run.results), 2)
        self.assertTrue(all(result.error is not None for result in run.results))
        self.assertTrue(all(result.elapsed_ms > 0.0 for result in run.results))


if __name__ == "__main__":
    unittest.main()
