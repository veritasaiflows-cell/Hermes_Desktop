# Workspace automation layer

Automation IDs A1-A17 are implemented as deterministic jobs; schedules and pause
state are listed below. A8 is
the feedback/evaluation sweep; it creates review-only candidates from repeated
metadata and never applies a production harness change. A18 has operator-approved
nightly code-only publication: its profile launcher requests `--promote-once`,
building and validating an isolated candidate when the selected graph is not fresh.

## Authoritative code (version-controlled)

- `scripts/cron_wiki_regen.py` — A1 logic (reattest + publish + report; silent on fresh, alerts on degraded/fail)
- `scripts/cron_health_check.py` — A2 logic (fast operational gate: routing freshness + wiki validate + alias sweep + cron registration + recall-index liveness)
- `scripts/cron_test_gate.py` — A2-full logic (full `run_checks.py --skip-smoke --record-telemetry` correctness gate)
- `scripts/cron_routing_cache_sweep.py` — A3 logic (evicts expired routing cache rows and rows with mismatched source signatures)
- `scripts/cron_archive_stale_workflows.py` — A4 logic (flags terminal-state workflows older than 30 days and archives derived capsules)
- `scripts/cron_routing_refresh.py` — A5 logic (regenerates routing index/capsules and fails closed on timeout or malformed output)
- `scripts/cron_alias_sweep.py` — A6 logic (reports aliases pointing to non-existent workflows)
- `scripts/cron_queue_hygiene.py` — A7 logic (removes terminal-state workflows older than 30 days from the authoritative queue and archives them)
- `scripts/feedback_evaluation_loop.py` — reads canonical + turn telemetry, writes compact derived report, records review candidates/baselines, evaluates prepared candidates, and records explicit human decisions
- `scripts/cron_telemetry_harvest.py` — A8 logic (consumes telemetry after A2-full, runs the feedback/evaluation refresh, and surfaces only pending-review candidate IDs)
- `scripts/cron_claim_drift_check.py` — A9 logic (monitors expiring claims, tampered workflow runs, and stale replays)
- `scripts/cron_graph_freshness.py` — A10 logic (graph orphan/duplicate sweep + coverage drift; alerts when canonical records lack expected edges)
- `scripts/cron_lane_lease_check.py` — coordination watchdog (alerts on expiring, expired, or missing active-lane leases)
- `scripts/workspace_status.py` — A11 logic (single-command JSON operating brief; runs gates serially to avoid shared-state races)
- `scripts/cron_retrieval_refresh.py` — A12 logic (refreshes both retrieval indexes, then synchronizes routing through A5 even after degraded/failed refresh attempts)
- `scripts/cron_canonical_integrity.py` — A13 logic (checks canonical SQLite integrity)
- `scripts/cron_graphify_artifact_monitor.py` — A15 logic (read-only Graphify freshness monitor)
- `scripts/cron_graphify_mcp_contract.py` — A16 logic (exact local facade/config/allowlist watchdog plus raw MCP schema/data-boundary probe)
- `scripts/cron_graphify_version_advisory.py` — A17 logic (read-only Graphify release advisory; never installs)
- `scripts/cron_graphify_code_refresh.py` — A18 explicit one-shot transaction (snapshot, candidate build/validation, source-drift recheck, atomic selection, and rollback on failed post-publication freshness)
- `scripts/graphify_generation.py` — same-volume immutable generation, pointer, rollback, artifact-integrity, and kernel-lock primitives used by A18
- `scripts/graphify_mcp_facade.py` — fixed-project read-only MCP server that resolves the selected generation once and rejects `project_path`
- `scripts/graph_backfill.py` — one-time idempotent backfill of durable graph edges from pre-graph canonical records
- `scripts/cron_registration_validator.py` — verifies every cron wrapper resolves to an existing repo script (run by tests and A2)
- `tests/test_cron_wrappers.py` — pins the exit-code + output contract for A1 and A2 wrappers
- `tests/test_queue_hygiene.py` — pins A7 and A2-full wrapper contracts
- `tests/test_cron_registration_validator.py` — pins the A9 wrapper + registration contract
- `tests/test_workspace_status.py` — pins A11 healthy/degraded/warning contracts
- `tests/test_graphify_cron_automation.py` — pins A15-A18 silence, drift, isolation, rollback, and contract behavior
- `tests/test_note_state_drift.py` — pins A19 detection, derived-field exclusion, and acknowledgment expiry

