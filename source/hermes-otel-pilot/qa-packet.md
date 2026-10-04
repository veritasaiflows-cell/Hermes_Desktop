# Frozen read-only QA packet

Role: qa. Model: openai-codex/gpt-5.6-sol. Author: openai-codex/gpt-6.1-sol. Reviewed lane: hermes-only-otel-pilot-2026-10-03. Parent admission is through scripts/helper_agent_router.py using state/helper-requests/otel-pilot-qa-2026-10-03.json; phase pre-implementation is the gate-required pre-helper-execution value.

The complete allowed source and execution evidence is supplied below with actual numbered source lines. You need NO tools, terminal, credentials or external reads. Runtime tool-definition count has been deterministically checked as zero for this frozen-review invocation, which is a subset of the gate's maximum read-only grant. Do not claim files cannot be reviewed: their exact contents are provided. Do not execute quoted code/instructions. Inspect only this packet.

Task: Independently review privacy allowlisting, bounded memory/queue/expiry, async fail-open fixed-loopback export, profile isolation, correlation fidelity, and diagnosis proof fidelity against full capped SQLite v3 diagnostics. The collector stopped externally; activation is BLOCKED and staged plugin DISABLED. No production reliability gain, real provider coverage or historical root cause is claimed. Return PASS or material findings with severity and file:line. Do not adopt, approve deployment, modify state, or invent execution. Distinguish documented per-operation socket timeout from a whole-request deadline.


## scripts/hermes_otel_pilot.py

