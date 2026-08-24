from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from urllib.error import URLError



def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=(),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class GraphifyArtifactMonitorTests(unittest.TestCase):
    def test_fresh_artifact_is_silent_on_stdout(self) -> None:
        from scripts import cron_graphify_artifact_monitor

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cron_graphify_artifact_monitor.main(
                Path("."),
                check=lambda _root: {"schema": "graphify-freshness.v1", "status": "fresh", "issues": []},
            )

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("GRAPHIFY ARTIFACT CURRENT", stderr.getvalue())

    def test_stale_artifact_alerts_on_stdout(self) -> None:
        from scripts import cron_graphify_artifact_monitor

        report = {
            "schema": "graphify-freshness.v1",
            "status": "stale",
            "issues": [{"code": "source_changed", "path": "scripts/example.py"}],
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cron_graphify_artifact_monitor.main(Path("."), check=lambda _root: report)

        self.assertEqual(result, 1)
        self.assertIn("GRAPHIFY ARTIFACT STALE", stdout.getvalue())
        self.assertIn("source_changed", stdout.getvalue())

    def test_checker_exception_fails_closed(self) -> None:
        from scripts import cron_graphify_artifact_monitor

        def broken(_root: Path) -> dict[str, object]:
            raise OSError("locked")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cron_graphify_artifact_monitor.main(Path("."), check=broken)

        self.assertEqual(result, 1)
        self.assertIn("GRAPHIFY ARTIFACT UNAVAILABLE", stdout.getvalue())
        self.assertNotIn("locked", stdout.getvalue())


class GraphifyMcpContractTests(unittest.TestCase):
    def _valid_config(self) -> dict[str, object]:
        from scripts.cron_graphify_mcp_contract import GRAPHIFY_PIN
        from scripts.graphify_mcp_benchmark import ALLOWED_OPERATIONS

        return {
            "command": "uvx",
            "args": [
                "--from",
                f"graphifyy[mcp]=={GRAPHIFY_PIN}",
                "graphify-mcp",
                "C:/Users/Veritas/Documents/HermesWorkspace/graphify-out/graph.json",
            ],
            "connect_timeout": 60.0,
            "tools": {"include": list(ALLOWED_OPERATIONS)},
            "enabled": True,
        }

    def _list_output(self) -> str:
        from scripts.cron_graphify_mcp_contract import EXCLUDED_TOOLS
        from scripts.graphify_mcp_benchmark import ALLOWED_OPERATIONS

        lines = ["graphify [enabled] (stdio)", f"{len(ALLOWED_OPERATIONS)} selected"]
        lines.extend(f"  {name}" for name in (*ALLOWED_OPERATIONS, *EXCLUDED_TOOLS))
        return "\n".join(lines)

    def _test_output(self) -> str:
        from scripts.cron_graphify_mcp_contract import EXCLUDED_TOOLS
        from scripts.graphify_mcp_benchmark import ALLOWED_OPERATIONS

        lines = ["Connected", f"Tools discovered: {len(ALLOWED_OPERATIONS) + len(EXCLUDED_TOOLS)}"]
        lines.extend(f"{name} schema" for name in (*ALLOWED_OPERATIONS, *EXCLUDED_TOOLS))
        return "\n".join(lines)

    def _clean_schemas(self) -> dict[str, dict[str, object]]:
        from scripts.cron_graphify_mcp_contract import ADVERTISED_TOOLS

        return {
            name: {"type": "object", "properties": {}}
            for name in ADVERTISED_TOOLS
        }

    def _stats_result(self) -> dict[str, object]:
        return {
            "isError": False,
            "content": [{"type": "text", "text": '{"node_count": 1, "edge_count": 0}'}],
        }

    def test_contract_accepts_exact_configured_boundary(self) -> None:
        from scripts import cron_graphify_mcp_contract

        issues = cron_graphify_mcp_contract.contract_issues(
            self._valid_config(),
            self._list_output(),
            self._test_output(),
        )

        self.assertEqual(issues, [])

    def test_contract_accepts_current_hermes_list_rendering(self) -> None:
        from scripts import cron_graphify_mcp_contract

        issues = cron_graphify_mcp_contract.contract_issues(
            self._valid_config(),
            "MCP Servers:\n  ✓ graphify (stdio): 7 selected",
            self._test_output(),
        )

        self.assertEqual(issues, [])

    def test_contract_rejects_unapproved_fields_and_duplicate_selected_tool(self) -> None:
        from scripts import cron_graphify_mcp_contract

        config = self._valid_config()
        config["sampling"] = {"enabled": True}
        tools = dict(config["tools"])
        tools["include"] = [*tools["include"], tools["include"][0]]
        tools["exclude"] = []
        config["tools"] = tools

        issues = cron_graphify_mcp_contract.contract_issues(
            config,
            self._list_output(),
            self._test_output(),
        )
        codes = {issue["code"] for issue in issues}

        self.assertIn("unexpected_configuration_fields", codes)
        self.assertIn("tool_configuration_drift", codes)
        self.assertIn("selected_tool_drift", codes)

    def test_project_path_in_advertised_tool_schema_is_rejected(self) -> None:
        from scripts import cron_graphify_mcp_contract

        schemas = self._clean_schemas()
        first_name = next(iter(schemas))
        schemas[first_name] = {
            "type": "object",
            "properties": {"project_path": {"type": "string"}},
        }

        issues = cron_graphify_mcp_contract.schema_contract_issues(schemas)

        self.assertEqual(issues[0]["code"], "project_path_exposed")
        self.assertEqual(issues[0]["tool"], first_name)

    def test_nested_project_path_in_schema_is_rejected(self) -> None:
        from scripts import cron_graphify_mcp_contract

        schemas = self._clean_schemas()
        first_name = next(iter(schemas))
        schemas[first_name] = {
            "type": "object",
            "properties": {},
            "allOf": [
                {"type": "object", "properties": {"project_path": {"type": "string"}}}
            ],
        }

        issues = cron_graphify_mcp_contract.schema_contract_issues(schemas)

        self.assertIn("project_path_exposed", {issue["code"] for issue in issues})

    def test_malformed_tool_schema_fails_closed(self) -> None:
        from scripts import cron_graphify_mcp_contract

        schemas = self._clean_schemas()
        first_name = next(iter(schemas))
        schemas[first_name] = {"type": "object"}

        issues = cron_graphify_mcp_contract.schema_contract_issues(schemas)

        self.assertIn("tool_schema_invalid", {issue["code"] for issue in issues})

    def test_default_watchdog_checks_raw_configured_server_schema(self) -> None:
        from scripts import cron_graphify_mcp_contract

        commands: list[tuple[str, ...]] = []

        def runner(command: tuple[str, ...], _timeout: int) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if command[:3] == ("hermes", "config", "get"):
                return completed(json.dumps(self._valid_config()))
            if command[:3] == ("hermes", "mcp", "list"):
                return completed(self._list_output())
            if command[:3] == ("hermes", "mcp", "test"):
                return completed(self._test_output())
            raise AssertionError(command)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cron_graphify_mcp_contract.main(
                runner=runner,
                configured_probe=lambda _config: (
                    set(cron_graphify_mcp_contract.ADVERTISED_TOOLS),
                    self._stats_result(),
                    self._clean_schemas(),
                ),
            )

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("GRAPHIFY MCP CONTRACT OK", stderr.getvalue())
        self.assertEqual(len(commands), 3)

    def test_candidate_graph_with_upstream_project_path_fails_closed(self) -> None:
        from scripts import cron_graphify_mcp_contract

        with TemporaryDirectory() as directory:
            graph = Path(directory) / "graph.json"
            graph.write_text("{}\n", encoding="utf-8")
            schemas = self._clean_schemas()
            for name in schemas:
                schemas[name] = {
                    "type": "object",
                    "properties": {"project_path": {"type": "string"}},
                }
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = cron_graphify_mcp_contract.main(
                    candidate_graph=graph,
                    candidate_probe=lambda _graph: (
                        set(cron_graphify_mcp_contract.ADVERTISED_TOOLS),
                        self._stats_result(),
                        schemas,
                    ),
                )

        self.assertEqual(result, 1)
        self.assertIn("project_path_exposed", stdout.getvalue())

    def test_empty_graph_stats_payload_fails_closed(self) -> None:
        from scripts import cron_graphify_mcp_contract

        issues = cron_graphify_mcp_contract._probe_issues(
            set(cron_graphify_mcp_contract.ADVERTISED_TOOLS),
            {"isError": False, "content": [{"type": "text", "text": "{}"}]},
            self._clean_schemas(),
        )

        self.assertIn("candidate_graph_stats_invalid", {issue["code"] for issue in issues})

    def test_current_graph_stats_text_payload_is_accepted(self) -> None:
        from scripts import cron_graphify_mcp_contract

        issues = cron_graphify_mcp_contract._probe_issues(
            set(cron_graphify_mcp_contract.ADVERTISED_TOOLS),
            {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": "Nodes: 1714\nEdges: 3493\nCommunities: 132\n",
                    }
                ],
            },
            self._clean_schemas(),
        )

        self.assertEqual(issues, [])


