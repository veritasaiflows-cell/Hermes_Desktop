#!/usr/bin/env python3
"""Deterministic corpus and transport primitives for Graphify MCP evaluation."""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


CASE_SCHEMA = "graphify-mcp-cases.v1"
BENCHMARK_SCHEMA = "graphify-mcp-benchmark.v1"
GRAPHIFY_MCP_VERSION = "0.9.45"
ALLOWED_OPERATIONS = frozenset(
    {
        "query_graph",
        "get_node",
        "get_neighbors",
        "get_community",
        "god_nodes",
        "graph_stats",
        "shortest_path",
    }
)
ALLOWED_CATEGORIES = frozenset({"topology_navigation", "symbol_relation", "boundary"})
ALLOWED_EXPECTED_BEHAVIORS = frozenset({"locator", "relationship", "aggregate", "no_path"})
_REQUIRED_CASE_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "question",
        "operation",
        "mcp_arguments",
        "expected_graph_behavior",
        "source_path",
        "source_line_hint",
        "proof_pattern",
        "search_terms",
        "fallback_policy",
    }
)


@dataclass(frozen=True)
class BenchmarkCase:
    """One immutable, source-verified Graphify benchmark case."""

    case_id: str
    category: str
    question: str
    operation: str
    mcp_arguments: dict[str, Any]
    expected_graph_behavior: str
    source_path: str
    source_line_hint: int
    proof_pattern: str
    search_terms: tuple[str, ...]
    fallback_policy: str


@dataclass(frozen=True)
class RouteResult:
    """A deterministic route result with source-verification evidence."""

    route: str
    case_id: str
    elapsed_ms: float
    source_verified: bool
    matched_sources: tuple[str, ...]
    evidence_excerpt: str
    error: str | None = None
    startup_ms: float | None = None


@dataclass(frozen=True)
class RawMcpRun:
    """Cold or warm raw-MCP transport measurements for one immutable corpus."""

    results: tuple[RouteResult, ...]
    advertised_tools: tuple[str, ...]
    session_startup_ms: float | None