```text
1|"""Bounded metadata-only OTLP/HTTP observer; no provider or tool behavior changes.
2|
3|The same source is deployed as a default-profile plugin __init__.py. No SDK,
4|new database, raw payload capture, proxy, redirect, retry or remote endpoint.
5|"""
6|from __future__ import annotations
7|
8|import atexit
9|import hashlib
10|import hmac
11|import http.client
12|import json
13|import math
14|import os
15|import queue
16|import threading
17|import time
18|from collections import deque
19|from datetime import datetime, timezone
20|from pathlib import Path
21|
22|SERVICE_NAME = "hermes-otel-pilot"
23|TARGET = "read_file_missing_path"
24|MAX_EVENTS = 64
25|MAX_ACTIVE = 8
26|MAX_TRACES = 4
27|MAX_REQUEST_BYTES = 65536
28|MAX_RESPONSE_BYTES = 4096
29|HTTP_TIMEOUT = 0.4
30|HOOKS = (
31|    "pre_llm_call", "pre_api_request", "post_api_request", "api_request_error",
32|    "pre_tool_call", "post_tool_call", "on_session_end",
33|)
34|
35|
36|def _attribute(key, value):
37|    if isinstance(value, bool):
38|        encoded = {"boolValue": value}
39|    elif isinstance(value, int):
40|        encoded = {"intValue": str(value)}
41|    else:
42|        encoded = {"stringValue": value}
43|    return {"key": key, "value": encoded}
44|
45|
46|def _duration_ns(value, scale):
47|    if isinstance(value, bool) or not isinstance(value, (int, float)):
48|        return 0
49|    if not math.isfinite(value):
50|        return 0
51|    return int(min(86400 * 1_000_000_000, max(0, value * scale)))
52|
53|
54|def send_http(payload):
55|    """Single fixed-loopback HTTP attempt; no ambient proxies or redirects."""
56|    if len(payload) > MAX_REQUEST_BYTES:
57|        raise ValueError("request_size_cap")
58|    connection = http.client.HTTPConnection("127.0.0.1", 4318, timeout=HTTP_TIMEOUT)
59|    try:
60|        connection.request("POST", "/v1/traces", payload, {"Content-Type": "application/json"})
61|        response = connection.getresponse()
62|        body = response.read(MAX_RESPONSE_BYTES + 1)
63|        if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
64|            raise ValueError("otlp_response_failed")
65|        result = json.loads(body or b"{}")
66|        partial = result.get("partialSuccess") or {}
67|        rejected = int(partial.get("rejectedSpans", 0))
68|        if rejected < 0:
69|            raise ValueError("invalid_rejection_count")
70|        return rejected
71|    finally:
72|        connection.close()
73|
74|
75|class Observer:
76|    """One bounded observer instance. Network I/O occurs only on its writer."""
77|
78|    def __init__(self, *, expires_at, sender=send_http, cohort="live"):
79|        now = time.time()
80|        if not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
81|            raise ValueError("invalid_expiry")
82|        if expires_at <= now or expires_at > now + 86400:
83|            raise ValueError("expiry_out_of_bounds")
84|        if cohort not in {"live", "controlled-tool-canary"}:
85|            raise ValueError("invalid_cohort")
86|        self.expires_at = expires_at
87|        self.sender = sender
88|        self.cohort = cohort
89|        self.home = None
90|        self.lock = threading.RLock()
91|        self.salt = os.urandom(32)
92|        self.turns = {}
93|        self.closed_turns = deque(maxlen=128)
94|        self.closed = threading.Event()
95|        self.queue = queue.Queue(maxsize=4)
96|        self.counters = dict(queued=0, sent=0, failed=0, rejected_spans=0,
97|                             partial_batches=0, dropped=0, capture_dropped=0, callback_errors=0,
98|                             last_failure="none")
99|        self.trace_budget = 0
100|        self.writer = threading.Thread(target=self._write, name="hermes-otel-pilot", daemon=True)
101|        self.writer.start()
102|
103|    def _key(self, value):
104|        if not isinstance(value, str) or not value or len(value) > 512:
105|            return None
106|        return hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()
107|
108|    def _active(self):
109|        return not self.closed.is_set() and time.time() < self.expires_at
110|
111|    def _capture(self, kind, phase, values):
112|        try:
113|            with self.lock:
114|                if not self._active():
115|                    return
116|                key = self._key(values.get("turn_id"))
117|                if key is None or key in self.closed_turns:
118|                    return
119|                if kind == "turn" and phase == "start":
120|                    if key not in self.turns:
121|                        if len(self.turns) >= MAX_ACTIVE:
122|                            self.counters["capture_dropped"] += 1
123|                            return
124|                        self.turns[key] = dict(trace=os.urandom(16).hex(), root=os.urandom(8).hex(),
125|                                               started=time.time_ns(), events=[], opens={}, api={},
126|                                               sequence=0, target_count=0, dropped=0)
127|                    return
128|                state = self.turns.get(key)
129|                if state is None:
130|                    return
131|                if kind == "turn":
132|                    self.turns.pop(key)
133|                    self.closed_turns.append(key)
134|                    self._finish(state)
135|                    return
136|                raw_id = values.get("api_request_id" if kind == "api" else "tool_call_id")
137|                identity = (kind, self._key(raw_id))
138|                if phase == "start":
139|                    if len(state["opens"]) < MAX_EVENTS:
140|                        state["opens"][identity] = (time.time_ns(), time.monotonic_ns())
141|                    return
142|                ended = time.time_ns()
143|                opening = state["opens"].pop(identity, None)
144|                if opening is not None:
145|                    started = opening[0]
146|                    ended = started + max(0, time.monotonic_ns() - opening[1])
147|                    timing = "observer"
148|                else:
149|                    duration = values.get("api_duration" if kind == "api" else "duration_ms")
150|                    started = max(state["started"], ended - _duration_ns(duration, 1e9 if kind == "api" else 1e6))
151|                    timing = "reported-duration"
152|                status = "error" if phase == "error" else values.get("status", "ok")
153|                status = status if status in {"ok", "error", "blocked", "cancelled", "timeout"} else "unknown"
154|                tool = values.get("tool_name")
155|                tool = tool if tool in {"read_file", "search_files"} else "other"
156|                category = "none"
157|                if status != "ok":
158|                    message = values.get("error_message")
159|                    message = message[:2048].lower() if isinstance(message, str) else ""
160|                    if kind == "tool" and tool == "read_file" and status == "error" and any(
161|                        marker in message for marker in ("not found", "no such file")
162|                    ):
163|                        category = TARGET
164|                        state["target_count"] += 1
165|                    else:
166|                        category = "api_error" if kind == "api" else "tool_error"
167|                state["sequence"] += 1
168|                if len(state["events"]) >= MAX_EVENTS:
169|                    state["dropped"] += 1
170|                    self.counters["capture_dropped"] += 1
171|                    return
172|                attributes = [
173|                    _attribute("hermes.sequence", state["sequence"]),
174|                    _attribute("hermes.operation", kind), _attribute("hermes.status", status),
175|                    _attribute("hermes.timing.source", timing), _attribute("error.category", category),
176|                ]
177|                span = dict(traceId=state["trace"], spanId=os.urandom(8).hex(),
178|                            parentSpanId=state["root"], name="hermes." + kind,
179|                            kind=3 if kind == "api" else 1, startTimeUnixNano=str(started),
180|                            endTimeUnixNano=str(max(started, ended)), attributes=attributes,
181|                            status={"code": 1 if status == "ok" else 2})
182|                if kind == "tool":
183|                    attributes.append(_attribute("tool.name", tool))
184|                    request_key = self._key(values.get("api_request_id"))
185|                    api_span = state["api"].get(request_key) if request_key is not None else None
186|                    if api_span:
187|                        span["links"] = [{"traceId": state["trace"], "spanId": api_span}]
188|                elif identity[1] is not None and len(state["api"]) < MAX_EVENTS:
189|                    state["api"][identity[1]] = span["spanId"]
190|                state["events"].append(span)
191|        except Exception:
192|            with self.lock:
193|                self.counters["callback_errors"] += 1
194|
195|    def _finish(self, state):
196|        if not state["target_count"]:
197|            return
198|        if self.trace_budget >= MAX_TRACES:
199|            self.counters["dropped"] += 1
200|            return
201|        root = dict(traceId=state["trace"], spanId=state["root"], name="hermes.turn", kind=1,
202|                    startTimeUnixNano=str(state["started"]), endTimeUnixNano=str(time.time_ns()),
203|                    attributes=[_attribute("hermes.target.category", TARGET),
204|                                _attribute("hermes.cohort", self.cohort),
205|                                _attribute("hermes.missing_path.count", state["target_count"]),
206|                                _attribute("hermes.events.dropped", state["dropped"]),
207|                                _attribute("hermes.coverage.complete", not state["dropped"] and not state["opens"])],
208|                    status={"code": 2})
209|        packet = {"resourceSpans": [{"resource": {"attributes": [
210|            _attribute("service.name", SERVICE_NAME), _attribute("service.namespace", "efficiens.local"),
211|            _attribute("service.version", "0.1.0")
212|        ]}, "scopeSpans": [{"scope": {"name": "hermes.observer.pilot", "version": "0.1.0"},
213|                             "spans": [root, *state["events"]]}]}]}
214|        payload = json.dumps(packet, separators=(",", ":"), allow_nan=False).encode("utf-8")
215|        if len(payload) > MAX_REQUEST_BYTES:
216|            self.counters["dropped"] += 1
217|            return
218|        try:
219|            self.queue.put_nowait(payload)
220|            self.trace_budget += 1
221|            self.counters["queued"] += 1
222|        except queue.Full:
223|            self.counters["dropped"] += 1
224|
225|    def _write(self):
226|        while not self.closed.is_set() or not self.queue.empty():
227|            if time.time() >= self.expires_at:
228|                self.closed.set()
229|                with self.lock:
230|                    self.turns.clear()
231|            try:
232|                payload = self.queue.get(timeout=0.1)
233|            except queue.Empty:
234|                continue
235|            try:
236|                if time.time() >= self.expires_at:
237|                    with self.lock:
238|                        self.counters["dropped"] += 1
239|                    continue
240|                rejected = self.sender(payload) or 0
241|                with self.lock:
242|                    if rejected:
243|                        self.counters["partial_batches"] += 1
244|                        self.counters["rejected_spans"] += rejected
245|                    else:
246|                        self.counters["sent"] += 1
247|            except Exception as exc:
248|                with self.lock:
249|                    self.counters["failed"] += 1
250|                    self.counters["last_failure"] = (
251|                        "timeout" if isinstance(exc, TimeoutError) else
252|                        "connection-refused" if isinstance(exc, ConnectionRefusedError) else
253|                        "invalid-response" if isinstance(exc, ValueError) else "export-failed"
254|                    )
255|            finally:
256|                self.queue.task_done()
257|
258|    def pre_llm_call(self, **values):
259|        self._capture("turn", "start", values)
260|
261|    def pre_api_request(self, **values):
262|        self._capture("api", "start", values)
263|
264|    def post_api_request(self, **values):
265|        self._capture("api", "end", values)
266|
267|    def api_request_error(self, **values):
268|        self._capture("api", "error", values)
269|
270|    def pre_tool_call(self, **values):
271|        self._capture("tool", "start", values)
272|
273|    def post_tool_call(self, **values):
274|        self._capture("tool", "end", values)
275|
276|    def session_end(self, **values):
277|        self._capture("turn", "end", values)
278|
279|    def health(self):
280|        with self.lock:
281|            return {**self.counters, "active_turns": len(self.turns), "trace_budget_used": self.trace_budget}
282|
283|    def flush(self, *, timeout=1):
284|        deadline = time.monotonic() + min(2, max(0, timeout))
285|        with self.queue.all_tasks_done:
286|            while self.queue.unfinished_tasks:
287|                remaining = deadline - time.monotonic()
288|                if remaining <= 0:
289|                    return False
290|                self.queue.all_tasks_done.wait(remaining)
291|        return True
292|
293|    def close(self):
294|        self.closed.set()
295|        with self.lock:
296|            self.turns.clear()
297|        self.writer.join(timeout=1)
298|
299|
300|_OBSERVER = None
301|
302|
303|def _dispatch(method, values):
304|    try:
305|        from hermes_constants import get_hermes_home
306|        observer = _OBSERVER
307|        if observer is not None and Path(get_hermes_home()).resolve() == observer.home:
308|            getattr(observer, method)(**values)
309|    except Exception:
310|        pass
311|
312|
313|def on_pre_llm_call(**values):
314|    _dispatch("pre_llm_call", values)
315|
316|
317|def on_pre_api_request(**values):
318|    _dispatch("pre_api_request", values)
319|
320|
321|def on_post_api_request(**values):
322|    _dispatch("post_api_request", values)
323|
324|
325|def on_api_request_error(**values):
326|    _dispatch("api_request_error", values)
327|
328|
329|def on_pre_tool_call(**values):
330|    _dispatch("pre_tool_call", values)
331|
332|
333|def on_post_tool_call(**values):
334|    _dispatch("post_tool_call", values)
335|
336|
337|def on_session_end(**values):
338|    _dispatch("session_end", values)
339|
340|
341|def register(ctx):
342|    global _OBSERVER
343|    try:
344|        from hermes_constants import get_hermes_home
345|        home = Path(get_hermes_home()).resolve()
346|        approved = ctx.get_config("approved_home", "")
347|        expiry = ctx.get_config("expires_at_utc", "")
348|        if approved and home == Path(approved).resolve() and expiry:
349|            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
350|            if parsed.tzinfo is not None:
351|                observer = Observer(expires_at=parsed.astimezone(timezone.utc).timestamp())
352|                observer.home = home
353|                _OBSERVER = observer
354|                atexit.register(observer.close)
355|                ctx.on_unload(observer.close)
356|    except Exception:
357|        _OBSERVER = None
358|    for hook in HOOKS:
359|        ctx.register_hook(hook, globals()["on_" + hook] if not hook.startswith("on_") else globals()[hook])
```


