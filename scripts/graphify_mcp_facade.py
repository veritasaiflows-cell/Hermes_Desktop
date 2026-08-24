#!/usr/bin/env python3
"""Fixed-project read-only MCP facade for an immutable Graphify generation."""
from __future__ import annotations

import argparse
import asyncio
import copy
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.graphify_generation import GraphifyGenerationError, resolve_current_generation
from scripts.graphify_mcp_benchmark import ALLOWED_OPERATIONS, GRAPHIFY_MCP_VERSION


class GraphifyMcpFacadeError(ValueError):
    """Raised when a facade caller tries to cross the fixed Graphify boundary."""


ToolInvoker = Callable[[Path, str, dict[str, Any]], Awaitable[object]]

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "query_graph": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Natural-language graph question or keyword search."},
            "mode": {"type": "string", "enum": ["bfs", "dfs"], "default": "bfs"},
            "depth": {"type": "integer", "default": 3},
            "token_budget": {"type": "integer", "default": 2000},
            "context_filter": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
    "get_node": {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "Node label or ID."}},
        "required": ["label"],
        "additionalProperties": False,
    },
    "get_neighbors": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "relation_filter": {"type": "string"},
            "token_budget": {"type": "integer", "default": 2000},
        },
        "required": ["label"],
        "additionalProperties": False,
    },
    "shortest_path": {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "max_hops": {"type": "integer", "default": 8},
            "undirected": {"type": "boolean", "default": False},
        },
        "required": ["source", "target"],
        "additionalProperties": False,
    },
    "get_community": {
        "type": "object",
        "properties": {
            "community_id": {"type": "integer", "description": "Zero-indexed community ID."},
            "token_budget": {"type": "integer", "default": 2000},
        },
        "required": ["community_id"],
        "additionalProperties": False,
    },
    "god_nodes": {
        "type": "object",
        "properties": {"top_n": {"type": "integer", "default": 10}},
        "additionalProperties": False,
    },
    "graph_stats": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

_TOOL_DESCRIPTIONS = {
    "query_graph": "Search the selected immutable knowledge graph using BFS or DFS.",
    "get_node": "Get one node from the selected immutable knowledge graph.",
    "get_neighbors": "Get direct neighbors and edge details for one selected graph node.",
    "shortest_path": "Find a path between two concepts in the selected immutable graph.",
    "get_community": "Get all nodes in one selected immutable graph community.",
    "god_nodes": "Return the most connected nodes in the selected immutable graph.",
    "graph_stats": "Return counts for the selected immutable knowledge graph.",
}


def tool_schemas() -> dict[str, dict[str, Any]]:
    """Return independent closed schemas for exactly the approved read-only operations."""
    if set(_TOOL_SCHEMAS) != set(ALLOWED_OPERATIONS):
        raise GraphifyMcpFacadeError("Facade tool contract does not match approved operations")
    return copy.deepcopy(_TOOL_SCHEMAS)


def _value_matches_schema(value: object, schema: Mapping[str, Any]) -> bool:
    """Perform the small primitive JSON-schema subset needed by facade inputs."""
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        item_schema = schema.get("items")
        return isinstance(value, list) and isinstance(item_schema, Mapping) and all(
            _value_matches_schema(item, item_schema) for item in value
        )
    return False


def validate_tool_arguments(tool_name: str, arguments: object) -> dict[str, Any]:
    """Reject undeclared and ill-typed fields before any upstream process is launched."""
    schemas = tool_schemas()
    if tool_name not in schemas:
        raise GraphifyMcpFacadeError("Tool is not allowlisted")
    if not isinstance(arguments, dict):
        raise GraphifyMcpFacadeError("Tool arguments must be an object")
    schema = schemas[tool_name]
    properties = schema["properties"]
    undeclared = sorted(set(arguments) - set(properties))
    if undeclared:
        raise GraphifyMcpFacadeError(f"Tool arguments contain undeclared fields: {undeclared}")
    required = schema.get("required", [])
    missing = sorted(field for field in required if field not in arguments)
    if missing:
        raise GraphifyMcpFacadeError(f"Tool arguments are missing required fields: {missing}")
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        property_schema = properties[key]
        if not _value_matches_schema(value, property_schema):
            raise GraphifyMcpFacadeError(f"Tool argument has invalid type: {key}")
        enum = property_schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise GraphifyMcpFacadeError(f"Tool argument has unsupported value: {key}")
        normalized[key] = value
    return normalized


