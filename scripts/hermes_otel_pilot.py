"""Bounded metadata-only OTLP/HTTP observer; no provider or tool behavior changes.

The same source is deployed as a default-profile plugin __init__.py. No SDK,
new database, raw payload capture, proxy, redirect, retry or remote endpoint.
"""
from __future__ import annotations

import atexit
import hashlib
import hmac
import http.client
import json
import math
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "hermes-otel-pilot"
TARGET = "read_file_missing_path"
MAX_EVENTS = 64
MAX_ACTIVE = 8
MAX_TRACES = 4
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 4096
HTTP_TIMEOUT = 0.4
HOOKS = (
    "pre_llm_call", "pre_api_request", "post_api_request", "api_request_error",
    "pre_tool_call", "post_tool_call", "on_session_end",
)


def _attribute(key, value):
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    else:
        encoded = {"stringValue": value}
    return {"key": key, "value": encoded}


def _duration_ns(value, scale):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(value):
        return 0
    return int(min(86400 * 1_000_000_000, max(0, value * scale)))


def send_http(payload):
    """Single fixed-loopback HTTP attempt; no ambient proxies or redirects."""
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("request_size_cap")
    connection = http.client.HTTPConnection("127.0.0.1", 4318, timeout=HTTP_TIMEOUT)
    try:
        connection.request("POST", "/v1/traces", payload, {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("otlp_response_failed")
        result = json.loads(body or b"{}")
        partial = result.get("partialSuccess") or {}
        rejected = int(partial.get("rejectedSpans", 0))
        if rejected < 0:
            raise ValueError("invalid_rejection_count")
        return rejected
    finally:
        connection.close()


class Observer:
    """One bounded observer instance. Network I/O occurs only on its writer."""

    def __init__(self, *, expires_at, sender=send_http, cohort="live"):
        now = time.time()
        if not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
            raise ValueError("invalid_expiry")
        if expires_at <= now or expires_at > now + 86400:
            raise ValueError("expiry_out_of_bounds")
        if cohort not in {"live", "controlled-tool-canary"}:
            raise ValueError("invalid_cohort")
        self.expires_at = expires_at
        self.deadline = time.monotonic() + (expires_at - now)
        self.sender = sender
        self.cohort = cohort
        self.home = None
        self.lock = threading.RLock()
        self.salt = os.urandom(32)
        self.turns = {}
        self.closed_turns = deque(maxlen=128)
        self.closed = threading.Event()
        self.queue = queue.Queue(maxsize=4)
        self.counters = dict(queued=0, sent=0, failed=0, rejected_spans=0,
                             partial_batches=0, dropped=0, capture_dropped=0, callback_errors=0,
                             openings_dropped=0, openings_ambiguous=0, last_failure="none")
        self.trace_budget = 0
        self.writer = threading.Thread(target=self._write, name="hermes-otel-pilot", daemon=True)
        self.writer.start()

    def _key(self, value):
        if not isinstance(value, str) or not value or len(value) > 512:
            return None
        return hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _active(self):
        return not self.closed.is_set() and time.monotonic() < self.deadline

    def _capture(self, kind, phase, values):
        try:
            with self.lock:
                if not self._active():
                    return
                key = self._key(values.get("turn_id"))
                if key is None or key in self.closed_turns:
                    return
                if kind == "turn" and phase == "start":
                    if key not in self.turns:
                        if len(self.turns) >= MAX_ACTIVE:
                            self.counters["capture_dropped"] += 1
                            return
                        self.turns[key] = dict(trace=os.urandom(16).hex(), root=os.urandom(8).hex(),
                                               started=time.time_ns(), events=[], opens={}, api={},
                                               sequence=0, target_count=0, dropped=0, openings_dropped=0,
                                               openings_ambiguous=0, ambiguous=set())
                    return
                state = self.turns.get(key)
                if state is None:
                    return
                if kind == "turn":
                    self.turns.pop(key)
                    self.closed_turns.append(key)
                    self._finish(state)
                    return
                raw_id = values.get("api_request_id" if kind == "api" else "tool_call_id")
                identity = (kind, self._key(raw_id))
                if phase == "start":
                    if state.get("correlation_disabled"):
                        state["openings_dropped"] += 1
                        self.counters["openings_dropped"] += 1
                        return
                    if identity[1] is None or identity in state["opens"] or identity in state["ambiguous"]:
                        state["opens"].pop(identity, None)
                        state["api"].pop(identity[1], None)
                        if identity[1] is not None and len(state["ambiguous"]) < MAX_EVENTS:
                            state["ambiguous"].add(identity)
                        elif identity[1] is not None:
                            state["correlation_disabled"] = True
                            state["opens"].clear()
                            state["api"].clear()
                        state["openings_ambiguous"] += 1
                        self.counters["openings_ambiguous"] += 1
                    elif len(state["opens"]) < MAX_EVENTS:
                        state["opens"][identity] = (time.time_ns(), time.monotonic_ns())
                    else:
                        state["openings_dropped"] += 1
                        self.counters["openings_dropped"] += 1
                        self.counters["capture_dropped"] += 1
                    return
                ended = time.time_ns()
                opening = state["opens"].pop(identity, None)
                if opening is not None:
                    started = opening[0]
                    ended = started + max(0, time.monotonic_ns() - opening[1])
                    timing = "observer"
                else:
                    duration = values.get("api_duration" if kind == "api" else "duration_ms")
                    started = max(state["started"], ended - _duration_ns(duration, 1e9 if kind == "api" else 1e6))
                    timing = "reported-duration"
                status = "error" if phase == "error" else values.get("status", "ok")
                status = status if status in {"ok", "error", "blocked", "cancelled", "timeout"} else "unknown"
                tool = values.get("tool_name")
                tool = tool if tool in {"read_file", "search_files"} else "other"
                category = "none"
                if status != "ok":
                    message = values.get("error_message")
                    message = message[:2048].lower() if isinstance(message, str) else ""
                    if kind == "tool" and tool == "read_file" and status == "error" and any(
                        marker in message for marker in ("not found", "no such file")
                    ):
                        category = TARGET
                        state["target_count"] += 1
                    else:
                        category = "api_error" if kind == "api" else "tool_error"
                state["sequence"] += 1
                if len(state["events"]) >= MAX_EVENTS:
                    state["dropped"] += 1
                    self.counters["capture_dropped"] += 1
                    return
                attributes = [
                    _attribute("hermes.sequence", state["sequence"]),
                    _attribute("hermes.operation", kind), _attribute("hermes.status", status),
                    _attribute("hermes.timing.source", timing), _attribute("error.category", category),
                ]
                span = dict(traceId=state["trace"], spanId=os.urandom(8).hex(),
                            parentSpanId=state["root"], name="hermes." + kind,
                            kind=3 if kind == "api" else 1, startTimeUnixNano=str(started),
                            endTimeUnixNano=str(max(started, ended)), attributes=attributes,
                            status={"code": 1 if status == "ok" else 2})
                if kind == "tool":
                    attributes.append(_attribute("tool.name", tool))
                    request_key = self._key(values.get("api_request_id"))
                    api_span = state["api"].get(request_key) if request_key is not None and (
                        "api", request_key
                    ) not in state["ambiguous"] else None
                    if api_span:
                        span["links"] = [{"traceId": state["trace"], "spanId": api_span}]
                elif not state.get("correlation_disabled") and identity[1] is not None and identity not in state["ambiguous"] and len(state["api"]) < MAX_EVENTS:
                    state["api"][identity[1]] = span["spanId"]
                state["events"].append(span)
        except Exception:
            with self.lock:
                self.counters["callback_errors"] += 1
                # A failed callback may not have a trustworthy turn identity.
                # Conservatively invalidate every currently open turn.
                for active in self.turns.values():
                    active["dropped"] += 1

    def _finish(self, state):
        if not state["target_count"]:
            return
        if self.trace_budget >= MAX_TRACES:
            self.counters["dropped"] += 1
            return
        root = dict(traceId=state["trace"], spanId=state["root"], name="hermes.turn", kind=1,
                    startTimeUnixNano=str(state["started"]), endTimeUnixNano=str(max(state["started"], time.time_ns(),
                        *(int(event["endTimeUnixNano"]) for event in state["events"]))),
                    attributes=[_attribute("hermes.target.category", TARGET),
                                _attribute("hermes.cohort", self.cohort),
                                _attribute("hermes.missing_path.count", state["target_count"]),
                                _attribute("hermes.events.dropped", state["dropped"]),
                                _attribute("hermes.openings.dropped", state["openings_dropped"]),
                                _attribute("hermes.openings.ambiguous", state["openings_ambiguous"]),
                                _attribute("hermes.coverage.complete", not state["dropped"] and not state["opens"]
                                           and not state["openings_dropped"] and not state["openings_ambiguous"])],
                    status={"code": 2})
        packet = {"resourceSpans": [{"resource": {"attributes": [
            _attribute("service.name", SERVICE_NAME), _attribute("service.namespace", "efficiens.local"),
            _attribute("service.version", "0.1.0")
        ]}, "scopeSpans": [{"scope": {"name": "hermes.observer.pilot", "version": "0.1.0"},
                             "spans": [root, *state["events"]]}]}]}
        payload = json.dumps(packet, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            self.counters["dropped"] += 1
            return
        try:
            self.queue.put_nowait(payload)
            self.trace_budget += 1
            self.counters["queued"] += 1
        except queue.Full:
            self.counters["dropped"] += 1

    def _write(self):
        while not self.closed.is_set() or not self.queue.empty():
            if time.monotonic() >= self.deadline:
                self.closed.set()
                with self.lock:
                    self.turns.clear()
            try:
                payload = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if time.monotonic() >= self.deadline:
                    with self.lock:
                        self.counters["dropped"] += 1
                    continue
                rejected = self.sender(payload) or 0
                with self.lock:
                    if rejected:
                        self.counters["partial_batches"] += 1
                        self.counters["rejected_spans"] += rejected
                    else:
                        self.counters["sent"] += 1
            except Exception as exc:
                with self.lock:
                    self.counters["failed"] += 1
                    self.counters["last_failure"] = (
                        "timeout" if isinstance(exc, TimeoutError) else
                        "connection-refused" if isinstance(exc, ConnectionRefusedError) else
                        "invalid-response" if isinstance(exc, ValueError) else "export-failed"
                    )
            finally:
                self.queue.task_done()

    def pre_llm_call(self, **values):
        self._capture("turn", "start", values)

    def pre_api_request(self, **values):
        self._capture("api", "start", values)

    def post_api_request(self, **values):
        self._capture("api", "end", values)

    def api_request_error(self, **values):
        self._capture("api", "error", values)

    def pre_tool_call(self, **values):
        self._capture("tool", "start", values)

    def post_tool_call(self, **values):
        self._capture("tool", "end", values)

    def session_end(self, **values):
        self._capture("turn", "end", values)

    def health(self):
        with self.lock:
            return {**self.counters, "active_turns": len(self.turns), "trace_budget_used": self.trace_budget}

    def flush(self, *, timeout=1):
        deadline = time.monotonic() + min(2, max(0, timeout))
        with self.queue.all_tasks_done:
            while self.queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.queue.all_tasks_done.wait(remaining)
        return True

    def close(self):
        self.closed.set()
        with self.lock:
            self.turns.clear()
        self.writer.join(timeout=1)


_OBSERVER = None


def _dispatch(method, values):
    try:
        from hermes_constants import get_hermes_home
        observer = _OBSERVER
        if observer is not None and Path(get_hermes_home()).resolve() == observer.home:
            getattr(observer, method)(**values)
    except Exception:
        pass


def on_pre_llm_call(**values):
    _dispatch("pre_llm_call", values)


def on_pre_api_request(**values):
    _dispatch("pre_api_request", values)


def on_post_api_request(**values):
    _dispatch("post_api_request", values)


def on_api_request_error(**values):
    _dispatch("api_request_error", values)


def on_pre_tool_call(**values):
    _dispatch("pre_tool_call", values)


def on_post_tool_call(**values):
    _dispatch("post_tool_call", values)


def on_session_end(**values):
    _dispatch("session_end", values)


def register(ctx):
    global _OBSERVER
    previous, _OBSERVER = _OBSERVER, None
    if previous is not None:
        previous.close()
    mode = ctx.get_config("mode", "diagnostic")
    if mode == "efficiency":
        # Load only the reviewed adjacent deployment, never an ambient module
        # from a different profile or workspace. Legacy mode stays unchanged.
        try:
            import importlib.util
            import sys
            name = __name__ + "_efficiency"
            spec = importlib.util.spec_from_file_location(
                name, Path(__file__).with_name("hermes_otel_efficiency.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            module.register(ctx)
            _OBSERVER = module._OBSERVER
        except Exception:
            _OBSERVER = None
        return
    if mode != "diagnostic":
        return
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home()).resolve()
        approved = ctx.get_config("approved_home", "")
        expiry = ctx.get_config("expires_at_utc", "")
        if approved and home == Path(approved).resolve() and expiry:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                observer = Observer(expires_at=parsed.astimezone(timezone.utc).timestamp())
                observer.home = home
                _OBSERVER = observer
                atexit.register(observer.close)
                ctx.on_unload(observer.close)
    except Exception:
        _OBSERVER = None
    for hook in HOOKS:
        ctx.register_hook(hook, globals()["on_" + hook] if not hook.startswith("on_") else globals()[hook])