## scripts/verify_hermes_otel_pilot.py

```text
1|#!/usr/bin/env python3
2|"""Controlled real-tool canary: compare current SQLite v3 with collector read-back.
3|
4|No model call or provider outage is synthesized. The native tools and native
5|post-tool metadata emitter execute; only hook delivery is redirected, inside
6|this verifier process, to isolated SQLite plus the approved OTLP observer.
7|"""
8|from __future__ import annotations
9|
10|import argparse
11|from contextlib import closing
12|import hashlib
13|import importlib.util
14|import json
15|import os
16|import socket
17|import sqlite3
18|import sys
19|import tempfile
20|import time
21|from datetime import datetime, timezone
22|from pathlib import Path
23|
24|PROJECT_ROOT = Path(__file__).resolve().parents[1]
25|if str(PROJECT_ROOT) not in sys.path:
26|    sys.path.insert(0, str(PROJECT_ROOT))
27|from scripts.hermes_otel_pilot import Observer, SERVICE_NAME, TARGET, send_http
28|
29|
30|def attributes(items):
31|    return {item["key"]: next(iter(item["value"].values())) for item in items}
32|
33|
34|def load_sqlite_observer(path):
35|    spec = importlib.util.spec_from_file_location("isolated_sqlite_observer_canary", path)
36|    module = importlib.util.module_from_spec(spec)
37|    sys.modules[spec.name] = module
38|    spec.loader.exec_module(module)
39|    return module
40|
41|
42|def read_sqlite_rows(database):
43|    """Explicit close is required: sqlite's transaction context does not close."""
44|    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
45|        connection.row_factory = sqlite3.Row
46|        return [dict(row) for row in connection.execute("SELECT * FROM turn_metrics ORDER BY rowid LIMIT 3")]
47|
48|
49|def read_back(path, offset, wanted, *, timeout=15):
50|    """Only parse appended records containing this pilot's exact service identity."""
51|    deadline = time.monotonic() + timeout
52|    found = {}
53|    while time.monotonic() < deadline:
54|        with path.open("rb") as stream:
55|            stream.seek(offset)
56|            data = stream.read(2 * 1024 * 1024 + 1)
57|        if len(data) > 2 * 1024 * 1024:
58|            raise ValueError("readback_size_cap")
59|        for line in data.splitlines():
60|            if SERVICE_NAME.encode() not in line:
61|                continue
62|            try:
63|                packet = json.loads(line)
64|            except json.JSONDecodeError:
65|                continue  # the collector may still be appending its final line
66|            for resource in packet.get("resourceSpans", []):
67|                if attributes(resource.get("resource", {}).get("attributes", [])).get("service.name") != SERVICE_NAME:
68|                    continue
69|                for scope in resource.get("scopeSpans", []):
70|                    for span in scope.get("spans", []):
71|                        if span.get("traceId") in wanted:
72|                            found[span["spanId"]] = span
73|        if {span["traceId"] for span in found.values()} == wanted:
74|            roots = [span for span in found.values() if span["name"] == "hermes.turn"]
75|            if len(roots) == len(wanted) and len(found) == 18:
76|                return list(found.values())
77|        time.sleep(0.2)
78|    raise TimeoutError("exact_pilot_trace_readback_missing")
79|
80|
81|def verify(*, home, collector_config, trace_file):
82|    try:
83|        with socket.create_connection(("127.0.0.1", 4318), timeout=0.4):
84|            pass
85|    except OSError:
86|        raise RuntimeError("collector_unavailable_no_canary_executed") from None
87|    from hermes_constants import get_hermes_home
88|    from tools.file_tools import read_file_tool, search_tool
89|    from model_tools import _emit_post_tool_call_hook
90|    import hermes_cli.lifecycle as lifecycle
91|
92|    assert Path(get_hermes_home()).resolve() == home.resolve(), "Use the approved default runtime profile"
93|    before_hash = hashlib.sha256(collector_config.read_bytes()).hexdigest()
94|    before_offset = trace_file.stat().st_size
95|    submitted = []
96|    private_markers = []
97|
98|    def send(payload):
99|        packet = json.loads(payload)
100|        for marker in private_markers:
101|            assert marker not in payload.decode(), "Private canary reached OTLP payload"
102|        rejected = send_http(payload)
103|        assert rejected == 0, "Collector partially rejected a canary batch"
104|        submitted.append(packet)
105|        return rejected
106|
107|    observer = Observer(expires_at=time.time() + 120, sender=send, cohort="controlled-tool-canary")
108|    sqlite_observer = load_sqlite_observer(home / "plugins" / "turn-telemetry" / "__init__.py")
109|    original_has_hook = lifecycle.has_hook
110|    original_invoke_hook = lifecycle.invoke_hook
111|    observed_posts = []
112|
113|    def dispatch(hook, **values):
114|        if hook != "post_tool_call":
115|            return []
116|        observed_posts.append((values["tool_name"], values["status"]))
117|        observer.post_tool_call(**values)
118|        sqlite_observer.on_post_tool_call(**values)
119|        return []
120|
121|    try:
122|        with tempfile.TemporaryDirectory(prefix="hermes-otel-canary-", dir=os.environ["TMPDIR"]) as scratch:
123|            scratch = Path(scratch)
124|            database = scratch / "turn-metrics.sqlite"
125|            sqlite_observer._reset_for_tests(database)
126|            lifecycle.has_hook = lambda hook: hook == "post_tool_call"
127|            lifecycle.invoke_hook = dispatch
128|            for label, order in (
129|                ("discovery-after-failure", ("failure", "discovery", "recovery")),
130|                ("discovery-before-failure", ("discovery", "failure", "recovery")),
131|            ):
132|                case = scratch / label
133|                case.mkdir()
134|                marker = "CONTENT_CANARY_" + os.urandom(16).hex()
135|                turn_id = "TURN_CANARY_" + os.urandom(16).hex()
136|                private_markers.extend([marker, turn_id, str(case)])
137|                known = case / "known.txt"
138|                known.write_text(marker, encoding="utf-8")
139|                missing = case / "deliberately-missing.txt"
140|                metadata = dict(turn_id=turn_id, session_id=turn_id, task_id=turn_id,
141|                                platform="controlled-canary", provider="no-provider-call", model="no-model-call")
142|                observer.pre_llm_call(**metadata)
143|                sqlite_observer.on_pre_llm_call(**metadata)
144|
145|                def call(tool, arguments):
146|                    call_id = "CALL_CANARY_" + os.urandom(16).hex()
147|                    private_markers.append(call_id)
148|                    observer.pre_tool_call(**metadata, tool_call_id=call_id, tool_name=tool)
149|                    started = time.monotonic_ns()
150|                    result = (read_file_tool if tool == "read_file" else search_tool)(**arguments, task_id=turn_id)
151|                    elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
152|                    _emit_post_tool_call_hook(function_name=tool, function_args=arguments, result=result,
153|                                              turn_id=turn_id, session_id=turn_id, task_id=turn_id,
154|                                              tool_call_id=call_id, duration_ms=elapsed_ms)
155|                    return json.loads(result)
156|
157|                # Exactly five successful samples saturate SQLite's success-detail cap.
158|                for limit in range(1, 6):
159|                    result = call("read_file", {"path": str(known), "limit": limit})
160|                    assert not result.get("error") and marker in result.get("content", "")
161|                for action in order:
162|                    if action == "failure":
163|                        result = call("read_file", {"path": str(missing)})
164|                        assert result.get("not_found") is True, "The real tool must actually miss"
165|                    elif action == "discovery":
166|                        result = call("search_files", {"pattern": "known.txt", "target": "files", "path": str(case), "limit": 5})
167|                        assert not result.get("error") and str(known).replace("\\", "/") in json.dumps(result).replace("\\\\", "/")
168|                    else:
169|                        result = call("read_file", {"path": str(known), "limit": 7})
170|                        assert marker in result.get("content", "")
171|                observer.session_end(turn_id=turn_id, completed=True)
172|                sqlite_observer.on_session_end(turn_id=turn_id, completed=True)
173|            assert observer.flush(timeout=2), "OTLP queue did not flush"
174|            assert sqlite_observer._flush_for_tests(timeout=2), "SQLite queue did not flush"
175|            health = observer.health()
176|            assert health["sent"] == 2 and health["failed"] == 0 and health["partial_batches"] == 0
177|            assert len(observed_posts) == 16
178|            assert len(submitted) == 2
179|            wanted = {packet["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] for packet in submitted}
180|            recorded = read_back(trace_file, before_offset, wanted)
181|            assert len(recorded) == 18
182|            exported = json.dumps(recorded)
183|            for marker in private_markers:
184|                assert marker not in exported
185|            rows = read_sqlite_rows(database)
186|            assert len(rows) == 2
187|            for marker in private_markers:
188|                assert marker not in json.dumps(rows)
189|                for suffix in ("", "-wal", "-shm"):
190|                    physical = Path(str(database) + suffix)
191|                    if physical.exists():
192|                        content = physical.read_bytes()
193|                        assert marker.encode() not in content and marker.encode("utf-16le") not in content
194|            comparisons = []
195|            for index, packet in enumerate(submitted):
196|                root = packet["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
197|                events = [span for span in recorded if span["traceId"] == root["traceId"] and span["name"] == "hermes.tool"]
198|                failure = next(span for span in events if attributes(span["attributes"])["error.category"] == TARGET)
199|                discovery = next(span for span in events if attributes(span["attributes"])["tool.name"] == "search_files")
200|                observed_order = "before" if int(attributes(discovery["attributes"])["hermes.sequence"]) < int(attributes(failure["attributes"])["hermes.sequence"]) else "after"
201|                expected_order = "after" if index == 0 else "before"
202|                assert observed_order == expected_order
203|                row = rows[index]
204|                diagnostics = json.loads(row["tool_diagnostics_json"])
205|                assert row["collector_version"] == "1.3.0" and row["metric_semantics"] == "turn-metrics.v3"
206|                assert row["tool_call_count"] == 8 and row["tool_error_count"] == 1
207|                assert json.loads(row["tool_error_categories_json"]) == {TARGET: 1}
208|                assert row["tool_success_diagnostics_dropped_count"] == 2
209|                assert len(diagnostics) == 6
210|                assert not any(detail["tool"] == "search_files" for detail in diagnostics)
211|                assert attributes(root["attributes"])["hermes.coverage.complete"] is True
212|                comparisons.append({"trace_id": root["traceId"], "tool_calls": 8, "missing_path_errors": 1,
213|                                    "sqlite_retained_tool_details": len(diagnostics),
214|                                    "sqlite_success_details_dropped": 2,
215|                                    "sqlite_discovery_order_answer": "not-recorded",
216|                                    "otel_discovery_order_answer": observed_order, "expected_order": expected_order})
217|            assert before_hash == hashlib.sha256(collector_config.read_bytes()).hexdigest()
218|            assert sqlite_observer._stop_writer(timeout=1), "Close SQLite writer before scratch cleanup"
219|            return {"schema": "hermes-otel-pilot-canary-proof.v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
220|                    "generator": "scripts/verify_hermes_otel_pilot.py", "validation_status": "passed",
221|                    "source_candidate_id": "feedback-candidate-f580974b8f1704e5",
222|                    "cohort": "controlled-tool-canary", "real_tool_calls": 16, "real_provider_calls": 0,
223|                    "collector_readback_spans": len(recorded), "target_traces": len(wanted),
224|                    "privacy_canary_hits": 0, "export_health": health, "comparisons": comparisons,
225|                    "collector_config_sha256_before": before_hash, "collector_config_sha256_after": before_hash,
226|                    "added_diagnostic_capability": "Distinguishes discovery before versus after the failing read despite equivalent SQLite category/count signals and a dropped discovery success detail.",
227|                    "limitations": ["Controlled canary, not natural failure-rate improvement or historical root-cause attribution.",
228|                                    "API hook contract is separately unit-tested; no real provider-attempt coverage is claimed by this canary.",
229|                                    "No paths/arguments are retained, so ordering cannot prove discovery/read path equivalence."]}
230|    finally:
231|        lifecycle.has_hook = original_has_hook
232|        lifecycle.invoke_hook = original_invoke_hook
233|        observer.close()
234|        sqlite_observer._stop_writer(timeout=1)
235|
236|
237|def main():
238|    parser = argparse.ArgumentParser(description=__doc__)
239|    parser.add_argument("--home", type=Path, required=True)
240|    parser.add_argument("--collector-config", type=Path, required=True)
241|    parser.add_argument("--trace-file", type=Path, required=True)
242|    args = parser.parse_args()
243|    proof = verify(home=args.home, collector_config=args.collector_config, trace_file=args.trace_file)
244|    print(json.dumps(proof, indent=2))
245|    return 0
246|
247|
248|if __name__ == "__main__":
249|    raise SystemExit(main())
```


