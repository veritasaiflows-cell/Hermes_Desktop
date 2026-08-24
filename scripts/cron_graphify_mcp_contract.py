#!/usr/bin/env python3
"""A16: Validate Graphify MCP configuration, tools, schemas, and read contract."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.graphify_mcp_benchmark import (
    ALLOWED_OPERATIONS,
    GRAPHIFY_MCP_VERSION,
    _protocol_error,
)

GRAPHIFY_PIN = GRAPHIFY_MCP_VERSION
GRAPH_PATH = "C:/Users/Veritas/Documents/HermesWorkspace/graphify-out/graph.json"
EXPECTED_COMMAND = "uvx"
EXPECTED_ARGS = (
    "--from",
    f"graphifyy[mcp]=={GRAPHIFY_PIN}",
    "graphify-mcp",
    GRAPH_PATH,
)
ALLOWED_TOOLS = frozenset(ALLOWED_OPERATIONS)
EXCLUDED_TOOLS = frozenset({"list_prs", "get_pr_impact", "triage_prs"})
ADVERTISED_TOOLS = ALLOWED_TOOLS | EXCLUDED_TOOLS
_APPROVED_CONFIG_FIELDS = frozenset(
    {"command", "args", "connect_timeout", "tools", "enabled"}
)
_TOOL_LINE = re.compile(r"^\s*([a-z][a-z0-9_]*)\b")

CommandRunner = Callable[[tuple[str, ...], int], subprocess.CompletedProcess[str]]
ProbeResult = tuple[set[str], object, dict[str, dict[str, Any]]]
CandidateProbe = Callable[[Path], ProbeResult]
ConfiguredProbe = Callable[[Mapping[str, Any]], ProbeResult]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(command: tuple[str, ...], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _issue(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, **details}


def _tool_names(output: str) -> set[str]:
    found: set[str] = set()
    for line in output.splitlines():
        match = _TOOL_LINE.match(line)
        if match and match.group(1) in ADVERTISED_TOOLS:
            found.add(match.group(1))
    return found


def contract_issues(
    config: dict[str, Any],
    list_output: str,
    test_output: str,
) -> list[dict[str, Any]]:
    """Return deterministic violations of the approved live Hermes contract."""
    issues: list[dict[str, Any]] = []
    unexpected_fields = sorted(set(config) - _APPROVED_CONFIG_FIELDS)
    if unexpected_fields:
        issues.append(_issue("unexpected_configuration_fields", fields=unexpected_fields))
    if config.get("command") != EXPECTED_COMMAND:
        issues.append(_issue("command_drift", expected=EXPECTED_COMMAND))
    if config.get("args") != list(EXPECTED_ARGS):
        issues.append(_issue("argument_drift", expected=list(EXPECTED_ARGS)))
    if config.get("enabled") is not True:
        issues.append(_issue("server_disabled"))
    if config.get("connect_timeout") != 60.0:
        issues.append(_issue("connect_timeout_drift", expected=60.0))

    tools = config.get("tools")
    if not isinstance(tools, dict) or set(tools) != {"include"}:
        issues.append(_issue("tool_configuration_drift"))
        selected: object = None
    else:
        selected = tools.get("include")
    if (
        not isinstance(selected, list)
        or len(selected) != len(ALLOWED_TOOLS)
        or set(selected) != ALLOWED_TOOLS
    ):
        issues.append(_issue("selected_tool_drift", expected=sorted(ALLOWED_TOOLS)))

    normalized_list = list_output.lower()
    if "graphify" not in normalized_list or f"{len(ALLOWED_TOOLS)} selected" not in normalized_list:
        issues.append(_issue("mcp_list_contract_drift"))
    if "Connected" not in test_output or f"Tools discovered: {len(ADVERTISED_TOOLS)}" not in test_output:
        issues.append(_issue("mcp_test_contract_drift"))
    discovered = _tool_names(test_output)
    if discovered != ADVERTISED_TOOLS:
        issues.append(
            _issue(
                "advertised_tool_drift",
                missing=sorted(ADVERTISED_TOOLS - discovered),
                unexpected=sorted(discovered - ADVERTISED_TOOLS),
            )
        )
    return issues


def schema_contract_issues(
    schemas: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reject any tool schema that can redirect the server to another project."""
    issues: list[dict[str, Any]] = []
    for tool in sorted(ADVERTISED_TOOLS):
        schema = schemas.get(tool)
        if not isinstance(schema, Mapping):
            issues.append(_issue("tool_schema_missing", tool=tool))
            continue
        if schema.get("type") != "object":
            issues.append(_issue("tool_schema_invalid", tool=tool))
            continue
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            issues.append(_issue("tool_schema_invalid", tool=tool))
            continue
        if "project_path" in _schema_property_names(schema):
            issues.append(_issue("project_path_exposed", tool=tool))
    return issues


def _schema_property_names(value: object) -> set[str]:
    """Collect explicitly named properties through nested JSON-schema branches."""
    names: set[str] = set()
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            names.update(str(name) for name in properties)
        for child in value.values():
            names.update(_schema_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_schema_property_names(child))
    return names


