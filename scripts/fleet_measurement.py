#!/usr/bin/env python3
"""Explicit Governor-owned lifecycle measurement; not a new dispatch authority.

Only a bounded, read-only Implementer and QA pilot is supported here. Every
helper request passes the real ``helper_agent_router`` admission gate (which
requires a non-empty allowlist) and is recorded in the gate's spawn audit log.
This runner further narrows requests to the read-only file tools below. The
child launch toolset must resolve to a subset of the admitted allowlist, or the
spawn is refused before it starts. Existing admission, model identity and
review diversity gates still apply.
Telemetry failure never changes the underlying work outcome. The local receipt
is authoritative; OTLP spans are attribution, not an acceptance capability.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from scripts import helper_agent_router as gate
from scripts.hermes_otel_efficiency import ACTIVATION_MAX
from scripts.qa_review_driver import effective_model, parse_session_id

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_ENV = "HERMES_FLEET_TRACE_CONTEXT"
# Narrow pilot allowlist: a strict subset of the gate's READ_ONLY_TOOLSETS.
PILOT_TOOLSETS = frozenset({"read_file", "search_files"})
# Launch toolset. Re-resolved on every spawn and required to be a subset of
# the admitted allowlist (it resolves to zero tools in the installed runtime).
LAUNCH_TOOLSET = "bot_room"
MAX_DURATION_MINUTES = 480
EXPORT_TIMEOUT_CAP = 60.0
# Health keys whose non-zero value means telemetry is lossy or unverifiable.
LOSS_KEYS = ("failed", "dropped", "capture_dropped", "callback_errors", "partial_batches",
             "rejected_spans", "drops_queue", "drops_active", "drops_lock", "phases_dropped",
             "turns_dropped", "malformed", "duplicates", "loss_epoch", "ambiguous_events",
             "unmatched", "late_events", "usage_invalid", "dedupe_saturated", "suspect_reopen",
             "loss_incomplete", "counters_best_effort", "snapshot_partial")


def _run(command, *, env, timeout):
    return subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _export_model(role, session_id, timeout=EXPORT_TIMEOUT_CAP):
    # Native session metadata only; never return or export conversation content.
    with tempfile.TemporaryDirectory(prefix="fleet-model-") as directory:
        target = Path(directory) / "session.jsonl"
        done = subprocess.run(["hermes", "-p", role, "sessions", "export", str(target),
                               "--session-id", session_id], capture_output=True, timeout=timeout)
        if done.returncode != 0 or not target.is_file():
            return None
        return effective_model(target.read_text(encoding="utf-8"))


def resolve_launch_tools(names):
    """Resolve launch toolsets to concrete tool names with the installed runtime."""
    import toolsets  # Hermes runtime module; absence fails closed in the caller.
    resolved = set()
    for name in names:
        if not toolsets.validate_toolset(name):
            raise ValueError("unknown launch toolset")
        resolved.update(toolsets.resolve_toolset(name))
    return sorted(resolved)


def gate_admitter(*, project_root=None, audit_log=None):
    """Real admission gate plus the gate's own spawn audit record."""
    root = Path(project_root) if project_root is not None else ROOT
    log = Path(audit_log) if audit_log is not None else root / gate.DEFAULT_AUDIT_LOG_RELATIVE

    def admit(request):
        result = gate.admit_request(request, project_root=root)
        gate.append_spawn_event(log, gate.build_spawn_event(request, result))
        return result
    return admit


def _telemetry_ok(health, flushed, stages):
    if flushed is not True or not isinstance(health, dict):
        return False
    # Healthy must be affirmed explicitly; a missing health shape is not healthy.
    if health.get("export_healthy") != 1:
        return False
    if any(health.get(key, 0) for key in LOSS_KEYS):
        return False
    return not any(stage.get("telemetry_error") for stage in stages)


