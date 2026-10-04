#!/usr/bin/env python3
"""Bounded, exact-service/exact-trace collector read-back. Never dump shared data."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SERVICE = "hermes-fleet-efficiency"
TRACE = re.compile(r"[0-9a-f]{32}\Z")
SPAN = re.compile(r"[0-9a-f]{16}\Z")
MAX_BYTES = 16 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024


def _attrs(items):
    result = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            continue
        # OTLP attributes are tagged scalars. Ignore objects/arrays, never echo them.
        value = item["value"]
        for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
            if key in value:
                result[item.get("key")] = value[key]
                break
    return result


def readback(path, *, offset, trace_ids, max_bytes=MAX_BYTES):
    if not trace_ids or any(not isinstance(t, str) or not TRACE.fullmatch(t) or int(t, 16) == 0 for t in trace_ids):
        raise ValueError("exact nonzero trace IDs required")
    if type(offset) is not int or offset < 0 or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("invalid read bounds")
    result = {"schema": "fleet-collector-readback.v1", "service": SERVICE,
              "offset": offset, "bytes_read": 0, "truncated": False,
              "malformed_records": 0, "duplicate_spans": 0,
              "invalid_intervals": 0, "incomplete_spans": 0, "loss_flagged_spans": 0,
              "missing_parents": 0,
              "matched_spans": 0, "matched_traces": 0, "names": {}, "roles": {},
              "outcomes": {}, "observed_usage": {}, "api_spans_without_usage": 0,
              "invalid_usage_values": 0, "cost_usd": None,
              "duration_ms_by_name": {}, "complete": False,
              "efficiency_claim_supported": False}
    seen, traces, parents = set(), set(), set()
    ids_by_trace = {}
    durations = {}
    source = Path(path)
    if not source.is_file() or source.stat().st_size < offset:
        result["source_unavailable_or_rotated"] = True
        return result
    with source.open("rb") as handle:
        handle.seek(offset)
        lines = 0
        while result["bytes_read"] < max_bytes and lines < 4096:
            remaining = max_bytes - result["bytes_read"]
            line = handle.readline(min(MAX_LINE, remaining) + 1)
            if not line:
                break
            lines += 1
            result["bytes_read"] += len(line)
            if len(line) > min(MAX_LINE, remaining) or not line.endswith(b"\n"):
                result["truncated"] = True
                break
            try:
                packet = json.loads(line)
                if not isinstance(packet, dict):
                    raise ValueError()
                resources = packet.get("resourceSpans", [])
                if not isinstance(resources, list):
                    raise ValueError()
                for resource in resources:
                    ra = _attrs(resource.get("resource", {}).get("attributes", []))
                    if ra.get("service.name") != SERVICE:
                        continue
                    for scope in resource.get("scopeSpans", []):
                        for span in scope.get("spans", []):
                            trace = span.get("traceId")
                            if trace not in trace_ids:
                                continue
                            sid = span.get("spanId")
                            if not isinstance(sid, str) or not SPAN.fullmatch(sid) or int(sid, 16) == 0:
                                raise ValueError()
                            key = (trace, sid)
                            if key in seen:
                                result["duplicate_spans"] += 1
                                continue
                            seen.add(key)
                            traces.add(trace)
                            ids_by_trace.setdefault(trace, set()).add(sid)
                            parent = span.get("parentSpanId")
                            if parent:
                                parents.add((trace, parent))
                            start, end = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                            if start < 0 or end < start:
                                result["invalid_intervals"] += 1
                            attrs = _attrs(span.get("attributes", []))
                            if attrs.get("hermes.coverage.complete") is False:
                                result["incomplete_spans"] += 1
                            # Loss flags ride on the first span of every packet,
                            # including Governor fleet.* phases with no coverage.
                            if (attrs.get("hermes.loss.incomplete") is True
                                    or attrs.get("hermes.counters.best_effort") is True
                                    or attrs.get("hermes.export.healthy") is False):
                                result["loss_flagged_spans"] += 1
                            name = span.get("name")
                            if name not in {"fleet.task", "fleet.dispatch", "fleet.execution", "fleet.verification",
                                            "fleet.review", "fleet.acceptance", "hermes.turn", "hermes.api", "hermes.tool"}:
                                name = "unknown"
                            role = attrs.get("hermes.role", ra.get("hermes.role", ra.get("role")))
                            if role not in {"governor", "implementer", "qa"}:
                                role = "unknown"
                            outcome = attrs.get("hermes.outcome", attrs.get("hermes.status", "unknown"))
                            if outcome not in {"ok", "error", "timeout", "cancelled", "accepted", "rejected", "unknown"}:
                                outcome = "unknown"
                            result["outcomes"][outcome] = result["outcomes"].get(outcome, 0) + 1
                            if name == "hermes.api":
                                observed = {}
                                for metric in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "prompt_tokens", "total_tokens"):
                                    key = "hermes.usage." + metric
                                    if key not in attrs:
                                        continue
                                    value = attrs[key]
                                    if ((type(value) is int or isinstance(value, str) and value.isascii() and value.isdecimal())
                                            and len(str(value)) <= 10 and 0 <= int(value) <= 10 ** 9):
                                        observed[metric] = int(value)
                                    else:
                                        result["invalid_usage_values"] += 1
                                if not observed:
                                    result["api_spans_without_usage"] += 1
                                elif role != "unknown":
                                    totals = result["observed_usage"].setdefault(role, {})
                                    for metric, count in observed.items():
                                        totals[metric] = totals.get(metric, 0) + count
                            result["names"][name] = result["names"].get(name, 0) + 1
                            result["roles"][role] = result["roles"].get(role, 0) + 1
                            durations[name] = durations.get(name, 0) + max(0, end - start)
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
                result["malformed_records"] += 1
        if handle.read(1):
            result["truncated"] = True
    result["matched_spans"] = len(seen)
    result["matched_traces"] = len(traces)
    result["missing_parents"] = sum(parent not in ids_by_trace.get(trace, set()) for trace, parent in parents)
    result["duration_ms_by_name"] = {name: round(ns / 1e6, 3) for name, ns in durations.items()}
    result["complete"] = (traces == set(trace_ids) and bool(seen) and not any(result[key] for key in
                          ("truncated", "malformed_records", "duplicate_spans", "invalid_intervals",
                           "incomplete_spans", "loss_flagged_spans", "missing_parents",
                           "invalid_usage_values")))
    # Complete means this bounded read is internally consistent, not that absent
    # spans never existed. Caller must additionally check expected roles/stages.
    result["coverage_requires_expected_stage_check"] = True
    return result


def check_expected(result, *, names, roles):
    """Exit-0 pass requires internal consistency AND every expected name/role."""
    missing_names = sorted(set(names) - set(result["names"]))
    missing_roles = sorted(set(roles) - set(result["roles"]))
    result["expected_names"] = sorted(set(names))
    result["expected_roles"] = sorted(set(roles))
    result["missing_names"] = missing_names
    result["missing_roles"] = missing_roles
    result["pass"] = bool(result["complete"] and not missing_names and not missing_roles)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-file", required=True, type=Path)
    parser.add_argument("--offset", required=True, type=int)
    parser.add_argument("--trace-id", required=True, action="append")
    parser.add_argument("--expect-name", required=True, action="append",
                        help="Span name that must be present (repeatable)")
    parser.add_argument("--expect-role", required=True, action="append",
                        help="Role that must be present (repeatable)")
    args = parser.parse_args(argv)
    result = readback(args.trace_file, offset=args.offset, trace_ids=set(args.trace_id))
    check_expected(result, names=args.expect_name, roles=args.expect_role)
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