def _probe_issues(
    tool_names: set[str],
    result: object,
    schemas: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if tool_names != ADVERTISED_TOOLS:
        issues.append(
            _issue(
                "candidate_tool_drift",
                missing=sorted(ADVERTISED_TOOLS - tool_names),
                unexpected=sorted(tool_names - ADVERTISED_TOOLS),
            )
        )
    error = _protocol_error(result)
    if error:
        issues.append(_issue("candidate_graph_stats_failed", error=error))
    elif not _usable_graph_stats(result):
        issues.append(_issue("candidate_graph_stats_invalid"))
    issues.extend(schema_contract_issues(schemas))
    return issues


def _usable_graph_stats(result: object) -> bool:
    """Require a non-error Graphify stats payload with at least one count field."""
    payload: object = result
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(by_alias=True)
    if not isinstance(payload, Mapping):
        return False
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        return False
    for item in content:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping) and any(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for key, value in decoded.items()
            if str(key).endswith("_count") or str(key) in {"nodes", "edges", "communities"}
        ):
            return True
        if re.search(
            r"(?im)^\s*(?:nodes|edges|communities|[a-z_]+_count)\s*:\s*\d+(?:\.\d+)?\s*$",
            text,
        ):
            return True
    return False


async def _probe_command_async(command: tuple[str, ...]) -> ProbeResult:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            schemas: dict[str, dict[str, Any]] = {}
            for tool in listed.tools:
                payload = tool.model_dump(by_alias=True)
                schema = payload.get("inputSchema") or payload.get("input_schema") or {}
                schemas[tool.name] = dict(schema) if isinstance(schema, dict) else {}
            result = await session.call_tool("graph_stats", {})
            return set(schemas), result, schemas


def _probe_command(command: tuple[str, ...]) -> ProbeResult:
    return asyncio.run(_probe_command_async(command))


def _probe_candidate_graph(graph_path: Path) -> ProbeResult:
    command = (
        "uvx",
        "--from",
        f"graphifyy[mcp]=={GRAPHIFY_PIN}",
        "graphify-mcp",
        str(graph_path.resolve()),
    )
    return _probe_command(command)


def _probe_configured_server(config: Mapping[str, Any]) -> ProbeResult:
    command = config.get("command")
    arguments = config.get("args")
    if not isinstance(command, str) or not isinstance(arguments, list) or not all(
        isinstance(value, str) for value in arguments
    ):
        raise ValueError("Configured MCP command is not executable")
    return _probe_command((command, *arguments))


def _candidate_main(graph_path: Path, probe: CandidateProbe) -> int:
    now = _utc_now()
    issues: list[dict[str, Any]] = []
    path = Path(graph_path).resolve()
    if not path.is_file():
        issues.append(_issue("candidate_graph_missing"))
    else:
        try:
            names, result, schemas = probe(path)
            issues.extend(_probe_issues(names, result, schemas))
        except Exception as exc:
            issues.append(_issue("candidate_probe_failed", error=type(exc).__name__))
    payload = {
        "schema": "graphify-mcp-candidate-contract.v1",
        "status": "blocked" if issues else "candidate_accepted",
        "issues": issues,
        "tool_count": len(ADVERTISED_TOOLS),
    }
    if issues:
        print(f"GRAPHIFY CANDIDATE MCP CONTRACT FAIL {now}")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(
    *,
    runner: CommandRunner = _run,
    configured_probe: ConfiguredProbe = _probe_configured_server,
    candidate_graph: Path | None = None,
    candidate_probe: CandidateProbe = _probe_candidate_graph,
) -> int:
    """Validate the live configured server or an explicit candidate graph."""
    if candidate_graph is not None:
        return _candidate_main(candidate_graph, candidate_probe)

    now = _utc_now()
    commands = (
        ("hermes", "config", "get", "mcp_servers.graphify", "--json"),
        ("hermes", "mcp", "list"),
        ("hermes", "mcp", "test", "graphify"),
    )
    results: list[subprocess.CompletedProcess[str]] = []
    try:
        for command in commands:
            result = runner(command, 120)
            results.append(result)
            if result.returncode != 0:
                raise RuntimeError(f"command_failed:{command[1]}")
        config = json.loads(results[0].stdout)
        if not isinstance(config, dict):
            raise ValueError("graphify config is not an object")
        issues = contract_issues(config, results[1].stdout, results[2].stdout)
        if not issues:
            names, probe_result, schemas = configured_probe(config)
            issues.extend(_probe_issues(names, probe_result, schemas))
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        print(f"GRAPHIFY MCP CONTRACT FAIL {now} error={type(exc).__name__}")
        return 1

    if issues:
        print(f"GRAPHIFY MCP CONTRACT FAIL {now}")
        print(json.dumps({"issues": issues}, indent=2, sort_keys=True))
        return 1
    print(f"GRAPHIFY MCP CONTRACT OK {now}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-graph", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(main(candidate_graph=arguments.candidate_graph))
