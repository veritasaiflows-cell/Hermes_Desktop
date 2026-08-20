# Workspace Status Maintenance

This file tracks how the single-command workspace status surface is kept fresh
and how it fits into the cron schedule.

## Single-command aggregator

`scripts/workspace_status.py` is the one-line status command. It runs these
gates in parallel and returns a JSON brief:

- `routing` — `scripts/workflow_router.py --all --answer summary`
- `wiki` — `scripts/wiki_bootstrap.py validate`
- `alias` — `scripts/cron_alias_sweep.py`
- `cron_registration` — `scripts/cron_registration_validator.py`
- `claim_drift` — `scripts/cron_claim_drift_check.py`
- `graph_integrity` — `scripts/graph_memory.py validate`
- `graph_freshness` — `scripts/cron_graph_freshness.py`
- `vector_memory` — `scripts/vector_memory_index.py status`
- `archive_stale` — `scripts/cron_archive_stale_workflows.py`

Exit code is 0 when healthy, 1 when degraded. The JSON body contains
`health.status`, `health.hard_failures`, `health.warnings`, and a
`recommended_next_action`.

## Cron schedule

| Job | Script | Schedule | Purpose |
|-----|--------|----------|---------|
| A1 | a1_wiki_regen.py | daily 08:00 | Regenerate wiki manifest freshness |
| A2 | a2_green_gate.py | every 4h | Fast operational health gate |
| A2-full | a2_full_test_gate.py | daily 06:00 | Full unit/smoke correctness gate |
| A3 | a3_routing_cache_sweep.py | daily 09:00 | Evict stale routing-cache rows |
| A4 | a4_archive_stale_workflows.py | weekly Sun 10:00 | Archive terminal workflows |
| A5 | a5_routing_refresh.py | hourly | Regenerate routing index + capsules |
| A6 | a6_alias_sweep.py | daily 09:30 | Detect dead aliases |
| A7 | a7_queue_hygiene.py | weekly Sun 11:00 | Active-workflows queue hygiene |
| A8 | a8_telemetry_harvest.py | daily 06:30 | Collect run_metrics telemetry |
| A9 | a9_claim_drift_check.py | hourly :45 | Claim/replay integrity monitor |
| A10 | a10_graph_freshness.py | hourly :15 | Graph coverage + integrity monitor |
| **A11** | **a11_workspace_status.py** | **every 4h :15** | **Single-command full operating brief** |

A11 is the new heartbeat for the status surface itself. It does not replace A2;
A2 remains the fast watchdog, while A11 returns the richer JSON brief that an
agent can parse in one call.

## Keeping the status surface current

1. **New gates**: If you add a cron-style operational check, consider adding it
   to `DEFAULT_GATES` in `scripts/workspace_status.py` and add a test in
   `tests/test_workspace_status.py`.
2. **Wrapper registration**: If the new check needs a Hermes cron wrapper, add
   it to `EXPECTED_JOBS` in `scripts/cron_registration_validator.py`.
3. **Wiki source changes**: Any edit to a page listed in
   `wiki/bootstrap-manifest.json` will make wiki validation stale. The A1 daily
   regen will refresh it; run `python scripts/wiki_bootstrap.py publish` manually
   if you need freshness immediately.
4. **Skill updates**: When the status command or cron schedule changes, update
   `workflow-status-audit` skill.

## What to do when A11 alerts

1. Read the brief: `python scripts/workspace_status.py`
2. Check `health.hard_failures` and drill into the matching `gates` entry.
3. Fix the underlying gate, or run the relevant individual cron script by hand.
4. Re-run `workspace_status.py` until `health.status == "healthy"`.
5. If the fix involved a new dependency on a wrapper script, run
   `python scripts/cron_registration_validator.py` to confirm registration.

## Tests

- `python -m unittest tests.test_workspace_status`
- `python -m unittest tests.test_cron_registration_validator`
