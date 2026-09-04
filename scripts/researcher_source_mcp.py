#!/usr/bin/env python3
"""Fixed-pack, read-only MCP facade for the Researcher Bot.

The server binds to one staged source pack when the process starts. It never
accepts a workspace root, path override, command, URL, or write operation.
Stage a new pack with ``researcher_task_router.py`` and start a fresh Hermes
session to use it.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import researcher_task_router

MAX_READ_LINES = 200
MAX_SEARCH_RESULTS = 50
MAX_QUERY_CHARS = 256


class ResearcherSourceMcpError(ValueError):
    """Raised for attempts to cross the fixed read-only source-pack boundary."""


_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "task_contract": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "list_sources": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "read_source": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
        },
        "required": ["path", "line_start", "line_end"],
        "additionalProperties": False,
    },
    "search_sources": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
_TOOL_DESCRIPTIONS = {
    "task_contract": "Return the immutable task ID, allowed read-only class, output schema, and source-pack hash.",
    "list_sources": "List files in the one immutable active source pack.",
    "read_source": "Read a bounded inclusive line range from one allowlisted source-pack file.",
    "search_sources": "Search literal text only within the one immutable active source pack.",
}


def tool_schemas() -> dict[str, dict[str, Any]]:
    """Return independent closed schemas for exactly the approved read-only operations."""
    return copy.deepcopy(_TOOL_SCHEMAS)


def _value_matches_schema(value: object, schema: Mapping[str, Any]) -> bool:
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return False


def validate_tool_arguments(tool_name: str, arguments: object) -> dict[str, Any]:
    """Reject undeclared/ill-typed fields before any frozen source is consulted."""
    schema = tool_schemas().get(tool_name)
    if schema is None:
        raise ResearcherSourceMcpError("tool is not allowlisted")
    if not isinstance(arguments, dict):
        raise ResearcherSourceMcpError("tool arguments must be an object")
    properties = schema["properties"]
    undeclared = sorted(set(arguments) - set(properties))
    if undeclared:
        raise ResearcherSourceMcpError(f"tool arguments contain undeclared fields: {undeclared}")
    required = schema.get("required", [])
    missing = sorted(name for name in required if name not in arguments)
    if missing:
        raise ResearcherSourceMcpError(f"tool arguments are missing required fields: {missing}")
    normalized: dict[str, Any] = {}
    for name, value in arguments.items():
        if not _value_matches_schema(value, properties[name]):
            raise ResearcherSourceMcpError(f"tool argument has invalid type: {name}")
        normalized[name] = value
    return normalized


def _active_sources(pack: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    sources = pack.get("sources")
    if pack.get("status") != "active" or not isinstance(sources, dict):
        raise ResearcherSourceMcpError("no active source pack")
    return sources


def load_pack(active_root: Path) -> dict[str, Any]:
    """Freeze one validated staged pack into memory for this MCP process lifetime."""
    manifest = researcher_task_router.load_active_pack(active_root)
    sources_root = manifest.pop("sources_root")
    sources: dict[str, tuple[str, ...]] = {}
    for entry in manifest["source_files"]:
        path = entry["path"]
        source = (sources_root / Path(path)).read_text(encoding="utf-8")
        sources[path] = tuple(source.splitlines())
    return {"status": "active", "manifest": manifest, "sources": sources}


def load_pack_or_inactive(active_root: Path) -> dict[str, Any]:
    """Fail closed for invalid packs; expose only a status response when no pack exists."""
    try:
        return load_pack(active_root)
    except researcher_task_router.ResearcherTaskError as exc:
        if str(exc) != "no active source pack":
            raise ResearcherSourceMcpError(str(exc)) from exc
        return {"status": "no_active_pack"}


def task_contract(pack: dict[str, Any]) -> dict[str, Any]:
    """Return no source contents; just the current task boundary or inactive state."""
    if pack.get("status") != "active":
        return {"status": "no_active_pack"}
    manifest = pack["manifest"]
    return {
        "status": "active",
        "task_id": manifest["task_id"],
        "task_class": manifest["task_class"],
        "phase": manifest["phase"],
        "mode": manifest["mode"],
        "objective": manifest["objective"],
        "output_schema": manifest["output_schema"],
        "source_pack_sha256": manifest["source_pack_sha256"],
        "source_count": len(manifest["source_files"]),
    }


def list_sources(pack: dict[str, Any]) -> dict[str, Any]:
    """Return the manifest's bounded allowlist, never the parent directory."""
    _active_sources(pack)
    manifest = pack["manifest"]
    return {
        "task_id": manifest["task_id"],
        "source_pack_sha256": manifest["source_pack_sha256"],
        "sources": [
            {
                "path": entry["path"],
                "sha256": entry["sha256"],
                "line_count": entry["line_count"],
            }
            for entry in manifest["source_files"]
        ],
    }