## Cron launchers (in ~/AppData/Local/hermes/scripts/, required location for cron)

- `a1_wiki_regen.py` -> execs `scripts/cron_wiki_regen.py`
- `a2_green_gate.py` -> execs `scripts/cron_health_check.py`
- `a2_full_test_gate.py` -> execs `scripts/cron_test_gate.py`
- `a3_routing_cache_sweep.py` -> execs `scripts/cron_routing_cache_sweep.py`
- `a4_archive_stale_workflows.py` -> execs `scripts/cron_archive_stale_workflows.py`
- `a5_routing_refresh.py` -> execs `scripts/cron_routing_refresh.py`
- `a6_alias_sweep.py` -> execs `scripts/cron_alias_sweep.py`
- `a7_queue_hygiene.py` -> execs `scripts/cron_queue_hygiene.py`
- `a8_telemetry_harvest.py` -> execs `scripts/cron_telemetry_harvest.py`
- `a9_claim_drift_check.py` -> execs `scripts/cron_claim_drift_check.py`
- `a10_graph_freshness.py` -> execs `scripts/cron_graph_freshness.py`
- `a11_workspace_status.py` -> execs `scripts/workspace_status.py`
- `a12_retrieval_refresh.py` -> execs `scripts/cron_retrieval_refresh.py`
- `a13_canonical_integrity.py` -> execs `scripts/cron_canonical_integrity.py`
- `a14_lane_lease_check.py` -> execs `scripts/cron_lane_lease_check.py`
- `a15_graphify_artifact_monitor.py` -> execs `scripts/cron_graphify_artifact_monitor.py`
- `a16_graphify_mcp_contract.py` -> execs `scripts/cron_graphify_mcp_contract.py`
- `a17_graphify_version_advisory.py` -> execs `scripts/cron_graphify_version_advisory.py`
- `a18_graphify_code_refresh.py` -> execs `scripts/cron_graphify_code_refresh.py`
- `a19_note_state_drift.py` -> execs `scripts/check_note_state_drift.py`

These are thin launchers so the real logic stays in the repo. Launchers fall
back to a repo root discovered from the launcher path if the hardcoded `TARGET`
is missing.

## Scheduled jobs (no_agent, silent-on-green)