def _safe_source_path(project_root: Path, source_path: str) -> Path:
    """Resolve a benchmark source path and reject paths outside the workspace."""
    normalized = source_path.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Benchmark source path must stay within the workspace: {source_path}")
    candidate = (project_root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Benchmark source path must stay within the workspace: {source_path}") from exc
    return candidate


def default_mcp_server_command(project_root: Path) -> tuple[str, ...]:
    """Return the isolated, version-pinned local stdio command for this graph."""
    graph_path = (Path(project_root).resolve() / "graphify-out" / "graph.json").resolve()
    return (
        "uvx",
        "--from",
        f"graphifyy[mcp]=={GRAPHIFY_MCP_VERSION}",
        "graphify-mcp",
        str(graph_path),
    )


def _require_string(value: object, field: str, case_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Case {case_id!r} field {field!r} must be a non-empty string")
    return value


def _parse_case(payload: object, project_root: Path) -> BenchmarkCase:
    """Validate one case against the immutable corpus contract."""
    if not isinstance(payload, dict):
        raise ValueError("Each Graphify benchmark case must be an object")
    case_id = _require_string(payload.get("case_id"), "case_id", "<unknown>")
    missing = sorted(_REQUIRED_CASE_FIELDS - set(payload))
    extra = sorted(set(payload) - _REQUIRED_CASE_FIELDS)
    if missing or extra:
        raise ValueError(f"Case {case_id!r} has invalid fields: missing={missing}, extra={extra}")

    category = _require_string(payload["category"], "category", case_id)
    if category not in ALLOWED_CATEGORIES:
        raise ValueError(f"Case {case_id!r} has unsupported category: {category}")
    operation = _require_string(payload["operation"], "operation", case_id)
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(f"Case {case_id!r} has unsupported MCP operation: {operation}")
    expected_graph_behavior = _require_string(
        payload["expected_graph_behavior"], "expected_graph_behavior", case_id
    )
    if expected_graph_behavior not in ALLOWED_EXPECTED_BEHAVIORS:
        raise ValueError(
            f"Case {case_id!r} has unsupported graph behavior: {expected_graph_behavior}"
        )
    mcp_arguments = payload["mcp_arguments"]
    if not isinstance(mcp_arguments, dict):
        raise ValueError(f"Case {case_id!r} mcp_arguments must be an object")
    source_line_hint = payload["source_line_hint"]
    if type(source_line_hint) is not int or source_line_hint < 1:
        raise ValueError(f"Case {case_id!r} source_line_hint must be a positive integer")
    search_terms = payload["search_terms"]
    if (
        not isinstance(search_terms, list)
        or not search_terms
        or not all(isinstance(term, str) and term for term in search_terms)
    ):
        raise ValueError(f"Case {case_id!r} search_terms must be a non-empty array of strings")

    source_path = _require_string(payload["source_path"], "source_path", case_id)
    source_file = _safe_source_path(project_root, source_path)
    if not source_file.is_file():
        raise ValueError(f"Case {case_id!r} source file is missing: {source_path}")
    proof_pattern = _require_string(payload["proof_pattern"], "proof_pattern", case_id)
    if proof_pattern not in source_file.read_text(encoding="utf-8"):
        raise ValueError(f"Case {case_id!r} proof pattern is absent from {source_path}")
    fallback_policy = _require_string(payload["fallback_policy"], "fallback_policy", case_id)
    if fallback_policy != "source_fallback":
        raise ValueError(f"Case {case_id!r} must require source_fallback")

    return BenchmarkCase(
        case_id=case_id,
        category=category,
        question=_require_string(payload["question"], "question", case_id),
        operation=operation,
        mcp_arguments=mcp_arguments,
        expected_graph_behavior=expected_graph_behavior,
        source_path=source_path,
        source_line_hint=source_line_hint,
        proof_pattern=proof_pattern,
        search_terms=tuple(search_terms),
        fallback_policy=fallback_policy,
    )


def load_cases(case_path: Path, project_root: Path) -> tuple[BenchmarkCase, ...]:
    """Load and validate the immutable benchmark corpus before any route is run."""
    project_root = Path(project_root).resolve()
    payload = json.loads(Path(case_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != CASE_SCHEMA:
        raise ValueError(f"Benchmark corpus must use schema {CASE_SCHEMA}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("Benchmark corpus cases must be an array")
    cases = tuple(_parse_case(case, project_root) for case in raw_cases)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Benchmark corpus case_id values must be unique")
    return cases


def _direct_source_files(project_root: Path) -> tuple[Path, ...]:
    """Return the governed Python corpus for the direct-source baseline."""
    files: list[Path] = []
    for directory in ("canonical", "scripts", "tests"):
        source_root = project_root / directory
        if not source_root.is_dir():
            continue
        files.extend(path for path in source_root.rglob("*.py") if path.is_file())
    return tuple(sorted(files))


def run_direct_source_case(case: BenchmarkCase, project_root: Path) -> RouteResult:
    """Search governed sources and verify the case's pre-registered proof."""
    root = Path(project_root).resolve()
    start_ns = time.perf_counter_ns()
    lowered_terms = tuple(term.lower() for term in case.search_terms)
    matched_sources: list[str] = []
    for source_file in _direct_source_files(root):
        content = source_file.read_text(encoding="utf-8", errors="replace")
        if all(term in content.lower() for term in lowered_terms):
            matched_sources.append(source_file.relative_to(root).as_posix())
    proof_file = _safe_source_path(root, case.source_path)
    proof_content = proof_file.read_text(encoding="utf-8", errors="replace")
    source_verified = case.source_path in matched_sources and case.proof_pattern in proof_content
    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
    return RouteResult(
        route="direct_source",
        case_id=case.case_id,
        elapsed_ms=elapsed_ms,
        source_verified=source_verified,
        matched_sources=tuple(matched_sources),
        evidence_excerpt=case.proof_pattern if source_verified else "",
    )


def _transport_payload(value: object) -> object:
    """Convert an MCP SDK result to a JSON-compatible payload when possible."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _serialize_transport_output(value: object) -> str:
    """Return MCP output in a stable, reviewable representation."""
    value = _transport_payload(value)
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


def _protocol_error(value: object) -> str | None:
    """Translate an MCP protocol-level tool error into benchmark evidence."""
    payload = _transport_payload(value)
    if not isinstance(payload, dict) or not (payload.get("is_error") or payload.get("isError")):
        return None
    content = payload.get("content")
    messages = [
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ] if isinstance(content, list) else []
    detail = "; ".join(messages) if messages else "server returned an unspecified tool error"
    return f"MCP tool error: {detail}"


def run_transport_cases(
    cases: list[BenchmarkCase] | tuple[BenchmarkCase, ...],
    invoke: Callable[[str, dict[str, Any]], object],
    *,
    repetitions: int,
) -> tuple[RouteResult, ...]:
    """Invoke only declared MCP operations and retain every result or failure."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    results: list[RouteResult] = []
    for _ in range(repetitions):
        for case in cases:
            start_ns = time.perf_counter_ns()
            try:
                output = invoke(case.operation, dict(case.mcp_arguments))
            except Exception as exc:  # noqa: BLE001 - retained as benchmark evidence
                results.append(
                    RouteResult(
                        route="raw_mcp",
                        case_id=case.case_id,
                        elapsed_ms=(time.perf_counter_ns() - start_ns) / 1_000_000,
                        source_verified=False,
                        matched_sources=(),
                        evidence_excerpt="",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            results.append(
                RouteResult(
                    route="raw_mcp",
                    case_id=case.case_id,
                    elapsed_ms=(time.perf_counter_ns() - start_ns) / 1_000_000,
                    source_verified=False,
                    matched_sources=(),
                    evidence_excerpt=_serialize_transport_output(output),
                    error=_protocol_error(output),
                )
            )
    return tuple(results)


def _stdio_environment() -> dict[str, str]:
    """Pass only the local process settings needed by an isolated stdio server."""
    allowed = (
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TERM",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "LOCALAPPDATA",
        "APPDATA",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


async def _raw_mcp_call(
    command: tuple[str, ...],
    project_root: Path,
    operation: str,
    arguments: dict[str, Any],
) -> tuple[object, float, float, tuple[str, ...]]:
    """Start a local stdio server, discover tools, and perform exactly one call."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - installation-specific guard
        raise RuntimeError("The MCP Python SDK is required for raw transport benchmarking") from exc

    if not command:
        raise ValueError("MCP server command must not be empty")
    startup_started_ns = time.perf_counter_ns()
    server = StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        cwd=str(project_root),
        env=_stdio_environment(),
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed_tools = await session.list_tools()
            tool_names = tuple(sorted(tool.name for tool in listed_tools.tools))
            if operation not in tool_names:
                raise RuntimeError(f"MCP server did not advertise required operation: {operation}")
            startup_ms = (time.perf_counter_ns() - startup_started_ns) / 1_000_000
            call_started_ns = time.perf_counter_ns()
            result = await session.call_tool(operation, arguments)
            call_ms = (time.perf_counter_ns() - call_started_ns) / 1_000_000
            return result, startup_ms, call_ms, tool_names


def run_raw_mcp_cold_cases(
    cases: list[BenchmarkCase] | tuple[BenchmarkCase, ...],
    project_root: Path,
    *,
    command: tuple[str, ...] | None = None,
) -> RawMcpRun:
    """Measure one independent raw-MCP server startup and call per case."""
    root = Path(project_root).resolve()
    server_command = command or default_mcp_server_command(root)
    results: list[RouteResult] = []
    advertised_tools: set[str] = set()
    for case in cases:
        attempt_started_ns = time.perf_counter_ns()
        try:
            output, startup_ms, call_ms, tool_names = asyncio.run(
                _raw_mcp_call(server_command, root, case.operation, dict(case.mcp_arguments))
            )
        except Exception as exc:  # noqa: BLE001 - a transport failure is a measured result
            results.append(
                RouteResult(
                    route="raw_mcp_cold",
                    case_id=case.case_id,
                    elapsed_ms=(time.perf_counter_ns() - attempt_started_ns) / 1_000_000,
                    source_verified=False,
                    matched_sources=(),
                    evidence_excerpt="",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        advertised_tools.update(tool_names)
        results.append(
            RouteResult(
                route="raw_mcp_cold",
                case_id=case.case_id,
                elapsed_ms=call_ms,
                source_verified=False,
                matched_sources=(),
                evidence_excerpt=_serialize_transport_output(output),
                error=_protocol_error(output),
                startup_ms=startup_ms,
            )
        )
    return RawMcpRun(
        results=tuple(results),
        advertised_tools=tuple(sorted(advertised_tools)),
        session_startup_ms=None,
    )


async def _run_raw_mcp_warm_cases(
    cases: tuple[BenchmarkCase, ...],
    project_root: Path,
    command: tuple[str, ...],
    repetitions: int,
) -> RawMcpRun:
    """Measure repeated calls through one initialized local stdio MCP session."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - installation-specific guard
        raise RuntimeError("The MCP Python SDK is required for raw transport benchmarking") from exc

    startup_started_ns = time.perf_counter_ns()
    server = StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        cwd=str(project_root),
        env=_stdio_environment(),
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed_tools = await session.list_tools()
            advertised_tools = tuple(sorted(tool.name for tool in listed_tools.tools))
            startup_ms = (time.perf_counter_ns() - startup_started_ns) / 1_000_000
            results: list[RouteResult] = []
            for _ in range(repetitions):
                for case in cases:
                    call_started_ns = time.perf_counter_ns()
                    try:
                        output = await session.call_tool(case.operation, dict(case.mcp_arguments))
                    except Exception as exc:  # noqa: BLE001 - retained as benchmark evidence
                        results.append(
                            RouteResult(
                                route="raw_mcp_warm",
                                case_id=case.case_id,
                                elapsed_ms=(time.perf_counter_ns() - call_started_ns) / 1_000_000,
                                source_verified=False,
                                matched_sources=(),
                                evidence_excerpt="",
                                error=f"{type(exc).__name__}: {exc}",
                                startup_ms=startup_ms,
                            )
                        )
                        continue
                    results.append(
                        RouteResult(
                            route="raw_mcp_warm",
                            case_id=case.case_id,
                            elapsed_ms=(time.perf_counter_ns() - call_started_ns) / 1_000_000,
                            source_verified=False,
                            matched_sources=(),
                            evidence_excerpt=_serialize_transport_output(output),
                            error=_protocol_error(output),
                            startup_ms=startup_ms,
                        )
                    )
    return RawMcpRun(
        results=tuple(results),
        advertised_tools=advertised_tools,
        session_startup_ms=startup_ms,
    )


def run_raw_mcp_warm_cases(
    cases: list[BenchmarkCase] | tuple[BenchmarkCase, ...],
    project_root: Path,
    *,
    repetitions: int,
    command: tuple[str, ...] | None = None,
) -> RawMcpRun:
    """Synchronously run repeated calls through one local raw-MCP connection."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    root = Path(project_root).resolve()
    server_command = command or default_mcp_server_command(root)
    case_tuple = tuple(cases)
    attempt_started_ns = time.perf_counter_ns()
    try:
        return asyncio.run(_run_raw_mcp_warm_cases(case_tuple, root, server_command, repetitions))
    except Exception as exc:  # noqa: BLE001 - startup failure is a measured benchmark result
        elapsed_ms = (time.perf_counter_ns() - attempt_started_ns) / 1_000_000
        return RawMcpRun(
            results=tuple(
                RouteResult(
                    route="raw_mcp_warm",
                    case_id=case.case_id,
                    elapsed_ms=elapsed_ms,
                    source_verified=False,
                    matched_sources=(),
                    evidence_excerpt="",
                    error=f"{type(exc).__name__}: {exc}",
                )
                for _ in range(repetitions)
                for case in case_tuple
            ),
            advertised_tools=(),
            session_startup_ms=None,
        )


def _route_payload(result: RouteResult) -> dict[str, object]:
    return {
        "route": result.route,
        "case_id": result.case_id,
        "elapsed_ms": round(result.elapsed_ms, 3),
        "startup_ms": None if result.startup_ms is None else round(result.startup_ms, 3),
        "source_verified": result.source_verified,
        "matched_sources": list(result.matched_sources),
        "evidence_excerpt": result.evidence_excerpt,
        "error": result.error,
    }


def run_benchmark(
    case_path: Path,
    project_root: Path,
    *,
    mode: str,
    warm_repetitions: int,
) -> dict[str, object]:
    """Run the selected deterministic source and raw-transport benchmark routes."""
    if mode not in {"direct_source", "raw_cold", "raw_warm", "all"}:
        raise ValueError(f"Unsupported benchmark mode: {mode}")
    root = Path(project_root).resolve()
    cases = load_cases(case_path, root)
    report: dict[str, object] = {
        "schema": BENCHMARK_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_schema": CASE_SCHEMA,
        "case_count": len(cases),
        "project_root": str(root),
        "mcp_server_command": list(default_mcp_server_command(root)),
        "routes": {},
    }
    routes: dict[str, object] = report["routes"]  # type: ignore[assignment]
    if mode in {"direct_source", "all"}:
        routes["direct_source"] = [_route_payload(run_direct_source_case(case, root)) for case in cases]
    if mode in {"raw_cold", "all"}:
        cold = run_raw_mcp_cold_cases(cases, root)
        routes["raw_mcp_cold"] = {
            "advertised_tools": list(cold.advertised_tools),
            "results": [_route_payload(result) for result in cold.results],
        }
    if mode in {"raw_warm", "all"}:
        warm = run_raw_mcp_warm_cases(cases, root, repetitions=warm_repetitions)
        routes["raw_mcp_warm"] = {
            "advertised_tools": list(warm.advertised_tools),
            "session_startup_ms": round(warm.session_startup_ms or 0.0, 3),
            "results": [_route_payload(result) for result in warm.results],
        }
    return report


def main() -> int:
    """Run the immutable direct-source and raw-MCP benchmark routes."""
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=project_root / "tests" / "fixtures" / "graphify_mcp_cases.json",
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument(
        "--mode",
        choices=("direct_source", "raw_cold", "raw_warm", "all"),
        default="direct_source",
    )
    parser.add_argument("--warm-repetitions", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = run_benchmark(
        args.cases,
        args.project_root,
        mode=args.mode,
        warm_repetitions=args.warm_repetitions,
    )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