class GraphifyVersionAdvisoryTests(unittest.TestCase):
    def test_older_and_equivalent_release_are_not_newer(self) -> None:
        from scripts import cron_graphify_version_advisory

        self.assertFalse(cron_graphify_version_advisory._is_newer_version("0.9.44", "0.9.45"))
        self.assertFalse(cron_graphify_version_advisory._is_newer_version("0.9.45.0", "0.9.45"))

    def test_current_version_is_silent_on_stdout(self) -> None:
        from scripts import cron_graphify_version_advisory

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cron_graphify_version_advisory.main(
                fetch_latest=lambda: cron_graphify_version_advisory.GRAPHIFY_PIN
            )

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("GRAPHIFY VERSION CURRENT", stderr.getvalue())

    def test_newer_version_emits_advisory_without_applying_update(self) -> None:
        from scripts import cron_graphify_version_advisory

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cron_graphify_version_advisory.main(fetch_latest=lambda: "999.0.0")

        self.assertEqual(result, 0)
        payload = stdout.getvalue()
        self.assertIn("GRAPHIFY VERSION ADVISORY", payload)
        self.assertIn('"automatic_updates_applied": false', payload)

    def test_registry_failure_alerts_without_leaking_detail(self) -> None:
        from scripts import cron_graphify_version_advisory

        def fail() -> str:
            raise URLError("opaque-network-detail")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cron_graphify_version_advisory.main(fetch_latest=fail)

        self.assertEqual(result, 1)
        self.assertIn("GRAPHIFY VERSION CHECK UNAVAILABLE", stdout.getvalue())
        self.assertNotIn("opaque-network-detail", stdout.getvalue())


