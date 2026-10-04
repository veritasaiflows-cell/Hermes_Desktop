"""Governor lifecycle instrumentation: no helper may grant acceptance."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from scripts import fleet_measurement as fleet


class FakeEmitter:
    def __init__(self):
        self.context = {"trace_id": "a" * 32, "role": "governor", "expires_at": time.time() + 60,
                        "cohort": "controlled", "v": 1, "parent_span_id": None, "sampled": True}
        self.records = []

    def emit_phase(self, phase, start_ns, end_ns, **values):
        self.records.append((phase, values["outcome"], values))
        return values.get("span_id")

    def health(self):
        return {"failed": 0, "dropped": 0}

    def flush(self, timeout=1):
        return True

    def close(self):
        pass


def request(role="implementer"):
    return {"schema": "helper-agent-request.v1", "task_id": "unit-helper", "role": role,
            "model": "ollama-cloud/deepseek-v4.1-flash" if role == "implementer" else "openai-codex/gpt-6.1-sol",
            "mode": "read-only", "task_class": "documentation" if role == "implementer" else "review",
            "phase": "pre-implementation", "owner": "unit", "allowed_toolsets": ["read_file"],
            "allowed_writes": [], "max_duration_minutes": 1,
            "objective": "synthetic probe", "scope": "inline only"}


def no_tools(names):
    """Deterministic launch resolver: bot_room resolves to zero tools."""
    return [] if list(names) == [fleet.LAUNCH_TOOLSET] else ["read_file"]


def make(tmp_path, runner=None, admission=None, exporter=None, resolver=no_tools):
    return fleet.MeasurementRun(expires_at=time.time() + 60, emitter=FakeEmitter(),
                                runner=runner, admitter=admission or (lambda r: {"status": "admitted", "reasons": []}),
                                exporter=exporter or (lambda role, sid: "ollama-cloud/deepseek-v4.1-flash" if role == "implementer" else "openai-codex/gpt-6.1-sol"),
                                tool_resolver=resolver)


def prompt(tmp_path):
    p = tmp_path / "prompt.txt"
    p.write_text("Synthetic bounded task", encoding="utf-8")
    return p


def success(command, *, env, timeout):
    return subprocess.CompletedProcess(command, 0, "PASS", "session_id: unit-session\n")


def test_gate_rejection_does_not_spawn_and_cannot_accept(tmp_path):
    called = []
    run = make(tmp_path, runner=lambda *a, **k: called.append(True),
               admission=lambda r: {"status": "rejected", "reasons": ["out-of-lane"]})
    result = run.helper(request(), prompt(tmp_path))
    assert result["status"] == "rejected" and called == []
    assert run.accept()["accepted"] is False
    assert ("dispatch", "rejected") in [(p, o) for p, o, _ in run.emitter.records]


def test_linked_child_env_is_local_and_no_baggage(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FLEET_TRACE_CONTEXT", "do-not-inherit")
    observed = []
    def runner(command, *, env, timeout):
        context = json.loads(env["HERMES_FLEET_TRACE_CONTEXT"])
        observed.append(context)
        assert context["trace_id"] == "a" * 32 and context["role"] == "implementer"
        assert context["parent_span_id"]
        assert "unit-helper" not in json.dumps(context)
        return success(command, env=env, timeout=timeout)
    run = make(tmp_path, runner=runner)
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    import os
    assert os.environ["HERMES_FLEET_TRACE_CONTEXT"] == "do-not-inherit"
    assert len(observed) == 1


def test_process_success_is_not_acceptance_and_missing_review_fails(tmp_path):
    run = make(tmp_path, runner=success)
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    assert run.verify(lambda: True)["status"] == "ok"
    assert not run.accept()["accepted"]
    assert run.helper(request("qa"), prompt(tmp_path))["status"] == "rejected", "acceptance closes a run"


def test_full_path_requires_explicit_review_verdict_and_verification(tmp_path):
    run = make(tmp_path, runner=success)
    run.helper(request(), prompt(tmp_path))
    run.verify(lambda: True)
    qa = run.helper(request("qa"), prompt(tmp_path))
    assert qa["effective_model"] == "openai-codex/gpt-6.1-sol"
    run.review_verdict(passed=True, evidence={"source": "governor-checked-qa"})
    receipt = run.accept()
    assert receipt["accepted"] is True
    phases = [p for p, _, _ in run.emitter.records]
    assert {"dispatch", "execution", "verification", "review", "acceptance", "task"} <= set(phases)
    assert run.accept() == receipt, "duplicate close never emits a second acceptance"


def test_timeout_and_verification_exception_produce_failed_stages(tmp_path):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("helper", 1)
    run = make(tmp_path, runner=timeout)
    assert run.helper(request(), prompt(tmp_path))["status"] == "timeout"
    def failed():
        raise ValueError("PRIVATE_ERROR")
    assert run.verify(failed)["status"] == "error"
    result = run.accept()
    assert result["accepted"] is False
    assert "PRIVATE_ERROR" not in json.dumps(result)
    assert ("execution", "timeout") in [(p, o) for p, o, _ in run.emitter.records]


@pytest.mark.parametrize("effective", [None, "openai-codex/gpt-6-astra"])
def test_unknown_or_unexpected_actual_model_fails_closed(tmp_path, effective):
    run = make(tmp_path, runner=success, exporter=lambda role, sid: effective)
    assert run.helper(request(), prompt(tmp_path))["status"] == "error"
    assert not run.accept()["accepted"]


def test_no_phase_after_expiry_and_no_helper_in_other_roles(tmp_path):
    run = make(tmp_path, runner=success)
    bad = request()
    bad["role"] = "researcher"
    assert run.helper(bad, prompt(tmp_path))["status"] == "rejected"
    run.deadline = time.monotonic() - 1
    assert run.helper(request(), prompt(tmp_path))["status"] == "rejected"


def test_transport_health_loss_marks_telemetry_incomplete_not_work_failed(tmp_path):
    run = make(tmp_path, runner=success)
    run.helper(request(), prompt(tmp_path))
    run.verify(lambda: True)
    run.helper(request("qa"), prompt(tmp_path))
    run.review_verdict(passed=True, evidence={"source": "local-proof"})
    run.emitter.health = lambda: {"failed": 1, "dropped": 2}
    receipt = run.accept()
    assert receipt["accepted"] is True
    assert receipt["telemetry_delivery_complete"] is False
    assert receipt["efficiency_claim_supported"] is False


@pytest.mark.parametrize("counter", ["partial_batches", "rejected_spans", "drops_queue", "phases_dropped", "turns_dropped", "malformed", "duplicates", "loss_epoch"])
def test_export_health_never_hides_nonstandard_loss_counters(tmp_path, counter):
    # export_healthy=1 so the counter itself is the deciding factor (QA note).
    clean = make(tmp_path, runner=success)
    clean.emitter.health = lambda: {"export_healthy": 1, counter: 0}
    assert clean.accept()["exporter_acknowledged"] is True
    run = make(tmp_path, runner=success)
    run.emitter.health = lambda: {"export_healthy": 1, counter: 1}
    assert run.accept()["exporter_acknowledged"] is False


def test_false_truthy_verification_is_not_pass(tmp_path):
    run = make(tmp_path, runner=success)
    assert run.verify(lambda: {"failed": True})["status"] == "error"


def test_verification_before_implementation_cannot_support_acceptance(tmp_path):
    run = make(tmp_path, runner=success)
    run.verify(lambda: True)
    # Feed a ledger with a stale verification; no dependency on a provider.
    run.stages.extend([{"phase": "dispatch", "outcome": "ok"},
                       {"phase": "execution", "outcome": "ok"},
                       {"phase": "review", "outcome": "ok"}])
    run.reviewer_passed = True
    assert not run.accept()["accepted"]


def test_readback_rejects_truncation_unrelated_data_and_duplicate_spans(tmp_path):
    from scripts import verify_fleet_measurement as check
    trace = "b" * 32
    item = {"traceId": trace, "spanId": "c" * 16, "name": "fleet.task",
            "startTimeUnixNano": "1", "endTimeUnixNano": "2", "attributes": []}
    packet = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "hermes-fleet-efficiency"}}]},
                                 "scopeSpans": [{"spans": [item, item]}]}]}
    p = tmp_path / "traces.jsonl"
    p.write_text(json.dumps(packet) + "\n", encoding="utf-8")
    report = check.readback(p, offset=0, trace_ids={trace})
    assert report["duplicate_spans"] == 1 and not report["complete"]
    report = check.readback(p, offset=0, trace_ids={trace}, max_bytes=2)
    assert not report["complete"] and report["truncated"]


def test_efficiency_mode_routes_to_adjacent_module(tmp_path):
    import importlib.util
    source = Path(__file__).resolve().parents[1] / "scripts/hermes_otel_pilot.py"
    target = tmp_path / "__init__.py"
    target.write_bytes(source.read_bytes())
    (tmp_path / "hermes_otel_efficiency.py").write_text(
        "_OBSERVER = None\ndef register(ctx):\n    global _OBSERVER\n    _OBSERVER = 'adjacent-module'\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("test_mode_router", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Context:
        def get_config(self, key, default=None):
            return "efficiency" if key == "mode" else default
        def register_hook(self, *args):
            pass
    module.register(Context())
    assert module._OBSERVER == "adjacent-module"


def test_readback_usage_is_observed_numeric_not_cost_or_invented_zero(tmp_path):
    from scripts.verify_fleet_measurement import readback
    def attr(key, value):
        return {"key": key, "value": value}
    spans = [{"traceId": "d" * 32, "spanId": f"{n:016x}", "name": "hermes.api",
              "startTimeUnixNano": "10", "endTimeUnixNano": "20", "attributes": attrs}
             for n, attrs in enumerate([
                 [attr("hermes.usage.input_tokens", {"intValue": "5"}), attr("hermes.status", {"stringValue": "ok"})],
                 [attr("hermes.usage.input_tokens", {"intValue": "NaN"}), attr("hermes.status", {"stringValue": "timeout"})],
                 []], 1)]
    packet = {"resourceSpans": [{"resource": {"attributes": [
        attr("service.name", {"stringValue": "hermes-fleet-efficiency"}),
        attr("hermes.role", {"stringValue": "implementer"})]}, "scopeSpans": [{"spans": spans}]}]}
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps(packet) + "\n", encoding="utf-8")
    report = readback(path, offset=0, trace_ids={"d" * 32})
    assert report["observed_usage"]["implementer"]["input_tokens"] == 5
    assert "output_tokens" not in report["observed_usage"]["implementer"]
    assert report["api_spans_without_usage"] == 2
    assert report["invalid_usage_values"] == 1
    assert report["outcomes"]["timeout"] == 1
    assert report["cost_usd"] is None


# --- Corrective attempt (operator-approved post-escalation, cycle 2) ---------
# Real admission gate, launch-scope binding, lifecycle completion and
# loss-aware reporting. No injected admitter in the real-gate tests.

import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def gate_root(tmp_path):
    """Isolated copy of the authoritative registry and lane register."""
    root = tmp_path / "gate-root"
    (root / "state").mkdir(parents=True)
    shutil.copy2(ROOT / "state" / "fleet-role-registry.json", root / "state" / "fleet-role-registry.json")
    register_path = ROOT / "state" / "concurrent-lane-register.sqlite"
    if not register_path.is_file():
        pytest.skip("local lane register absent (untracked runtime state)")
    register = register_path.as_posix()
    source = sqlite3.connect(f"file:{register}?mode=ro", uri=True)
    target = sqlite3.connect(str(root / "state" / "concurrent-lane-register.sqlite"))
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    return root


def qa_request():
    item = request("qa")
    item.update(model="anthropic/claude-sonnet-5-5", reviewer_model="anthropic/claude-sonnet-5-5",
                reviews_lane="otel-efficiency-repair1-2026-10-04",
                allowed_toolsets=["read_file", "search_files"])
    return item


def real_run(tmp_path, runner=success, exporter=None, resolver=no_tools):
    root = gate_root(tmp_path)
    audit = tmp_path / "spawns.jsonl"
    run = fleet.MeasurementRun(
        expires_at=time.time() + 60, emitter=FakeEmitter(), runner=runner,
        admitter=fleet.gate_admitter(project_root=root, audit_log=audit),
        exporter=exporter or (lambda role, sid: "ollama-cloud/deepseek-v4.1-flash" if role == "implementer"
                              else "anthropic/claude-sonnet-5-5"),
        tool_resolver=resolver)
    return run, audit


def test_real_gate_admits_narrow_read_only_allowlist_and_audits(tmp_path):
    seen = []

    def runner(command, *, env, timeout):
        seen.append(command)
        return success(command, env=env, timeout=timeout)
    run, audit = real_run(tmp_path, runner=runner)
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [e["status"] for e in events] == ["admitted"]
    assert events[0]["allowed_toolsets"] == ["read_file"]
    command = seen[0]
    assert command[command.index("-t") + 1] == fleet.LAUNCH_TOOLSET


def test_real_gate_contract_rejects_empty_allowlist():
    """The real gate requires a non-empty allowlist (root cause of the QA finding)."""
    bad = request()
    bad["allowed_toolsets"] = []
    verdict = fleet.gate.admit_request(bad, project_root=ROOT)
    assert verdict["status"] == "rejected"
    assert any("non-empty" in reason for reason in verdict["reasons"])
    assert fleet.gate.admit_request(request(), project_root=ROOT)["status"] == "admitted"


def test_real_gate_rejection_is_audited_and_never_spawns(tmp_path):
    called = []
    run, audit = real_run(tmp_path, runner=lambda *a, **k: called.append(1))
    bad = request()
    bad["model"] = "openai-codex/gpt-6-astra"  # in pilot scope, but not the role binding
    result = run.helper(bad, prompt(tmp_path))
    assert result == {"status": "rejected", "reason": "admission_gate"} and called == []
    event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert event["status"] == "rejected" and event["reasons"]


def test_empty_allowlist_never_reaches_spawn(tmp_path):
    called = []
    run, audit = real_run(tmp_path, runner=lambda *a, **k: called.append(1))
    bad = request()
    bad["allowed_toolsets"] = []
    assert run.helper(bad, prompt(tmp_path))["status"] == "rejected" and called == []


@pytest.mark.parametrize("toolsets", [["write_file"], ["terminal"], ["read_file", "patch"], ["bot_room"],
                                      "read_file", [1]])
def test_runner_refuses_non_read_only_toolsets_before_gate(tmp_path, toolsets):
    gated = []
    run = make(tmp_path, runner=success, admission=lambda r: gated.append(r) or {"status": "admitted"})
    bad = request()
    bad["allowed_toolsets"] = toolsets
    assert run.helper(bad, prompt(tmp_path)) == {"status": "rejected", "reason": "outside_inline_pilot_scope"}
    assert gated == []


def test_real_gate_qa_review_with_model_diversity(tmp_path):
    run, audit = real_run(tmp_path)
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    run.verify(lambda: True)
    qa = run.helper(qa_request(), prompt(tmp_path))
    assert qa["status"] == "ok" and qa["effective_model"] == "anthropic/claude-sonnet-5-5"
    statuses = [json.loads(line)["status"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert statuses == ["admitted", "admitted"]


@pytest.mark.parametrize("resolved", ["extra", "error"])
def test_launch_tools_must_be_subset_of_admitted(tmp_path, resolved):
    called = []

    def resolver(names):
        if resolved == "error":
            raise RuntimeError("resolver unavailable")
        return ["read_file", "terminal"]
    run = make(tmp_path, runner=lambda *a, **k: called.append(1), resolver=resolver)
    result = run.helper(request(), prompt(tmp_path))
    assert result == {"status": "rejected", "reason": "launch_scope_exceeds_admission"}
    assert called == []
    assert ("dispatch", "rejected") in [(p, o) for p, o, _ in run.emitter.records]


def test_launch_tools_equal_to_admitted_are_allowed(tmp_path):
    run = make(tmp_path, runner=success, resolver=lambda names: ["read_file"])
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    assert run.stages[-1]["launch_tools"] == ["read_file"]


def test_default_resolver_uses_installed_runtime_and_bot_room_is_empty():
    pytest.importorskip("toolsets")
    assert fleet.resolve_launch_tools([fleet.LAUNCH_TOOLSET]) == []


def test_default_admitter_is_the_real_gate(tmp_path, monkeypatch):
    root = gate_root(tmp_path)
    monkeypatch.setattr(fleet, "ROOT", root)
    run = fleet.MeasurementRun(expires_at=time.time() + 60, emitter=FakeEmitter(), runner=success,
                               exporter=lambda role, sid: "ollama-cloud/deepseek-v4.1-flash",
                               tool_resolver=no_tools)
    bad = request()
    bad["model"] = "openai-codex/gpt-6-astra"
    assert run.helper(bad, prompt(tmp_path))["reason"] == "admission_gate"
    assert run.helper(request(), prompt(tmp_path))["status"] == "ok"
    lines = (root / "state" / "helper-agent-spawns.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["status"] for line in lines] == ["rejected", "admitted"]


def test_setup_failure_after_dispatch_records_execution_error(tmp_path, monkeypatch):
    import scripts.hermes_otel_efficiency as efficiency

    def explode(*a, **k):
        raise ValueError("context expired")
    monkeypatch.setattr(efficiency, "new_context", explode)
    called = []
    run = make(tmp_path, runner=lambda *a, **k: called.append(1))
    result = run.helper(request(), prompt(tmp_path))
    assert result["status"] == "error" and called == []
    phases = [(p, o) for p, o, _ in run.emitter.records]
    assert ("dispatch", "ok") in phases and ("execution", "error") in phases


@pytest.mark.parametrize("duration", ["1", 1.5, True, 0, None, 481])
def test_invalid_duration_is_rejected_not_coerced(tmp_path, duration):
    called = []
    run = make(tmp_path, runner=lambda *a, **k: called.append(1))
    bad = request()
    bad["max_duration_minutes"] = duration
    assert run.helper(bad, prompt(tmp_path))["status"] == "rejected"
    assert called == []


def test_child_timeout_never_exceeds_run_deadline(tmp_path):
    seen = []

    def runner(command, *, env, timeout):
        seen.append(timeout)
        return success(command, env=env, timeout=timeout)
    run = make(tmp_path, runner=runner)
    run.deadline = time.monotonic() + 0.5
    result = run.helper(request(), prompt(tmp_path))
    assert result["status"] == "ok"
    assert len(seen) == 1 and 0 < seen[0] <= 0.5, "child budget is bounded by the run deadline"


def test_export_timeout_is_not_a_helper_timeout_and_exit_is_recorded(tmp_path):
    def exporter(role, sid):
        raise subprocess.TimeoutExpired("export", 1)
    run = make(tmp_path, runner=success, exporter=exporter)
    result = run.helper(request(), prompt(tmp_path))
    assert result["status"] == "error"
    assert result["returncode"] == 0
    assert result["export_outcome"] == "timeout"
    stage = run.stages[-1]
    assert stage["phase"] == "execution" and stage["outcome"] == "error"
    assert stage["returncode"] == 0 and stage["export_outcome"] == "timeout"


def test_helper_timeout_records_no_returncode(tmp_path):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("helper", 1)
    run = make(tmp_path, runner=timeout)
    result = run.helper(request(), prompt(tmp_path))
    assert result["status"] == "timeout" and result["returncode"] is None
    assert result["export_outcome"] == "not_attempted"


@pytest.mark.parametrize("flag", ["loss_incomplete", "counters_best_effort", "snapshot_partial", "suspect_reopen"])
def test_receipt_honors_sticky_loss_flags(tmp_path, flag):
    clean = make(tmp_path, runner=success)
    clean.emitter.health = lambda: {"failed": 0, flag: 0, "export_healthy": 1}
    assert clean.accept()["exporter_acknowledged"] is True
    run = make(tmp_path, runner=success)
    run.emitter.health = lambda: {"failed": 0, flag: 1, "export_healthy": 1}
    receipt = run.accept()
    assert receipt["exporter_acknowledged"] is False
    assert receipt["telemetry_delivery_complete"] is False


def test_receipt_requires_export_healthy_explicitly(tmp_path):
    run = make(tmp_path, runner=success)
    run.emitter.health = lambda: {"export_healthy": 0}
    assert run.accept()["exporter_acknowledged"] is False
    run2 = make(tmp_path, runner=success)
    run2.emitter.health = lambda: {}
    assert run2.accept()["exporter_acknowledged"] is False, "missing health shape is not healthy"


def test_receipt_with_real_emitter_health_shape(tmp_path):
    from scripts.hermes_otel_efficiency import Emitter
    emitter = Emitter(role="governor", expires_at=time.time() + 60, context=None,
                      sender=lambda payload: 0)
    run = fleet.MeasurementRun(expires_at=time.time() + 60, emitter=emitter, runner=success,
                               admitter=lambda r: {"status": "admitted"},
                               exporter=lambda role, sid: None, tool_resolver=no_tools)
    try:
        run.verify(lambda: True)
        clean = run.accept()
        assert {"loss_incomplete", "counters_best_effort", "export_healthy",
                "snapshot_partial"} <= set(clean["telemetry_health"])
        assert clean["exporter_acknowledged"] is True, clean["telemetry_health"]
    finally:
        run.close()
    emitter2 = Emitter(role="governor", expires_at=time.time() + 60, context=None,
                       sender=lambda payload: 0)
    run2 = fleet.MeasurementRun(expires_at=time.time() + 60, emitter=emitter2, runner=success,
                                admitter=lambda r: {"status": "admitted"},
                                exporter=lambda role, sid: None, tool_resolver=no_tools)
    try:
        emitter2.loss_incomplete = True  # sticky flag without any counter
        receipt = run2.accept()
        assert receipt["exporter_acknowledged"] is False
        assert receipt["telemetry_health"]["loss_incomplete"] == 1
    finally:
        run2.close()


def _packet(trace, spans):
    return {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": "hermes-fleet-efficiency"}},
        {"key": "hermes.role", "value": {"stringValue": "governor"}}]},
        "scopeSpans": [{"spans": spans}]}]}


def _write_trace(tmp_path, trace, attributes, name="fleet.task"):
    span = {"traceId": trace, "spanId": "1" * 16, "name": name,
            "startTimeUnixNano": "1", "endTimeUnixNano": "2", "attributes": attributes}
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(_packet(trace, [span])) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("key,value", [("hermes.loss.incomplete", True),
                                       ("hermes.counters.best_effort", True),
                                       ("hermes.export.healthy", False)])
def test_readback_counts_loss_flags_on_any_span(tmp_path, key, value):
    from scripts.verify_fleet_measurement import readback
    trace = "e" * 32
    path = _write_trace(tmp_path, trace, [{"key": key, "value": {"boolValue": value}}])
    report = readback(path, offset=0, trace_ids={trace})
    assert report["loss_flagged_spans"] == 1
    assert report["complete"] is False


def test_readback_clean_flags_remain_complete(tmp_path):
    from scripts.verify_fleet_measurement import readback
    trace = "e" * 32
    path = _write_trace(tmp_path, trace, [
        {"key": "hermes.loss.incomplete", "value": {"boolValue": False}},
        {"key": "hermes.counters.best_effort", "value": {"boolValue": False}},
        {"key": "hermes.export.healthy", "value": {"boolValue": True}}])
    report = readback(path, offset=0, trace_ids={trace})
    assert report["loss_flagged_spans"] == 0 and report["complete"] is True


def test_readback_cli_requires_expected_names_and_roles(tmp_path, capsys):
    from scripts import verify_fleet_measurement as check
    trace = "c" * 32
    path = _write_trace(tmp_path, trace, [])
    base = ["--trace-file", str(path), "--offset", "0", "--trace-id", trace]
    with pytest.raises(SystemExit):
        check.main(base)  # expectations are mandatory, not optional
    assert check.main(base + ["--expect-name", "fleet.task", "--expect-role", "governor"]) == 0
    assert check.main(base + ["--expect-name", "fleet.review", "--expect-role", "governor"]) == 1
    assert check.main(base + ["--expect-name", "fleet.task", "--expect-role", "qa"]) == 1
    capsys.readouterr()