def _upstream_command(graph_path: Path) -> tuple[str, ...]:
    """Construct the pinned private upstream command behind the fixed facade."""
    return (
        "uvx",
        "--from",
        f"graphifyy[mcp]=={GRAPHIFY_MCP_VERSION}",
        "graphify-mcp",
        str(Path(graph_path).resolve()),
    )


async def invoke_upstream(graph_path: Path, tool_name: str, arguments: dict[str, Any]) -> object:
    """Forward one already-validated approved call to a private pinned Graphify process."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = _upstream_command(graph_path)
    parameters = StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        cwd=str(Path(graph_path).resolve().parent),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            advertised = {tool.name for tool in listed.tools}
            if tool_name not in advertised:
                raise GraphifyMcpFacadeError("Pinned upstream server omitted an approved operation")
            return await session.call_tool(tool_name, arguments)


def create_server(graph_path: Path, *, invoker: ToolInvoker = invoke_upstream):
    """Create a stdio MCP server bound to one graph path selected at process startup."""
    from mcp.server import Server
    import mcp_types as types

    fixed_graph = Path(graph_path).resolve()
    if not fixed_graph.is_file():
        raise GraphifyMcpFacadeError("Selected Graphify graph is unavailable")
    schemas = tool_schemas()

    async def on_list_tools(_context: object, _params: object) -> object:
        """Advertise only the closed fixed-project tool surface."""
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=_TOOL_DESCRIPTIONS[name],
                    inputSchema=schema,
                )
                for name, schema in sorted(schemas.items())
            ]
        )

    async def on_call_tool(_context: object, params: object) -> object:
        """Validate and proxy a read-only call without accepting path overrides."""
        name = getattr(params, "name", None)
        arguments = getattr(params, "arguments", None) or {}
        try:
            if not isinstance(name, str):
                raise GraphifyMcpFacadeError("Tool name is invalid")
            validated = validate_tool_arguments(name, arguments)
            result = await invoker(fixed_graph, name, validated)
            return result
        except GraphifyMcpFacadeError as exc:
            return types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=f"Graphify facade rejected request: {exc}")],
            )
        except Exception as exc:  # noqa: BLE001 - turn upstream transport errors into MCP evidence
            return types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=f"Graphify facade upstream failure: {type(exc).__name__}")],
            )

    return Server(
        "graphify",
        version="fixed-generation.v1",
        instructions="Read-only navigation of one immutable selected Graphify generation.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve(graph_path: Path) -> None:
    """Serve the fixed facade over stdio without re-resolving the generation pointer."""
    from mcp.server.stdio import stdio_server

    server = create_server(graph_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _candidate_graph_path(project_root: Path, candidate_graph: Path | None) -> Path:
    """Resolve either a selected live graph or an explicit candidate graph inside this workspace."""
    root = Path(project_root).resolve()
    if candidate_graph is None:
        return resolve_current_generation(root).graph_path
    graph = Path(candidate_graph).resolve()
    try:
        graph.relative_to(root)
    except ValueError as exc:
        raise GraphifyMcpFacadeError("Candidate graph is outside the workspace") from exc
    if not graph.is_file():
        raise GraphifyMcpFacadeError("Candidate graph is unavailable")
    return graph


def main(argv: list[str] | None = None) -> int:
    """Resolve one fixed graph once, then run the local stdio facade until disconnected."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--candidate-graph", type=Path)
    arguments = parser.parse_args(argv)
    try:
        graph_path = _candidate_graph_path(arguments.project_root, arguments.candidate_graph)
        asyncio.run(serve(graph_path))
    except (GraphifyGenerationError, GraphifyMcpFacadeError, OSError, RuntimeError) as exc:
        print(f"Graphify MCP facade unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
