"""Deterministic contract tests for the bounded fleet efficiency observer.

Written before execution: the Governor runs these against
scripts/hermes_otel_efficiency.py. No test claims acceptance by itself.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hermes_otel_efficiency.py"


def load_efficiency():
    assert MODULE_PATH.is_file(), "The efficiency observer implementation is missing"
    spec = importlib.util.spec_from_file_location("hermes_otel_efficiency_test_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def spans(packet):
    return packet["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attrs(span):
    return {item["key"]: next(iter(item["value"].values())) for item in span["attributes"]}


def resource_attrs(packet):
    return {item["key"]: next(iter(item["value"].values()))
            for item in packet["resourceSpans"][0]["resource"]["attributes"]}


def sample_context(module, role="implementer", expires_in=60, **kwargs):
    return module.new_context(role, time.time() + expires_in, **kwargs)


def make_observer(module, *, role="implementer", expires_in=60, context=None):
    packets = []
    observer = module.Observer(role=role, expires_at=time.time() + expires_in, context=context,
                               sender=lambda payload: (packets.append(json.loads(payload)), 0)[1])
    return observer, packets


def success_turn(observer, turn_id="turn-a", *, api_id="api-a", tool_id="tool-a"):
    observer.pre_llm_call(turn_id=turn_id)
    observer.pre_api_request(turn_id=turn_id, api_request_id=api_id)
    observer.post_api_request(turn_id=turn_id, api_request_id=api_id)
    observer.pre_tool_call(turn_id=turn_id, tool_call_id=tool_id)
    observer.post_tool_call(turn_id=turn_id, tool_call_id=tool_id, tool_name="read_file",
                            api_request_id=api_id, status="ok")
    observer.session_end(turn_id=turn_id, completed=True)


class FakeContext:
    def __init__(self, config=None):
        self.config = config or {}
        self.config_reads = []
        self.hooked = []
        self.unloaded = []

    def get_config(self, key, default=""):
        self.config_reads.append(key)
        return self.config.get(key, default)

    def register_hook(self, name, function):
        self.hooked.append(name)

    def on_unload(self, function):
        self.unloaded.append(function)


# ---------------------------------------------------------------------------
# context envelope
# ---------------------------------------------------------------------------

def test_context_roundtrip_uses_fresh_lowercase_nonzero_ids():
    module = load_efficiency()
    first = sample_context(module)
    second = sample_context(module)
    assert set(first) == {"v", "trace_id", "parent_span_id", "role", "expires_at", "cohort", "sampled"}
    assert first["v"] == 1 and first["sampled"] is True and first["parent_span_id"] is None
    assert first["role"] == "implementer" and first["cohort"] == "controlled"
    assert isinstance(first["expires_at"], float)
    for context in (first, second):
        assert len(context["trace_id"]) == 32
        assert context["trace_id"] == context["trace_id"].lower() and context["trace_id"].strip("0")
    assert first["trace_id"] != second["trace_id"]


def test_new_context_rejects_wrong_fields_types_and_zero_or_mixed_ids():
    module = load_efficiency()
    now = time.time()
    for role in ("other", "", None, True):
        with pytest.raises(ValueError):
            module.new_context(role, now + 60)
    for expiry in (now - 1, now + module.ACTIVATION_MAX + 1, float("nan"), float("inf"),
                   True, "123", None):
        with pytest.raises(ValueError):
            module.new_context("qa", expiry)
    with pytest.raises(ValueError):
        module.new_context("qa", now + 60, cohort="other")
    for bad in ("0" * 32, "A" * 32, "abc", "g" * 32, 42, "a" * 1025):
        with pytest.raises(ValueError):
            module.new_context("qa", now + 60, trace_id=bad)
    for bad in ("0" * 16, "B" * 16, "abc", 7):
        with pytest.raises(ValueError):
            module.new_context("qa", now + 60, parent_span_id=bad)
    context = module.new_context("governor", now + 60, trace_id="12" * 16, parent_span_id="ab" * 8)
    assert context["trace_id"] == "12" * 16 and context["parent_span_id"] == "ab" * 8


def test_encode_context_is_compact_and_validates_before_encoding():
    module = load_efficiency()
    context = sample_context(module, "governor", trace_id="1f" * 16, parent_span_id="ab" * 8)
    encoded = module.encode_context(context)
    assert " " not in encoded and "\n" not in encoded
    decoded = module.decode_context(encoded, role="governor", expires_at=context["expires_at"] + 1)
    assert decoded == context
    for mutate in (
        lambda raw: dict(raw, extra="baggage"),
        lambda raw: dict(raw, sampled=False),
        lambda raw: dict(raw, cohort="mixed"),
        lambda raw: dict(raw, v=2),
        lambda raw: dict(raw, trace_id="ff" * 512),  # oversized raw string
    ):
        with pytest.raises(ValueError):
            module.encode_context(mutate(dict(context)))
    # qa is valid to encode; only the receiver's configured role can mismatch.
    qa_encoded = module.encode_context(dict(context, role="qa"))
    with pytest.raises(ValueError, match="context_role_mismatch"):
        module.decode_context(qa_encoded, role="governor", expires_at=context["expires_at"])


def test_decode_context_fails_closed_without_standalone_fallback():
    module = load_efficiency()
    context = sample_context(module, "implementer", trace_id="2a" * 16, parent_span_id="cd" * 8)
    encoded = module.encode_context(context)
    expiry = context["expires_at"]
    assert module.decode_context(encoded, role="implementer", expires_at=expiry) == context
    with pytest.raises(ValueError, match="context_role_mismatch"):
        module.decode_context(encoded, role="qa", expires_at=expiry)
    with pytest.raises(ValueError, match="context_expiry_exceeds_deadline"):
        module.decode_context(encoded, role="implementer", expires_at=expiry - 1)
    with pytest.raises(ValueError, match="invalid_deadline"):
        module.decode_context(encoded, role="implementer", expires_at=True)
    with pytest.raises(ValueError, match="invalid_context_envelope"):
        module.decode_context(encoded.encode(), role="implementer", expires_at=expiry)
    with pytest.raises(ValueError, match="invalid_context_envelope"):
        module.decode_context("x" * 1025, role="implementer", expires_at=expiry)
    with pytest.raises(ValueError, match="invalid_context_envelope"):
        module.decode_context("not json", role="implementer", expires_at=expiry)
    with pytest.raises(ValueError, match="invalid_context_schema"):
        module.decode_context("{}", role="implementer", expires_at=expiry)
    with pytest.raises(ValueError, match="invalid_context_schema"):
        module.decode_context("[1,2]", role="implementer", expires_at=expiry)
    nan_raw = encoded.replace(str(expiry), "NaN")
    with pytest.raises(ValueError, match="invalid_context_envelope"):
        module.decode_context(nan_raw, role="implementer", expires_at=expiry)
    past = dict(context, expires_at=time.time() - 5)
    with pytest.raises(ValueError, match="invalid_expiry"):
        module.decode_context(json.dumps(past, separators=(",", ":")), role="implementer",
                              expires_at=expiry)
    mixed = dict(context, trace_id="2A" * 16)
    with pytest.raises(ValueError, match="invalid_trace_id"):
        module.decode_context(json.dumps(mixed, separators=(",", ":")), role="implementer",
                              expires_at=expiry)


def test_cross_process_envelope_is_plain_metadata_json():
    module = load_efficiency()
    parent = sample_context(module, "governor", trace_id="3b" * 16)
    encoded = module.encode_context(parent)
    # Simulated process boundary: only the string crosses, never Python objects.
    payload = json.loads(encoded)
    assert payload["trace_id"] == "3b" * 16 and "prompt" not in encoded and "task" not in encoded
    child = module.decode_context(encoded, role="governor", expires_at=parent["expires_at"])
    assert child["trace_id"] == parent["trace_id"] and child["sampled"] is True


def test_emitter_context_is_standalone_or_strict_propagation():
    module = load_efficiency()
    standalone = module.Emitter(role="governor", expires_at=time.time() + 60,
                                sender=lambda payload: 0)
    try:
        assert standalone.propagated is False
        assert standalone.context["role"] == "governor"
        assert standalone.context["parent_span_id"] is None
    finally:
        standalone.close()
    parent = sample_context(module, "implementer", trace_id="4c" * 16, parent_span_id="ef" * 8)
    propagated = module.Emitter(role="implementer", expires_at=parent["expires_at"],
                                context=parent, sender=lambda payload: 0)
    try:
        assert propagated.context == parent and propagated.propagated is True
    finally:
        propagated.close()
    with pytest.raises(ValueError):
        module.Emitter(role="qa", expires_at=parent["expires_at"], context=parent,
                       sender=lambda payload: 0)
    with pytest.raises(ValueError):
        module.Emitter(role="implementer", expires_at=time.time() + 30, context=parent,
                       sender=lambda payload: 0)


# ---------------------------------------------------------------------------
# lifecycle phase emission
# ---------------------------------------------------------------------------

def test_governor_phase_span_schema_and_given_ids():
    module = load_efficiency()
    context = sample_context(module, "governor", trace_id="5d" * 16, parent_span_id="9a" * 8)
    packets = []
    emitter = module.Emitter(role="governor", expires_at=time.time() + 60, context=context,
                             sender=lambda payload: (packets.append(json.loads(payload)), 0)[1])
    try:
        span_id = emitter.emit_phase("task", 1_000, 2_000, outcome="ok", span_id="0123abcd4567ef89")
        assert span_id == "0123abcd4567ef89"
        second_id = emitter.emit_phase("execution", 3_000, 4_000, outcome="accepted",
                                       parent_span_id="beefbeefbeefbeef")
        assert isinstance(second_id, str) and len(second_id) == 16 and second_id.strip("0")
        assert emitter.flush(timeout=2)
        assert len(packets) == 2
        root = spans(packets[0])[0]
        assert root["name"] == "fleet.task" and root["spanId"] == "0123abcd4567ef89"
        assert root["traceId"] == "5d" * 16
        assert root["parentSpanId"] == "9a" * 8  # context parent used when none is given
        assert root["startTimeUnixNano"] == "1000" and root["endTimeUnixNano"] == "2000"
        assert root["status"]["code"] == 1
        recorded = attrs(root)
        assert recorded["hermes.role"] == "governor" and recorded["hermes.phase"] == "task"
        assert recorded["hermes.outcome"] == "ok" and recorded["hermes.attempt"] == "1"
        assert resource_attrs(packets[0])["service.name"] == module.SERVICE
        second = spans(packets[1])[0]
        assert second["parentSpanId"] == "beefbeefbeefbeef"
        assert second["status"]["code"] == 1
    finally:
        emitter.close()


def test_phase_emission_rejects_malformed_input_and_non_governor_roles():
    module = load_efficiency()
    packets = []
    emitter = module.Emitter(role="governor", expires_at=time.time() + 60,
                             sender=lambda payload: (packets.append(json.loads(payload)), 0)[1])
    try:
        assert emitter.emit_phase("unknown", 1, 2, outcome="ok") is None
        assert emitter.emit_phase("task", 1, 2, outcome="bogus") is None
        assert emitter.emit_phase("task", 1, 2, outcome="ok", attempt=True) is None
        assert emitter.emit_phase("task", True, 2, outcome="ok") is None
        assert emitter.emit_phase("task", 1, -2, outcome="ok") is None
        assert emitter.emit_phase("task", 1.5, 2, outcome="ok") is None
        assert emitter.emit_phase("task", 1, 2, outcome="ok", span_id="0" * 16) is None
        assert emitter.emit_phase("task", 1, 2, outcome="ok", span_id="AB" * 8) is None
        assert emitter.emit_phase("task", 1, 2, outcome="ok", parent_span_id="zz" * 8) is None
        assert emitter.health()["malformed"] == 9
        assert emitter.emit_phase("task", 10, 20, outcome="ok") is not None
        assert emitter.emit_phase("review", 30, 20, outcome="ok") is not None  # reversed clamp
        assert emitter.flush(timeout=2)
        assert int(spans(packets[1])[0]["endTimeUnixNano"]) == 30
        assert emitter.health()["phases_emitted"] == 2
        assert packets[0]["resourceSpans"][0]["resource"]["attributes"][1]["value"] == {
            "stringValue": "governor"}
    finally:
        emitter.close()
    for role in ("implementer", "qa"):
        observer, packets = make_observer(module, role=role)
        try:
            assert observer.emit_phase("execution", 1, 2, outcome="ok") is None
            assert observer.emit_phase("acceptance", 1, 2, outcome="accepted") is None
            assert observer.health()["malformed"] == 2
            assert observer.flush(timeout=1)
            assert packets == []
        finally:
            observer.close()


def test_phase_queue_full_drops_without_retry_and_in_flight_finishes():
    module = load_efficiency()
    context = sample_context(module, "governor")
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    def blocking_sender(payload):
        attempts.append(1)
        if len(attempts) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return 0

    emitter = module.Emitter(role="governor", expires_at=time.time() + 60, context=context,
                             sender=blocking_sender)
    try:
        assert emitter.emit_phase("task", 1, 2, outcome="ok") is not None
        assert entered.wait(timeout=2)
        # The writer is blocked inside the in-flight first request, so it cannot
        # drain the queue: emissions 2..17 fill all 16 slots deterministically and
        # the 18th emission must drop.
        results = [emitter.emit_phase("execution", 3, 4, outcome="ok") for _ in range(17)]
        assert all(result is not None for result in results[:16])
        assert results[16] is None  # queue full, no retry
        health = emitter.health()
        assert health["drops_queue"] == 1 and health["phases_emitted"] == 17
        release.set()
        assert emitter.flush(timeout=2)
        assert attempts == [1] * 17 and emitter.health()["sent"] == 17
    finally:
        release.set()
        emitter.close()


def test_phase_count_cap_is_bounded_and_visible():
    module = load_efficiency()
    packets = []
    emitter = module.Emitter(role="governor", expires_at=time.time() + 60,
                             sender=lambda payload: (packets.append(json.loads(payload)), 0)[1])
    try:
        accepted = 0
        for index in range(module.MAX_PHASES + 5):
            if emitter.emit_phase("verification", index * 2, index * 2 + 1, outcome="ok") is not None:
                accepted += 1
            if index % 16 == 15:
                assert emitter.flush(timeout=2)
        assert accepted == module.MAX_PHASES
        health = emitter.health()
        assert health["phases_emitted"] == module.MAX_PHASES and health["phases_dropped"] == 5
    finally:
        emitter.close()


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def test_http_transport_is_fixed_local_bounded_and_handles_partial_success(monkeypatch):
    module = load_efficiency()
    calls = []

    class Response:
        status = 200

        def read(self, size):
            assert size == module.MAX_RESPONSE + 1
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
    assert module.send_http(b"{}") == 1
    assert calls[0] == ("127.0.0.1", 4318, 0.4)
    assert calls[1] == ("POST", "/v1/traces", {"Content-Type": "application/json"})
    assert calls[-1] == "closed"


def test_http_request_and_response_size_caps_are_enforced(monkeypatch):
    module = load_efficiency()
    with pytest.raises(ValueError, match="request_size_cap"):
        module.send_http(b"x" * (module.MAX_BYTES + 1))

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
        module.send_http(b"{}")


def test_collector_outage_and_partial_success_are_fail_open_without_retry():
    module = load_efficiency()
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    def unavailable(payload):
        attempts.append(1)
        entered.set()
        assert release.wait(timeout=5)
        raise TimeoutError("private transport text must not reach health")

    observer = module.Observer(role="implementer", expires_at=time.time() + 60, sender=unavailable)
    try:
        success_turn(observer, "outage")
        assert entered.wait(timeout=2)
        assert observer.pre_llm_call(turn_id="still-usable") is None
        assert not observer.flush(timeout=0.01)
        release.set()
        assert observer.flush(timeout=2)
        assert attempts == [1]
        health = observer.health()
        assert health["failed"] == 1 and health["last_failure"] == "timeout" and health["sent"] == 0
        assert "private" not in json.dumps(health)
    finally:
        release.set()
        observer.close()

    observer = module.Observer(role="implementer", expires_at=time.time() + 60,
                               sender=lambda payload: 1)
    try:
        success_turn(observer, "partial")
        assert observer.flush(timeout=2)
        health = observer.health()
        assert health["sent"] == 0 and health["partial_batches"] == 1
        assert health["rejected_spans"] == 1
    finally:
        observer.close()


# ---------------------------------------------------------------------------
# turn observation
# ---------------------------------------------------------------------------

def test_success_turn_exports_root_and_children_metadata_only():
    module = load_efficiency()
    observer, packets = make_observer(module)
    private = "NEVER_EXPORT_private_prompt_path_key_and_result"
    try:
        observer.pre_llm_call(turn_id="raw-turn-id", user_message=private, prompt=private)
        observer.pre_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id",
                                 provider="ollama-cloud", model="deepseek-v4.1-flash",
                                 request={"messages": private})
        observer.post_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id",
                                  usage={"input_tokens": 12, "output_tokens": 3},
                                  response=private)
        observer.pre_tool_call(turn_id="raw-turn-id", tool_call_id="raw-tool-id", tool_name="patch")
        observer.post_tool_call(turn_id="raw-turn-id", tool_call_id="raw-tool-id", tool_name="patch",
                                api_request_id="raw-api-id", status="ok", duration_ms=5,
                                result=private, args={"path": private})
        observer.session_end(turn_id="raw-turn-id", completed=True)
        assert observer.flush(timeout=2)
        assert len(packets) == 1
        packet = packets[0]
        serialized = json.dumps(packet)
        for forbidden in (private, "raw-turn-id", "raw-api-id", "raw-tool-id"):
            assert forbidden not in serialized
        recorded = spans(packet)
        assert len(recorded) == 3  # root + one matched API + one matched tool
        assert len({span["traceId"] for span in recorded}) == 1
        assert resource_attrs(packet)["service.name"] == module.SERVICE
        assert resource_attrs(packet)["hermes.role"] == "implementer"
        root = next(span for span in recorded if span["name"] == "hermes.turn")
        children = [span for span in recorded if span is not root]
        assert all(span["parentSpanId"] == root["spanId"] for span in children)
        assert "parentSpanId" not in root  # standalone: fresh trace, no invented parent
        recorded_root = attrs(root)
        assert recorded_root["hermes.role"] == "implementer"
        assert recorded_root["hermes.cohort"] == "controlled"
        assert recorded_root["hermes.sample.policy"] == "all-within-bounds"
        assert recorded_root["hermes.coverage.complete"] is True
        assert recorded_root["hermes.outcome"] == "ok" and root["status"]["code"] == 1
        api = next(span for span in children if span["name"] == "hermes.api")
        recorded_api = attrs(api)
        assert recorded_api["hermes.model"] == "deepseek-v4.1-flash"
        assert recorded_api["hermes.provider"] == "ollama-cloud"
        assert int(recorded_api["hermes.usage.input_tokens"]) == 12
        assert int(recorded_api["hermes.usage.output_tokens"]) == 3
        assert recorded_api["hermes.status"] == "ok"
        tool = next(span for span in children if span["name"] == "hermes.tool")
        assert attrs(tool)["tool.name"] == "patch"
        assert tool["links"][0]["spanId"] == api["spanId"]
        assert tool["links"][0]["traceId"] == root["traceId"]
        assert observer.health()["sent"] == 1 and observer.health()["turns_exported"] == 1
        for name in observer.health():
            if name != "last_failure":
                assert isinstance(observer.health()[name], int)
    finally:
        observer.close()


def test_failed_and_unknown_turns_are_sampled_not_skipped():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="failed")
        observer.pre_api_request(turn_id="failed", api_request_id="api-f", model="gpt-6-astra",
                                 provider="openai-codex")
        observer.api_request_error(turn_id="failed", api_request_id="api-f", status="timeout")
        observer.pre_tool_call(turn_id="failed", tool_call_id="tool-f", tool_name="read_file")
        observer.post_tool_call(turn_id="failed", tool_call_id="tool-f", tool_name="read_file",
                                status="error", error_message="SECRET ERROR TEXT")
        observer.session_end(turn_id="failed", completed=False)
        observer.pre_llm_call(turn_id="unknown")
        observer.post_api_request(turn_id="unknown", api_request_id="absent")
        observer.session_end(turn_id="unknown")
        assert observer.flush(timeout=2)
        assert len(packets) == 2
        failed = packets[0]
        failed_serialized = json.dumps(failed)
        assert "SECRET ERROR TEXT" not in failed_serialized
        root = spans(failed)[0]
        assert attrs(root)["hermes.outcome"] == "error" and root["status"]["code"] == 2
        api = next(span for span in spans(failed) if span["name"] == "hermes.api")
        assert attrs(api)["hermes.status"] == "timeout"
        tool = next(span for span in spans(failed) if span["name"] == "hermes.tool")
        assert attrs(tool)["hermes.status"] == "error"
        assert attrs(root)["hermes.coverage.complete"] is True
        unknown_root = spans(packets[1])[0]
        assert attrs(unknown_root)["hermes.outcome"] == "unknown"
        assert unknown_root["status"]["code"] == 0
        assert attrs(unknown_root)["hermes.unmatched.count"] == "1"
        assert attrs(unknown_root)["hermes.coverage.complete"] is False
        assert observer.health()["unmatched"] == 1
    finally:
        observer.close()


def test_failed_event_overrides_completed_true():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="override")
        observer.pre_api_request(turn_id="override", api_request_id="api-o")
        observer.api_request_error(turn_id="override", api_request_id="api-o")
        observer.session_end(turn_id="override", completed=True)
        assert observer.flush(timeout=2)
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.outcome"] == "error"
    finally:
        observer.close()


def test_propagated_context_drives_trace_and_parent_for_every_turn_root():
    module = load_efficiency()
    context = sample_context(module, "implementer", trace_id="6e" * 16, parent_span_id="7f" * 8)
    observer, packets = make_observer(module, context=context)
    try:
        success_turn(observer, "propagated-a")
        success_turn(observer, "propagated-b", api_id="api-b", tool_id="tool-b")
        assert observer.flush(timeout=2)
        assert len(packets) == 2
        for packet in packets:
            recorded = spans(packet)
            root = next(span for span in recorded if span["name"] == "hermes.turn")
            assert root["traceId"] == "6e" * 16
            assert root["parentSpanId"] == "7f" * 8
            for child in recorded:
                if child is not root:
                    assert child["traceId"] == "6e" * 16
                    assert child["parentSpanId"] == root["spanId"]
        assert spans(packets[0])[0]["spanId"] != spans(packets[1])[0]["spanId"]
    finally:
        observer.close()


def test_usage_unknown_is_never_reported_as_zero():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="usage")
        observer.pre_api_request(turn_id="usage", api_request_id="api-u")
        observer.post_api_request(turn_id="usage", api_request_id="api-u",
                                  usage={"input_tokens": float("nan"), "output_tokens": True,
                                         "cache_read_tokens": 5, "total_tokens": 10 ** 10,
                                         "prompt_tokens": -1})
        observer.post_api_request(turn_id="usage", api_request_id="absent-b")
        observer.session_end(turn_id="usage", completed=True)
        assert observer.flush(timeout=2)
        recorded = attrs(next(span for span in spans(packets[0]) if span["name"] == "hermes.api"))
        assert int(recorded["hermes.usage.cache_read_tokens"]) == 5
        assert "hermes.usage.input_tokens" not in recorded
        assert "hermes.usage.output_tokens" not in recorded
        assert "hermes.usage.total_tokens" not in recorded
        assert "hermes.usage.prompt_tokens" not in recorded
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
        assert observer.health()["usage_invalid"] == 4
    finally:
        observer.close()


@pytest.mark.parametrize("end_model,expected,source,changed", [
    ("gpt-6-astra", "gpt-6-astra", "both_hooks", False),
    (None, "gpt-6-astra", "start_hook", False),
    ("gpt-6.1-sol", "unknown", "conflicting_hooks", True),
    ("private-model", "unknown", "unrecognized_hook", False),
])
def test_api_attribution_preserves_start_end(end_model, expected, source, changed):
    module = load_efficiency()
    observer, packets = make_observer(module, role="governor")
    try:
        observer.pre_llm_call(turn_id="attribution")
        observer.pre_api_request(turn_id="attribution", api_request_id="a",
                                 model="gpt-6-astra", provider="openai-codex")
        ending = {} if end_model is None else {"model": end_model, "provider": "openai-codex"}
        observer.post_api_request(turn_id="attribution", api_request_id="a", **ending)
        observer.session_end(turn_id="attribution", completed=True)
        assert observer.flush(timeout=2)
        api = attrs(next(s for s in spans(packets[0]) if s["name"] == "hermes.api"))
        assert api["hermes.role"] == "governor"
        assert api["hermes.model.start"] == "gpt-6-astra"
        assert api["hermes.model.end"] == (end_model if end_model in module.MODELS else "unknown")
        assert api["hermes.model"] == expected
        assert api["hermes.attribution.source"] == source
        assert api["hermes.attribution.changed"] is changed
        assert "private-model" not in json.dumps(packets)
    finally:
        observer.close()


@pytest.mark.parametrize("tool", ["execute_code", "browser_exec", "terminal", "mem0_search", "delegate_task"])
def test_current_tools_have_safe_labels_and_roles(tool):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="tools")
        observer.pre_tool_call(turn_id="tools", tool_call_id="t", tool_name=tool)
        observer.post_tool_call(turn_id="tools", tool_call_id="t", status="error")
        observer.session_end(turn_id="tools", completed=True)
        assert observer.flush(timeout=2)
        recorded = attrs(next(s for s in spans(packets[0]) if s["name"] == "hermes.tool"))
        assert recorded["tool.name"] == tool
        assert recorded["hermes.role"] == "implementer"
        assert recorded["hermes.status"] == "error"
    finally:
        observer.close()


def test_fallback_attempts_keep_separate_model_and_failure_evidence():
    module = load_efficiency()
    observer, packets = make_observer(module, role="governor")
    try:
        observer.pre_llm_call(turn_id="fallback")
        observer.pre_api_request(turn_id="fallback", api_request_id="primary",
                                 model="claude-opus-5-5", provider="anthropic")
        observer.api_request_error(turn_id="fallback", api_request_id="primary", status="timeout")
        observer.pre_api_request(turn_id="fallback", api_request_id="fallback",
                                 model="gpt-6-astra", provider="openai-codex")
        observer.post_api_request(turn_id="fallback", api_request_id="fallback")
        observer.session_end(turn_id="fallback", completed=True)
        assert observer.flush(timeout=2)
        calls = [attrs(s) for s in spans(packets[0]) if s["name"] == "hermes.api"]
        assert len(calls) == 2
        assert [(c["hermes.model"], c["hermes.status"]) for c in calls] == [
            ("claude-opus-5-5", "timeout"), ("gpt-6-astra", "ok")]
        assert all(c["hermes.attribution.complete"] for c in calls)
        assert all(c["hermes.role"] == "governor" for c in calls)
    finally:
        observer.close()


def test_unrecognized_model_provider_and_tool_labels_degrade_to_unknown():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="labels")
        observer.pre_api_request(turn_id="labels", api_request_id="api-l1", model="evil-model",
                                 provider="evil-provider")
        observer.post_api_request(turn_id="labels", api_request_id="api-l1")
        observer.pre_api_request(turn_id="labels", api_request_id="api-l2-before")
        observer.pre_api_request(turn_id="labels", api_request_id="api-l2", model=True,
                                 provider=["anthropic"])
        observer.post_api_request(turn_id="labels", api_request_id="api-l2", model="gpt-6.1-sol",
                                  provider="openai-codex")
        # A label test needs an actual start; unmatched completions never create spans.
        observer.pre_tool_call(turn_id="labels", tool_call_id="tool-l", tool_name="exec_shell")
        observer.post_tool_call(turn_id="labels", tool_call_id="tool-l", tool_name="exec_shell",
                                status="ok")
        observer.session_end(turn_id="labels", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        first = next(span for span in recorded if span["name"] == "hermes.api"
                     and attrs(span)["hermes.model"] == "unknown")
        assert attrs(first)["hermes.provider"] == "unknown"
        second = next(span for span in recorded if span["name"] == "hermes.api"
                      and span is not first)
        assert attrs(second)["hermes.model"] == "gpt-6.1-sol"
        assert attrs(second)["hermes.provider"] == "openai-codex"
        tool = next(span for span in recorded if span["name"] == "hermes.tool")
        assert attrs(tool)["tool.name"] == "other"
    finally:
        observer.close()


def test_duplicate_turn_ids_mark_incomplete_without_inflation():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="dup")
        observer.pre_llm_call(turn_id="dup")
        observer.post_tool_call(turn_id="dup", tool_call_id="t", tool_name="read_file", status="ok")
        observer.session_end(turn_id="dup", completed=True)
        observer.session_end(turn_id="dup", completed=True)
        assert observer.flush(timeout=2)
        assert len(packets) == 1
        health = observer.health()
        assert health["duplicates"] == 1 and health["turns_exported"] == 1
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
        assert attrs(root)["hermes.duplicates.count"] == "1"
    finally:
        observer.close()


def test_duplicate_api_and_tool_ids_never_invent_links_or_spans():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="ambiguous")
        observer.pre_api_request(turn_id="ambiguous", api_request_id="same-api")
        observer.pre_api_request(turn_id="ambiguous", api_request_id="same-api")
        observer.post_api_request(turn_id="ambiguous", api_request_id="same-api")
        observer.post_api_request(turn_id="ambiguous", api_request_id="same-api")
        observer.pre_tool_call(turn_id="ambiguous", tool_call_id="same-tool", tool_name="read_file")
        observer.pre_tool_call(turn_id="ambiguous", tool_call_id="same-tool", tool_name="read_file")
        observer.post_tool_call(turn_id="ambiguous", tool_call_id="same-tool", tool_name="read_file",
                                api_request_id="same-api", status="ok")
        observer.post_tool_call(turn_id="ambiguous", tool_call_id="same-tool", tool_name="read_file",
                                api_request_id="same-api", status="ok")
        observer.session_end(turn_id="ambiguous", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        assert len(recorded) == 1  # only the root: ambiguous events are dropped, never invented
        root = recorded[0]
        assert attrs(root)["hermes.coverage.complete"] is False
        assert attrs(root)["hermes.duplicates.count"] == "2"
        assert attrs(root)["hermes.ambiguous.count"] == "4"
        health = observer.health()
        assert health["duplicates"] == 2 and health["ambiguous_events"] == 4
    finally:
        observer.close()


def test_unmatched_completions_are_dropped_without_guessed_timing():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="unmatched")
        observer.post_api_request(turn_id="unmatched", api_request_id="api-x")
        observer.post_tool_call(turn_id="unmatched", tool_call_id="tool-x", tool_name="read_file",
                                status="ok", duration_ms=999)
        observer.session_end(turn_id="unmatched", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        assert len(recorded) == 1
        assert attrs(recorded[0])["hermes.unmatched.count"] == "2"
        assert attrs(recorded[0])["hermes.coverage.complete"] is False
        assert observer.health()["unmatched"] == 2
    finally:
        observer.close()


def test_privacy_sentinels_never_reach_packets_or_health():
    module = load_efficiency()
    observer, packets = make_observer(module)
    private = [
        "SECRET_PROMPT_TEXT", "SECRET_FILE_CONTENT", "https://private.example/SECRET_URL",
        "C:\\Users\\private\\SECRET_PATH", "SECRET_ERROR_MESSAGE", "raw-turn", "raw-api", "raw-tool",
    ]
    try:
        observer.pre_llm_call(turn_id="raw-turn", platform="cli", user_message=private[0])
        observer.pre_api_request(turn_id="raw-turn", api_request_id="raw-api", model="gpt-6-astra",
                                 provider="openai-codex", request=private[0])
        observer.post_api_request(turn_id="raw-turn", api_request_id="raw-api", response=private[1],
                                  usage={"input_tokens": 1})
        observer.pre_tool_call(turn_id="raw-turn", tool_call_id="raw-tool", tool_name="read_file",
                               args={"path": private[3]})
        observer.post_tool_call(turn_id="raw-turn", tool_call_id="raw-tool", tool_name="read_file",
                                api_request_id="raw-api", status="error",
                                error_message=private[4], result=private[1],
                                args={"url": private[2]})
        observer.api_request_error(turn_id="raw-turn", api_request_id="raw-api",
                                   error_message=private[4])
        observer.session_end(turn_id="raw-turn", completed=False)
        assert observer.flush(timeout=2)
        exported = json.dumps(packets)
        health = json.dumps(observer.health())
        for marker in private:
            assert marker not in exported and marker not in health
    finally:
        observer.close()


def test_event_cap_is_bounded_and_visible_without_false_coverage():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="long")
        for index in range(module.MAX_EVENTS + 2):
            observer.pre_api_request(turn_id="long", api_request_id=f"api-{index}")
            observer.post_api_request(turn_id="long", api_request_id=f"api-{index}")
        observer.session_end(turn_id="long", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        assert len(recorded) == module.MAX_EVENTS + 1
        root = recorded[0]
        assert attrs(root)["hermes.events.dropped"] == "2"
        assert attrs(root)["hermes.coverage.complete"] is False
        assert observer.health()["capture_dropped"] == 2
        assert len(json.dumps(packets[0]).encode()) < module.MAX_BYTES
    finally:
        observer.close()


def test_turn_export_cap_and_dedupe_saturation_are_conservative():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        for index in range(65):
            observer.pre_llm_call(turn_id=f"t{index}")
            observer.session_end(turn_id=f"t{index}", completed=True)
            if index % 16 == 15:
                assert observer.flush(timeout=2)
        assert observer.flush(timeout=2)
        health = observer.health()
        assert len(packets) == module.MAX_TURNS
        assert health["turns_exported"] == module.MAX_TURNS
        assert health["turns_dropped"] == 65 - module.MAX_TURNS
        assert health["dedupe_saturated"] == 1
        for packet in packets:
            assert attrs(spans(packet)[0])["hermes.coverage.complete"] is True
        # Corrective attempt (operator-approved, cycle 2): the bounded ended set
        # is NEVER evicted, so a forgotten ID can never be confidently reused.
        # The original premise ("t0 evicted") was invalid; these assertions are
        # stricter: a replayed completed ID is a counted duplicate/late event,
        # keeps coverage invalid and never yields a new packet or turn.
        assert len(observer.ended) == module.MAX_ENDED
        before = observer.health()
        assert before["suspect_reopen"] == 1  # from t64 at saturation
        assert before["duplicates"] == 0 and before["late_events"] == 0
        observer.pre_llm_call(turn_id="t0")
        observer.session_end(turn_id="t0", completed=True)
        assert observer.flush(timeout=2)
        replay = observer.health()
        assert replay["duplicates"] == 1
        assert replay["late_events"] == 2  # replayed start and end
        assert replay["turns_dropped"] == before["turns_dropped"] == 65 - module.MAX_TURNS
        assert replay["suspect_reopen"] == 1
        assert replay["loss_incomplete"] == 1
        assert replay["active_turns"] == 0
        assert len(packets) == module.MAX_TURNS
        # A never-seen ID after saturation is a suspected reopen and a dropped turn.
        observer.pre_llm_call(turn_id="never-seen")
        observer.session_end(turn_id="never-seen", completed=True)
        assert observer.flush(timeout=2)
        fresh = observer.health()
        assert fresh["suspect_reopen"] == 2
        assert fresh["turns_dropped"] == 65 - module.MAX_TURNS + 1
        assert len(packets) == module.MAX_TURNS
    finally:
        observer.close()


def test_expiry_stops_idle_writer_and_blocks_new_state():
    module = load_efficiency()
    observer, packets = make_observer(module, expires_in=0.15)
    try:
        observer.pre_llm_call(turn_id="expires-unclosed")
        assert observer.closed.wait(timeout=2), "Expiry must stop idle telemetry"
        observer.writer.join(timeout=2)
        assert not observer.writer.is_alive()
        assert observer.health()["active_turns"] == 0
        assert observer.pre_llm_call(turn_id="past-deadline") is None
        assert packets == []
    finally:
        observer.close()


def test_queued_payloads_are_discarded_at_expiry_but_in_flight_may_finish():
    module = load_efficiency()
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    def held_sender(payload):
        attempts.append(1)
        if len(attempts) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return 0

    observer = module.Observer(role="implementer", expires_at=time.time() + 0.6,
                               sender=held_sender)
    try:
        for index in range(5):
            observer.pre_llm_call(turn_id=f"q{index}")
            observer.session_end(turn_id=f"q{index}", completed=True)
        assert entered.wait(timeout=2)
        time.sleep(0.8)  # cross the deadline while the in-flight request is still held
        assert observer.health()["queued"] == 5 and observer.health()["sent"] == 0
        release.set()
        assert observer.closed.wait(timeout=2)
        observer.writer.join(timeout=2)
        assert not observer.writer.is_alive()
        health = observer.health()
        assert health["sent"] == 1  # in-flight request may finish after the deadline
        assert health["dropped"] == 4  # queued remainder is discarded, never retried
    finally:
        release.set()
        observer.close()


def test_backward_wall_clock_never_reverses_span_intervals(monkeypatch):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="rollback")
        observer.pre_api_request(turn_id="rollback", api_request_id="api-r")
        monkeypatch.setattr(module.time, "time_ns", lambda: 1)
        observer.post_api_request(turn_id="rollback", api_request_id="api-r")
        observer.session_end(turn_id="rollback", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        for span in recorded:
            assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
        root = next(span for span in recorded if span["name"] == "hermes.turn")
        for child in recorded:
            if child is not root:
                assert int(root["endTimeUnixNano"]) >= int(child["endTimeUnixNano"])
    finally:
        observer.close()


def test_wall_clock_rollback_cannot_extend_monotonic_activation(monkeypatch):
    module = load_efficiency()
    now = time.time()
    observer, packets = make_observer(module, expires_in=0.15)
    try:
        monkeypatch.setattr(module.time, "time", lambda: now - 86400)
        assert observer.closed.wait(timeout=2)
        observer.writer.join(timeout=2)
        assert not observer.writer.is_alive()
        assert observer.pre_llm_call(turn_id="past-deadline") is None
        assert observer.health()["active_turns"] == 0
    finally:
        observer.close()


def test_lock_contention_drops_callback_and_invalidates_coverage():
    module = load_efficiency()
    observer, packets = make_observer(module)

    class ContendedLock:
        ok = False

        def acquire(self, blocking=True):
            return self.ok

        def release(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    contended = ContendedLock()
    original = observer.lock
    try:
        observer.lock = contended
        observer.pre_llm_call(turn_id="dropped-at-lock")
        assert observer.health()["drops_lock"] == 1
        assert observer.health()["active_turns"] == 0
        observer.lock = original
        observer.pre_llm_call(turn_id="contended")
        observer.lock = contended
        observer.post_tool_call(turn_id="contended", tool_call_id="t", tool_name="read_file",
                                status="ok")
        observer.lock = original
        assert observer.health()["drops_lock"] == 2
        observer.session_end(turn_id="contended", completed=True)
        assert observer.flush(timeout=2)
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
    finally:
        observer.close()


def test_health_read_under_contention_is_side_effect_free():
    """Corrective attempt: observation must never perturb loss accounting."""
    module = load_efficiency()
    observer, packets = make_observer(module)

    Held = RejectBlockingLock  # fails if health() ever requests a blocking wait

    try:
        baseline = observer.health()
        assert baseline["loss_incomplete"] == 0 and baseline["drops_lock"] == 0
        original_lock, original_stats = observer.lock, observer.stats_lock
        observer.lock = Held()
        first = observer.health()
        observer.stats_lock = Held()
        second = observer.health()
        observer.lock, observer.stats_lock = original_lock, original_stats
        assert first["snapshot_partial"] == 1 and second["snapshot_partial"] == 1
        after = observer.health()
        assert after["drops_lock"] == 0
        assert after["loss_epoch"] == 0
        assert after["loss_incomplete"] == 0 and observer.loss_incomplete is False
        assert after["counters_best_effort"] == 0 and observer.counters_best_effort is False
        assert after["snapshot_partial"] == 0
        # A clean turn after contended reads still exports with complete coverage.
        success_turn(observer)
        assert observer.flush(timeout=2)
        assert attrs(spans(packets[0])[0])["hermes.coverage.complete"] is True
    finally:
        observer.close()


def test_callback_exception_is_counted_and_invalidates_open_turns():
    module = load_efficiency()
    observer, packets = make_observer(module)

    class ExplodingStr(str):
        def __len__(self):
            raise RuntimeError("SECRET exception text")

    try:
        observer.pre_llm_call(turn_id="bad")
        observer.pre_llm_call(turn_id=ExplodingStr("crash"))
        health = observer.health()
        assert health["callback_errors"] >= 1
        assert "SECRET" not in json.dumps(health)
        observer.session_end(turn_id="bad", completed=True)
        assert observer.flush(timeout=2)
        root = spans(packets[0])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
    finally:
        observer.close()


# ---------------------------------------------------------------------------
# plugin registration
# ---------------------------------------------------------------------------

def make_home(tmp_path, name):
    home = tmp_path / name
    home.mkdir()
    return home


def register_config(home, role, *, expires_in=120, expiry_style="aware"):
    stamp = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc)
    if expiry_style == "aware":
        expiry = stamp.isoformat()
    elif expiry_style == "z":
        expiry = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        expiry = datetime.fromtimestamp(time.time() + expires_in).isoformat()
    return {"approved_home": str(home), "expires_at_utc": expiry, "role": role}


def test_register_revokes_previous_observer_before_config_checks(monkeypatch):
    module = load_efficiency()
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    observer, packets = make_observer(module)
    module._OBSERVER = observer
    try:
        context = FakeContext()
        module.register(context)
        assert module._OBSERVER is None
        assert observer.closed.is_set()
        assert set(context.hooked) == set(module.HOOKS)
    finally:
        module._OBSERVER = None
        observer.close()


def test_register_governor_success_and_hook_profile_recheck(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    home = make_home(tmp_path, "hermes")
    packets = []
    monkeypatch.setattr(module, "send_http",
                        lambda payload: (packets.append(json.loads(payload)), 0)[1])
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
    context = FakeContext(register_config(home, "governor"))
    try:
        module.register(context)
        observer = module._OBSERVER
        assert observer is not None and observer.home == home.resolve()
        assert observer.context["role"] == "governor" and observer.propagated is False
        assert set(context.hooked) == set(module.HOOKS) and len(context.hooked) == 7
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "other")
        module.on_pre_llm_call(turn_id="must-not-capture")
        assert observer.health()["active_turns"] == 0
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
        success_turn(observer, "e2e")
        assert observer.flush(timeout=2)
        assert len(packets) == 1
        assert attrs(spans(packets[0])[0])["hermes.role"] == "governor"
    finally:
        if module._OBSERVER is not None:
            module._OBSERVER.close()
        module._OBSERVER = None


def test_register_propagation_e2e_uses_envelope_trace_and_parent(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    home = make_home(tmp_path, "implementer")
    packets = []
    monkeypatch.setattr(module, "send_http",
                        lambda payload: (packets.append(json.loads(payload)), 0)[1])
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    envelope = module.encode_context(sample_context(module, "implementer", trace_id="8a" * 16,
                                                    parent_span_id="9b" * 8))
    monkeypatch.setenv(module.CONTEXT_ENV, envelope)
    context = FakeContext(register_config(home, "implementer"))
    try:
        module.register(context)
        observer = module._OBSERVER
        assert observer is not None and observer.propagated is True
        assert observer.context["trace_id"] == "8a" * 16
        success_turn(observer, "propagated")
        assert observer.flush(timeout=2)
        root = spans(packets[0])[0]
        assert root["traceId"] == "8a" * 16 and root["parentSpanId"] == "9b" * 8
    finally:
        if module._OBSERVER is not None:
            module._OBSERVER.close()
        module._OBSERVER = None


def test_register_stays_inert_on_bad_config_context_or_profile(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    governor_home = make_home(tmp_path, "hermes")
    implementer_home = make_home(tmp_path, "implementer")
    cases = [
        (governor_home, {"approved_home": str(governor_home), "role": "governor"}, None),  # no expiry
        (governor_home, register_config(governor_home, "governor", expires_in=-5), None),  # past
        (governor_home, register_config(governor_home, "governor", expiry_style="naive"), None),
        (implementer_home, register_config(tmp_path / "hermes", "governor"), None),  # home mismatch
        (implementer_home, register_config(implementer_home, "qa"), None),  # profile/role mismatch
        (governor_home, register_config(governor_home, "governor"), "not json"),
        (implementer_home, register_config(implementer_home, "implementer"),
         module.encode_context(sample_context(module, "qa", trace_id="ab" * 16))),
    ]
    for home, config, envelope in cases:
        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda home=home: home)
        if envelope is None:
            monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
        else:
            monkeypatch.setenv(module.CONTEXT_ENV, envelope)
        context = FakeContext(config)
        module.register(context)
        assert module._OBSERVER is None, f"Expected inert registration for {config}"
        assert set(context.hooked) == set(module.HOOKS)
    module._OBSERVER = None


def test_register_never_raises_when_context_api_fails(monkeypatch):
    module = load_efficiency()

    class ExplodingContext:
        def get_config(self, key, default=""):
            raise RuntimeError("SECRET config failure")

        def register_hook(self, name, function):
            raise RuntimeError("SECRET hook failure")

        def on_unload(self, function):
            pass

    module.register(ExplodingContext())
    assert module._OBSERVER is None
    assert module.on_pre_llm_call(turn_id="inert") is None


# Repair-cycle 1 regressions: added before the implementation repair.

@pytest.mark.parametrize("version", [1.0, True, "1", None])
def test_context_version_requires_an_integer(version):
    module = load_efficiency()
    context = dict(sample_context(module), v=version)
    with pytest.raises(ValueError):
        module.encode_context(context)
    with pytest.raises(ValueError):
        module.decode_context(json.dumps(context), role="implementer",
                              expires_at=context["expires_at"])


def test_context_duplicate_json_keys_fail_closed():
    module = load_efficiency()
    context = sample_context(module)
    encoded = module.encode_context(context)
    raw = '{"v":2,' + encoded[1:]
    with pytest.raises(ValueError):
        module.decode_context(raw, role="implementer", expires_at=context["expires_at"])


@pytest.mark.parametrize("flags,expected", [
    ({"completed": True, "failed": True}, "error"),
    ({"completed": True, "interrupted": True}, "cancelled"),
    ({"completed": True}, "unknown"),
    ({"completed": "true"}, "unknown"),
    ({"completed": True, "failed": 1}, "unknown"),
    ({"completed": True, "interrupted": "false"}, "unknown"),
])
def test_finalizer_flags_and_unfinished_operations_invalidate_coverage(flags, expected):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="unfinished")
        observer.pre_api_request(turn_id="unfinished", api_request_id="open-api")
        observer.pre_tool_call(turn_id="unfinished", tool_call_id="open-tool")
        observer.session_end(turn_id="unfinished", **flags)
        assert observer.flush(timeout=2)
        root = spans(packets[0])[0]
        assert len(spans(packets[0])) == 1  # no fabricated finishes
        assert attrs(root)["hermes.outcome"] == expected
        assert attrs(root)["hermes.coverage.complete"] is False
        assert attrs(root)["hermes.unmatched.count"] == "2"
    finally:
        observer.close()


@pytest.mark.parametrize("flag", ["completed", "failed", "interrupted"])
@pytest.mark.parametrize("value", [None, 0, float("nan"), "false"])
def test_malformed_finalizer_booleans_without_opens_are_incomplete(flag, value):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="flags")
        values = {"completed": True, "failed": False, "interrupted": False, flag: value}
        observer.session_end(turn_id="flags", **values)
        assert observer.flush(timeout=2)
        root = attrs(spans(packets[0])[0])
        assert root["hermes.outcome"] == "unknown"
        assert root["hermes.coverage.complete"] is False
        assert root["hermes.malformed.count"] == "1"
        assert observer.health()["malformed"] == 1
    finally:
        observer.close()


@pytest.mark.parametrize("kind", ["api", "tool"])
def test_completed_event_replay_does_not_inflate_usage_or_retain_stale_links(kind):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="replay")
        start = observer.pre_api_request if kind == "api" else observer.pre_tool_call
        end = observer.post_api_request if kind == "api" else observer.post_tool_call
        identity = {"api_request_id" if kind == "api" else "tool_call_id": "replayed"}
        start(turn_id="replay", **identity)
        end(turn_id="replay", **identity, usage={"input_tokens": 7}, status="ok")
        if kind == "api":
            observer.pre_tool_call(turn_id="replay", tool_call_id="before-replay")
            observer.post_tool_call(turn_id="replay", tool_call_id="before-replay",
                                    api_request_id="replayed", status="ok")
        start(turn_id="replay", **identity)
        end(turn_id="replay", **identity, usage={"input_tokens": 700}, status="ok")
        if kind == "api":
            observer.pre_tool_call(turn_id="replay", tool_call_id="after-replay")
            observer.post_tool_call(turn_id="replay", tool_call_id="after-replay",
                                    api_request_id="replayed", status="ok")
        observer.session_end(turn_id="replay", completed=True)
        assert observer.flush(timeout=2)
        recorded = spans(packets[0])
        operations = [span for span in recorded if span["name"] == "hermes." + kind]
        assert len(operations) == 1
        if kind == "api":
            assert attrs(operations[0])["hermes.usage.input_tokens"] == "7"
            assert all("links" not in span for span in recorded if span["name"] == "hermes.tool")
        assert attrs(recorded[0])["hermes.coverage.complete"] is False
        assert attrs(recorded[0])["hermes.duplicates.count"] == "1"
        assert observer.health()["duplicates"] == 1
    finally:
        observer.close()


def test_completed_event_dedupe_is_bounded_and_disables_correlation_on_overflow():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="overflow")
        for index in range(module.MAX_EVENTS + 2):
            observer.pre_api_request(turn_id="overflow", api_request_id=f"a{index}")
            observer.post_api_request(turn_id="overflow", api_request_id=f"a{index}")
        state = next(iter(observer.turns.values()))
        assert len(state["completed"]) <= module.MAX_EVENTS
        assert state["correlation_disabled"] is True
        observer.pre_api_request(turn_id="overflow", api_request_id="a0")
        observer.post_api_request(turn_id="overflow", api_request_id="a0",
                                  usage={"input_tokens": 999})
        observer.session_end(turn_id="overflow", completed=True)
        assert observer.flush(timeout=2)
        assert len(spans(packets[0])) == module.MAX_EVENTS + 1
        assert not any("hermes.usage.input_tokens" in attrs(span) for span in spans(packets[0]))
        assert observer.health()["dedupe_saturated"] >= 1
    finally:
        observer.close()


@pytest.mark.parametrize("kind", ["api", "tool"])
def test_replayed_completion_is_counted_without_reopening(kind):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="double-end")
        start = observer.pre_api_request if kind == "api" else observer.pre_tool_call
        end = observer.post_api_request if kind == "api" else observer.post_tool_call
        identity = {"api_request_id" if kind == "api" else "tool_call_id": "once"}
        start(turn_id="double-end", **identity)
        end(turn_id="double-end", **identity, status="ok")
        end(turn_id="double-end", **identity, status="ok")
        observer.session_end(turn_id="double-end", completed=True)
        assert observer.flush(timeout=2)
        assert len(spans(packets[0])) == 2
        assert attrs(spans(packets[0])[0])["hermes.duplicates.count"] == "1"
        assert observer.health()["duplicates"] == 1
    finally:
        observer.close()


def test_hook_labels_are_allowlisted_at_open_not_retained_raw():
    module = load_efficiency()
    observer, packets = make_observer(module)
    private = {"SECRET": ["private"] * 1000}
    try:
        observer.pre_llm_call(turn_id="bounded-labels")
        observer.pre_api_request(turn_id="bounded-labels", api_request_id="a",
                                 model=private, provider=private)
        state = next(iter(observer.turns.values()))
        assert "SECRET" not in repr(state["opens"])
        observer.post_api_request(turn_id="bounded-labels", api_request_id="a")
        observer.session_end(turn_id="bounded-labels", completed=True)
        assert observer.flush(timeout=2)
        api = attrs(spans(packets[0])[1])
        assert api["hermes.model"] == api["hermes.provider"] == "unknown"
        assert attrs(spans(packets[0])[0])["hermes.coverage.complete"] is False
    finally:
        observer.close()


@pytest.mark.parametrize("status,expected", [("timeout", "timeout"), ("cancelled", "cancelled"),
                                             ("ok", "error"), (None, "error")])
def test_error_hook_preserves_only_fixed_failure_categories(status, expected):
    module = load_efficiency()
    observer, packets = make_observer(module)

    class PrivateException(Exception):
        def __str__(self):
            raise AssertionError("exception text must not be inspected")

    try:
        observer.pre_llm_call(turn_id="error-category")
        observer.pre_api_request(turn_id="error-category", api_request_id="a")
        observer.api_request_error(turn_id="error-category", api_request_id="a", status=status,
                                   error=PrivateException(), error_message=PrivateException())
        observer.session_end(turn_id="error-category", completed=True)
        assert observer.flush(timeout=2)
        assert attrs(spans(packets[0])[1])["hermes.status"] == expected
        assert observer.health()["callback_errors"] == 0
    finally:
        observer.close()


def test_rollback_before_child_start_uses_the_turn_anchor(monkeypatch):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="anchor")
        monkeypatch.setattr(module.time, "time_ns", lambda: 1)
        observer.pre_api_request(turn_id="anchor", api_request_id="a")
        observer.post_api_request(turn_id="anchor", api_request_id="a")
        observer.pre_tool_call(turn_id="anchor", tool_call_id="t")
        observer.post_tool_call(turn_id="anchor", tool_call_id="t", status="ok")
        observer.session_end(turn_id="anchor", completed=True)
        assert observer.flush(timeout=2)
        root, *children = spans(packets[0])
        for child in children:
            assert int(root["startTimeUnixNano"]) <= int(child["startTimeUnixNano"])
            assert int(child["endTimeUnixNano"]) <= int(root["endTimeUnixNano"])
    finally:
        observer.close()


@pytest.mark.parametrize("cls_name", ["Emitter", "Observer"])
def test_propagated_expiry_shortens_capture_and_writer_deadline(cls_name):
    module = load_efficiency()
    role = "governor" if cls_name == "Emitter" else "implementer"
    context = sample_context(module, role, expires_in=0.2)
    packets = []
    emitter = getattr(module, cls_name)(role=role, expires_at=time.time() + 60, context=context,
                                        sender=lambda payload: packets.append(payload))
    try:
        assert emitter.expires_at == context["expires_at"]
        assert emitter.closed.wait(timeout=2)
        emitter.writer.join(timeout=2)
        assert not emitter.writer.is_alive()
        assert emitter.emit_phase("task", 1, 2, outcome="ok") is None
        if cls_name == "Observer":
            emitter.pre_llm_call(turn_id="expired-envelope")
            assert emitter.health()["active_turns"] == 0
        assert packets == []
    finally:
        emitter.close()


class RejectBlockingLock:
    """A held telemetry lock that fails tests if any caller requests a wait."""
    def acquire(self, blocking=True, timeout=-1):
        assert blocking is False, "callback/close requested a blocking telemetry lock"
        return False

    def release(self):
        raise AssertionError("unacquired lock released")

    def __enter__(self):
        raise AssertionError("blocking lock context entered")

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize("path", ["start", "duplicate", "malformed", "finish", "main-contention",
                                 "phase", "ended-overflow"])
def test_callback_nested_stats_lock_never_waits_and_loss_is_sticky(path):
    module = load_efficiency()
    observer, packets = make_observer(module, role="governor")
    original_stats, original_main = observer.stats_lock, observer.lock
    try:
        observer.pre_llm_call(turn_id="held")
        if path == "ended-overflow":
            observer.ended_order.extend(str(index) for index in range(module.MAX_ENDED))
            observer.ended.update(observer.ended_order)
        observer.stats_lock = RejectBlockingLock()
        if path == "start":
            observer.pre_llm_call(turn_id="other")
        elif path == "duplicate":
            observer.pre_llm_call(turn_id="held")
        elif path == "malformed":
            observer.pre_api_request(turn_id="held", api_request_id=True)
        elif path in ("finish", "ended-overflow"):
            observer.session_end(turn_id="held", completed=True)
        elif path == "main-contention":
            observer.lock = RejectBlockingLock()
            observer.pre_llm_call(turn_id="lost")
            observer.lock = original_main
        else:
            assert observer.emit_phase("task", 1, 2, outcome="ok") is None
        observer.stats_lock = original_stats
        observer.lock = original_main
        if path == "ended-overflow":
            # Saturated turn identity storage rejects new roots rather than
            # forgetting an old ID. The contended finish is also dropped.
            assert observer.health()["loss_incomplete"] == 1
            assert observer.health()["counters_best_effort"] == 1
            assert observer._turn_dedupe_saturated is True
            return
        success_turn(observer, "after-loss")
        assert observer.flush(timeout=2)
        root = spans(packets[-1])[0]
        assert attrs(root)["hermes.coverage.complete"] is False
        assert observer.health()["loss_incomplete"] == 1
        assert observer.health()["counters_best_effort"] == 1
    finally:
        observer.stats_lock = original_stats
        observer.lock = original_main
        observer.close()


def test_queue_mutex_contention_never_blocks_callback():
    module = load_efficiency()
    entered, release = threading.Event(), threading.Event()

    def sender(payload):
        entered.set()
        assert release.wait(timeout=5)
        return 0

    observer = module.Observer(role="governor", expires_at=time.time() + 60, sender=sender)
    try:
        success_turn(observer, "in-flight")
        assert entered.wait(timeout=2)
        # Holding the real mutex in this same thread deterministically detects
        # queue.put_nowait's otherwise hidden blocking acquisition.
        with observer.queue.mutex:
            worker = threading.Thread(target=lambda: success_turn(observer, "held-queue"), daemon=True)
            worker.start()
            worker.join(timeout=0.3)
            assert not worker.is_alive(), "callback waited on queue mutex"
            assert observer.emit_phase("task", 1, 2, outcome="ok") is None
        release.set()
        assert observer.flush(timeout=2)
        assert observer.health()["loss_incomplete"] == 1
    finally:
        release.set()
        observer.close()


def test_close_never_waits_on_main_or_stats_lock():
    module = load_efficiency()
    entered, release = threading.Event(), threading.Event()

    def sender(payload):
        entered.set()
        release.wait(timeout=5)
        return 0

    observer = module.Observer(role="implementer", expires_at=time.time() + 60, sender=sender)
    original_main, original_stats = observer.lock, observer.stats_lock
    try:
        success_turn(observer, "closing")
        assert entered.wait(timeout=2)
        observer.lock = RejectBlockingLock()
        observer.stats_lock = RejectBlockingLock()
        started = time.monotonic()
        observer.close()
        # Scheduling tolerance only; implementation's join budget must be <=1s total.
        assert time.monotonic() - started < 1.2
        assert observer.closed.is_set()
    finally:
        observer.lock, observer.stats_lock = original_main, original_stats
        release.set()
        observer.writer.join(timeout=2)
        observer.close()


@pytest.mark.parametrize("loss", ["malformed", "capture_dropped", "drops_queue", "phases_dropped",
                                  "failed", "partial_batches", "rejected_spans", "drops_active"])
def test_all_loss_health_categories_invalidate_future_turn_coverage(loss):
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer._bump(loss)
        success_turn(observer, "after-export-loss")
        assert observer.flush(timeout=2)
        assert attrs(spans(packets[-1])[0])["hermes.coverage.complete"] is False
        assert observer.health()["loss_incomplete"] == 1
    finally:
        observer.close()


def test_real_partial_delivery_invalidates_future_capture():
    module = load_efficiency()
    packets = []

    def sender(payload):
        packets.append(json.loads(payload))
        return 2 if len(packets) == 1 else 0

    observer = module.Observer(role="implementer", expires_at=time.time() + 60, sender=sender)
    try:
        success_turn(observer, "partial-first")
        assert observer.flush(timeout=2)
        success_turn(observer, "partial-followup")
        assert observer.flush(timeout=2)
        assert attrs(spans(packets[-1])[0])["hermes.coverage.complete"] is False
        assert observer.health()["partial_batches"] == 1
        assert observer.health()["rejected_spans"] == 2
    finally:
        observer.close()


def test_malformed_turn_identity_is_counted_globally_and_on_open_roots():
    module = load_efficiency()
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="known")
        observer.pre_llm_call(turn_id=True, session_id="known")
        observer.session_end(turn_id="known", completed=True)
        assert observer.flush(timeout=2)
        assert observer.health()["malformed"] == 1
        root = attrs(spans(packets[0])[0])
        assert root["hermes.malformed.count"] == "1"
        assert root["hermes.coverage.complete"] is False
    finally:
        observer.close()


def test_phase_ids_reject_duplicate_self_parent_and_locally_visible_cycles():
    module = load_efficiency()
    packets = []
    emitter = module.Emitter(role="governor", expires_at=time.time() + 60,
                             sender=lambda payload: (packets.append(json.loads(payload)), 0)[1])
    a, b, c = "a1" * 8, "b2" * 8, "c3" * 8
    try:
        assert emitter.emit_phase("task", 1, 2, outcome="ok", span_id=a, parent_span_id=b) == a
        assert emitter.emit_phase("task", 1, 2, outcome="ok", span_id=a) is None
        assert emitter.emit_phase("execution", 2, 3, outcome="ok", span_id=b, parent_span_id=a) is None
        assert emitter.emit_phase("review", 3, 4, outcome="ok", span_id=c, parent_span_id=c) is None
        assert emitter.flush(timeout=2)
        assert len(packets) == 1
        assert emitter.health()["duplicates"] == 1
        assert emitter.health()["loss_incomplete"] == 1
        assert len(emitter.phase_ids) <= module.MAX_PHASES
    finally:
        emitter.close()


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 10 ** 100, -1])
def test_phase_numeric_values_fail_closed_without_serializing_oversize(value):
    module = load_efficiency()
    emitter = module.Emitter(role="governor", expires_at=time.time() + 60,
                             sender=lambda payload: 0)
    try:
        assert emitter.emit_phase("task", value, 2, outcome="ok") is None
        assert emitter.emit_phase("task", 1, value, outcome="ok") is None
        assert emitter.health()["malformed"] == 2
    finally:
        emitter.close()


@pytest.mark.parametrize("result", [True, float("nan"), float("inf"), -1, 10 ** 100,
                                    {"SECRET": "response"}])
def test_malformed_sender_rejection_counts_are_fixed_failure_not_health_payloads(result):
    module = load_efficiency()
    observer = module.Observer(role="implementer", expires_at=time.time() + 60,
                               sender=lambda payload: result)
    try:
        success_turn(observer, "bad-sender")
        assert observer.flush(timeout=2)
        health = observer.health()
        assert health["failed"] == 1 and health["last_failure"] == "invalid-response"
        assert health["loss_incomplete"] == 1
        assert "SECRET" not in json.dumps(health)
        assert all(isinstance(value, int) for key, value in health.items() if key != "last_failure")
    finally:
        observer.close()


def test_registered_callback_does_not_resolve_paths_or_import_disk(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    home = make_home(tmp_path, "implementer")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
    module.register(FakeContext(register_config(home, "implementer")))
    observer = module._OBSERVER
    assert observer is not None
    try:
        def forbidden(*args, **kwargs):
            raise AssertionError("callback attempted filesystem resolution")
        monkeypatch.setattr(module.Path, "resolve", forbidden)
        module.on_pre_llm_call(turn_id="no-disk")
        assert observer.health()["active_turns"] == 1
        module.on_session_end(turn_id="no-disk", completed=True)
        assert observer.flush(timeout=2)
        assert observer.health()["turns_exported"] == 1
    finally:
        observer.close()
        module._OBSERVER = None


# ---------------------------------------------------------------------------
# all-roles extension (lane otel-allroles-impl-20261005)
# ---------------------------------------------------------------------------

FLEET_ROLES = ("governor", "architect", "implementer", "senior_engineer", "qa", "researcher")
NON_ROLES = ("integrator", "Governor", "senior-engineer", "senior engineer", "")


def test_new_context_accepts_all_six_fleet_roles_and_rejects_non_roles():
    module = load_efficiency()
    assert module.ROLES == FLEET_ROLES
    now = time.time()
    for role in FLEET_ROLES:
        context = module.new_context(role, now + 60)
        assert context["role"] == role
        decoded = module.decode_context(module.encode_context(context), role=role,
                                        expires_at=context["expires_at"])
        assert decoded == context
    for role in NON_ROLES:
        with pytest.raises(ValueError, match="invalid_role"):
            module.new_context(role, now + 60)


@pytest.mark.parametrize("cls_name", ["Emitter", "Observer"])
def test_emitter_and_observer_accept_every_fleet_role(cls_name):
    module = load_efficiency()
    cls = getattr(module, cls_name)
    now = time.time()
    for role in FLEET_ROLES:
        instance = cls(role=role, expires_at=now + 60, sender=lambda payload: 0)
        try:
            assert instance.context["role"] == role and instance.propagated is False
        finally:
            instance.close()
    context = sample_context(module, "researcher", trace_id="cd" * 16, parent_span_id="ef" * 8)
    propagated = cls(role="researcher", expires_at=context["expires_at"], context=context,
                     sender=lambda payload: 0)
    try:
        assert propagated.context == context and propagated.propagated is True
    finally:
        propagated.close()


@pytest.mark.parametrize("cls_name", ["Emitter", "Observer"])
def test_emitter_and_observer_reject_roles_outside_the_fleet_allowlist(cls_name):
    module = load_efficiency()
    cls = getattr(module, cls_name)
    now = time.time()
    for role in NON_ROLES:
        with pytest.raises(ValueError, match="invalid_role"):
            cls(role=role, expires_at=now + 60, sender=lambda payload: 0)


def test_register_senior_engineer_activation_requires_seniorengineer_home(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
    matching = make_home(tmp_path, "seniorengineer")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: matching)
    context = FakeContext(register_config(matching, "senior_engineer"))
    try:
        module.register(context)
        observer = module._OBSERVER
        assert observer is not None
        assert observer.role == "senior_engineer" and observer.home == matching.resolve()
        assert observer.context["role"] == "senior_engineer"
        assert set(context.hooked) == set(module.HOOKS)
    finally:
        if module._OBSERVER is not None:
            module._OBSERVER.close()
        module._OBSERVER = None
    mismatched = make_home(tmp_path, "senior_engineer")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: mismatched)
    context = FakeContext(register_config(mismatched, "senior_engineer"))
    module.register(context)
    assert module._OBSERVER is None, "senior_engineer must not activate on a senior_engineer folder"
    assert set(context.hooked) == set(module.HOOKS)
    module._OBSERVER = None


@pytest.mark.parametrize("role", ["architect", "researcher"])
def test_register_activates_for_architect_and_researcher_with_matching_homes(monkeypatch, tmp_path,
                                                                              role):
    module = load_efficiency()
    import hermes_constants
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
    home = make_home(tmp_path, role)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    context = FakeContext(register_config(home, role))
    try:
        module.register(context)
        observer = module._OBSERVER
        assert observer is not None
        assert observer.role == role and observer.home == home.resolve()
        assert observer.propagated is False and observer.context["role"] == role
        assert set(context.hooked) == set(module.HOOKS)
    finally:
        if module._OBSERVER is not None:
            module._OBSERVER.close()
        module._OBSERVER = None


def test_register_stays_inert_when_architect_claims_researcher_named_home(monkeypatch, tmp_path):
    module = load_efficiency()
    import hermes_constants
    monkeypatch.setattr(module, "send_http", lambda payload: 0)
    monkeypatch.delenv(module.CONTEXT_ENV, raising=False)
    researcher_home = make_home(tmp_path, "researcher")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: researcher_home)
    context = FakeContext(register_config(researcher_home, "architect"))
    module.register(context)
    assert module._OBSERVER is None, "role architect must not activate on a researcher folder"
    assert set(context.hooked) == set(module.HOOKS)
    module._OBSERVER = None


def test_api_model_label_recognizes_gpt_6_luna_but_not_near_misses():
    module = load_efficiency()
    assert module.MODELS[-1] == "gpt-6-luna"
    observer, packets = make_observer(module)
    try:
        observer.pre_llm_call(turn_id="luna")
        observer.pre_api_request(turn_id="luna", api_request_id="api-luna", model="gpt-6-luna",
                                 provider="ollama-cloud")
        observer.post_api_request(turn_id="luna", api_request_id="api-luna")
        observer.pre_api_request(turn_id="luna", api_request_id="api-near-miss")
        observer.post_api_request(turn_id="luna", api_request_id="api-near-miss",
                                  model="gpt-6-luna-preview")
        observer.session_end(turn_id="luna", completed=True)
        assert observer.flush(timeout=2)
        api_spans = [span for span in spans(packets[0]) if span["name"] == "hermes.api"]
        assert len(api_spans) == 2
        assert attrs(api_spans[0])["hermes.model"] == "gpt-6-luna"
        assert attrs(api_spans[1])["hermes.model"] == "unknown"
    finally:
        observer.close()


# ---------------------------------------------------------------------------
# expiry extension (lane otel-expiry-extend-impl-20261005)
# ---------------------------------------------------------------------------

def test_activation_max_is_exactly_seven_days():
    """Operator-approved extension: the cap is exactly 7 days, never longer."""
    module = load_efficiency()
    assert module.ACTIVATION_MAX == 7 * 86400.0
