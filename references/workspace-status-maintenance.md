# Workspace Status Maintenance

This file tracks how the single-command workspace status surface is kept fresh
and how it fits into the cron schedule.

## Single-command aggregator

`scripts/workspace_status.py` is the one-line startup and closeout status command.
`--fast` runs the startup tier only (`organization`, `routing` with cache
reads, `wiki`, `lane_register`) plus git and cached correctness — about 0.8s
vs 2.0s full. Use `--fast` at session startup; the full brief (no flags) is
for closeout and the A11 heartbeat. The brief reports `mode: fast|full`.
It runs these gates serially in declared order to avoid shared SQLite and
artifact races, and returns a JSON brief:

- `organization` — `scripts/workspace_organization_validator.py`
- `routing` — `scripts/workflow_router.py --all --answer summary --validate --no-cache --skip-recall-context`
- `wiki` — `scripts/wiki_bootstrap.py validate`
- `alias` — `scripts/cron_alias_sweep.py`
- `cron_registration` — `scripts/cron_registration_validator.py`
- `feedback_evaluation` — `scripts/feedback_evaluation_loop.py status`
- `claim_drift` — `scripts/cron_claim_drift_check.py`
- `graph_integrity` — `scripts/graph_memory.py validate`
- `graph_freshness` — `scripts/cron_graph_freshness.py`
- `graphify_freshness` — `scripts/graphify_freshness.py`
- `vector_memory` — `scripts/vector_memory_index.py status`
- `workspace_index` — `scripts/workspace_index.py status`
- `archive_stale` — `scripts/cron_archive_stale_workflows.py --check-only`

Organization drift and missing or corrupt retrieval indexes are hard failures.
Stale vector/workspace indexes are warnings (`vector_memory_stale`,
`workspace_index_stale`): the derived indexes have a direct-source fallback,
like the graphify policy below. Gates skipped by `--fast` are ignored rather
than failed; the A11 heartbeat still covers them.
Graphify drift is a warning because the derived code graph has a direct-source
fallback. Refresh it with `graphify update .`, then reconcile static gate edges
with `python scripts/graphify_gate_edges.py` before recording the verified
artifact with `python scripts/graphify_freshness.py --write-baseline`.
Exit code is 0 when healthy, 1 when degraded. The JSON body contains
`health.status`, `health.hard_failures`, `health.warnings`, and a
`recommended_next_action`.

The brief also includes `correctness`, sourced from the latest `run_checks`
telemetry row. It reports the test outcome, completion timestamp, duration,
test count, tested commit, and whether the recorded source fingerprint still
matches current code. The source fingerprint is cached in
`tmp/workspace-fingerprint-cache.json` keyed by per-file mtime/size and
re-hashed only when sources change; branch/commit come from two git forks
(`status --short --branch` + `rev-parse --short HEAD`). `run_checks` fingerprints sources before and after the
test/smoke run and returns nonzero if they differ or cannot be read, independently
of telemetry recording. It parses numeric outcomes and exact failure node IDs only
from pytest's final summary sections, persisting a bounded ID list plus its hash.
The full failure set is hashed even when only the first 100 IDs are retained.
Accepted evidence must report at least one executed test, zero failures, and the
same source-file count as the current snapshot. Stale, failed, unavailable, or
malformed correctness evidence produces a warning and the brief recommends
`python scripts/cron_test_gate.py`; the fast operating-status command does not
rerun the full suite itself.

The brief (fast and full) also includes `uncommitted_work`: one
`git --git-dir=<root>/.git --work-tree=<root> status --porcelain=v1 -z
--no-renames --untracked-files=all` read (~0.6s; every inherited `GIT_*`
variable removed, an invalid `.git` reports `unavailable`) summarising
tracked modifications, staged changes, deletions, and non-ignored untracked
files. Per-path `lstat` work is capped by `UNCOMMITTED_WORK_STAT_LIMIT` and
`UNCOMMITTED_WORK_STAT_BUDGET_SECONDS`; a capped read reports `truncated`.
When any examined file's mtime is at least 4h old
(`UNCOMMITTED_WORK_WARN_HOURS`) the brief warns `uncommitted_work_aged`, lists
up to 20 aged paths, and recommends a commit or `checkpoint_pending`
disposition. It is a warning only (never a hard failure) and an mtime
heuristic, not "time since the change became uncommitted"; deletions have
unknown age and never warn alone. The compact brief carries
`status/changed_count/examined_count/truncated/aged_count/oldest_age_hours`.
Age counts, oldest age, and warnings describe only the examined subset when
`truncated` is true, not every changed path. Filesystem resolution/probing
failures report `unavailable` without adding a hard failure or changing the
existing exit outcome.

