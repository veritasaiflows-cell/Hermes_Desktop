# Workspace automation layer

Automation IDs A1-A17 are implemented as scheduled, deterministic jobs. A8 is
the feedback/evaluation sweep; it creates review-only candidates from repeated
metadata and never applies a production harness change. A18 is installed as a
read-only fail-closed hold and remains paused; no Graphify writer is active.

## Authoritative code (version-controlled)

- `scripts/cron_wiki_regen.py` — A1 logic (publish + report; silent on fresh, alerts on degraded/fail)
- `scripts/cron_health_check.py` — A2 logic (fast operational gate: routing freshness + wiki validate + alias sweep + cron registration)
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
- `scripts/cron_graphify_mcp_contract.py` — A16 logic (exact local stdio/config/allowlist watchdog plus raw MCP schema/data-boundary probe)
- `scripts/cron_graphify_version_advisory.py` — A17 logic (read-only Graphify release advisory; never installs)
- `scripts/cron_graphify_code_refresh.py` — A18 hold (reads freshness, reports `atomic_publication_not_activated`, and never invokes a writer)
- `scripts/graph_backfill.py` — one-time idempotent backfill of durable graph edges from pre-graph canonical records
- `scripts/cron_registration_validator.py` — verifies every cron wrapper resolves to an existing repo script (run by tests and A2)
- `tests/test_cron_wrappers.py` — pins the exit-code + output contract for A1 and A2 wrappers
- `tests/test_queue_hygiene.py` — pins A7 and A2-full wrapper contracts
- `tests/test_cron_registration_validator.py` — pins the A9 wrapper + registration contract
- `tests/test_workspace_status.py` — pins A11 healthy/degraded/warning contracts
- `tests/test_graphify_cron_automation.py` — pins A15-A18 silence, drift, isolation, rollback, and contract behavior

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

These are thin launchers so the real logic stays in the repo. Launchers fall
back to a repo root discovered from the launcher path if the hardcoded `TARGET`
is missing.

## Scheduled jobs (no_agent, silent-on-green)

| ID | Job | Schedule | Behaviour |
|----|-----|----------|-----------|
| A1 | wiki freshness regen | daily 08:00 local | republishes wiki; alerts only on failure |
| A2 | green-gate watchdog | every 4h | fast operational gate: routing + wiki + alias + cron registration; alerts on routing/alias/registration failure |
| A2-full | code-correctness gate | daily 06:00 | runs full test/smoke suite with telemetry recording; alerts on regression |
| A3 | routing cache sweep | daily 09:00 | evicts expired and signature-mismatched `canonical/efficiens.db` routing_cache rows |
| A4 | stale-workflow archive sweep | weekly Sunday 10:00 | flags terminal workflows older than 30 days; archives capsules |
| A5 | routing index refresh | hourly at :35 | regenerates `state/workflow-routing-index.json` and capsules with bounded fail-closed reporting |
| A6 | alias dead-target sweep | daily 09:30 | reports aliases that no longer point to active workflows |
| A7 | authoritative queue hygiene | weekly Sunday 11:00 | removes terminal workflows older than 30 days from `active_workflows.json`; archives them |
| A8 | feedback/evaluation refresh | daily 06:30 | consumes canonical + turn telemetry after A2-full, emits review-only candidates and one shared baseline cohort when new candidates appear |
| A9 | claim-drift check | hourly at :45 | monitors expiring claims, tampered workflow runs, and stale replays |
| A10 | graph freshness | hourly at :15 | graph orphan/duplicate sweep + coverage drift; alerts when canonical records lack expected edges |
| A11 | workspace status brief | every 4h at :15 | single-command JSON operating brief; alerts on hard failures |
| A12 | retrieval index refresh | every 6h at :30 | refreshes exact + semantic indexes and invokes A5 after successful, degraded, or exceptional refresh attempts |
| A13 | canonical integrity | daily 07:00 | verifies canonical SQLite integrity |
| A14 | lane lease watchdog | hourly at :55 | alerts on missing, expiring, or expired active-lane leases; saves locally |
| A15 | Graphify artifact monitor | hourly at :20 | alerts on stale or unavailable legacy artifacts |
| A16 | Graphify MCP contract | daily 07:25 | checks the pinned local stdio configuration, selected/advertised tools, raw schemas, and `graph_stats`; alerts while stock schemas expose `project_path` |
| A17 | Graphify version advisory | Sunday 12:00 | reports only newer stable Graphify releases; never installs |
| A18 | Graphify code refresh hold | daily 02:10 (paused) | reads freshness and fails closed with `atomic_publication_not_activated`; never executes a writer |

All are `no_agent` (no LLM). They print to STDOUT only on failure/degraded,
except A8 intentionally emits a compact candidate ID when human review is
required and A17 emits an update advisory when a strictly newer release exists;
ready/current runs remain silent.

A18 never runs semantic extraction, `graphify update`, or any publication path.
Activation requires immutable same-volume generations, an atomic pointer switch,
a fixed-project MCP facade that rejects `project_path`, kernel-backed coordination,
candidate verification, source-drift rechecks, recovery tests, and explicit
operator approval for the Hermes configuration change. Until then, the stale
legacy graph remains visible as a warning rather than being refreshed unsafely.

## Feedback/evaluation loop (A8)

```text
A2-full correctness telemetry + profile-local turn telemetry
  -> A8 `feedback_evaluation_loop.py refresh`
  -> derived/feedback-evaluation/latest.json
  -> canonical tasks + baseline validation_results + events
  -> explicit `evaluate` against the fixed feedback-harness-v2 cohort
  -> explicit human `decide`
  -> canonical decisions/events history
```

Repeated category-only signals require at least three observations in seven
days. A8 can create candidates and baselines, but cannot evaluate a prepared
change, accept/reject it, alter source code, or change model/provider settings.
Those actions require explicit CLI invocation and existing workspace approval
rules. `accepted` requires passing baseline and candidate cohort evidence with
no additional test failures, identical test count, and no more than 20% cohort
duration regression. Candidate evaluation pins the exact baseline validation ID,
so a later baseline row cannot silently change the decision comparison. A failed
or zero-test baseline makes A8 exit nonzero, remains visible as `baseline_failed`,
and is retried by the next refresh. A5 fails closed on timeout or malformed router
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
