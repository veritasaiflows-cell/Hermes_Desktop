#!/usr/bin/env python3
"""Controlled real-tool canary: compare current SQLite v3 with collector read-back.

No model call or provider outage is synthesized. The native tools and native
post-tool metadata emitter execute; only hook delivery is redirected, inside
this verifier process, to isolated SQLite plus the approved OTLP observer.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts.hermes_otel_pilot import Observer, SERVICE_NAME, TARGET, send_http


def attributes(items):
    return {item["key"]: next(iter(item["value"].values())) for item in items}


def require(condition, category):
    if not condition:
        raise ValueError(category)


def compare_diagnosis(rows, recorded, trace_ids):
    """Re-derive sequence answers; never trust answers from an earlier proof."""
    require(len(rows) == len(trace_ids) == 2, "paired_canary_count_mismatch")
    comparisons = []
    for index, (row, trace_id) in enumerate(zip(rows, trace_ids)):
        require(row.get("collector_version") == "1.3.0" and row.get("metric_semantics") == "turn-metrics.v3",
                "sqlite_v3_semantics_mismatch")
        require(row["tool_call_count"] == 8 and row["tool_error_count"] == 1, "sqlite_tool_count_mismatch")
        require(row["api_request_count"] == 0, "unexpected_provider_attempt")
        require(json.loads(row["tool_error_categories_json"]) == {TARGET: 1}, "sqlite_category_mismatch")
        require(json.loads(row["tool_names_json"]) == ["read_file", "search_files"], "sqlite_tool_names_mismatch")
        diagnostics = json.loads(row["tool_diagnostics_json"])
        require(row["tool_success_diagnostics_dropped_count"] == 2 and len(diagnostics) == 6,
                "sqlite_diagnostic_cap_mismatch")
        require([detail["tool"] for detail in diagnostics] == ["read_file"] * 6,
                "sqlite_discovery_not_withheld")
        roots = [span for span in recorded if span["traceId"] == trace_id and span["name"] == "hermes.turn"]
        events = [span for span in recorded if span["traceId"] == trace_id and span["name"] == "hermes.tool"]
        require(len(roots) == 1 and len(events) == 8, "trace_attempt_count_mismatch")
        root = roots[0]
        root_attrs = attributes(root["attributes"])
        require(root_attrs["hermes.coverage.complete"] is True and
                root_attrs["hermes.cohort"] == "controlled-tool-canary", "trace_coverage_or_cohort_mismatch")
        require(all(span["parentSpanId"] == root["spanId"] for span in events), "trace_parent_mismatch")
        started = datetime.fromtimestamp(int(root["startTimeUnixNano"]) / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        require(started == row["started_at"], "sqlite_trace_start_mismatch")
        ordered = sorted(events, key=lambda span: int(attributes(span["attributes"])["hermes.sequence"]))
        require([int(attributes(span["attributes"])["hermes.sequence"]) for span in ordered] == list(range(1, 9)),
                "trace_sequence_mismatch")
        failures = [span for span in events if attributes(span["attributes"])["error.category"] == TARGET]
        discoveries = [span for span in events if attributes(span["attributes"])["tool.name"] == "search_files"]
        require(len(failures) == len(discoveries) == 1, "trace_failure_or_discovery_count_mismatch")
        failure_seq = int(attributes(failures[0]["attributes"])["hermes.sequence"])
        discovery_seq = int(attributes(discoveries[0]["attributes"])["hermes.sequence"])
        answer = "before" if discovery_seq < failure_seq else "after"
        expected = "after" if index == 0 else "before"
        require(answer == expected, "controlled_sequence_answer_mismatch")
        comparisons.append({"trace_id": trace_id, "sqlite_collector_version": row["collector_version"],
                            "sqlite_metric_semantics": row["metric_semantics"], "tool_calls": row["tool_call_count"],
                            "missing_path_errors": row["tool_error_count"],
                            "sqlite_error_categories": json.loads(row["tool_error_categories_json"]),
                            "sqlite_tool_names": json.loads(row["tool_names_json"]),
                            "sqlite_retained_tool_details": diagnostics,
                            "sqlite_success_details_dropped": row["tool_success_diagnostics_dropped_count"],
                            "sqlite_discovery_order_answer": "not-recorded",
                            "otel_discovery_order_answer": answer, "expected_order": expected,
                            "failure_sequence": failure_seq, "discovery_sequence": discovery_seq,
                            "otel_sequence": [{"sequence": int(attributes(span["attributes"])["hermes.sequence"]),
                                               "tool": attributes(span["attributes"])["tool.name"],
                                               "status": attributes(span["attributes"])["hermes.status"],
                                               "category": attributes(span["attributes"])["error.category"]}
                                              for span in ordered]})
    require(len(recorded) == 18, "collector_span_count_mismatch")
    return comparisons


def load_sqlite_observer(path):
    spec = importlib.util.spec_from_file_location("isolated_sqlite_observer_canary", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_sqlite_rows(database):
    """Explicit close is required: sqlite's transaction context does not close."""
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM turn_metrics ORDER BY rowid LIMIT 3")]