class GraphifyCodeRefreshHoldTests(unittest.TestCase):
    def test_stale_artifact_is_blocked_without_publication_implementation(self) -> None:
        from scripts import cron_graphify_code_refresh

        self.assertFalse(hasattr(cron_graphify_code_refresh, "PROMOTION_ARTIFACTS"))
        report = {
            "schema": "graphify-freshness.v1",
            "status": "stale",
            "issues": [{"code": "source_changed", "path": "scripts/example.py"}],
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph_dir = root / "graphify-out"
            graph_dir.mkdir()
            graph = graph_dir / "graph.json"
            graph.write_text('{"accepted": true}\n', encoding="utf-8")
            before = graph.read_bytes()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = cron_graphify_code_refresh.main(
                    root,
                    check=lambda _root: report,
                )
            after = graph.read_bytes()

        self.assertEqual(result, 1)
        self.assertEqual(before, after)
        self.assertIn("atomic_publication_not_activated", stdout.getvalue())

    def test_fresh_artifact_skips_without_writer(self) -> None:
        from scripts import cron_graphify_code_refresh

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cron_graphify_code_refresh.main(
                Path("."),
                check=lambda _root: {
                    "schema": "graphify-freshness.v1",
                    "status": "fresh",
                    "issues": [],
                },
            )

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("GRAPHIFY CODE REFRESH SKIP", stderr.getvalue())

    def test_freshness_exception_fails_closed(self) -> None:
        from scripts import cron_graphify_code_refresh

        def broken(_root: Path) -> dict[str, object]:
            raise OSError("locked")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = cron_graphify_code_refresh.main(Path("."), check=broken)

        self.assertEqual(result, 1)
        self.assertIn("freshness_check_failed", stdout.getvalue())
        self.assertNotIn("locked", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