The brief also includes the compact `feedback_evaluation` packet. A fresh
`review_required` report is a warning, not a hard failure: it means repeated
metadata has produced a baseline-backed candidate that needs explicit human
review. Missing, malformed, or stale feedback reports are warnings and the
brief recommends `python scripts/cron_telemetry_harvest.py`. Canonical pending
candidate IDs remain visible even when the derived report is missing or stale;
the packet's `report_status` independently reports `fresh`, `stale`, or
`unavailable`. A failed baseline adds `feedback_evaluation_failed` while A8
returns nonzero and retries the cohort on the next refresh.

The A11 path is read-only: routing bypasses cache reads/writes and vector recall,
all canonical graph/claim/freshness reads use `CanonicalDB(..., read_only=True)`
or SQLite `mode=ro`, and stale-workflow archiving runs in check-only mode. Mutating
archive and refresh operations remain separate explicit jobs.

## Cron schedule

| Job | Script | Schedule | Purpose |
|-----|--------|----------|---------|
| A1 | a1_wiki_regen.py | daily 08:00 | Regenerate wiki manifest freshness |
| A2 | a2_green_gate.py | every 4h | Fast operational health gate |
| A2-full | a2_full_test_gate.py | daily 06:00 | Full unit/smoke correctness gate |
| A3 | a3_routing_cache_sweep.py | daily 09:00 | Evict stale routing-cache rows |
| A4 | a4_archive_stale_workflows.py | weekly Sun 10:00 | Archive terminal workflows |
| A5 | a5_routing_refresh.py | hourly :35 | Regenerate routing index + capsules with bounded fail-closed reporting |
| A6 | a6_alias_sweep.py | daily 09:30 | Detect dead aliases |
| A7 | a7_queue_hygiene.py | weekly Sun 11:00 | Active-workflows queue hygiene |
| A8 | a8_telemetry_harvest.py | daily 06:30 | Aggregate canonical + turn telemetry; create baseline-only review candidates after A2-full |
| A9 | a9_claim_drift_check.py | hourly :45 | Claim/replay integrity monitor |
| A10 | a10_graph_freshness.py | hourly :15 | Graph coverage + integrity monitor |
| **A11** | **a11_workspace_status.py** | **every 4h :15** | **Single-command full operating brief** |
| A12 | a12_retrieval_refresh.py | every 6h :30 | Refresh indexes, then invoke A5 even after degraded/failed refresh attempts |
| A13 | a13_canonical_integrity.py | daily 07:00 | Verify canonical SQLite integrity |

A11 is the new heartbeat for the status surface itself. It does not replace A2;
A2 remains the fast watchdog, while A11 returns the richer JSON brief that an
agent can parse in one call.

A12 proactively repairs retrieval-source hash drift detected by A11 and treats
successful A5 routing synchronization as a required postcondition. It invokes A5
after successful or degraded refreshes and best-effort after exceptions, covering
partial index writes. A13 gives canonical SQLite integrity an explicit deterministic
schedule instead of relying on incidental database access by other gates.

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
5. **Graphify refreshes**: Run `graphify update .`, then
   `python scripts/graphify_gate_edges.py`, after code changes. Write a new
   baseline only after `graphify diagnose multigraph` and source-coverage checks
   pass.

## What to do when A11 alerts

1. Read the brief: `python scripts/workspace_status.py`
2. Check `health.hard_failures` and drill into the matching `gates` entry.
3. Fix the underlying gate, or run the relevant individual cron script by hand.
4. Re-run `workspace_status.py` until `health.status == "healthy"`.
5. If the fix involved a new dependency on a wrapper script, run
   `python scripts/cron_registration_validator.py` to confirm registration.

## Tests

- `python -m unittest tests.test_workspace_status`
- `python -m unittest tests.test_graphify_freshness`
- `python -m unittest tests.test_cron_registration_validator`
