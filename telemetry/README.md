# Telemetry layer

Record meaningful runs and improvement signals here.

Capture, where available:

- request type
- route selected
- tools used
- model or agent
- input and handoff size
- duration
- token or resource usage
- errors and retries
- verification result
- user correction
- final outcome
- accepted, repaired, or rejected status

Use telemetry to measure correctness, evidence coverage, completion, latency, cost, retries, escaped defects, memory usefulness, and repeated failure patterns.

## 1-1-1 storage contract

Telemetry uses one live raw store, one durable proof store, and one current
derived view per report family:

1. Recent metadata-only turns remain in the profile-local
   `$HERMES_HOME/telemetry/turn-metrics.sqlite`. No per-turn files are created.
2. Compact daily rollups and their validation/hash chain remain in the existing
   `canonical/efficiens.db`; canonical proof is written before raw deletion.
3. Each derived report family owns one overwrite-in-place `latest.json`.
   Temporary replacement files are atomic and must not survive a successful run.

`scripts/telemetry_retention.py maintain` is the sole raw-deletion owner. It
retains 90 days, enforces a 10 MiB physical database budget, and protects both
the active UTC day and A8's seven-day evidence window. A shorter configured
retention is rejected. Concurrent maintainers serialize through one
stale-recoverable lease row inside the raw database; no lock file is created.
Before deletion it recomputes every rollup hash,
validates the append chain, matches validation evidence and provenance, and
compares each raw row again under `BEGIN IMMEDIATE`. Deletes run in batches of
at most 500. It checkpoints WAL state, uses incremental vacuum, and uses a full
vacuum at most once during size pruning when the physical threshold still
requires it. Existing freelist pages are reclaimed before size eligibility is
evaluated. The collector itself never prunes rows.

Collector schema `turn-metrics.v3` separates summed API/tool work from API,
tool, and combined external wall-time unions. Residual orchestration is derived
from the combined union. Legacy rows have no semantics version and their
versioned timing fields are treated as unknown—not backfilled or inferred.
Each provider attempt has its own monotonic interval; retry backoff is excluded
from API work and wall unions, retries are reconciled per logical request, and
open attempts are closed at the observed turn boundary. Missing request IDs are
paired through an in-memory queue. Mid-turn provider/model changes are recorded
as `mixed`. Raw identifiers use domain-separated, process-ephemeral keyed HMACs,
so no hash key or identifier sidecar is added. All diagnostic free text collapses
to category markers, and registration scrubs legacy diagnostic text in place.
Existing `turn-metrics.v2` rows retain their older cumulative-retry semantics
and are never relabeled or backfilled as v3.

Optional monthly metadata-only archives are enabled with
`HERMES_TELEMETRY_MONTHLY_ARCHIVES=1`. They are deterministic
`YYYY-MM.jsonl.gz` files under `$HERMES_HOME/telemetry/archive/`, outside the
workspace. Unknown columns are excluded by allowlist and every retained value is
validated against a bounded metadata schema. Offset-less timestamps fail closed;
accepted offsets normalize to UTC before ordering and hashing. Existing archive
bytes must match the latest provenance- and validation-backed canonical manifest,
and duplicate turn keys are rejected before merge. Archive replacement uses an
exclusive random temporary file, read-back verification, compare-before-replace,
and atomic publication. The canonical database records archive row counts,
manifest hashes, and archive SHA-256 proof.
With the option unset, no archive directory or archive file is created.

## Feedback/evaluation consumption

`scripts/feedback_evaluation_loop.py` is the metadata-only consumer for
repeated telemetry patterns. A8 first runs the retention owner and fails closed
if compaction, proof, or pruning fails; it then runs `refresh` daily at 06:30,
after A2-full records correctness evidence at 06:00. It reads canonical
`run_metrics` and profile-local turn metrics, writes
`derived/feedback-evaluation/latest.json`, and reuses canonical `tasks`,
`validation_results`, `decisions`, and `events` for candidates, fixed-cohort
evidence, and history.

The process is deliberately human-gated:

```bash
python scripts/feedback_evaluation_loop.py status
python scripts/feedback_evaluation_loop.py evaluate <candidate-id>
python scripts/feedback_evaluation_loop.py decide <candidate-id> --decision accepted --reviewer operator
```

`refresh` may create a candidate and baseline only. It never applies source,
prompt, model, provider, or authority changes. Raw prompts, completions, tool
arguments/results, and raw provider error messages remain excluded from both stores.
Candidate evaluation records the exact baseline validation ID and decisions use
that pinned record rather than the latest baseline row. Zero-test or failed
baselines fail closed, are exposed as `baseline_failed`, and are retried on the
next A8 refresh. Canonical pending candidate IDs remain visible to status even
when the derived report is stale or missing.
