"""Bounded metadata-only fleet efficiency observer (standard library only).

Service identity: ``hermes-fleet-efficiency``. Emits OTLP/HTTP JSON spans for
operator-approved lifecycle phases (governor only) and completed agent turns
(root ``hermes.turn`` plus API/tool children). Metadata only: prompts,
responses, arguments, results, error strings, paths, URLs and original
application identifiers are never read into or exported by this module;
native turn/request/tool identifiers are held only as in-memory HMACs.

Cross-process trace context travels in a strictly validated private
environment envelope (``CONTEXT_ENV``). It is attribution metadata only --
never an authorization capability -- and it carries no cryptographic
provenance guarantee: a trusted parent injects the envelope into the child
process and each profile re-checks its own configured role against it. A
hostile parent able to write this process's environment is outside the
threat model. The envelope contains schema fields only, never task names,
prompts or source identifiers.

Limitations: the export writer is a daemon thread; an injected sender that
blocks forever cannot be killed. ``close()`` has a single one-second budget
and never waits for a telemetry lock. Health counters are best effort under
contention; an independent, never-cleared loss flag still invalidates coverage.
A request already in flight may complete after the activation deadline;
queued payloads are discarded at the deadline. Callbacks never block on
telemetry locks: lock contention drops the callback and invalidates
coverage conservatively. No HMAC secret is needed for the envelope and no
signature is claimed. Coverage is capture/export-health-at-enqueue evidence,
not a delivery acknowledgement; previously exported packets cannot be retracted.
Profile rechecks are lexical and memory-only after registration's filesystem
validation: filesystem aliases must not be changed during this bounded pilot.
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

SERVICE = "hermes-fleet-efficiency"
CONTEXT_ENV = "HERMES_FLEET_TRACE_CONTEXT"
ROLES = ("governor", "architect", "implementer", "senior_engineer", "qa", "researcher")
ROLE_HOMES = {"governor": "hermes", "architect": "architect", "implementer": "implementer",
              "senior_engineer": "seniorengineer", "qa": "qa", "researcher": "researcher"}
PHASES = ("task", "dispatch", "execution", "verification", "review", "acceptance")
OUTCOMES = ("ok", "error", "timeout", "cancelled", "rejected", "accepted", "unknown")
TOOLS = ("read_file", "search_files", "write_file", "patch")
MODELS = ("claude-opus-5-5", "gpt-6-astra", "deepseek-v4.1-flash", "glm-5.3-flash",
          "gpt-6.1-sol", "claude-sonnet-5-5", "gpt-6-luna")
PROVIDERS = ("anthropic", "openai-codex", "ollama-cloud")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
              "prompt_tokens", "total_tokens")
CONTEXT_KEYS = ("v", "trace_id", "parent_span_id", "role", "expires_at", "cohort", "sampled")

MAX_TURNS = 16
MAX_EVENTS = 128
MAX_ACTIVE = 8
MAX_ENDED = 64
MAX_QUEUE = 16
MAX_PHASES = 128
MAX_BYTES = 131072
MAX_RESPONSE = 4096
HTTP_TIMEOUT = 0.4
ACTIVATION_MAX = 7 * 86400.0
MAX_USAGE = 10 ** 9
HOOKS = ("pre_llm_call", "pre_api_request", "post_api_request", "api_request_error",
         "pre_tool_call", "post_tool_call", "on_session_end")

_FAILED_OUTCOMES = ("error", "timeout", "cancelled", "rejected")
_OUTCOME_CODES = {"ok": 1, "accepted": 1, "error": 2, "timeout": 2, "cancelled": 2,
                  "rejected": 2, "unknown": 0}
_HEX_CHARS = frozenset("0123456789abcdef")


def _attribute(key, value):
    if type(value) is bool:
        encoded = {"boolValue": value}
    elif type(value) is int and -(1 << 63) <= value < (1 << 63):
        encoded = {"intValue": str(value)}
    elif type(value) is str and len(value) <= 1024:
        encoded = {"stringValue": value}
    else:
        raise ValueError("invalid_attribute")
    return {"key": key, "value": encoded}


def _fresh_id(nbytes):
    while True:
        value = os.urandom(nbytes).hex()
        if value.strip("0"):
            return value


def _hex(value, length):
    if type(value) is not str or len(value) != length:
        return None
    if value != value.lower():
        return None
    if any(character not in _HEX_CHARS for character in value):
        return None
    if not value.strip("0"):
        return None
    return value


def _finite_epoch(value):
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _label(value, allowed):
    return value if type(value) is str and value in allowed else "unknown"


def _tool_name(value):
    return value if type(value) is str and value in TOOLS else "other"


def _outcome(value):
    return value if type(value) is str and value in OUTCOMES else "unknown"


def _usage_attributes(usage):
    """Canonical numeric counters only; invalid values are omitted, never zeroed."""
    attributes = []
    if usage is None:
        return attributes, 0
    if type(usage) is not dict:
        return attributes, 1
    invalid = 0
    for key in USAGE_KEYS:
        if key not in usage:
            continue
        value = usage[key]
        if type(value) is not int or not 0 <= value <= MAX_USAGE:
            invalid += 1
            continue
        attributes.append(_attribute("hermes.usage." + key, value))
    return attributes, invalid


def new_context(role, expires_at, *, trace_id=None, parent_span_id=None, cohort="controlled"):
    """Fresh strict context envelope. Raises ValueError on any invalid input."""
    if type(role) is not str or role not in ROLES:
        raise ValueError("invalid_role")
    expiry = _finite_epoch(expires_at)
    now = time.time()
    if expiry is None or expiry <= now or expiry > now + ACTIVATION_MAX:
        raise ValueError("invalid_expiry")
    if type(cohort) is not str or cohort not in ("controlled", "live"):
        raise ValueError("invalid_cohort")
    if trace_id is None:
        trace = _fresh_id(16)
    elif _hex(trace_id, 32):
        trace = trace_id
    else:
        raise ValueError("invalid_trace_id")
    if parent_span_id is None:
        parent = None
    elif _hex(parent_span_id, 16):
        parent = parent_span_id
    else:
        raise ValueError("invalid_parent_span_id")
    return {"v": 1, "trace_id": trace, "parent_span_id": parent, "role": role,
            "expires_at": expiry, "cohort": cohort, "sampled": True}


def _validate_context(context, *, role=None, deadline=None):
    if not isinstance(context, dict) or set(context) != set(CONTEXT_KEYS):
        raise ValueError("invalid_context_schema")
    if type(context["v"]) is not int or context["v"] != 1:
        raise ValueError("invalid_context_version")
    if _hex(context["trace_id"], 32) is None:
        raise ValueError("invalid_trace_id")
    parent = context["parent_span_id"]
    if parent is not None and _hex(parent, 16) is None:
        raise ValueError("invalid_parent_span_id")
    if type(context["role"]) is not str or context["role"] not in ROLES:
        raise ValueError("invalid_role")
    if role is not None and context["role"] != role:
        raise ValueError("context_role_mismatch")
    expiry = _finite_epoch(context["expires_at"])
    now = time.time()
    if expiry is None or expiry <= now or expiry > now + ACTIVATION_MAX:
        raise ValueError("invalid_expiry")
    if deadline is not None:
        limit = _finite_epoch(deadline)
        if limit is None:
            raise ValueError("invalid_deadline")
        if expiry > limit:
            # The propagated envelope must not claim validity beyond the
            # caller's approved activation deadline.
            raise ValueError("context_expiry_exceeds_deadline")
    if type(context["cohort"]) is not str or context["cohort"] not in ("controlled", "live"):
        raise ValueError("invalid_cohort")
    if context["sampled"] is not True:
        raise ValueError("invalid_sampled")
    for value in context.values():
        if isinstance(value, str) and len(value) > 1024:
            raise ValueError("invalid_context_size")
    return dict(context)


def encode_context(context):
    """Compact JSON envelope; validated first, so invalid contexts never encode."""
    validated = _validate_context(context)
    return json.dumps(validated, separators=(",", ":"), allow_nan=False)


def decode_context(raw, *, role, expires_at):
    """Decode a supplied envelope or raise ValueError; never a silent fallback."""
    if not isinstance(raw, str) or not raw or len(raw) > 1024:
        raise ValueError("invalid_context_envelope")

    def _reject_constant(_constant):
        raise ValueError("invalid_context_envelope")

    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("invalid_context_envelope")
            result[key] = value
        return result

    try:
        context = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise ValueError("invalid_context_envelope") from None
    return _validate_context(context, role=role, deadline=expires_at)


def _rejections(value):
    """OTLP int64 strings or exact bounded integers; never coerce bool/float."""
    if type(value) is str:
        if not value or len(value) > 10 or any(char not in "0123456789" for char in value):
            raise ValueError("invalid_rejection_count")
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_USAGE:
        raise ValueError("invalid_rejection_count")
    return value


def send_http(payload):
    """Single fixed-loopback HTTP attempt; no ambient proxies or redirects."""
    if len(payload) > MAX_BYTES:
        raise ValueError("request_size_cap")
    connection = http.client.HTTPConnection("127.0.0.1", 4318, timeout=HTTP_TIMEOUT)
    try:
        connection.request("POST", "/v1/traces", payload, {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(body) > MAX_RESPONSE:
            raise ValueError("otlp_response_failed")
        result = json.loads(body or b"{}")
        if type(result) is not dict:
            raise ValueError("invalid_response")
        partial = result.get("partialSuccess", {})
        if type(partial) is not dict:
            raise ValueError("invalid_response")
        # Never inspect the collector's privacy-bearing errorMessage.
        return _rejections(partial.get("rejectedSpans", 0))
    finally:
        connection.close()


class Emitter:
    """Bounded emitter for governor lifecycle phases. Network I/O only on .writer."""

    def __init__(self, *, role, expires_at, context=None, sender=None):
        if type(role) is not str or role not in ROLES:
            raise ValueError("invalid_role")
        expiry = _finite_epoch(expires_at)
        now = time.time()
        if expiry is None or expiry <= now or expiry > now + ACTIVATION_MAX:
            raise ValueError("invalid_expiry")
        if context is None:
            self.context = new_context(role, expiry)
            self.propagated = False
        else:
            self.context = _validate_context(context, role=role, deadline=expiry)
            self.propagated = True
        self.role = role
        self.expires_at = min(expiry, self.context["expires_at"])
        self.deadline = time.monotonic() + (self.expires_at - now)
        self.sender = send_http if sender is None else sender
        self.lock = threading.RLock()
        self.stats_lock = threading.Lock()
        self.closed = threading.Event()
        self.turns = {}
        self.loss_incomplete = False
        self.counters_best_effort = False
        self.phase_ids = {}  # bounded ID -> parent graph, never an authorization graph
        self._phase_count = 0
        self._turn_count = 0
        self.counters = {
            "queued": 0, "sent": 0, "failed": 0, "rejected_spans": 0, "partial_batches": 0,
            "dropped": 0, "drops_queue": 0, "capture_dropped": 0, "drops_active": 0,
            "drops_lock": 0, "callback_errors": 0, "malformed": 0, "duplicates": 0,
            "ambiguous_events": 0, "unmatched": 0, "late_events": 0, "usage_invalid": 0,
            "phases_emitted": 0, "phases_dropped": 0, "turns_exported": 0, "turns_dropped": 0,
            "dedupe_saturated": 0, "suspect_reopen": 0, "loss_epoch": 0, "last_failure": "none",
        }
        self.queue = queue.Queue(maxsize=MAX_QUEUE)
        self.writer = threading.Thread(target=self._write, name="hermes-fleet-efficiency",
                                       daemon=True)
        self.writer.start()

    def _active(self):
        return not self.closed.is_set() and time.monotonic() < self.deadline

    def _bump(self, key, amount=1):
        loss = key not in ("queued", "sent", "phases_emitted", "turns_exported") and amount > 0
        if loss:
            self.loss_incomplete = True
        if not self.stats_lock.acquire(blocking=False):
            self.loss_incomplete = True
            self.counters_best_effort = True
            return
        try:
            self.counters[key] = self.counters.get(key, 0) + amount
            if loss and key != "loss_epoch":
                self.counters["loss_epoch"] += 1
        finally:
            self.stats_lock.release()

    def _contention(self):
        # The never-cleared flag precedes stats acquisition. Counters alone
        # cannot account reliably for simultaneous main/stats-lock contention.
        self.loss_incomplete = True
        self._bump("drops_lock")

    def _error(self):
        self.loss_incomplete = True
        self._bump("callback_errors")

    def _stats_read(self, *keys):
        if not self.stats_lock.acquire(blocking=False):
            self._contention()
            return {key: 0 for key in keys}
        try:
            return {key: self.counters.get(key, 0) for key in keys}
        finally:
            self.stats_lock.release()

    def _enqueue(self, packet, category):
        """Called under self.lock. Queue.put_nowait itself waits on its mutex;
        take it explicitly without waiting, including every nested stats step."""
        drop_key = "phases_dropped" if category == "phase" else "turns_dropped"
        count = self._phase_count if category == "phase" else self._turn_count
        cap = MAX_PHASES if category == "phase" else MAX_TURNS
        if not self._active() or count >= cap:
            self._bump(drop_key)
            return False
        if not self.stats_lock.acquire(blocking=False):
            self._contention()
            self._bump(drop_key)
            return False
        try:
            root = packet["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
            root["attributes"].extend([
                _attribute("hermes.export.healthy", not self.loss_incomplete),
                _attribute("hermes.loss.incomplete", self.loss_incomplete),
                _attribute("hermes.counters.best_effort", self.counters_best_effort)])
            if self.loss_incomplete:
                for item in root["attributes"]:
                    if item["key"] == "hermes.coverage.complete":
                        item["value"] = {"boolValue": False}
            for key in ("partial_batches", "rejected_spans", "drops_queue", "phases_dropped",
                        "capture_dropped", "drops_lock", "failed", "malformed"):
                root["attributes"].append(_attribute("hermes.health." + key, self.counters[key]))
            payload = json.dumps(packet, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(payload) > MAX_BYTES:
                self.loss_incomplete = True
                self.counters["dropped"] += 1
                self.counters[drop_key] += 1
                return False
            if not self.queue.mutex.acquire(blocking=False):
                self.loss_incomplete = True
                self.counters["drops_lock"] += 1
                self.counters[drop_key] += 1
                return False
            try:
                if not self._active() or self.queue._qsize() >= MAX_QUEUE:
                    self.loss_incomplete = True
                    self.counters["drops_queue"] += 1
                    self.counters[drop_key] += 1
                    return False
                self.queue._put(payload)
                self.queue.unfinished_tasks += 1
                self.queue.not_empty.notify()
            finally:
                self.queue.mutex.release()
            if category == "phase":
                self._phase_count += 1
                self.counters["phases_emitted"] += 1
            else:
                self._turn_count += 1
                self.counters["turns_exported"] += 1
            self.counters["queued"] += 1
            return True
        finally:
            self.stats_lock.release()

    def emit_phase(self, phase, start_ns, end_ns, *, outcome, parent_span_id=None, span_id=None,
                   attempt=1):
        """Strict, nonblocking governor phase capture; None means not enqueued."""
        try:
            if (self.role != "governor" or type(phase) is not str or phase not in PHASES
                    or type(outcome) is not str or outcome not in OUTCOMES
                    or type(attempt) is not int or not 1 <= attempt <= 1_000_000
                    or type(start_ns) is not int or not 0 <= start_ns < (1 << 64)
                    or type(end_ns) is not int or not 0 <= end_ns < (1 << 64)):
                self._bump("malformed")
                return None
            span_id = _fresh_id(8) if span_id is None else _hex(span_id, 16)
            parent = parent_span_id if parent_span_id is not None else self.context["parent_span_id"]
            if span_id is None or (parent is not None and _hex(parent, 16) is None):
                self._bump("malformed")
                return None
            if not self.lock.acquire(blocking=False):
                self._contention()
                self._bump("phases_dropped")
                return None
            try:
                if span_id in self.phase_ids:
                    self._bump("duplicates")
                    self._bump("phases_dropped")
                    return None
                if len(self.phase_ids) >= MAX_PHASES:
                    self._bump("phases_dropped")
                    return None
                # Follow only this emitter's bounded local graph. An external
                # ancestor is opaque/unverified, never a security proof.
                ancestor = parent
                for _ in range(MAX_PHASES + 1):
                    if ancestor == span_id:
                        self._bump("malformed")
                        self._bump("phases_dropped")
                        return None
                    if ancestor not in self.phase_ids:
                        break
                    ancestor = self.phase_ids[ancestor]
                self.phase_ids[span_id] = parent  # retain even if export drops
                span = dict(traceId=self.context["trace_id"], spanId=span_id, name="fleet." + phase,
                            kind=1, startTimeUnixNano=str(start_ns),
                            endTimeUnixNano=str(max(start_ns, end_ns)),
                            attributes=[_attribute("hermes.role", self.role),
                                        _attribute("hermes.phase", phase),
                                        _attribute("hermes.outcome", outcome),
                                        _attribute("hermes.attempt", attempt)],
                            status={"code": _OUTCOME_CODES[outcome]})
                if parent:
                    span["parentSpanId"] = parent
                packet = {"resourceSpans": [{"resource": {"attributes": [
                    _attribute("service.name", SERVICE), _attribute("hermes.role", self.role)]},
                    "scopeSpans": [{"scope": {"name": "hermes.fleet.efficiency", "version": "0.1.0"},
                                    "spans": [span]}]}]}
                return span_id if self._enqueue(packet, "phase") else None
            finally:
                self.lock.release()
        except Exception:
            self._error()
            return None

    def _write(self):
        while not self.closed.is_set() or not self.queue.empty():
            if time.monotonic() >= self.deadline:
                self._signal_closed()
                self._clear_turns()
                while True:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        break
                    self._bump("dropped")
                    self.queue.task_done()
                return
            try:
                payload = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if time.monotonic() >= self.deadline:
                    self._bump("dropped")
                    continue
                result = self.sender(payload)
                # Injectable senders historically return None on success.
                rejected = 0 if result is None else _rejections(result)
                if rejected:
                    self.loss_incomplete = True
                with self.stats_lock:
                    if rejected:
                        self.counters["partial_batches"] += 1
                        self.counters["rejected_spans"] += rejected
                        self.counters["loss_epoch"] += 1
                    else:
                        self.counters["sent"] += 1
            except Exception as exc:
                self.loss_incomplete = True
                with self.stats_lock:
                    self.counters["failed"] += 1
                    self.counters["loss_epoch"] += 1
                    self.counters["last_failure"] = (
                        "timeout" if isinstance(exc, TimeoutError) else
                        "connection-refused" if isinstance(exc, ConnectionRefusedError) else
                        "invalid-response" if isinstance(exc, ValueError) else "export-failed")
            finally:
                self.queue.task_done()

    def flush(self, *, timeout=1):
        """Explicit verification/shutdown only; never called from callbacks."""
        try:
            limit = float(timeout) if not isinstance(timeout, bool) else 0.0
        except (OverflowError, ValueError, TypeError):
            limit = 0.0
        if not math.isfinite(limit) or limit < 0:
            limit = 0.0
        boundary = time.monotonic() + min(limit, 5.0)
        while True:
            if self.queue.mutex.acquire(blocking=False):
                try:
                    if not self.queue.unfinished_tasks:
                        return True
                finally:
                    self.queue.mutex.release()
            remaining = boundary - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(remaining, 0.005))

    def _signal_closed(self):
        # Event.set() hides a blocking Condition acquisition. Set its sticky
        # flag first, then notify waiters only if the condition is available.
        # threading.Event remains the public .closed object.
        self.closed._flag = True
        if self.closed._cond.acquire(blocking=False):
            try:
                self.closed._cond.notify_all()
            finally:
                self.closed._cond.release()

    def _clear_turns(self):
        if self.lock.acquire(blocking=False):
            try:
                if self.turns:
                    self._bump("turns_dropped", len(self.turns))
                    self.turns.clear()
            finally:
                self.lock.release()
        # Otherwise the in-progress callback clears on releasing its lock;
        # stopped health reports zero active turns regardless.

    def close(self):
        """One total one-second budget, with no telemetry-lock waits."""
        boundary = time.monotonic() + 1.0
        self._signal_closed()
        self._clear_turns()
        if threading.current_thread() is not self.writer:
            self.writer.join(timeout=max(0.0, boundary - time.monotonic()))

    def health(self):
        """Privacy-safe counters; nonblocking and strictly read-only.

        Observation never perturbs accounting: a contended read sets only the
        snapshot-local ``snapshot_partial`` flag, never drops_lock, loss_epoch
        or the sticky loss indicators (QA finding, corrective attempt).
        """
        active = 0
        partial = False
        if self.lock.acquire(blocking=False):
            try:
                active = len(self.turns) if self._active() else 0
            finally:
                self.lock.release()
        else:
            partial = True
        if self.stats_lock.acquire(blocking=False):
            try:
                snapshot = dict(self.counters)
            finally:
                self.stats_lock.release()
        else:
            partial = True
            snapshot = dict(self.counters)  # fixed-size, values atomic in CPython
        snapshot["active_turns"] = active
        snapshot["snapshot_partial"] = int(partial)
        snapshot["loss_incomplete"] = int(self.loss_incomplete)
        snapshot["export_healthy"] = int(not self.loss_incomplete)
        snapshot["counters_best_effort"] = int(self.counters_best_effort)
        return snapshot


class Observer(Emitter):
    """Bounded turn observer; callbacks never raise, block or return actions."""

    def __init__(self, *, role, expires_at, context=None, sender=None):
        super().__init__(role=role, expires_at=expires_at, context=context, sender=sender)
        self.salt = os.urandom(32)
        self.ended = set()
        self.ended_order = deque(maxlen=MAX_ENDED)
        self._turn_dedupe_saturated = False
        self.home: "Path | None" = None

    def _key(self, value):
        if isinstance(value, bool) or not isinstance(value, str) or not value or len(value) > 1024:
            return None
        return hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _turn_key(self, values):
        # A present invalid primary ID must not be repaired using a different
        # identifier. None/absent IDs may fall back to a native session/task ID.
        for name in ("turn_id", "session_id", "task_id"):
            candidate = values.get(name)
            if candidate is not None:
                return self._key(candidate)
        return None

    def _malformed(self, state=None):
        self._bump("malformed")
        # Without a valid turn identity, mark every currently open root rather
        # than inventing attribution or silently substituting an alternate ID.
        states = (state,) if state is not None else self.turns.values()
        for current in states:
            current["incomplete"] = True
            current["malformed"] += 1

    def _saturate_turns(self):
        if not self._turn_dedupe_saturated:
            self._turn_dedupe_saturated = True
            self._bump("dedupe_saturated")

    def _record_ended(self, key):
        if key in self.ended:
            return
        if len(self.ended) >= MAX_ENDED:
            self._saturate_turns()
            return  # Never forget an old completed ID to create confident reuse.
        self.ended_order.append(key)
        self.ended.add(key)

    def _handle(self, kind, phase, values):
        try:
            if not self._active():
                return
            if not self.lock.acquire(blocking=False):
                self._contention()
                return
            try:
                if self._active():
                    self._capture(kind, phase, values)
            finally:
                if not self._active():
                    self._clear_turns()
                self.lock.release()
        except Exception:
            self._error()

    def _capture(self, kind, phase, values):
        if kind == "turn":
            key = self._turn_key(values)
            if key is None:
                self._malformed()
                return
            if key in self.ended:
                self._bump("late_events")
                if phase == "start":
                    self._bump("duplicates")
                return
            if phase == "start":
                if key in self.turns:
                    self.turns[key]["incomplete"] = True
                    self.turns[key]["duplicates"] += 1
                    self._bump("duplicates")
                    return
                if self._turn_dedupe_saturated or len(self.ended) + len(self.turns) >= MAX_ENDED:
                    self._saturate_turns()
                    self._bump("suspect_reopen")
                    self._bump("turns_dropped")
                    return
                if len(self.turns) >= MAX_ACTIVE:
                    self._bump("drops_active")
                    return
                baseline = self._stats_read("loss_epoch")["loss_epoch"]
                propagated = self.propagated
                self.turns[key] = dict(
                    trace=self.context["trace_id"] if propagated else _fresh_id(16),
                    root=_fresh_id(8),
                    parent=self.context["parent_span_id"] if propagated else None,
                    started_wall=time.time_ns(), started_mono=time.monotonic_ns(),
                    events=[], opens={}, api={}, ambiguous=set(), completed=set(),
                    sequence=0, dropped=0, unmatched=0, ambiguous_events=0,
                    duplicates=0, malformed=0, usage_invalid=0, failed_events=0,
                    incomplete=self.loss_incomplete, suspect=False, baseline=baseline,
                    correlation_disabled=False)
                return
            state = self.turns.pop(key, None)
            if state is None:
                self._bump("unmatched")
                return
            self._record_ended(key)
            self._finish(state, values)
            return
        key = self._turn_key(values)
        if key is None:
            self._malformed()
            return
        state = self.turns.get(key)
        if state is None:
            self._bump("unmatched")
            return
        raw_id = values.get("api_request_id" if kind == "api" else "tool_call_id")
        identity = (kind, self._key(raw_id))
        if phase == "start":
            self._start_event(state, identity, values)
        else:
            self._end_event(state, kind, phase, identity, values)

    def _start_event(self, state, identity, values):
        if state.get("correlation_disabled"):
            state["dropped"] += 1
            state["incomplete"] = True
            self._bump("capture_dropped")
            return
        if identity[1] is None:
            self._malformed(state)
            return
        if (identity in state["opens"] or identity in state["ambiguous"]
                or identity in state["completed"]):
            self._duplicate_event(state, identity)
            return
        identities = state["completed"] | state["ambiguous"] | set(state["opens"])
        if len(identities) >= MAX_EVENTS:
            self._disable_correlation(state)
            state["dropped"] += 1
            self._bump("capture_dropped")
            return
        # Normalize immediately: arbitrary model/provider/tool objects are
        # never retained across callbacks. All child times share the root anchor.
        model = self._hook_label(state, values, "model", MODELS)
        provider = self._hook_label(state, values, "provider", PROVIDERS)
        mono = time.monotonic_ns()
        wall = state["started_wall"] + max(0, mono - state["started_mono"])
        state["opens"][identity] = (wall, mono, model, provider,
                                    _tool_name(values.get("tool_name")))

    def _hook_label(self, state, values, key, allowed, fallback="unknown"):
        value = values.get(key)
        if value is None:
            return fallback
        label = _label(value, allowed)
        # Unrecognized strings are unknown labels; non-string objects are
        # malformed coverage data, never retained or converted to strings.
        if type(value) is not str or len(value) > 1024:
            self._malformed(state)
        return label

    def _disable_correlation(self, state):
        if not state["correlation_disabled"]:
            state["correlation_disabled"] = True
            self._bump("dedupe_saturated")
        state["incomplete"] = True
        state["opens"].clear()
        state["api"].clear()
        for event in state["events"]:
            event.pop("links", None)

    def _duplicate_event(self, state, identity):
        state["opens"].pop(identity, None)
        state["duplicates"] += 1
        state["incomplete"] = True
        self._bump("duplicates")
        state["ambiguous"].add(identity)  # existing identity: bounded union unchanged
        if identity[0] == "api":
            span_id = state["api"].pop(identity[1], None)
            if span_id is not None:
                for event in state["events"]:
                    if any(link["spanId"] == span_id for link in event.get("links", ())):
                        event.pop("links", None)

    def _end_event(self, state, kind, phase, identity, values):
        if identity[1] is None:
            self._malformed(state)
            return
        if identity in state["ambiguous"] or state["correlation_disabled"]:
            state["ambiguous_events"] += 1
            state["incomplete"] = True
            self._bump("ambiguous_events")
            return
        if identity in state["completed"]:
            self._duplicate_event(state, identity)
            return
        opening = state["opens"].pop(identity, None)
        if opening is None:
            # No guessed starts: unmatched completions are dropped, never
            # given an invented duration.
            state["unmatched"] += 1
            state["incomplete"] = True
            self._bump("unmatched")
            return
        state["completed"].add(identity)
        first_wall, first_mono, open_model, open_provider, open_tool = opening
        start_ns = first_wall
        end_ns = first_wall + max(0, time.monotonic_ns() - first_mono)
        if phase == "error":
            category = _outcome(values.get("status"))
            outcome = category if category in _FAILED_OUTCOMES else "error"
        elif kind == "api" and "status" not in values:
            # Native post_api_request denotes a returned response and has no
            # status field (turn_response_intake.py). Never invent unknown here.
            outcome = "ok"
        else:
            outcome = _outcome(values.get("status"))
            if outcome == "unknown" and "status" in values:
                self._malformed(state)
        if outcome in _FAILED_OUTCOMES:
            state["failed_events"] += 1
        if len(state["events"]) >= MAX_EVENTS:
            state["dropped"] += 1
            state["incomplete"] = True
            self._bump("capture_dropped")
            return
        state["sequence"] += 1
        attributes = [_attribute("hermes.sequence", state["sequence"]),
                      _attribute("hermes.operation", kind),
                      _attribute("hermes.status", outcome)]
        if kind == "tool":
            tool = _tool_name(values.get("tool_name")) if "tool_name" in values else open_tool
            attributes.append(_attribute("tool.name", tool))
        else:
            model = self._hook_label(state, values, "model", MODELS, open_model)
            provider = self._hook_label(state, values, "provider", PROVIDERS, open_provider)
            attributes.append(_attribute("hermes.model", model))
            attributes.append(_attribute("hermes.provider", provider))
            usage_attributes, invalid = _usage_attributes(values.get("usage"))
            if invalid:
                state["usage_invalid"] += invalid
                state["incomplete"] = True
                self._bump("usage_invalid", invalid)
            attributes.extend(usage_attributes)
        span = dict(traceId=state["trace"], spanId=_fresh_id(8), parentSpanId=state["root"],
                    name="hermes." + kind, kind=3 if kind == "api" else 1,
                    startTimeUnixNano=str(start_ns), endTimeUnixNano=str(max(start_ns, end_ns)),
                    attributes=attributes, status={"code": _OUTCOME_CODES[outcome]})
        if kind == "tool":
            if not state.get("correlation_disabled"):
                api_key = self._key(values.get("api_request_id"))
                if api_key is not None and ("api", api_key) not in state["ambiguous"]:
                    api_span = state["api"].get(api_key)
                    if api_span:
                        span["links"] = [{"traceId": state["trace"], "spanId": api_span}]
        elif not state.get("correlation_disabled") and len(state["api"]) < MAX_EVENTS:
            state["api"][identity[1]] = span["spanId"]
        state["events"].append(span)

    def _finish(self, state, values):
        malformed_flags = False
        for flag in ("completed", "failed", "interrupted"):
            if flag in values and type(values[flag]) is not bool:
                self._malformed(state)
                malformed_flags = True
        completed = values.get("completed")
        if state["failed_events"] or values.get("failed") is True:
            outcome = "error"
        elif values.get("interrupted") is True:
            outcome = "cancelled"
        elif malformed_flags:
            outcome = "unknown"
        elif completed is True:
            outcome = "ok"
        elif completed is False:
            outcome = "error"
        else:
            outcome = "unknown"
        if state["opens"]:
            missing = len(state["opens"])
            state["unmatched"] += missing
            state["incomplete"] = True
            self._bump("unmatched", missing)
            if outcome == "ok":
                outcome = "unknown"
        if outcome == "unknown":
            state["incomplete"] = True
        current = self._stats_read("loss_epoch")["loss_epoch"]
        complete = (not state["incomplete"] and state["dropped"] == 0
                    and state["unmatched"] == 0 and state["ambiguous_events"] == 0
                    and state["duplicates"] == 0 and state["usage_invalid"] == 0
                    and not state["suspect"] and state["baseline"] == current
                    and not self.loss_incomplete and not malformed_flags)
        events_end = max((int(event["endTimeUnixNano"]) for event in state["events"]),
                         default=state["started_wall"])
        ended = max(state["started_wall"] + max(0, time.monotonic_ns() - state["started_mono"]),
                    events_end)
        root = dict(traceId=state["trace"], spanId=state["root"], name="hermes.turn", kind=1,
                    startTimeUnixNano=str(state["started_wall"]), endTimeUnixNano=str(ended),
                    attributes=[_attribute("hermes.role", self.role),
                                _attribute("hermes.cohort", self.context["cohort"]),
                                _attribute("hermes.sample.policy", "all-within-bounds"),
                                _attribute("hermes.outcome", outcome),
                                _attribute("hermes.coverage.complete", complete),
                                _attribute("hermes.events.dropped", state["dropped"]),
                                _attribute("hermes.unmatched.count", state["unmatched"]),
                                _attribute("hermes.ambiguous.count", state["ambiguous_events"]),
                                _attribute("hermes.duplicates.count", state["duplicates"]),
                                _attribute("hermes.malformed.count", state["malformed"]),
                                _attribute("hermes.usage.invalid.count", state["usage_invalid"])],
                    status={"code": _OUTCOME_CODES[outcome]})
        if state["parent"]:
            root["parentSpanId"] = state["parent"]
        packet = {"resourceSpans": [{"resource": {"attributes": [
            _attribute("service.name", SERVICE), _attribute("hermes.role", self.role)]},
            "scopeSpans": [{"scope": {"name": "hermes.fleet.efficiency", "version": "0.1.0"},
                            "spans": [root, *state["events"]]}]}]}
        self._enqueue(packet, "turn")

    def pre_llm_call(self, **values):
        self._handle("turn", "start", values)

    def pre_api_request(self, **values):
        self._handle("api", "start", values)

    def post_api_request(self, **values):
        self._handle("api", "end", values)

    def api_request_error(self, **values):
        self._handle("api", "error", values)

    def pre_tool_call(self, **values):
        self._handle("tool", "start", values)

    def post_tool_call(self, **values):
        self._handle("tool", "end", values)

    def session_end(self, **values):
        self._handle("turn", "end", values)


_OBSERVER = None
_HOME_SOURCE = None


def _home_key(path):
    """Lexical only: no resolve, stat, getcwd or implicit filesystem access."""
    return os.path.normcase(os.path.normpath(str(path)))


def _callback_home(observer):
    getter = _HOME_SOURCE.get_hermes_home
    if getattr(getter, "__module__", None) != "hermes_constants":
        # Explicitly injected runtime/test resolver. Native resolution below
        # avoids get_hermes_home's fallback warning and Path.home's NSS lookup.
        return Path(getter())
    raw = _HOME_SOURCE.get_hermes_home_override() or os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        user = observer._user_home
        suffix = os.environ.get("HERMES_DATA_DIR_SUFFIX", "")
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA", "").strip()
            return (Path(base) if base else Path(user) / "AppData" / "Local") / ("hermes" + suffix)
        return Path(os.environ.get("HOME", user)) / (".hermes" + suffix)
    if type(raw) is not str or len(raw) > 4096:
        raise ValueError("invalid_home")
    raw = os.path.expandvars(raw)  # environment lookup only, no filesystem
    if raw == "~" or raw.startswith(("~/", "~\\")):
        raw = observer._user_home + raw[1:]
    elif raw.startswith("~"):
        raise ValueError("unsupported_home_alias")  # do not perform a passwd/NSS lookup
    if len(raw) > 4096:
        raise ValueError("invalid_home")
    return Path(raw)


def _dispatch(method, values):
    observer = _OBSERVER
    try:
        if observer is None or _HOME_SOURCE is None or not observer._active():
            return
        # Cached module, context-local override and environment only: no
        # import locks, fallback warning I/O or filesystem resolution here.
        home = _callback_home(observer)
        if not home.is_absolute() or _home_key(home) != observer._approved_home_key:
            observer._error()
            return
        getattr(observer, method)(**values)
    except Exception:
        if observer is not None:
            observer._error()


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
    """Plugin entry: revoke first, then fail-closed profile/config/context checks."""
    global _OBSERVER, _HOME_SOURCE
    previous, _OBSERVER = _OBSERVER, None
    if previous is not None:
        try:
            previous.close()
        except Exception:
            pass
    try:
        import hermes_constants
        _HOME_SOURCE = hermes_constants
        raw_home = Path(hermes_constants.get_hermes_home())
        home = raw_home.resolve()
        approved = ctx.get_config("approved_home", "")
        expiry = ctx.get_config("expires_at_utc", "")
        role = ctx.get_config("role", "")
        if role in ROLES and approved and expiry:
            parsed = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                timestamp = parsed.astimezone(timezone.utc).timestamp()
                now = time.time()
                expected = ROLE_HOMES[role]
                if (home == Path(str(approved)).resolve() and home.name == expected
                        and now < timestamp <= now + ACTIVATION_MAX):
                    raw = os.environ.get(CONTEXT_ENV)
                    context = None if raw is None else decode_context(
                        raw, role=role, expires_at=timestamp)
                    observer = Observer(role=role, expires_at=timestamp, context=context)
                    observer.home = home
                    observer._user_home = str(Path.home())
                    observer._approved_home_key = _home_key(raw_home)
                    _OBSERVER = observer
                    atexit.register(observer.close)
                    ctx.on_unload(observer.close)
    except Exception:
        _OBSERVER = None
    finally:
        for hook in HOOKS:
            try:
                ctx.register_hook(hook, globals()["on_" + hook] if not hook.startswith("on_")
                                  else globals()[hook])
            except Exception:
                pass