def read_source(pack: dict[str, Any], arguments: object) -> dict[str, Any]:
    """Read a bounded line range from exactly one listed in-memory source."""
    validated = validate_tool_arguments("read_source", arguments)
    sources = _active_sources(pack)
    path = validated["path"]
    lines = sources.get(path)
    if lines is None:
        raise ResearcherSourceMcpError("source path is not allowlisted")
    start, end = validated["line_start"], validated["line_end"]
    if start < 1 or end < start or end > len(lines):
        raise ResearcherSourceMcpError("line range is invalid")
    if end - start + 1 > MAX_READ_LINES:
        raise ResearcherSourceMcpError(f"line range exceeds maximum of {MAX_READ_LINES}")
    return {
        "path": path,
        "line_start": start,
        "line_end": end,
        "text": "\n".join(lines[start - 1 : end]),
    }


def search_sources(pack: dict[str, Any], arguments: object) -> dict[str, Any]:
    """Literal, bounded search over the loaded pack; no regex or filesystem access."""
    validated = validate_tool_arguments("search_sources", arguments)
    sources = _active_sources(pack)
    query = validated["query"].strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        raise ResearcherSourceMcpError(f"query must contain 1-{MAX_QUERY_CHARS} characters")
    path_filter = validated.get("path")
    if path_filter is not None and path_filter not in sources:
        raise ResearcherSourceMcpError("source path is not allowlisted")
    max_results = validated.get("max_results", MAX_SEARCH_RESULTS)
    if max_results < 1 or max_results > MAX_SEARCH_RESULTS:
        raise ResearcherSourceMcpError(f"max_results must be 1-{MAX_SEARCH_RESULTS}")

    matches: list[dict[str, Any]] = []
    needle = query.casefold()
    for path, lines in sources.items():
        if path_filter is not None and path != path_filter:
            continue
        for number, line in enumerate(lines, start=1):
            if needle in line.casefold():
                matches.append({"path": path, "line": number, "text": line})
                if len(matches) >= max_results:
                    return {"query": query, "match_count": len(matches), "matches": matches}
    return {"query": query, "match_count": len(matches), "matches": matches}


def create_server(active_root: Path):
    """Create a local stdio MCP server bound to the pack loaded at startup."""
    from mcp.server import Server
    import mcp_types as types

    fixed_pack = load_pack_or_inactive(active_root)
    schemas = tool_schemas()

    async def on_list_tools(_context: object, _params: object) -> object:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=name, description=_TOOL_DESCRIPTIONS[name], inputSchema=schema)
                for name, schema in sorted(schemas.items())
            ]
        )

    async def on_call_tool(_context: object, params: object) -> object:
        name = getattr(params, "name", None)
        arguments = getattr(params, "arguments", None) or {}
        try:
            if not isinstance(name, str):
                raise ResearcherSourceMcpError("tool name is invalid")
            validated = validate_tool_arguments(name, arguments)
            if name == "task_contract":
                result = task_contract(fixed_pack)
            elif name == "list_sources":
                result = list_sources(fixed_pack)
            elif name == "read_source":
                result = read_source(fixed_pack, validated)
            elif name == "search_sources":
                result = search_sources(fixed_pack, validated)
            else:  # validation already rejects this; retain a future-proof guard.
                raise ResearcherSourceMcpError("tool is not allowlisted")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, sort_keys=True))]
            )
        except ResearcherSourceMcpError as exc:
            return types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=f"Researcher source proxy rejected request: {exc}")],
            )
        except Exception as exc:  # noqa: BLE001 - surface only safe failure class to the model
            return types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=f"Researcher source proxy failed: {type(exc).__name__}")],
            )

    return Server(
        "researcher-source",
        version="fixed-pack.v1",
        instructions="Read-only access to one task-class-allowlisted immutable source pack.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve(active_root: Path) -> None:
    from mcp.server.stdio import stdio_server

    server = create_server(active_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--active-root", type=Path, default=PROJECT_ROOT / "tmp" / "researcher-active"
    )
    arguments = parser.parse_args(argv)
    try:
        asyncio.run(serve(arguments.active_root))
    except (OSError, RuntimeError, ResearcherSourceMcpError) as exc:
        print(f"Researcher source MCP unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
