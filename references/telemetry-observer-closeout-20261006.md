# Telemetry observer closeout — 2026-10-06

Owner: Governor / WF-1200. Parent job: `telemetry-improvement-20261006`.
Scope: verified baseline diagnostics and per-attempt observer attribution;
operator-approved six-profile rollout and exact QA identity-text correction.
This tracked record preserves consequential approvals; local evidence under
`derived/otel-efficiency/2026-10-06/` remains git-ignored.

## Operator decisions and final disposition

The operator directed implementation and code changes to remain with main,
with the existing QA bot pinned to `openai-codex/gpt-6.1-sol`. No Implementer
write exception, qualification, fleet binding or fallback policy changed.

The operator explicitly approved deploying the reviewed observer to `default`,
`architect`, `implementer`, `seniorengineer`, `qa`, and `researcher`, keeping model
routes and the October 12 expiry unchanged. Deployment did not change configs.

After the rollout review refused acceptance because its identity text was
stale, the operator explicitly approved changing the two `GPT-5.6-Sol`
references in the QA profile's `SOUL.md` to `GPT-6.1-Sol`, declined another QA
run, and directed closure, commit and push. Governor closes the deployment lane
on verified technical evidence and that explicit operator disposition.
**The final identity/documentation correction was not re-reviewed; the last
rollout QA verdict remains blocked/FAIL, not a newly asserted PASS.** This is a
job-specific disposition, not standing authority to bypass independent review.

## Changes and verification

- `scripts/feedback_evaluation_loop.py`: bounded failure categories for missing
  pytest, collection/interruption, no tests, test failure, launch failure and
  timeout; exit code and Python version without raw stderr/exception payloads.
- `scripts/hermes_otel_efficiency.py`: role on API/tool children; separate
  opening/ending model/provider labels; conflicting recognized endpoints retain
  evidence but canonicalize to unknown. Observable failed primary and fallback
  invocations remain separate. Five current tool names join the safe allowlist.
- Corresponding regression additions in both test files. Existing assertions
  retained. Unknown attribution is never replaced with configured model intent.
- Six failed baseline candidates recovered via the supported refresh path under
  Python 3.13, initially 127 tests passing. This is baseline recovery, not candidate
  acceptance or permanent cron remediation.
- Main/Governor ran focused baseline tests (129 passed), related observer/runner
  tests (210 passed), then full pytest: **1088 passed and 80 subtests passed** in
  227.55 seconds. Source fingerprint unchanged before, after and on readback:
  `ab3cd1389d931723855b33f76a8d133401dadfc81060cc334130df9f6ec2a958`.
  The earlier 180-second timeout remains recorded, superseded by this complete run.

Source-only QA returned PASS_WITH_NOTES with no HIGH/MEDIUM findings:
- Baseline: actual Sol session `20261005_175041_73d579`.
- Attribution: actual Sol session `20261005_180941_3a9c33`.

The main author is session-reported `openai-codex/gpt-6-astra`; this is distinct
from independently verified reviewer session models and from registry defaults.
Optional QA notes (expanded attribution assertions, dense recognized-packet
budget checks) remain recorded; no silent expansion of observer caps occurred.

## Deployment and native capture

Only each existing `hermes-otel-pilot/hermes_otel_efficiency.py` was replaced.
Reviewed/installed SHA256:
`55579544889e7bf4192c46bd6fde34108a2b54eb086a37a4f129c2c30709295d`.
All six configurations remained byte-for-byte unchanged, preserving model
routes, permissions and `2026-10-12T14:00:00Z` expiry. Router, manifest and
collector configuration/PID remained unchanged. No desktop, gateway or
collector restart occurred.

All six fresh-process real-settings activation probes registered seven hooks,
and all six plugin doctors exited zero. Exact collector readback found
**12 controlled traces / 36 spans** with complete capture, explicit child roles,
new model metadata and tool labels. Synthetic labels prove capability, not actual
model use or natural-workload performance.

A real fresh native QA call also passed exact-context collector verification:
- Session: `20261005_185202_1df690`.
- Actual model: `openai-codex/gpt-6.1-sol`.
- Trace: `611370e86e90bf6fb8878ee6de75b687`.
- One turn and one API child, complete coverage/attribution, healthy export,
  no ambiguity, malformed events or loss counters.

That same session's substantive rollout review withheld acceptance over stale
`SOUL.md` identity. Runtime identity verification and capture PASS do not
convert its blocked verdict into QA acceptance.

## Identity correction and rollback

Profile-local path: `%LOCALAPPDATA%/hermes/profiles/qa/SOUL.md`.
The exact two ASCII model-name replacements were verified by comparing the
entire post-edit byte sequence to the backed-up file with only those replacements.
No other content or profile configuration changed.

- Before SHA256: `11953da3bfbcf4e7acd6ca4f746fdf70f2352c7d4b2c862f18e9407c9d20106e`.
- After SHA256: `9a74475ef6c609f174f228a2e4d82de2c5aa6e94bd87f04c626e8492fb6fddaa`.
- Local backup: `derived/otel-efficiency/2026-10-06/closeout/qa-SOUL.before.md`.

Each original observer module is backed up under
`derived/otel-efficiency/2026-10-06/deploy/backup/<profile>/hermes_otel_efficiency.py`.
All six backups were read back and matched original SHA256
`d563477217da318419df8630959866d00b389780842c2519127b68b4157cf0fd`.
Rollback, if authorized, restores only the matching module after checking current
hash; never restore a whole profile config (none changed).

## Durability and remaining limits

Source/tests, this record and the refreshed efficiency status are Git-committable.
Profile-local plugins, `SOUL.md`, skills, canonical DB and lane register are not
part of the repository commit. Their exact changes are backed by local artifacts
and this narrative, not falsely represented as files in the remote repository.
Unrelated pre-existing source, workflow, audit and wiki changes are excluded.

Closed scope is the implemented diagnostics/attribution rollout. The broader
improvement plan remains incomplete:
- Serving desktop/gateway processes may retain the old module until an approved
  reload; fresh native QA capture does not prove those processes refreshed.
- Scheduled Python/pytest environment repair is not implemented.
- The OTEL-to-feedback adapter and matched before/after efficiency evaluation
  are not implemented. No efficiency, cost, quality or speed gain is claimed.
- Models outside the approved labels remain unknown. Capture completeness and
  attribution completeness must be considered separately.

Evidence owners: `baseline/qa-record.json`, `attribution/qa-record.json`,
`full-verification/result.json`, `full-verification/output.txt`,
`deploy/deployment.json`, `deploy/collector-readback.json`,
`deploy/native-qa-readback.json`, `deploy/qa-record.json`, and the closeout
artifacts beneath `derived/otel-efficiency/2026-10-06/`.