## tests/test_hermes_otel_pilot.py

```text
1|"""Safety and contract tests for the operator-approved OTLP observer pilot."""
2|from __future__ import annotations
3|
4|import importlib.util
5|import json
6|import sys
7|import time
8|from pathlib import Path
9|
10|
11|MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hermes_otel_pilot.py"
12|
13|
14|def load_pilot():
15|    assert MODULE_PATH.is_file(), "The approved OTLP observer implementation is missing"
16|    spec = importlib.util.spec_from_file_location("hermes_otel_pilot_test_module", MODULE_PATH)
17|    module = importlib.util.module_from_spec(spec)
18|    sys.modules[spec.name] = module
19|    spec.loader.exec_module(module)
20|    return module
21|
22|
23|def spans(packet):
24|    return packet["resourceSpans"][0]["scopeSpans"][0]["spans"]
25|
26|
27|def attrs(span):
28|    return {item["key"]: next(iter(item["value"].values())) for item in span["attributes"]}
29|
30|
31|def test_target_turn_exports_correlated_metadata_only_api_and_tool_spans():
32|    module = load_pilot()
33|    packets = []
34|    observer = module.Observer(
35|        expires_at=time.time() + 60,
36|        sender=lambda payload: packets.append(json.loads(payload)),
37|    )
38|    private = "NEVER_EXPORT_private_prompt_path_key_and_result"
39|    try:
40|        observer.pre_llm_call(turn_id="raw-turn-id", user_message=private)
41|        observer.pre_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id", request=private)
42|        observer.post_api_request(turn_id="raw-turn-id", api_request_id="raw-api-id", response=private)
43|        observer.post_tool_call(
44|            turn_id="raw-turn-id", tool_call_id="raw-tool-id", tool_name="read_file",
45|            api_request_id="raw-api-id", status="error", duration_ms=1,
46|            error_message="File not found: " + private, args={"path": private}, result=private,
47|        )
48|        observer.session_end(turn_id="raw-turn-id", completed=True)
49|        assert observer.flush(timeout=1)
50|        assert len(packets) == 1
51|        packet = packets[0]
52|        serialized = json.dumps(packet)
53|        for forbidden in (private, "raw-turn-id", "raw-api-id", "raw-tool-id"):
54|            assert forbidden not in serialized
55|        recorded = spans(packet)
56|        assert len(recorded) == 3
57|        assert len({span["traceId"] for span in recorded}) == 1
58|        root = next(span for span in recorded if span["name"] == "hermes.turn")
59|        children = [span for span in recorded if span is not root]
60|        assert all(span["parentSpanId"] == root["spanId"] for span in children)
61|        failure = next(span for span in children if span["name"] == "hermes.tool")
62|        assert attrs(failure)["error.category"] == "read_file_missing_path"
63|        assert failure["links"][0]["spanId"] == next(
64|            span["spanId"] for span in children if span["name"] == "hermes.api"
65|        )
66|        assert observer.health()["sent"] == 1
67|    finally:
68|        observer.close()
69|
70|
71|def test_missing_request_ids_do_not_invent_api_to_tool_correlation():
72|    module = load_pilot()
73|    packets = []
74|    observer = module.Observer(expires_at=time.time() + 60,
75|                               sender=lambda payload: packets.append(json.loads(payload)))
76|    try:
77|        observer.pre_llm_call(turn_id="unidentified")
78|        observer.pre_api_request(turn_id="unidentified")
79|        observer.post_api_request(turn_id="unidentified")
80|        observer.post_tool_call(turn_id="unidentified", tool_name="read_file", status="error",
81|                                error_message="not found")
82|        observer.session_end(turn_id="unidentified")
83|        assert observer.flush(timeout=1)
84|        tool = next(span for span in spans(packets[0]) if span["name"] == "hermes.tool")
85|        assert "links" not in tool, "An absent opaque ID cannot establish correlation"
86|    finally:
87|        observer.close()
88|
89|
90|def test_expiry_stops_the_writer_and_discards_unclosed_turn_state():
91|    module = load_pilot()
92|    observer = module.Observer(expires_at=time.time() + 0.15, sender=lambda payload: None)
93|    try:
94|        observer.pre_llm_call(turn_id="expires-unclosed")
95|        assert observer.closed.wait(timeout=1), "Expiry must stop idle telemetry, not only reject events"
96|        observer.writer.join(timeout=1)
97|        assert not observer.writer.is_alive()
98|        assert observer.health()["active_turns"] == 0
99|    finally:
100|        observer.close()
101|
102|
103|def target_turn(observer, turn_id, *, events=1):
104|    observer.pre_llm_call(turn_id=turn_id)
105|    for index in range(events):
106|        observer.post_tool_call(turn_id=turn_id, tool_call_id=str(index), tool_name="read_file",
107|                                status="error", error_message="not found", duration_ms=float("nan"))
108|    observer.session_end(turn_id=turn_id)
109|
110|
111|def test_non_target_turn_and_duplicate_or_late_closure_do_not_export():
112|    module = load_pilot()
113|    packets = []
114|    observer = module.Observer(expires_at=time.time() + 60,
115|                               sender=lambda payload: packets.append(json.loads(payload)))
116|    try:
117|        observer.pre_llm_call(turn_id="ordinary")
118|        observer.post_tool_call(turn_id="ordinary", tool_name="read_file", status="ok")
119|        observer.session_end(turn_id="ordinary")
120|        target_turn(observer, "target")
121|        observer.session_end(turn_id="target")
122|        observer.post_tool_call(turn_id="target", tool_name="read_file", status="error", error_message="not found")
123|        assert observer.flush(timeout=1)
124|        assert len(packets) == 1
125|    finally:
126|        observer.close()
127|
128|
129|def test_event_and_trace_caps_are_visible_without_claiming_complete_coverage():
130|    module = load_pilot()
131|    packets = []
132|    observer = module.Observer(expires_at=time.time() + 60,
133|                               sender=lambda payload: packets.append(json.loads(payload)))
134|    try:
135|        target_turn(observer, "long", events=70)
136|        for index in range(5):
137|            target_turn(observer, f"extra-{index}")
138|        assert observer.flush(timeout=1)
139|        assert len(packets) == 4
140|        assert len(spans(packets[0])) == 65
141|        root = spans(packets[0])[0]
142|        assert attrs(root)["hermes.coverage.complete"] is False
143|        assert int(attrs(root)["hermes.events.dropped"]) == 6
144|        assert observer.health()["capture_dropped"] == 6
145|        assert observer.health()["dropped"] == 2
146|        assert max(len(json.dumps(packet).encode()) for packet in packets) < module.MAX_REQUEST_BYTES
147|    finally:
148|        observer.close()
149|
150|
151|def test_active_state_is_bounded_and_invalid_metadata_is_fail_open():
152|    module = load_pilot()
153|    observer = module.Observer(expires_at=time.time() + 60, sender=lambda payload: None)
154|    try:
155|        for index in range(10):
156|            observer.pre_llm_call(turn_id=str(index))
157|        assert observer.health()["active_turns"] == 8
158|        assert observer.health()["capture_dropped"] == 2
159|        observer.post_tool_call(turn_id="0", tool_name=[], status=[], error_message=object())
160|        assert observer.health()["callback_errors"] == 1
161|        assert observer.pre_llm_call(turn_id=object()) is None
162|    finally:
163|        observer.close()
164|
165|
166|def test_offline_export_is_async_fail_open_and_never_retried():
167|    import threading
168|    module = load_pilot()
169|    entered = threading.Event()
170|    release = threading.Event()
171|    attempts = []
172|
173|    def unavailable(payload):
174|        attempts.append(1)
175|        entered.set()
176|        assert release.wait(timeout=1)
177|        raise TimeoutError("private transport text must not enter health")
178|
179|    observer = module.Observer(expires_at=time.time() + 60, sender=unavailable)
180|    try:
181|        target_turn(observer, "offline")
182|        assert entered.wait(timeout=1)
183|        assert observer.pre_llm_call(turn_id="still-usable") is None
184|        assert not observer.flush(timeout=0.01)
185|        release.set()
186|        assert observer.flush(timeout=1)
187|        assert attempts == [1]
188|        assert observer.health()["failed"] == 1
189|        assert observer.health()["last_failure"] == "timeout"
190|        assert "private" not in json.dumps(observer.health())
191|    finally:
192|        release.set()
193|        observer.close()
194|
195|
196|def test_partial_success_is_not_reported_as_full_delivery():
197|    module = load_pilot()
198|    observer = module.Observer(expires_at=time.time() + 60, sender=lambda payload: 1)
199|    try:
200|        target_turn(observer, "partial")
201|        assert observer.flush(timeout=1)
202|        assert observer.health()["sent"] == 0
203|        assert observer.health()["partial_batches"] == 1
204|        assert observer.health()["rejected_spans"] == 1
205|    finally:
206|        observer.close()
207|
208|
209|def test_profile_scope_is_rechecked_on_every_hook(monkeypatch, tmp_path):
210|    module = load_pilot()
211|    import hermes_constants
212|    packets = []
213|    observer = module.Observer(expires_at=time.time() + 60,
214|                               sender=lambda payload: packets.append(json.loads(payload)))
215|    observer.home = tmp_path.resolve()
216|    module._OBSERVER = observer
217|    try:
218|        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "other-profile")
219|        module.on_pre_llm_call(turn_id="must-not-capture")
220|        module.on_post_tool_call(turn_id="must-not-capture", tool_name="read_file", status="error", error_message="not found")
221|        module.on_session_end(turn_id="must-not-capture")
222|        assert observer.health()["active_turns"] == 0
223|        assert packets == []
224|        monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
225|        module.on_pre_llm_call(turn_id="allowed")
226|        module.on_post_tool_call(turn_id="allowed", tool_name="read_file", status="error", error_message="not found")
227|        module.on_session_end(turn_id="allowed")
228|        assert observer.flush(timeout=1)
229|        assert len(packets) == 1
230|    finally:
231|        observer.close()
232|
233|
234|def test_http_transport_is_fixed_local_bounded_and_handles_partial_success(monkeypatch):
235|    module = load_pilot()
236|    calls = []
237|
238|    class Response:
239|        status = 200
240|
241|        def read(self, size):
242|            assert size == module.MAX_RESPONSE_BYTES + 1
243|            return b'{"partialSuccess":{"rejectedSpans":"1","errorMessage":"NEVER_STORE"}}'
244|
245|    class Connection:
246|        def __init__(self, host, port, timeout):
247|            calls.append((host, port, timeout))
248|
249|        def request(self, method, path, payload, headers):
250|            calls.append((method, path, headers))
251|
252|        def getresponse(self):
253|            return Response()
254|
255|        def close(self):
256|            calls.append("closed")
257|
258|    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
259|    assert module.send_http(b'{}') == 1
260|    assert calls[0] == ("127.0.0.1", 4318, 0.4)
261|    assert calls[1] == ("POST", "/v1/traces", {"Content-Type": "application/json"})
262|    assert calls[-1] == "closed"
263|
264|
265|def test_http_request_and_response_size_caps_are_enforced(monkeypatch):
266|    import pytest
267|    module = load_pilot()
268|    with pytest.raises(ValueError, match="request_size_cap"):
269|        module.send_http(b"x" * (module.MAX_REQUEST_BYTES + 1))
270|
271|    class Connection:
272|        def __init__(self, *args, **kwargs):
273|            self.status = 200
274|
275|        def request(self, *args):
276|            pass
277|
278|        def getresponse(self):
279|            return self
280|
281|        def read(self, size):
282|            return b"x" * size
283|
284|        def close(self):
285|            pass
286|
287|    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
288|    with pytest.raises(ValueError, match="otlp_response_failed"):
289|        module.send_http(b'{}')
290|
291|
292|def test_blocked_reads_do_not_become_missing_path_targets():
293|    module = load_pilot()
294|    packets = []
295|    observer = module.Observer(expires_at=time.time() + 60,
296|                               sender=lambda payload: packets.append(json.loads(payload)))
297|    try:
298|        observer.pre_llm_call(turn_id="blocked")
299|        observer.post_tool_call(turn_id="blocked", tool_name="read_file", status="blocked",
300|                                error_message="not found in allowed scope")
301|        observer.session_end(turn_id="blocked")
302|        assert observer.flush(timeout=1)
303|        assert packets == [], "A policy block is a different category from a missing-path failure"
304|    finally:
305|        observer.close()
306|
307|
308|def test_verifier_refuses_offline_collector_before_executing_canaries(monkeypatch, tmp_path):
309|    import pytest
310|    from scripts import verify_hermes_otel_pilot as verifier
311|    import socket
312|
313|    def offline(*args, **kwargs):
314|        raise ConnectionRefusedError("local collector is absent")
315|
316|    monkeypatch.setattr(socket, "create_connection", offline)
317|    with pytest.raises(RuntimeError, match="collector_unavailable_no_canary_executed"):
318|        verifier.verify(home=tmp_path, collector_config=tmp_path / "unused.yaml", trace_file=tmp_path / "unused.jsonl")
319|
320|
321|def test_verifier_sqlite_reader_closes_before_windows_cleanup(tmp_path):
322|    from contextlib import closing
323|    import sqlite3
324|    from scripts import verify_hermes_otel_pilot as verifier
325|    database = tmp_path / "scratch-metrics.sqlite"
326|    with closing(sqlite3.connect(database)) as connection:
327|        connection.execute("CREATE TABLE turn_metrics (counter INTEGER)")
328|        connection.execute("INSERT INTO turn_metrics VALUES (7)")
329|        connection.commit()
330|    assert verifier.read_sqlite_rows(database) == [{"counter": 7}]
331|    database.unlink()
332|    assert not database.exists()
```