def read_back(path, offset, wanted, *, timeout=15):
    """Only parse appended records containing this pilot's exact service identity."""
    deadline = time.monotonic() + timeout
    found = {}
    while time.monotonic() < deadline:
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("readback_size_cap")
        for line in data.splitlines():
            if SERVICE_NAME.encode() not in line:
                continue
            try:
                packet = json.loads(line)
            except json.JSONDecodeError:
                continue  # the collector may still be appending its final line
            for resource in packet.get("resourceSpans", []):
                if attributes(resource.get("resource", {}).get("attributes", [])).get("service.name") != SERVICE_NAME:
                    continue
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        if span.get("traceId") in wanted:
                            found[span["spanId"]] = span
        if {span["traceId"] for span in found.values()} == wanted:
            roots = [span for span in found.values() if span["name"] == "hermes.turn"]
            if len(roots) == len(wanted) and len(found) == 18:
                return list(found.values())
        time.sleep(0.2)
    raise TimeoutError("exact_pilot_trace_readback_missing")


def verify(*, home, collector_config, trace_file):
    if sys.flags.optimize or not __debug__:
        raise RuntimeError("optimized_verification_forbidden")
    try:
        with socket.create_connection(("127.0.0.1", 4318), timeout=0.4):
            pass
    except OSError:
        raise RuntimeError("collector_unavailable_no_canary_executed") from None
    from hermes_constants import get_hermes_home
    from tools.file_tools import read_file_tool, search_tool
    from model_tools import _emit_post_tool_call_hook
    import hermes_cli.lifecycle as lifecycle

    assert Path(get_hermes_home()).resolve() == home.resolve(), "Use the approved default runtime profile"
    before_hash = hashlib.sha256(collector_config.read_bytes()).hexdigest()
    before_offset = trace_file.stat().st_size
    submitted = []
    private_markers = []

    def send(payload):
        packet = json.loads(payload)
        for marker in private_markers:
            assert marker not in payload.decode(), "Private canary reached OTLP payload"
        rejected = send_http(payload)
        assert rejected == 0, "Collector partially rejected a canary batch"
        submitted.append(packet)
        return rejected

    observer = Observer(expires_at=time.time() + 120, sender=send, cohort="controlled-tool-canary")
    sqlite_observer = load_sqlite_observer(home / "plugins" / "turn-telemetry" / "__init__.py")
    original_has_hook = lifecycle.has_hook
    original_invoke_hook = lifecycle.invoke_hook
    observed_posts = []

    def dispatch(hook, **values):
        if hook != "post_tool_call":
            return []
        observed_posts.append((values["tool_name"], values["status"]))
        observer.post_tool_call(**values)
        sqlite_observer.on_post_tool_call(**values)
        return []

    try:
        with tempfile.TemporaryDirectory(prefix="hermes-otel-canary-", dir=os.environ["TMPDIR"]) as scratch:
            scratch = Path(scratch)
            database = scratch / "turn-metrics.sqlite"
            sqlite_observer._reset_for_tests(database)
            lifecycle.has_hook = lambda hook: hook == "post_tool_call"
            lifecycle.invoke_hook = dispatch
            for label, order in (
                ("discovery-after-failure", ("failure", "discovery", "recovery")),
                ("discovery-before-failure", ("discovery", "failure", "recovery")),
            ):
                case = scratch / label
                case.mkdir()
                marker = "CONTENT_CANARY_" + os.urandom(16).hex()
                turn_id = "TURN_CANARY_" + os.urandom(16).hex()
                private_markers.extend([marker, turn_id, str(case)])
                known = case / "known.txt"
                known.write_text(marker, encoding="utf-8")
                missing = case / "deliberately-missing.txt"
                metadata = dict(turn_id=turn_id, session_id=turn_id, task_id=turn_id,
                                platform="controlled-canary", provider="no-provider-call", model="no-model-call")
                observer.pre_llm_call(**metadata)
                sqlite_observer.on_pre_llm_call(**metadata)

                def call(tool, arguments):
                    call_id = "CALL_CANARY_" + os.urandom(16).hex()
                    private_markers.append(call_id)
                    observer.pre_tool_call(**metadata, tool_call_id=call_id, tool_name=tool)
                    started = time.monotonic_ns()
                    result = (read_file_tool if tool == "read_file" else search_tool)(**arguments, task_id=turn_id)
                    elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
                    _emit_post_tool_call_hook(function_name=tool, function_args=arguments, result=result,
                                              turn_id=turn_id, session_id=turn_id, task_id=turn_id,
                                              tool_call_id=call_id, duration_ms=elapsed_ms)
                    return json.loads(result)

                # Exactly five successful samples saturate SQLite's success-detail cap.
                for limit in range(1, 6):
                    result = call("read_file", {"path": str(known), "limit": limit})
                    assert not result.get("error") and marker in result.get("content", "")
                for action in order:
                    if action == "failure":
                        result = call("read_file", {"path": str(missing)})
                        assert result.get("not_found") is True, "The real tool must actually miss"
                    elif action == "discovery":
                        result = call("search_files", {"pattern": "known.txt", "target": "files", "path": str(case), "limit": 5})
                        assert not result.get("error") and str(known).replace("\\", "/") in json.dumps(result).replace("\\\\", "/")
                    else:
                        result = call("read_file", {"path": str(known), "limit": 7})
                        assert marker in result.get("content", "")
                observer.session_end(turn_id=turn_id, completed=True)
                sqlite_observer.on_session_end(turn_id=turn_id, completed=True)
            assert observer.flush(timeout=2), "OTLP queue did not flush"
            assert sqlite_observer._flush_for_tests(timeout=2), "SQLite queue did not flush"
            health = observer.health()
            assert health["sent"] == 2 and health["failed"] == 0 and health["partial_batches"] == 0
            assert len(observed_posts) == 16
            assert len(submitted) == 2
            wanted = {packet["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] for packet in submitted}
            recorded = read_back(trace_file, before_offset, wanted)
            assert len(recorded) == 18
            exported = json.dumps(recorded)
            for marker in private_markers:
                assert marker not in exported
            rows = read_sqlite_rows(database)
            assert len(rows) == 2
            for marker in private_markers:
                assert marker not in json.dumps(rows)
                for suffix in ("", "-wal", "-shm"):
                    physical = Path(str(database) + suffix)
                    if physical.exists():
                        content = physical.read_bytes()
                        assert marker.encode() not in content and marker.encode("utf-16le") not in content
            comparisons = []
            for index, packet in enumerate(submitted):
                root = packet["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
                events = [span for span in recorded if span["traceId"] == root["traceId"] and span["name"] == "hermes.tool"]
                failure = next(span for span in events if attributes(span["attributes"])["error.category"] == TARGET)
                discovery = next(span for span in events if attributes(span["attributes"])["tool.name"] == "search_files")
                observed_order = "before" if int(attributes(discovery["attributes"])["hermes.sequence"]) < int(attributes(failure["attributes"])["hermes.sequence"]) else "after"
                expected_order = "after" if index == 0 else "before"
                assert observed_order == expected_order
                row = rows[index]
                diagnostics = json.loads(row["tool_diagnostics_json"])
                assert row["collector_version"] == "1.3.0" and row["metric_semantics"] == "turn-metrics.v3"
                assert row["tool_call_count"] == 8 and row["tool_error_count"] == 1
                assert json.loads(row["tool_error_categories_json"]) == {TARGET: 1}
                assert row["tool_success_diagnostics_dropped_count"] == 2
                assert len(diagnostics) == 6
                assert not any(detail["tool"] == "search_files" for detail in diagnostics)
                assert attributes(root["attributes"])["hermes.coverage.complete"] is True
                comparisons.append({"trace_id": root["traceId"], "tool_calls": 8, "missing_path_errors": 1,
                                    "sqlite_retained_tool_details": len(diagnostics),
                                    "sqlite_success_details_dropped": 2,
                                    "sqlite_discovery_order_answer": "not-recorded",
                                    "otel_discovery_order_answer": observed_order, "expected_order": expected_order})
            assert before_hash == hashlib.sha256(collector_config.read_bytes()).hexdigest()
            assert sqlite_observer._stop_writer(timeout=1), "Close SQLite writer before scratch cleanup"
            return {"schema": "hermes-otel-pilot-canary-proof.v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "generator": "scripts/verify_hermes_otel_pilot.py", "validation_status": "passed",
                    "python_optimization": sys.flags.optimize,
                    "source_candidate_id": "feedback-candidate-f580974b8f1704e5",
                    "cohort": "controlled-tool-canary", "real_tool_calls": 16, "real_provider_calls": 0,
                    "collector_readback_spans": len(recorded), "target_traces": len(wanted),
                    "privacy_canary_hits": 0, "export_health": health, "comparisons": comparisons,
                    "collector_config_sha256_before": before_hash, "collector_config_sha256_after": before_hash,
                    "added_diagnostic_capability": "Distinguishes discovery before versus after the failing read despite equivalent SQLite category/count signals and a dropped discovery success detail.",
                    "limitations": ["Controlled canary, not natural failure-rate improvement or historical root-cause attribution.",
                                    "API hook contract is separately unit-tested; no real provider-attempt coverage is claimed by this canary.",
                                    "No paths/arguments are retained, so ordering cannot prove discovery/read path equivalence."]}
    finally:
        lifecycle.has_hook = original_has_hook
        lifecycle.invoke_hook = original_invoke_hook
        observer.close()
        sqlite_observer._stop_writer(timeout=1)


def recover(*, database, trace_file, collector_config, baseline_proof):
    """Read original canary records only; no exports, provider calls or new tools."""
    if sys.flags.optimize or not __debug__:
        raise RuntimeError("optimized_verification_forbidden")
    require(baseline_proof.stat().st_size <= 65536, "baseline_proof_size_cap")
    baseline = json.loads(baseline_proof.read_text(encoding="utf-8"))
    trace_ids = [item.get("trace_id") for item in baseline.get("comparisons", [])]
    require(len(trace_ids) == 2 and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
                                      for value in trace_ids) and len(set(trace_ids)) == 2,
            "invalid_recovery_trace_ids")
    before_hash = hashlib.sha256(collector_config.read_bytes()).hexdigest()
    require(before_hash == baseline["collector_config_sha256_after"], "collector_config_changed")
    with trace_file.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    require(len(data) <= 8 * 1024 * 1024, "recovery_readback_size_cap")
    found = {}
    for line in data.splitlines():
        if SERVICE_NAME.encode() not in line:
            continue
        packet = json.loads(line)
        for resource in packet.get("resourceSpans", []):
            if attributes(resource.get("resource", {}).get("attributes", [])).get("service.name") != SERVICE_NAME:
                continue
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    if span.get("traceId") in trace_ids:
                        identity = (span["traceId"], span["spanId"])
                        require(identity not in found or found[identity] == span, "conflicting_recovery_span")
                        found[identity] = span
    recorded = list(found.values())
    original_rows = read_sqlite_rows(database)
    fields = ("started_at", "collector_version", "metric_semantics", "api_request_count", "tool_call_count",
              "tool_error_count", "tool_error_categories_json", "tool_names_json", "tool_diagnostics_json",
              "tool_success_diagnostics_dropped_count")
    rows = [{key: row[key] for key in fields} for row in original_rows]
    inputs = {"sqlite_rows": rows, "collector_spans": recorded, "trace_ids": trace_ids}
    serialized = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    markers = (b"CONTENT_CANARY_", b"TURN_CANARY_", b"CALL_CANARY_", b"hermes-otel-canary-")
    prefix_hits = sum(serialized.count(marker) for marker in markers)
    require(prefix_hits == 0, "recovery_privacy_prefix_hit")
    comparisons = compare_diagnosis(rows, recorded, trace_ids)
    require(before_hash == hashlib.sha256(collector_config.read_bytes()).hexdigest(), "collector_config_changed")
    return {"schema": "hermes-otel-pilot-reproducible-diagnosis-proof.v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "generator": "scripts/verify_hermes_otel_pilot.py --recover-database",
            "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python_optimization": sys.flags.optimize,
            "validation_status": "diagnostic-capability-passed-live-acceptance-blocked",
            "source_candidate_id": "feedback-candidate-f580974b8f1704e5",
            "source_hashes": {"sqlite_file": hashlib.sha256(database.read_bytes()).hexdigest(),
                              "collector_file": hashlib.sha256(data).hexdigest(),
                              "baseline_proof": hashlib.sha256(baseline_proof.read_bytes()).hexdigest(),
                              "selected_metadata_inputs": hashlib.sha256(serialized).hexdigest()},
            "source_paths": {"sqlite": str(database), "collector": str(trace_file), "baseline": str(baseline_proof)},
            "collector_config_sha256_before": before_hash, "collector_config_sha256_after": before_hash,
            "target_traces": len(trace_ids), "collector_readback_spans": len(recorded),
            "archived_real_tool_calls": sum(row["tool_call_count"] for row in rows),
            "real_provider_calls": 0, "new_tool_calls": 0, "new_exports": 0,
            "privacy_prefix_hits": prefix_hits, "comparisons": comparisons, "inputs": inputs,
            "limitations": ["Archived controlled observations predate QA fixes; not current live acceptance.",
                            "Prefix scan is narrower than the original full private-marker checks.",
                            "No real-provider coverage, historical root cause, path equality or production reliability gain.",
                            "Diagnostic value comes from ordered retained metadata, not OTEL alone; SQLite could be extended similarly.",
                            "Collector unavailable; plugin remains disabled and expansion is not authorized."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--collector-config", type=Path, required=True)
    parser.add_argument("--trace-file", type=Path, required=True)
    parser.add_argument("--recover-database", type=Path)
    parser.add_argument("--baseline-proof", type=Path)
    args = parser.parse_args()
    if args.recover_database:
        if not args.baseline_proof:
            parser.error("--recover-database requires --baseline-proof")
        proof = recover(database=args.recover_database, trace_file=args.trace_file,
                        collector_config=args.collector_config, baseline_proof=args.baseline_proof)
    else:
        if not args.home:
            parser.error("live verification requires --home")
        proof = verify(home=args.home, collector_config=args.collector_config, trace_file=args.trace_file)
    print(json.dumps(proof, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
