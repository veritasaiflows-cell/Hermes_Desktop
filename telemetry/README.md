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

## Feedback/evaluation consumption

`scripts/feedback_evaluation_loop.py` is the metadata-only consumer for
repeated telemetry patterns. A8 runs `refresh` daily at 06:30, after A2-full
records correctness evidence at 06:00. It reads canonical `run_metrics` and
profile-local turn metrics, writes `derived/feedback-evaluation/latest.json`,
and reuses canonical `tasks`, `validation_results`, `decisions`, and `events`
for candidates, fixed-cohort evidence, and history.

The process is deliberately human-gated:

```bash
python scripts/feedback_evaluation_loop.py status
python scripts/feedback_evaluation_loop.py evaluate <candidate-id>
python scripts/feedback_evaluation_loop.py decide <candidate-id> --decision accepted --reviewer operator
```

`refresh` may create a candidate and baseline only. It never applies source,
prompt, model, provider, or authority changes. Raw prompts, completions, tool
arguments/results, and provider error messages remain excluded from both stores.
Candidate evaluation records the exact baseline validation ID and decisions use
that pinned record rather than the latest baseline row. Zero-test or failed
baselines fail closed, are exposed as `baseline_failed`, and are retried on the
next A8 refresh. Canonical pending candidate IDs remain visible to status even
when the derived report is stale or missing.