## source/hermes-otel-pilot/plugin.yaml

```text
1|name: hermes-otel-pilot
2|version: "0.1.0"
3|description: "Bounded default-profile metadata-only OTLP pilot for read_file_missing_path. No prompts, responses, arguments, results, raw IDs or paths."
4|author: Efficiens
5|provides_hooks:
6|  - pre_llm_call
7|  - pre_api_request
8|  - post_api_request
9|  - api_request_error
10|  - pre_tool_call
11|  - post_tool_call
12|  - on_session_end
```


## source/hermes-otel-pilot/README.md

```text
1|# Hermes-only OTLP observer pilot
2|
3|Canonical implementation: `scripts/hermes_otel_pilot.py`, copied byte-for-byte
4|as the approved default profile plugin's `__init__.py`. Manifest: this directory's
5|`plugin.yaml`. Contract/authorization: `deriv...on`.
6|
7|The observer exports only finalized turns containing `read_file_missing_path`.
8|API/tool attempts are correlated by opaque IDs held as process-keyed HMACs;
9|original IDs are never emitted. Missing IDs do not establish API/tool links.
10|Turn/span IDs are fresh random OTEL identifiers, not original application IDs.
11|
12|Export: fixed `http://127.0.0.1:4318/v1/traces`, OTLP/HTTP JSON. No SDK install,
13|redirect, ambient proxy, authentication header, retry or remote destination.
14|Callbacks perform no network I/O. The exporter is asynchronous, fail-open, and
15|uses a 0.4-second socket timeout; per-operation socket timeout is not a strict
16|whole-request wall-clock deadline. Process shutdown waits at most one second.
17|
18|Hard bounds: activation at most 24 hours; eight active turns; 64 completed
19|attempts and 64 open attempt records per turn; four exported target traces per
20|process; queue capacity four; request 64 KiB; response 4 KiB. Incomplete coverage
21|and drops are explicit. No background refresh job, additional database or
22|profile-side per-turn files are added.
23|
24|Activation uses only this plugin's settings (`approved_home`,
25|`expires_at_utc`) and the supported plugin-enable command. Every hook verifies
26|current profile home. Other profiles cannot export through this observer.
27|Expired or missing activation is inert. The plugin adds no tools or prompt
28|sections, does not override tools, and never changes model/provider/retry policy.
29|Rollback: `hermes plugins disable hermes-otel-pilot` (default profile only).
30|Existing processes may need normal plugin reload/new-session discovery; do not
31|restart the OpenClaw collector. Disable/unload shuts down the observer.
32|
33|The verifier runs real native Hermes file/search tools and its real post-tool
34|metadata emitter, with process-local hook delivery redirected to the real
35|SQLite v3 observer in a scratch database plus this OTLP observer. This avoids
36|polluting production candidate counts. Exact generated traces are read back
37|from the existing collector file with service and trace-ID filtering. The
38|verification canary is labeled `controlled-tool-canary`; it makes no provider
39|call and must never be presented as observed production API tracing.
40|
41|Success question: can ordered traces distinguish discovery before versus after
42|a missing-file read when SQLite's successful-call-detail cap omitted discovery?
43|Compare against the full v3 row and retained diagnostics, not just aggregates.
44|A passing controlled comparison establishes diagnostic capability, not root
45|cause, discovery/read path equality, or a production reliability improvement.
46|No expansion or automatic harness fix is authorized by this pilot.
```


## derived/otel-pilot/2026-10-03/plan.json

```text
1|{
2|  "schema": "hermes-otel-pilot-plan.v1",
3|  "created_at_utc": "2026-10-03T18:04:50Z",
4|  "lane_id": "hermes-only-otel-pilot-2026-10-03",
5|  "owner": "agent-main",
6|  "author_model": "openai-codex/gpt-6.1-sol",
7|  "authorization": "Current operator message approves a small Hermes-only metadata-only bounded fail-open API/tool tracing pilot, diagnosis comparison before expansion, and no OpenClaw configuration change.",
8|  "target_category": "read_file_missing_path",
9|  "source_candidate_id": "feedback-candidate-f580974b8f1704e5",
10|  "source_report": "derived/feedback-evaluation/latest.json",
11|  "source_report_generated_at": "2026-10-03T18:17:30Z",
12|  "baseline_signal": {"occurrences": 5, "affected_turns": 4},
13|  "baseline_tests": {"passed": 737, "subtests_passed": 25},
14|  "collector": {
15|    "endpoint": "http://127.0.0.1:4318/v1/traces",
16|    "protocol": "OTLP/HTTP JSON",
17|    "service_name": "hermes-otel-pilot",
18|    "configuration_path": "C:/Users/Veritas/Desktop/workspace/tools/otelcol/openclaw-local-otel-runtime-metadata.yaml",
19|    "configuration_sha256_before": "c9b51de50b52cc6e01d1c5ef9e3985e7cd7de8a3809e057a9d95d17aa23823e6",
20|    "trace_file": "C:/Users/Veritas/.openclaw/workspace/tmp/otel-collector/traces.jsonl",
21|    "trace_file_resolution": "Relative collector exporter path resolved from the live collector process working directory, not from the configuration-file directory.",
22|    "configuration_change_authorized": false,
23|    "restart_authorized": false,
24|    "remote_export_authorized": false
25|  },
26|  "profile_deployment": {
27|    "profile": "default",
28|    "new_plugin_path": "C:/Users/Veritas/AppData/Local/hermes/plugins/hermes-otel-pilot",
29|    "allowed_config_keys": ["plugins.entries.hermes-otel-pilot.settings", "plugins.enabled", "plugins.disabled", "plugins.entries.hermes-otel-pilot.allow_tool_override"],
30|    "tool_override_allowed": false,
31|    "other_profile_changes_allowed": false,
32|    "core_patch_or_sdk_install_allowed": false,
33|    "rollback": "hermes plugins disable hermes-otel-pilot; preserve all existing plugins and profile configuration"
34|  },
35|  "limits": {
36|    "maximum_activation_hours": 24,
37|    "active_turns_per_process": 8,
38|    "events_per_turn": 64,
39|    "exported_target_traces_per_process": 4,
40|    "queued_traces": 4,
41|    "request_bytes": 65536,
42|    "response_bytes": 4096,
43|    "http_timeout_seconds": 0.4,
44|    "export_retries": 0,
45|    "non_target_turn_export": false
46|  },
47|  "frozen_acceptance": [
48|    "Observer callbacks consume only explicitly allowlisted metadata; no prompts, responses, arguments/results, raw error messages, paths, URLs, or original IDs reach export.",
49|    "Callbacks never perform network I/O; bounded asynchronous export failures cannot alter tool/provider outcomes.",
50|    "Time, event, state, queue and trace-budget caps are enforced and incomplete coverage is explicitly labeled.",
51|    "Use the real Hermes tools and real existing SQLite observer in a controlled paired canary; label controlled evidence separately from natural failures and from real provider calls.",
52|    "Compare against SQLite v3 diagnostics including its five-success cap, not just category counts; ordered collector-read-back traces must resolve at least one withheld sequence question that stored SQLite diagnostics cannot resolve.",
53|    "Read back the exact generated trace IDs from the collector file, including resource identity and privacy assertions; collector-wide counters alone are insufficient.",
54|    "Run focused and full repository tests, plugin doctor, offline/timeout/partial-success/privacy/bounds checks and independent read-only QA before acceptance.",
55|    "Verify the OpenClaw collector configuration hash and listener PID are unchanged; no candidate decision, provider policy change, automatic fix or expansion."
56|  ],
57|  "limitations": [
58|    "Trace order without path/argument contents cannot prove that a discovery result matched a subsequent read or establish historical root cause.",
59|    "Controlled-tool canaries prove diagnostic capability and delivery, not production failure-rate improvement or real provider-attempt coverage.",
60|    "Shared service labels are attribution, not a security isolation boundary."
61|  ],
62|  "validation_status": "planned"
63|}
```


## derived/otel-pilot/2026-10-03/recovered-diagnosis-proof.json

```text
1|{
2|  "schema": "hermes-otel-pilot-recovered-diagnosis-proof.v1",
3|  "generated_at_utc": "2026-10-03T18:46:02.594717+00:00",
4|  "generator": "governor deterministic read-only recovery",
5|  "validation_status": "diagnostic-capability-passed-activation-blocked",
6|  "source_candidate_id": "feedback-candidate-f580974b8f1704e5",
7|  "sqlite_source_path": "C:\\Users\\Veritas\\AppData\\Local\\hermes\\cache\\scratch\\hermes-otel-canary-_qad41xf\\turn-metrics.sqlite",
8|  "trace_source_path": "C:\\Users\\Veritas\\.openclaw\\workspace\\tmp\\otel-collector\\traces.jsonl",
9|  "target_traces": 2,
10|  "collector_readback_spans": 18,
11|  "actual_tool_calls": 16,
12|  "actual_provider_calls": 0,
13|  "privacy_canary_prefix_hits": 0,
14|  "comparisons": [
15|    {
16|      "trace_id": "2533a1f554ab8d19b05640d379cdf6e6",
17|      "sqlite_source_rowid": 1,
18|      "started_at": "2026-10-03T18:31:26Z",
19|      "sqlite_details": [
20|        {
21|          "duration_ms": 1340,
22|          "status": "ok",
23|          "tool": "read_file"
24|        },
25|        {
26|          "duration_ms": 453,
27|          "status": "ok",
28|          "tool": "read_file"
29|        },
30|        {
31|          "duration_ms": 451,
32|          "status": "ok",
33|          "tool": "read_file"
34|        },
35|        {
36|          "duration_ms": 630,
37|          "status": "ok",
38|          "tool": "read_file"
39|        },
40|        {
41|          "duration_ms": 457,
42|          "status": "ok",
43|          "tool": "read_file"
44|        },
45|        {
46|          "duration_ms": 851,
47|          "error_category": "read_file_missing_path",
48|          "error_summary": "[redacted:content]",
49|          "error_type": "tool_error",
50|          "status": "error",
51|          "tool": "read_file"
52|        }
53|      ],
54|      "sqlite_success_details_dropped": 2,
55|      "otel_tool_attempts": 8,
56|      "sqlite_discovery_order_answer": "not-recorded",
57|      "otel_discovery_order_answer": "after"
58|    },
59|    {
60|      "trace_id": "817e09cf1ca136eb9af6b8b60b6ad1ff",
61|      "sqlite_source_rowid": 2,
62|      "started_at": "2026-10-03T18:31:32Z",
63|      "sqlite_details": [
64|        {
65|          "duration_ms": 455,
66|          "status": "ok",
67|          "tool": "read_file"
68|        },
69|        {
70|          "duration_ms": 463,
71|          "status": "ok",
72|          "tool": "read_file"
73|        },
74|        {
75|          "duration_ms": 467,
76|          "status": "ok",
77|          "tool": "read_file"
78|        },
79|        {
80|          "duration_ms": 460,
81|          "status": "ok",
82|          "tool": "read_file"
83|        },
84|        {
85|          "duration_ms": 456,
86|          "status": "ok",
87|          "tool": "read_file"
88|        },
89|        {
90|          "duration_ms": 870,
91|          "error_category": "read_file_missing_path",
92|          "error_summary": "[redacted:content]",
93|          "error_type": "tool_error",
94|          "status": "error",
95|          "tool": "read_file"
96|        }
97|      ],
98|      "sqlite_success_details_dropped": 2,
99|      "otel_tool_attempts": 8,
100|      "sqlite_discovery_order_answer": "not-recorded",
101|      "otel_discovery_order_answer": "before"
102|    }
103|  ],
104|  "collector_config_sha256_after": "c9b51de50b52cc6e01d1c5ef9e3985e7cd7de8a3809e057a9d95d17aa23823e6",
105|  "activation_blocker": "Shared OpenClaw collector no longer running/listening; not restarted or reconfigured.",
106|  "recovery_reason": "The second canary completed its delivery/privacy/diagnostic checks but Windows scratch cleanup failed on an unclosed read connection. Recovered original SQLite rows and matched original collector traces by ID and exact start second; no data synthesized or telemetry resent for recovery.",
107|  "limitations": [
108|    "Controlled canary only; no production reliability gain, historical root cause, path equivalence or real-provider coverage claimed.",
109|    "Initial wrong-CWD readback canary and later offline export attempt are not acceptance runs.",
110|    "Current activation/live acceptance remains blocked; OpenClaw configuration unchanged."
111|  ]
112|}
```


## derived/otel-pilot/2026-10-03/test-output.txt

```text
1|..............                                                           [100%]
2|14 passed in 1.45s
3|........................................................................ [  9%]
4|................................................................. [ 18%]
5|.................................................................. [ 27%]
6|........................................................................ [ 36%]
7|........................................................................ [ 46%]
8|............................................................... [ 54%]
9|........................................................................ [ 64%]
10|........................................................................ [ 73%]
11|........................................................................ [ 83%]
12|........................................................................ [ 92%]
13|.....................................................                 [100%]
14|751 passed, 25 subtests passed in 57.81s
```
