# Workspace automation layer

Automation IDs A1-A9. A1, A2, A2-full, A3, A4, A5, A6, A7, A8, A9 are implemented
as scheduled jobs; A10+ remain deferred until additional workspace maturity.

## Authoritative code (version-controlled)

- `scripts/cron_wiki_regen.py` — A1 logic (publish + report; silent on fresh, alerts on degraded/fail)
- `scripts/cron_health_check.py` — A2 logic (fast operational gate: routing freshness + wiki validate + alias sweep + cron registration)
- `scripts/cron_test_gate.py` — A2-full logic (full `run_checks.py --skip-smoke --record-telemetry` correctness gate)
- `scripts/cron_routing_cache_sweep.py` — A3 logic (evicts expired routing cache rows and rows with mismatched source signatures)
- `scripts/cron_archive_stale_workflows.py` — A4 logic (flags terminal-state workflows older than 30 days and archives derived capsules)
- `scripts/cron_routing_refresh.py` — A5 logic (regenerates routing index and capsules hourly)
- `scripts/cron_alias_sweep.py` — A6 logic (reports aliases pointing to non-existent workflows)
- `scripts/cron_queue_hygiene.py` — A7 logic (removes terminal-state workflows older than 30 days from the authoritative queue and archives them)
- `scripts/cron_telemetry_harvest.py` — A8 logic (runs `run_checks.py --record-telemetry` daily to populate `run_metrics`)
- `scripts/cron_claim_drift_check.py` — A9 logic (monitors expiring claims, tampered workflow runs, and stale replays)
- `scripts/cron_registration_validator.py` — verifies every cron wrapper resolves to an existing repo script (run by tests and A2)
- `tests/test_cron_wrappers.py` — pins the exit-code + output contract for A1 and A2 wrappers
- `tests/test_queue_hygiene.py` — pins A7 and A2-full wrapper contracts
- `tests/test_cron_registration_validator.py` — pins the A9 wrapper + registration contract

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
| A5 | routing index refresh | hourly | regenerates `state/workflow-routing-index.json` and capsules |
| A6 | alias dead-target sweep | daily 09:30 | reports aliases that no longer point to active workflows |
| A7 | authoritative queue hygiene | weekly Sunday 11:00 | removes terminal workflows older than 30 days from `active_workflows.json`; archives them |
| A8 | telemetry harvest | daily 06:30 | runs `run_checks.py --record-telemetry` to populate `run_metrics` |
| A9 | claim-drift check | hourly at :45 | monitors expiring claims, tampered workflow runs, and stale replays |

All are `no_agent` (no LLM) and print to STDOUT only on failure/degraded, so a healthy
run delivers nothing.

## Delivery

All jobs use `deliver: origin` so failure/degraded output reaches the originating
chat. A job with no gateway-connected channel falls back to local save (no
notification); update delivery to a connected platform (e.g. `deliver='telegram'`
or `deliver='all'`) once a channel is wired.

## Deferred

- **WF-1000 scheduled resume.** Blocked: "No approved external connector
  configuration yet for phase 3." Wrap `default_resume_command` in a cron only
  after an approved source catalog + connector exists.
- **A10 — Telemetry scorecard report.** Needs 1-2 weeks of A8 data before the
  weekly scorecard is meaningful. Build `scripts/cron_telemetry_scorecard.py` once
  there are 10+ `run_metrics` rows.
- **A11 — Improvement signal sweep.** Needs a `follow_ups` table in
  `canonical/schema.sql` and enough historical telemetry to detect recurring
  patterns.
- **A12 — Scorecard review / promotion gate.** Model-driven weekly synthesis of
  the scorecard + open follow-ups into routed proposals. Defer until A11 is
  producing real signals.
