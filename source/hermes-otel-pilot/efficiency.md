# Bounded fleet efficiency measurement

Owner: WF-1200 / Governor. Operator authorization and acceptance gates:
`derived/otel-efficiency/2026-10-04/plan.md`. This is an extension, not a fleet
qualification or a replacement for independent acceptance.

## Architecture

`hermes-otel-pilot` keeps its historical diagnostic mode. `mode: efficiency`
loads the adjacent `hermes_otel_efficiency.py` observer. Deployment copies the
reviewed versions of both files; copying only `__init__.py` is insufficient.
The efficiency service is `hermes-fleet-efficiency`, separate from the old
`hermes-otel-pilot` diagnostic service.

All six fleet roles are authorized (operator approval 2026-10-05): governor
(default home `hermes`), architect, implementer, senior_engineer (home
`seniorengineer`), qa and researcher; the role-to-home map is `ROLE_HOMES`.
Match actual profile home to `approved_home`, and match configured role to the home. Each process has
an absolute UTC expiry, converted to a monotonic deadline. Capture expires even
when idle. A configuration entry left enabled after expiry is not evidence of
active capture. No implicit or automatic extension is permitted.

## Sampling and limits

Both successes and failures are captured at 100% **within process-local bounds**:
16 completed turns, 8 active turns, 128 events per turn, queue capacity 16,
128 lifecycle phases, 128 KiB request, 4 KiB response, 0.4-second socket timeout,
no retries. This is not a statistically representative population sample.
Restart/reload resets process-local budgets; these are not fleet-wide caps.
Dropped, malformed, unmatched and ambiguous observations invalidate coverage.
Telemetry callbacks never wait for network delivery or a full queue. Socket
timeout is per operation, not an absolute HTTP-request wall-clock deadline.
Shutdown is bounded; an in-flight export may complete after the expiry boundary.

Destination remains the existing local collector at `127.0.0.1:4318/v1/traces`.
No SDK, database, remote endpoint, ambient proxy, redirect or collector change.
The shared collector's existing retention policy remains its owner's policy;
this pilot does not install a separate retention or cleanup job.

## Safe attribution

Propagate only the strict `HERMES_FLEET_TRACE_CONTEXT` JSON envelope through the
specific child's subprocess environment: version, fresh random trace and parent
span IDs, bounded role, expiry, cohort and sampling flag. Never propagate prompts,
client/task names, native IDs, credentials, URLs or W3C baggage. Do not mutate
process-global environment for a child. Supplied invalid context disables that
observer; it must not quietly become an apparently linked standalone trace.

The envelope is **not a signature or authorization token**. A same-user process
can forge metadata. Trace IDs and role labels cannot confer write access, qualify
a model, or accept a result. Admission remains in `helper_agent_router.py`;
acceptance remains the Governor's local verified evidence record.

API usage is numeric allowlisted metadata, not calculated billing. Missing usage
is unknown, not free. Final session-model verification and per-request labels
are distinct; neither silently certifies every fallback attempt in the run.

## Explicit lifecycle runner

`scripts/fleet_measurement.py:MeasurementRun` is the bounded integration point:

1. Gate a scoped inline/no-tools Implementer request.
2. Emit dispatch and execution spans; pass child context in a copied environment.
3. Verify the real helper output through a Governor-owned deterministic operation.
4. Gate an independent QA request, run it on a pinned route and check its actual
   final-session model. Governor reconciles the review disposition explicitly.
5. Accept only after successful execution, verification and review in order.
   Rejection/timeout/error paths end with rejection, not silent acceptance.

This runner deliberately does **not** replace all workspace dispatch or intercept
commands globally. Direct CLI runs outside it can have standalone observer spans
but no measured parent lifecycle. The implementation-build run that constructs
this feature predates deployment and is not retroactively labeled instrumented.

`exporter_acknowledged` is only local export health. Delivery is unverified until
exact collector read-back. Work can pass while telemetry fails: these are
separate outcomes. The report always sets `efficiency_claim_supported: false`
until a separate matched solo-versus-fleet evaluation exists.

## Verification and acceptance

Run focused tests with the runtime interpreter (the system Python lacks pytest):

`C:/Users/Veritas/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe -m pytest tests/test_otel_efficiency.py tests/test_fleet_measurement.py tests/test_hermes_otel_pilot.py -q`

Read exact traces using `scripts/verify_fleet_measurement.py`, a baseline byte
offset, a bounded scan, the exact service and known trace IDs. Do not print shared
collector records. Check expected roles and lifecycle stages in addition to the
parser's interval/deduplication/parent checks; internally consistent data does not
prove absence of dropped spans. Negative and malformed controls are deliberate,
`cohort=controlled`, and excluded from claims about natural failure rates.

Live deployment requires independent code QA, source/deployed hash parity,
profile settings read-back, fresh-process activation, native hook delivery and
exact collector read-back. Plugin doctor alone proves only hook registration.
Do not reload the currently serving desktop or restart the collector to obtain
a convenient passing result. Report fresh CLI coverage separately from existing
long-lived sessions.

## Deployment keys and rollback

Only these keys are authorized in the six fleet profiles:
`plugins.entries.hermes-otel-pilot.settings` (approved_home, expires_at_utc,
role, mode), `plugins.entries.hermes-otel-pilot.allow_tool_override` (false),
and this plugin's membership in `plugins.enabled`/`plugins.disabled`.
Use the supported enable command with `--no-allow-tool-override`; preserve all
other plugins, credentials, model routes and configuration keys.

Back up prior plugin code and this plugin's settings/membership only. Rollback
restores only those fields/files, never overwrites a whole profile config from a
stale copy. Expiry stops capture; the Governor/operator owns the subsequent
explicit disable and fresh-session/reload decision. Existing read-only expiry
reports for the historical pilot are not authorized to disable, extend or accept
this newer measurement window.

## Status

Code-only QA accepted the baseline diagnostics and model-attribution changes
with notes (verified GPT-6.1 Sol). On 2026-10-06 the reviewed observer was deployed
to `default`, `architect`, `implementer`, `seniorengineer`, `qa`, and `researcher`.
All profile configurations remained byte-for-byte unchanged; the existing expiry
is `2026-10-12T14:00:00Z`. Fresh-process activation and controlled collector
read-back passed for all six profiles. A real native Sol QA turn also passed
exact-trace model-attribution and coverage checks. Existing desktop/gateway
processes were not restarted; their updated-code capture remains unverified.

The final rollout reviewer withheld acceptance because its profile identity text
still named GPT-5.6-Sol, despite verified GPT-6.1 Sol execution. The operator then
approved correcting exactly those two identity references, explicitly declined
another QA run, and directed lane closure, commit and push. Governor closeout is
under that explicit operator disposition, **not a new independent QA PASS**.
The last reviewer verdict and all prior evidence are preserved. Exact approvals,
proof pointers, rollback hashes and remaining work are recorded in
[`telemetry-observer-closeout-20261006.md`](../../references/telemetry-observer-closeout-20261006.md).
No efficiency, cost or quality improvement is asserted.