| ID | Job | Schedule | Behaviour |
|----|-----|----------|-----------|
| A1 | wiki freshness regen | daily 08:00 local | republishes wiki; alerts only on failure |
| A2 | green-gate watchdog | every 4h | fast operational gate: routing + wiki + alias + cron registration + recall-index liveness; alerts on routing/alias/registration failure; stale wiki/vector warn softly; graph depth checks live on A10/A11 |
| A2-full | code-correctness gate | daily 06:00 | runs full test/smoke suite with telemetry recording; alerts on regression; defers silently (exit 0) when active write lanes touch the correctness surface; pytest budget 240s inside the 300s gate with partial-output logging, no double-rerun |
| A3 | routing cache sweep | daily 09:00 | evicts expired and signature-mismatched `canonical/efficiens.db` routing_cache rows |
| A4 | stale-workflow archive sweep | weekly Sunday 10:00 | flags terminal workflows older than 30 days; archives capsules |
| A5 | routing index refresh | hourly at :35 | regenerates `state/workflow-routing-index.json` and capsules with bounded fail-closed reporting |
| A6 | alias dead-target sweep | daily 09:30 | reports aliases that no longer point to active workflows |
| A7 | authoritative queue hygiene | weekly Sunday 11:00 | removes terminal workflows older than 30 days from `active_workflows.json`; archives them |
| A8 | feedback/evaluation refresh | daily 06:30 | consumes canonical + turn telemetry after A2-full, emits review-only candidates and one shared baseline cohort when new candidates appear; logs an explicit stderr note when the latest run_checks proof is missing, rejected, or stale |
| A9 | claim-drift check | hourly at :45 | monitors expiring claims, tampered workflow runs, and stale replays |
| A10 | graph freshness | hourly at :15 | graph orphan/duplicate sweep + coverage drift; alerts when canonical records lack expected edges |
| A11 | workspace status brief | every 4h at :15 | single-command JSON operating brief; alerts on hard failures; cron launcher passes `--compact` so scheduler error stores stay small (full brief persisted under `tmp/`) |
| A12 | retrieval index refresh | every 6h at :30 | refreshes exact + semantic indexes and invokes A5 after successful, degraded, or exceptional refresh attempts |
| A13 | canonical integrity | daily 07:00 | verifies canonical SQLite integrity |
| A14 | lane lease watchdog | hourly at :55 | alerts on missing, expiring, or expired active-lane leases; saves locally |
| A15 | Graphify artifact monitor | hourly at :20 | alerts on stale or unavailable selected Graphify artifacts; pages once per stale episode (digest-keyed) then heartbeats on stderr until healed or the report changes; alert payload is compact counts+paths |
| A16 | Graphify MCP contract | paused (daily 07:25 retained) | live-connector watchdog paused with the operator-disabled default-profile connector; isolated A18 candidate validation remains available |
| A17 | Graphify version advisory | Sunday 12:00 | reports only newer stable Graphify releases; never installs |
| A18 | Graphify code refresh | daily 02:10 Arizona | standing operator-approved code-only publication; launcher passes `--promote-once`, skips fresh graphs, and selects a candidate only after all gates pass |
| A19 | Note/state drift | daily 08:40 | every authored blocker, stop line, and `effective_status` in `state/workflows/WF-*.json` must appear in that workflow's continuity note, and the generated role roster in the WF-1200 note must equal what `state/fleet-role-registry.json` renders (`scripts/fleet_roster_block.py`). Also runs in the fast startup gate (`workspace_status.py --fast`, label `note_drift`) as a warning-class check; the heartbeat remains the scheduled owner. Regenerate the roster with `python scripts/fleet_roster_block.py --write` after any registry change |

All are `no_agent` (no LLM). They print to STDOUT only on failure/degraded,
except A8 intentionally emits a compact candidate ID when human review is
required, A17 emits an update advisory when a strictly newer release exists, and
A18 emits a promotion receipt after a successful rebuild; ready/current runs remain silent.

A18 never invokes semantic extraction, `graphify update`, or any Graphify writer
against the selected or legacy `graphify-out/` artifact. Standing operator approval
(2026-10-06 UTC) authorizes the existing nightly launcher to pass `--promote-once`
for code-only publication; unflagged direct invocations remain check-only. It acquires a kernel-backed writer
lock before source fingerprinting, creates a Git-visible source snapshot, builds an
isolated code-only candidate, reconciles/diagnoses/baselines it, checks the fixed MCP
facade against that candidate, rechecks source hashes, and atomically replaces one
pointer only after the complete immutable generation is accepted. A failed
post-publication freshness check restores the prior pointer. Existing MCP clients
remain bound to their startup generation; a fresh selected graph is not proof that
a long-running client reconnected. Verify the serving generation before use.
Hermes configuration changes still require explicit operator approval.

### Graphify profile deployment and reversible connector disablement

The operator authorized nightly code-only publication on 2026-10-06 UTC and
subsequently directed: "continue with disabling the MCP connector". These are
separate approvals: connector disablement does not withdraw A18 publication
authority or enable semantic/cloud-model extraction.

Default-profile state verified at 2026-10-06T14:53:32Z:

- `mcp_servers.graphify.enabled` is `false`; the server entry, command,
  arguments and seven-tool selection remain intact for rollback.
