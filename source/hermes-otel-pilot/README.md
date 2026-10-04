# Hermes-only OTLP observer pilot

The historical diagnostic contract below remains unchanged. The separately
operator-approved Governor/Implementer/QA efficiency extension is specified in
[`efficiency.md`](efficiency.md); it is opt-in and requires its own deployment
acceptance. Do not infer measurement coverage from the historical pilot status.

Canonical implementation: `scripts/hermes_otel_pilot.py`, copied byte-for-byte
as the approved default profile plugin's `__init__.py`. Manifest: this directory's
`plugin.yaml`. Contract/authorization: `derived/otel-pilot/2026-10-03/plan.json`.

The observer exports only finalized turns containing `read_file_missing_path`.
API/tool attempts are correlated by opaque IDs held as process-keyed HMACs;
original IDs are never emitted. Missing IDs do not establish API/tool links.
Turn/span IDs are fresh random OTEL identifiers, not original application IDs.

Export: fixed `http://127.0.0.1:4318/v1/traces`, OTLP/HTTP JSON. No SDK install,
redirect, ambient proxy, authentication header, retry or remote destination.
Callbacks perform no network I/O. The exporter is asynchronous, fail-open, and
uses a 0.4-second socket timeout; per-operation socket timeout is not a strict
whole-request wall-clock deadline. Process shutdown waits at most one second.

Hard bounds: activation at most 24 hours; eight active turns; 64 completed
attempts and 64 open attempt records per turn; four queued (exported) target traces per
observer instance (reload resets this budget); queue capacity four; request 64 KiB; response 4 KiB. Incomplete coverage
and drops are explicit. No background refresh job, additional database or
profile-side per-turn files are added.

UTC activation is converted to a monotonic runtime deadline, so a backward clock
adjustment cannot extend an observer's lifetime. Dropped and ambiguous openings
are counted and invalidate complete-coverage claims; ambiguous API identities
do not create links. An export begun before expiry may finish afterward; queued
exports are discarded at expiry.

Activation uses only this plugin's settings (`approved_home`,
`expires_at_utc`) and the supported plugin-enable command. Every hook verifies
current profile home. Other profiles cannot export through this observer.
Expired or missing activation is inert. The plugin adds no tools or prompt
sections, does not override tools, and never changes model/provider/retry policy.
Rollback: `hermes plugins disable hermes-otel-pilot` (default profile only).
Existing processes may need normal plugin reload/new-session discovery; do not
restart the OpenClaw collector. Disable/unload requests observer shutdown;
control-plane reload success alone is not proof of old-thread termination.

The verifier runs real native Hermes file/search tools and its real post-tool
metadata emitter, with process-local hook delivery redirected to the real
SQLite v3 observer in a scratch database plus this OTLP observer. This avoids
polluting production candidate counts. Exact generated traces are read back
from the existing collector file with service and trace-ID filtering. The
verification canary is labeled `controlled-tool-canary`; it makes no provider
call and must never be presented as observed production API tracing.

Success question: can ordered traces distinguish discovery before versus after
a missing-file read when SQLite's successful-call-detail cap omitted discovery?
Compare against the full v3 row and retained diagnostics, not just aggregates.
A passing controlled comparison establishes diagnostic capability, not root
cause, discovery/read path equality, or a production reliability improvement.
No expansion or automatic harness fix is authorized by this pilot.

## Current acceptance status

The repaired source has passed Sonnet 5.5 code-only independent review and has
been copied to the default-profile plugin with source/deployed SHA-256 equality
recorded in `derived/otel-pilot/2026-10-03/repaired-deployment.json`.
The operator-enabled bounded pilot expires at `2026-10-04T20:21:35Z`.
At 2026-10-03T21:55Z it was temporarily disabled and gateway-reloaded for a
negative-control turn. A real gateway missing read in that window exported zero new
roots (live root count stayed 1): `negative-control-20261003.json`.
Restored 2026-10-03T22:01:22Z with `--no-allow-tool-override`; selected config
and gateway reload activations (7 hooks) verified, expiry unchanged, source/deployed
hash equal, collector PID 27324 and config hash unchanged:
`restore-binding-20261003.json`. Each reload resets the per-observer trace budget.
A controlled gateway injection produced one live root with 11 children and one
missing-read event, valid intervals and complete=true. It is not a natural incident;
it and both verifier canaries are listed in `controlled-injection-exclusion-list.json`.
Post-deployment Sonnet review (14:50 local) was PASS-WITH-NOTES for the pilot, not
full acceptance. Final focused Sonnet review of the M1-M3 evidence (22:23-22:26Z):
PASS-WITH-NOTES, no HIGH; M1 and M2 closed within limits, M3 closed subject to the
roster wording below: `final-independent-review.json`.
Post-restore positive control PASS: a primary gateway turn at 2026-10-03T22:09Z with a
deliberate nonexistent `read_file` (controlled injection) produced exactly one new root
after the 22:01:45Z restore readback; 16 children (9 API, 7 tool) with exactly one
`hermes.tool` `read_file` child categorized `read_file_missing_path`; complete=true,
zero dropped/ambiguous openings, contiguous sequence, valid intervals, no duplicate
root/child records, no unknown attributes. Collector listener PID 27324 (start
20:18:23Z) and config hash `c9b51de5...23e6` unchanged; source/deployed SHA-256
`67f526b3...4a61` equal: `positive-control-20261003.json`. This is behavioral
no-export/one-export evidence only; it does not prove any old observer thread terminated.
All four controlled traces (two verifier canaries, two gateway injections) are in the
internal-only `controlled-injection-exclusion-list.json`; daily reviews must exclude them.
Governor provenance was explicitly ratified (Option A); historical author identities
remain unchanged. QA roster reconciliation, factual state (22:27Z): the tracked test
files modified against git HEAD show 119 added and 0 removed `assert` lines. The roster
test `tests/test_fleet_roster_block.py` and `scripts/fleet_roster_block.py` are
untracked and have no baseline, so "no assertions weakened" is UNVERIFIED for them.
Their current hashes are in `post-review-reconciliation-20261003.json`. Full suite after the
positive control: 852 passed, 30 subtests passed (runtime venv, 22:15:57-22:17:00Z),
with source hash unchanged before and after and equal to prior records:
`test-evidence-20261003.json`.

Historical recovery is RETIRED-SUPERSEDED, not passed: `historical-recovery-retirement.json`.
It was not executed post-fix; the original `recovered-diagnosis-proof.json` is preserved
as pre-repair historical evidence and is not cited as acceptance evidence. The
diagnostic success question rests on the controlled comparison, not the gateway
delivery checks. Full deployment acceptance and lane closure are decided by the
primary Governor after final independent QA; this README does not record acceptance.

At expiry the enabled configuration can remain present while capture is inert.
The closeout must request operator-authorized disable and gateway reload; do not
stop or reconfigure the shared collector or automatically extend activation.

The verifier rejects optimized Python before live verification. Its bounded,
read-only `--recover-database` mode can re-derive archived sequence answers from
the original SQLite and collector records. That path is retired as superseded and
is not an acceptance gate. Recovery does not send new telemetry or establish current
live/API coverage. Preserve the original recovered artifact as historical evidence,
not as post-fix acceptance.
