"""Safety and contract tests for the operator-approved OTLP observer pilot."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hermes_otel_pilot.py"


def load_pilot():
    assert MODULE_PATH.is_file(), "The approved OTLP observer implementation is missing"
    spec = importlib.util.spec_from_file_location("hermes_otel_pilot_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def spans(packet):
    return packet["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attrs(span):
    return {item["key"]: next(iter(item["value"].values())) for item in span["attributes"]}


def test_root_interval_never_reverses_on_wall_clock_rollback(monkeypatch):
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="rollback-root")
        observer.post_tool_call(turn_id="rollback-root", tool_name="read_file",
                                status="error", error_message="not found")
        monkeypatch.setattr(module.time, "time_ns", lambda: 1)
        observer.session_end(turn_id="rollback-root")
        assert observer.flush(timeout=1)
        root = next(s for s in spans(packets[0]) if s["name"] == "hermes.turn")
        assert int(root["endTimeUnixNano"]) >= int(root["startTimeUnixNano"])
    finally:
        observer.close()


def test_callback_loss_invalidates_turn_coverage():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="bad-callback")
        observer.post_tool_call(turn_id="bad-callback", tool_name="read_file",
                                status="error", error_message="not found")
        observer.post_tool_call(turn_id="bad-callback", status=[], tool_name="read_file")
        assert observer.health()["callback_errors"] == 1
        observer.session_end(turn_id="bad-callback")
        assert observer.flush(timeout=1)
        root = next(s for s in spans(packets[0]) if s["name"] == "hermes.turn")
        assert attrs(root)["hermes.coverage.complete"] is False
    finally:
        observer.close()


def test_registration_revokes_and_closes_previous_observer():
    module = load_pilot()
    observer = module.Observer(expires_at=time.time() + 60, sender=lambda payload: None)
    module._OBSERVER = observer
    class Context:
        def get_config(self, key, default=""):
            return default
        def register_hook(self, *args):
            pass
        def on_unload(self, *args):
            pass
    try:
        module.register(Context())
        assert module._OBSERVER is None
        assert observer.closed.is_set()
    finally:
        observer.close()


def test_ambiguity_saturation_never_restores_api_links():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="saturation")
        for index in range(module.MAX_EVENTS + 1):
            for _ in range(2):
                observer.pre_api_request(turn_id="saturation", api_request_id=f"id-{index}")
        request = f"id-{module.MAX_EVENTS}"
        observer.pre_api_request(turn_id="saturation", api_request_id=request)
        observer.post_api_request(turn_id="saturation", api_request_id=request)
        observer.post_tool_call(turn_id="saturation", api_request_id=request,
                                tool_name="read_file", status="error", error_message="not found")
        observer.session_end(turn_id="saturation")
        assert observer.flush(timeout=1)
        tool = next(s for s in spans(packets[0]) if s["name"] == "hermes.tool")
        assert "links" not in tool
    finally:
        observer.close()


def test_target_turn_exports_correlated_metadata_only_api_and_tool_spans():
    module = load_pilot()
    packets = []
    observer = module.Observer(
        expires_at=time.time() + 60,
        sender=lambda payload: packets.append(json.loads(payload)),
    )
    private = "NEVER_EXPORT_private_prompt_path_key_and_result"
    try:
        observer.pre_llm_call(turn_id="raw-turn-id", user_message=private)
        observer.pre_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id", request=private)
        observer.post_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id", response=private)
        observer.post_tool_call(
            turn_id="raw-turn-id", tool_call_id="raw-tool-id", tool_name="read_file",
            api_request_id="raw-api-id", status="error", duration_ms=1,
            error_message="File not found: " + private, args={"path": private}, result=private,
        )
        observer.session_end(turn_id="raw-turn-id", completed=True)
        assert observer.flush(timeout=1)
        assert len(packets) == 1
        packet = packets[0]
        serialized = json.dumps(packet)
        for forbidden in (private, "raw-turn-id", "raw-api-id", "raw-tool-id"):
            assert forbidden not in serialized
        recorded = spans(packet)
        assert len(recorded) == 3
        assert len({span["traceId"] for span in recorded}) == 1
        root = next(span for span in recorded if span["name"] == "hermes.turn")
        children = [span for span in recorded if span is not root]
        assert all(span["parentSpanId"] == root["spanId"] for span in children)
        failure = next(span for span in children if span["name"] == "hermes.tool")
        assert attrs(failure)["error.category"] == "read_file_missing_path"
        assert failure["links"][0]["spanId"] == next(
            span["spanId"] for span in children if span["name"] == "hermes.api"
        )
        assert observer.health()["sent"] == 1
    finally:
        observer.close()


def test_missing_request_ids_do_not_invent_api_to_tool_correlation():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="unidentified")
        observer.pre_api_request(turn_id="unidentified")
        observer.post_api_request(turn_id="unidentified")
        observer.post_tool_call(turn_id="unidentified", tool_name="read_file", status="error",
                                error_message="not found")
        observer.session_end(turn_id="unidentified")
        assert observer.flush(timeout=1)
        tool = next(span for span in spans(packets[0]) if span["name"] == "hermes.tool")
        assert "links" not in tool, "An absent opaque ID cannot establish correlation"
    finally:
        observer.close()


def test_expiry_stops_the_writer_and_discards_unclosed_turn_state():
    module = load_pilot()
    observer = module.Observer(expires_at=time.time() + 0.15, sender=lambda payload: None)
    try:
        observer.pre_llm_call(turn_id="expires-unclosed")
        assert observer.closed.wait(timeout=1), "Expiry must stop idle telemetry, not only reject events"
        observer.writer.join(timeout=1)
        assert not observer.writer.is_alive()
        assert observer.health()["active_turns"] == 0
    finally:
        observer.close()


def target_turn(observer, turn_id, *, events=1):
    observer.pre_llm_call(turn_id=turn_id)
    for index in range(events):
        observer.post_tool_call(turn_id=turn_id, tool_call_id=str(index), tool_name="read_file",
                                status="error", error_message="not found", duration_ms=float("nan"))
    observer.session_end(turn_id=turn_id)


def test_non_target_turn_and_duplicate_or_late_closure_do_not_export():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="ordinary")
        observer.post_tool_call(turn_id="ordinary", tool_name="read_file", status="ok")
        observer.session_end(turn_id="ordinary")
        target_turn(observer, "target")
        observer.session_end(turn_id="target")
        observer.post_tool_call(turn_id="target", tool_name="read_file", status="error", error_message="not found")
        assert observer.flush(timeout=1)
        assert len(packets) == 1
    finally:
        observer.close()


def test_event_and_trace_caps_are_visible_without_claiming_complete_coverage():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        target_turn(observer, "long", events=70)
        for index in range(5):
            target_turn(observer, f"extra-{index}")
        assert observer.flush(timeout=1)
        assert len(packets) == 4
        assert len(spans(packets[0])) == 65
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
        assert int(attrs(root)["hermes.events.dropped"]) == 6
        assert observer.health()["capture_dropped"] == 6
        assert observer.health()["dropped"] == 2
        assert max(len(json.dumps(packet).encode()) for packet in packets) < module.MAX_REQUEST_BYTES
    finally:
        observer.close()


def test_active_state_is_bounded_and_invalid_metadata_is_fail_open():
    module = load_pilot()
    observer = module.Observer(expires_at=time.time() + 60, sender=lambda payload: None)
    try:
        for index in range(10):
            observer.pre_llm_call(turn_id=str(index))
        assert observer.health()["active_turns"] == 8
        assert observer.health()["capture_dropped"] == 2
        observer.post_tool_call(turn_id="0", tool_name=[], status=[], error_message=object())
        assert observer.health()["callback_errors"] == 1
        assert observer.pre_llm_call(turn_id=object()) is None
    finally:
        observer.close()


def test_offline_export_is_async_fail_open_and_never_retried():
    import threading
    module = load_pilot()
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    def unavailable(payload):
        attempts.append(1)
        entered.set()
        assert release.wait(timeout=1)
        raise TimeoutError("private transport text must not enter health")

    observer = module.Observer(expires_at=time.time() + 60, sender=unavailable)
    try:
        target_turn(observer, "offline")
        assert entered.wait(timeout=1)
        assert observer.pre_llm_call(turn_id="still-usable") is None
        assert not observer.flush(timeout=0.01)
        release.set()
        assert observer.flush(timeout=1)
        assert attempts == [1]
        assert observer.health()["failed"] == 1
        assert observer.health()["last_failure"] == "timeout"
        assert "private" not in json.dumps(observer.health())
    finally:
        release.set()
        observer.close()


def test_partial_success_is_not_reported_as_full_delivery():
    module = load_pilot()
    observer = module.Observer(expires_at=time.time() + 60, sender=lambda payload: 1)
    try:
        target_turn(observer, "partial")
        assert observer.flush(timeout=1)
        assert observer.health()["sent"] == 0
        assert observer.health()["partial_batches"] == 1
        assert observer.health()["rejected_spans"] == 1
    finally:
        observer.close()


def test_profile_scope_is_rechecked_on_every_hook(monkeypatch, tmp_path):
    module = load_pilot()
    import hermes_constants
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    observer.home = tmp_path.resolve()
    module._OBSERVER = observer
    try:
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "other-profile")
        module.on_pre_llm_call(turn_id="must-not-capture")
        module.on_post_tool_call(turn_id="must-not-capture", tool_name="read_file", status="error", error_message="not found")
        module.on_session_end(turn_id="must-not-capture")
        assert observer.health()["active_turns"] == 0
        assert packets == []
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
        module.on_pre_llm_call(turn_id="allowed")
        module.on_post_tool_call(turn_id="allowed", tool_name="read_file", status="error", error_message="not found")
        module.on_session_end(turn_id="allowed")
        assert observer.flush(timeout=1)
        assert len(packets) == 1
    finally:
        observer.close()


def test_http_transport_is_fixed_local_bounded_and_handles_partial_success(monkeypatch):
    module = load_pilot()
    calls = []

    class Response:
        status = 200

        def read(self, size):
            assert size == module.MAX_RESPONSE_BYTES + 1
            return b'{"partialSuccess":{"rejectedSpans":"1","errorMessage":"NEVER_STORE"}}'

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path, payload, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    assert module.send_http(b'{}') == 1
    assert calls[0] == ("127.0.0.1", 4318, 0.4)
    assert calls[1] == ("POST", "/v1/traces", {"Content-Type": "application/json"})
    assert calls[-1] == "closed"


def test_http_request_and_response_size_caps_are_enforced(monkeypatch):
    import pytest
    module = load_pilot()
    with pytest.raises(ValueError, match="request_size_cap"):
        module.send_http(b"x" * (module.MAX_REQUEST_BYTES + 1))

    class Connection:
        def __init__(self, *args, **kwargs):
            self.status = 200

        def request(self, *args):
            pass

        def getresponse(self):
            return self

        def read(self, size):
            return b"x" * size

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    with pytest.raises(ValueError, match="otlp_response_failed"):
        module.send_http(b'{}')


def test_blocked_reads_do_not_become_missing_path_targets():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="blocked")
        observer.post_tool_call(turn_id="blocked", tool_name="read_file", status="blocked",
                                error_message="not found in allowed scope")
        observer.session_end(turn_id="blocked")
        assert observer.flush(timeout=1)
        assert packets == [], "A policy block is a different category from a missing-path failure"
    finally:
        observer.close()


def test_verifier_refuses_offline_collector_before_executing_canaries(monkeypatch, tmp_path):
    import pytest
    from scripts import verify_hermes_otel_pilot as verifier
    import socket

    def offline(*args, **kwargs):
        raise ConnectionRefusedError("local collector is absent")

    monkeypatch.setattr(socket, "create_connection", offline)
    with pytest.raises(RuntimeError, match="collector_unavailable_no_canary_executed"):
        verifier.verify(home=tmp_path, collector_config=tmp_path / "unused.yaml", trace_file=tmp_path / "unused.jsonl")


def test_verifier_sqlite_reader_closes_before_windows_cleanup(tmp_path):
    from contextlib import closing
    import sqlite3
    from scripts import verify_hermes_otel_pilot as verifier
    database = tmp_path / "scratch-metrics.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE turn_metrics (counter INTEGER)")
        connection.execute("INSERT INTO turn_metrics VALUES (7)")
        connection.commit()
    assert verifier.read_sqlite_rows(database) == [{"counter": 7}]
    database.unlink()
    assert not database.exists()


def test_open_attempt_overflow_cannot_claim_complete_coverage():
    module = load_pilot()
    packets = []
    observer = module.Observer(expires_at=time.time() + 60,
                               sender=lambda payload: packets.append(json.loads(payload)))
    try:
        observer.pre_llm_call(turn_id="overflow")
        for index in range(65):
            observer.pre_tool_call(turn_id="overflow", tool_call_id=str(index))
        for index in range(64):
            observer.post_tool_call(turn_id="overflow", tool_call_id=str(index), tool_name="read_file",
                                    status="error" if index == 0 else "ok", error_message="not found")
        observer.session_end(turn_id="overflow")
        assert observer.flush(timeout=1)
        root = attrs(spans(packets[0])[0])
        assert root["hermes.coverage.complete"] is False
        assert int(root["hermes.openings.dropped"]) == 1
        assert observer.health()["openings_dropped"] == 1
    finally:
        observer.close()


def test_ambiguous_openings_do_not_invent_timing_or_api_links():
    module = load_pilot()
    for kind, request_id in (("tool", "duplicate"), ("tool", None), ("api", "duplicate-api")):
        packets = []
        observer = module.Observer(expires_at=time.time() + 60,
                                   sender=lambda payload: packets.append(json.loads(payload)))
        try:
            observer.pre_llm_call(turn_id="ambiguous")
            identity = {"tool_call_id" if kind == "tool" else "api_request_id": request_id}
            start = observer.pre_tool_call if kind == "tool" else observer.pre_api_request
            end = observer.post_tool_call if kind == "tool" else observer.post_api_request
            start(turn_id="ambiguous", **identity)
            start(turn_id="ambiguous", **identity)
            for index in range(2):
                end(turn_id="ambiguous", tool_name="read_file", status="error" if kind == "tool" else "ok",
                    error_message="not found", **identity)
            if kind == "api":
                observer.post_tool_call(turn_id="ambiguous", tool_name="read_file", status="error",
                                        api_request_id=request_id, error_message="not found")
            observer.session_end(turn_id="ambiguous")
            assert observer.flush(timeout=1)
            root = attrs(spans(packets[0])[0])
            assert root["hermes.coverage.complete"] is False
            assert int(root["hermes.openings.ambiguous"]) > 0
            assert observer.health()["openings_ambiguous"] > 0
            for span in spans(packets[0])[1:]:
                assert attrs(span)["hermes.timing.source"] == "reported-duration"
                assert "links" not in span
        finally:
            observer.close()


def test_clock_rollback_cannot_extend_activation(monkeypatch):
    module = load_pilot()
    now = time.time()
    observer = module.Observer(expires_at=now + 0.15, sender=lambda payload: None)
    try:
        monkeypatch.setattr(module.time, "time", lambda: now - 86400)
        assert observer.closed.wait(timeout=1), "Runtime lifetime must use a monotonic deadline"
        observer.writer.join(timeout=1)
        assert not observer.writer.is_alive()
        assert observer.pre_llm_call(turn_id="past-deadline") is None
        assert observer.health()["active_turns"] == 0
    finally:
        observer.close()


def test_optimized_verifier_fails_closed_before_any_canary():
    import subprocess
    code = (
        "from pathlib import Path; from scripts import verify_hermes_otel_pilot as v; "
        "v.socket.create_connection=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('NETWORK_CHECK_REACHED')); "
        "v.verify(home=Path('.'),collector_config=Path('.'),trace_file=Path('.'))"
    )
    result = subprocess.run([sys.executable, "-O", "-c", code],
                            cwd=MODULE_PATH.parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "optimized_verification_forbidden" in result.stderr
    assert "NETWORK_CHECK_REACHED" not in result.stderr


def test_recovery_comparison_checks_full_sqlite_v3_semantics():
    import pytest
    from scripts import verify_hermes_otel_pilot as verifier
    assert callable(getattr(verifier, "compare_diagnosis", None)), "Reproducible comparison is missing"
    rows = [{"collector_version": "0.0.0", "metric_semantics": "obsolete"}] * 2
    with pytest.raises(ValueError, match="sqlite_v3_semantics_mismatch"):
        verifier.compare_diagnosis(rows, [], ["a" * 32, "b" * 32])


def test_recovery_rejects_invalid_trace_ids_before_reading_sources(tmp_path):
    import pytest
    from scripts import verify_hermes_otel_pilot as verifier
    assert callable(getattr(verifier, "recover", None)), "Bounded read-only recovery is missing"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"comparisons": [{"trace_id": "malformed"}, {"trace_id": "b" * 32}]}))
    with pytest.raises(ValueError, match="invalid_recovery_trace_ids"):
        verifier.recover(database=tmp_path / "missing.sqlite", trace_file=tmp_path / "missing.jsonl",
                         collector_config=tmp_path / "missing.yaml", baseline_proof=baseline)
