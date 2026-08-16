# Workspace automation layer

Automation IDs A1-A5 from the workspace efficiency audit. A1, A2, A5 are
implemented; A3, A4 are deferred with explicit triggers.

## Authoritative code (version-controlled)

- `scripts/cron_wiki_regen.py` — A1 logic (publish + report; silent on fresh, alerts on degraded/fail)
- `scripts/cron_health_check.py` — A2 logic (run_checks --skip-smoke + wiki validate; stale wiki is a soft warning)
- `tests/test_cron_wrappers.py` — pins the exit-code + output contract for both wrappers
- `.git/hooks/pre-commit` — A5 gate (NOT version-controlled; git hooks live outside the tree)

## Cron launchers (in ~/AppData/Local/hermes/scripts/, required location for cron)

- `a1_wiki_regen.py` -> execs `scripts/cron_wiki_regen.py`
- `a2_green_gate.py` -> execs `scripts/cron_health_check.py`

These are thin launchers so the real logic stays in the repo. If the workspace
moves, update the hardcoded `TARGET` path in each launcher.

## Scheduled jobs (no_agent, silent-on-green)

| ID | Job | Schedule | Behaviour |
|----|-----|----------|-----------|
| A1 | wiki freshness regen | daily 08:00 local | republishes wiki; alerts only on failure |
| A2 | green-gate watchdog | every 6h | runs check suite; alerts only on test error/timeout |

Both are `no_agent` (no LLM) and print to STDOUT only on failure, so a healthy
run delivers nothing.

## DELIVERY CAVEAT

Jobs were created with `deliver: local` because this session had no
gateway-connected channel. Local delivery SAVES output but does NOT notify.
To actually receive failure alerts, update each job's delivery to a connected
platform, e.g. `deliver='telegram'` or `deliver='all'`, once a channel is wired.

## Deferred

- **A3 — WF-1000 scheduled resume.** Blocked: "No approved external connector
  configuration yet for phase 3." Wrap `default_resume_command` in a cron only
  after an approved source catalog + connector exists.
- **A4 — Telemetry harvest loop.** The `telemetry/` layer has a README but no
  writer. Needs a defined metrics schema before a periodic writer is worth
  building. See `prompts/hermes-otel-data-to-self-improvement-routing-prompt.md`.