class MeasurementRun:
    """One bounded task, local ledger and asynchronous lifecycle spans.

    Do not share an instance between threads. Child env is a copy, never a
    mutation of os.environ. The role-specific opaque envelope is passed only
    to the exact subprocess, never as prompt text or global tracing baggage.
    """

    def __init__(self, *, expires_at, emitter=None, runner=None, admitter=None,
                 exporter=None, cohort="controlled", tool_resolver=None):
        if isinstance(expires_at, bool) or not isinstance(expires_at, (float, int)):
            raise ValueError("expiry must be an epoch number")
        remaining = expires_at - time.time()
        if not 0 < remaining <= ACTIVATION_MAX:
            raise ValueError("expiry must be within the maximum activation window")
        if cohort not in {"controlled", "live"}:
            raise ValueError("invalid cohort")
        owned = emitter is None
        if owned:
            from scripts.hermes_otel_efficiency import Emitter
            emitter = Emitter(role="governor", expires_at=expires_at, context=None)
        try:
            if emitter.context.get("role") != "governor":
                raise ValueError("only Governor can orchestrate acceptance")
            self.expires_at = min(expires_at, emitter.context["expires_at"])
        except Exception:
            if owned:  # never leak a started writer thread on a failed init
                emitter.close()
            raise
        self.emitter = emitter
        self.deadline = time.monotonic() + min(remaining, self.expires_at - time.time())
        self.cohort = cohort
        self.runner = runner or _run
        self.admitter = admitter or gate_admitter()
        self.exporter = exporter
        self.tool_resolver = tool_resolver or resolve_launch_tools
        self.root_id = secrets.token_hex(8)
        self.started_ns = time.time_ns()
        self.started_mono = time.monotonic_ns()
        self.stages = []
        self.receipt = None
        self.qa_result = None
        self.author_models = set()
        self.reviewer_passed = False
        self.review_evidence = None

    def _now(self):
        return self.started_ns + max(0, time.monotonic_ns() - self.started_mono)

    def _active(self):
        return self.receipt is None and time.monotonic() < self.deadline

    def _record(self, phase, start, outcome, *, span_id=None, role="governor", attempt=1, **extra):
        end = self._now()
        span_id = span_id or secrets.token_hex(8)
        stage = {"phase": phase, "role": role, "outcome": outcome, "attempt": attempt,
                 "duration_ms": round(max(0, end - start) / 1e6, 3), **extra}
        self.stages.append(stage)
        try:
            emitted = self.emitter.emit_phase(phase, start, end, outcome=outcome,
                                             span_id=span_id, parent_span_id=self.root_id, attempt=attempt)
            if emitted is None:
                stage["telemetry_error"] = True
        except Exception:
            # Observability is fail-open; never suppress the actual task result.
            stage["telemetry_error"] = True
        return stage

    @staticmethod
    def _in_pilot_scope(request):
        toolsets = request.get("allowed_toolsets")
        duration = request.get("max_duration_minutes")
        return (request.get("role") in {"implementer", "qa"}
                and request.get("mode") == "read-only"
                and request.get("allowed_writes") == []
                and isinstance(toolsets, list) and bool(toolsets)
                and all(isinstance(item, str) and item in PILOT_TOOLSETS for item in toolsets)
                and len(set(toolsets)) == len(toolsets)
                and type(duration) is int and 1 <= duration <= MAX_DURATION_MINUTES)

    def _launch_within_admission(self, admitted):
        """Resolved launch tools if they are a subset of the admitted list, else None."""
        try:
            resolved = self.tool_resolver([LAUNCH_TOOLSET])
        except Exception:
            return None
        if not isinstance(resolved, (list, tuple)) or not all(isinstance(x, str) for x in resolved):
            return None
        resolved = sorted(set(resolved))
        return resolved if set(resolved) <= set(admitted) else None

    def _actual_model(self, role, session):
        """Separate export outcome; an export failure is never a helper timeout."""
        budget = min(EXPORT_TIMEOUT_CAP, self.deadline - time.monotonic())
        if budget <= 0:
            return None, "deadline"
        try:
            if self.exporter is None:
                actual = _export_model(role, session, timeout=budget)
            else:
                actual = self.exporter(role, session)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except Exception:
            return None, "error"
        return actual, ("ok" if actual else "unavailable")

    def helper(self, request, prompt_file):
        """Gate and run a bounded read-only helper. No retry or fallback bypass."""
        if not isinstance(request, dict):
            return {"status": "rejected", "reason": "outside_inline_pilot_scope"}
        role = request.get("role")
        start = self._now()
        if not self._active():
            return {"status": "rejected", "reason": "closed_or_expired"}
        if len(self.stages) >= 32:
            return {"status": "rejected", "reason": "stage_budget"}
        # This pilot is not a generic runner for privileged jobs.
        if not self._in_pilot_scope(request):
            self._record("dispatch", start, "rejected")
            return {"status": "rejected", "reason": "outside_inline_pilot_scope"}
        try:
            admission = self.admitter(request)
        except Exception:
            admission = {"status": "rejected"}
        if not isinstance(admission, dict) or admission.get("status") != "admitted":
            self._record("dispatch", start, "rejected", role=role)
            return {"status": "rejected", "reason": "admission_gate"}
        launch_tools = self._launch_within_admission(request["allowed_toolsets"])
        if launch_tools is None:
            self._record("dispatch", start, "rejected", role=role)
            return {"status": "rejected", "reason": "launch_scope_exceeds_admission"}
        model = request.get("model", "")
        provider, separator, name = model.partition("/") if isinstance(model, str) else ("", "", "")
        if not separator or not Path(prompt_file).is_file():
            self._record("dispatch", start, "rejected", role=role)
            return {"status": "rejected", "reason": "invalid_route_or_prompt"}
        self._record("dispatch", start, "ok", role=role)
        phase = "review" if role == "qa" else "execution"
        span_id = secrets.token_hex(8)
        start = self._now()
        status, actual, stdout, session = "error", None, "", None
        returncode, export_outcome = None, "not_attempted"
        try:
            # All setup is inside the stage: any failure still ends this stage.
            from scripts.hermes_otel_efficiency import new_context, encode_context
            context = new_context(role, self.expires_at, trace_id=self.emitter.context["trace_id"],
                                  parent_span_id=span_id, cohort=self.cohort)
            env = os.environ.copy()
            env[CONTEXT_ENV] = encode_context(context)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("run deadline reached before launch")
            # Never exceeds the run deadline (no 1 s floor past it).
            timeout = min(600.0, request["max_duration_minutes"] * 60.0, remaining)
            command = ["hermes", "-p", role, "--provider", provider, "-m", name, "chat", "-Q",
                       "-t", LAUNCH_TOOLSET, "--ignore-rules",
                       "--query-file", str(Path(prompt_file).resolve()),
                       "--max-turns", "2", "--run-budget", str(max(1, int(timeout)))]
            done = self.runner(command, env=env, timeout=timeout)
            returncode = done.returncode
            stdout = done.stdout
            session = parse_session_id(done.stderr)
            if session:
                actual, export_outcome = self._actual_model(role, session)
            else:
                export_outcome = "no_session"
            status = "ok" if returncode == 0 and actual == model else "error"
            if role == "qa" and actual in self.author_models:
                status = "error"
        except subprocess.TimeoutExpired:
            status = "timeout"  # only the helper process itself can time out here
        except Exception:
            status = "error"
        attempt = 1 + sum(x["phase"] == phase for x in self.stages)
        self._record(phase, start, status, span_id=span_id, role=role, attempt=attempt,
                     returncode=returncode, export_outcome=export_outcome,
                     launch_tools=launch_tools)
        # Output remains local to the caller for verification. Never an OTLP attribute.
        result = {"status": status, "role": role, "effective_model": actual,
                  "session_id": session, "stdout": stdout, "returncode": returncode,
                  "export_outcome": export_outcome}
        if role == "qa":
            self.qa_result = result
            self.reviewer_passed = False
        elif actual:
            self.author_models.add(actual)
        return result

    def verify(self, operation: Callable[[], bool]):
        if not self._active():
            return {"status": "rejected", "reason": "closed_or_expired"}
        start = self._now()
        try:
            outcome = "ok" if operation() is True else "error"
        except Exception:
            outcome = "error"
        self._record("verification", start, outcome)
        return {"status": outcome}

    def review_verdict(self, *, passed, evidence):
        """Governor records a checked disposition; not inferred from helper exit 0."""
        if not self._active():
            return False
        valid = (passed is True and isinstance(evidence, dict) and bool(evidence)
                 and self.qa_result is not None and self.qa_result["status"] == "ok"
                 and self.qa_result["effective_model"] not in self.author_models)
        self.reviewer_passed = valid
        self.review_evidence = evidence if valid else None
        return valid

    def accept(self):
        if self.receipt is not None:
            return self.receipt
        required = {"dispatch", "execution", "verification", "review"}
        latest = {stage["phase"]: index for index, stage in enumerate(self.stages)}
        ordered = (latest.get("execution", -1) < latest.get("verification", -1)
                   < latest.get("review", -1))
        accepted = (self._active() and self.reviewer_passed
                    and required <= {s["phase"] for s in self.stages}
                    and ordered
                    and all(s["outcome"] == "ok" for s in self.stages))
        outcome = "accepted" if accepted else "rejected"
        self._record("acceptance", self._now(), outcome)
        try:
            self.emitter.emit_phase("task", self.started_ns, self._now(), outcome=outcome,
                                    span_id=self.root_id)
            flushed = self.emitter.flush(timeout=1)
            health = self.emitter.health()
        except Exception:
            flushed, health = False, {"failed": 1}
        telemetry_ok = _telemetry_ok(health, flushed, self.stages)
        self.receipt = {"schema": "fleet-measurement.v1", "accepted": accepted,
                        "cohort": self.cohort, "sampling_policy": "all-within-bounds",
                        "elapsed_ms": round((self._now() - self.started_ns) / 1e6, 3),
                        "stages": list(self.stages), "telemetry_health": health,
                        "exporter_acknowledged": bool(telemetry_ok),
                        "telemetry_delivery_complete": None if telemetry_ok else False,
                        "collector_readback_required": True,
                        "efficiency_claim_supported": False,
                        "usage_cost": None,
                        "review_evidence": self.review_evidence}
        return self.receipt

    def close(self):
        try:
            if self.receipt is None:
                self.accept()
        finally:
            self.emitter.close()