- A16 `9d97cea727e1` is paused to avoid false live-connector alarms.
- A15 `bdaad284019a` remains enabled; A18 `1d045780243a` remains enabled at
  `10 2 * * *` Arizona. Their scheduler records were unchanged by disablement.
- A fresh Hermes process registered zero Graphify tools or connections. The
  existing chat's connection/tool cache was not forcibly unloaded; restart or
  a supported reload is needed to release an already-running connection.
- The selected generation `g-20261006T141150Z-2264476d3aa5` was fresh and its
  graph and selector hashes stayed unchanged. The isolated seven-tool candidate
  contract passed despite connector disablement, and the deployed A18 launcher
  returned `GRAPHIFY CODE REFRESH SKIP ... reason=artifact_current`.

The nightly deployment and rollback repair evidence is in
`derived/graphify-nightly/2026-10-06/closeout.json`; the operator-directed
disablement readbacks are in
`derived/graphify-mcp-disable/2026-10-06/closeout.json`. These gitignored receipts
describe local execution, not files shipped by a source commit. The rollback
repair was independently reviewed before rollout; the later configuration
disablement was deterministically verified, not relabeled as independent QA.

To reverse the disablement, use
`hermes config set mcp_servers.graphify.enabled true`, resume A16 with
`hermes cron resume 9d97cea727e1`, and refresh the owning Hermes process. Local
Graphify artifacts, profile launchers/configuration, cron state and profile-local
skills are outside this workspace's Git history; a checkout alone does not
reproduce their deployment. A15/A18 and local graph access do not require the
user-facing connector to be enabled.

## Feedback/evaluation loop (A8)

```text
A2-full correctness telemetry + profile-local turn telemetry
  -> A8 `feedback_evaluation_loop.py refresh`
  -> derived/feedback-evaluation/latest.json
  -> canonical tasks + baseline validation_results + events
  -> explicit human `rebaseline` for a pending candidate after harness maintenance and before candidate work
  -> explicit `evaluate` against the fixed feedback-harness-v2 cohort
  -> explicit human `decide`
  -> canonical decisions/events history
```

Repeated category-only signals require at least three observations in seven
days. A8 can create candidates and baselines, but cannot evaluate a prepared
change, accept/reject it, alter source code, or change model/provider settings.
Those actions require explicit CLI invocation and existing workspace approval
rules. `rebaseline <candidate-id> --reviewer <token> --reason <token>` is the
explicit human path for a pending candidate whose harness baseline must be
renewed after maintenance and before the candidate change. It rejects evaluated
or decided candidates and records the replaced baseline validation ID, reviewer,
and reason in the event history. `accepted` requires passing baseline and
candidate cohort evidence with no additional test failures, identical test count,
and no more than 20% cohort duration regression. Candidate evaluation pins the
exact baseline validation ID, so a later baseline row cannot silently change the
decision comparison. A failed or zero-test baseline makes A8 exit nonzero,
remains visible as `baseline_failed`, and is retried by the next refresh. A5 fails closed on timeout or malformed router
JSON. A12 invokes it after every completed refresh and best-effort after exceptions,
so partial or degraded retrieval changes cannot silently leave routing stale.

## Delivery

All jobs except A14 use `deliver: origin` so failure/degraded output reaches the
originating chat. A14 intentionally uses `deliver: local`; its hourly lease
watchdog result is retained without chat delivery. An origin-delivered job with
no gateway-connected channel falls back to local save (no notification); update
delivery to a connected platform (e.g. `deliver='telegram'` or `deliver='all'`)
once a channel is wired.

## Deferred

- **WF-1000 scheduled resume.** Blocked: "No approved external connector
  configuration yet for phase 3." Wrap `default_resume_command` in a cron only
  after an approved source catalog + connector exists.
- **Autonomous promotion or model-driven scorecard synthesis.** Candidates are
  intentionally human-gated; no scheduled job may apply a harness change.
- **Additional follow-up table or dashboard.** Reuse `tasks`,
  `validation_results`, `decisions`, `events`, and the compact derived report
  until evidence shows they are insufficient.
