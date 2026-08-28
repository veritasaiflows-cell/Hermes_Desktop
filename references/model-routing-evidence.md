# Option 5 — Smart Model Routing: Evidence-Gathering Phase

**Status:** evidence gathering. No routing policy is active, and none should be
activated from this document alone.
**Owner action required:** yes, before any activation.
**Created:** 2026-08-28

## Purpose

Decide whether Hermes' `smart_model_routing` (currently `enabled: false`) should
be turned on, and if so with what policy — using measured per-model behaviour
rather than assumption.

This phase is deliberately read-only. It produces evidence; it does not route.

## Why this is gated

An earlier pass in this workspace blamed a Mixture-of-Agents fan-out setting for
a provider error rate. Telemetry then showed MoA had run in **0 of 243 turns** —
the config was declared but never selected, so the diagnosis was wrong and two of
three proposed fixes were inert. The lesson generalises:

> A configuration block being present is not evidence that the code path executes.
> Confirm from telemetry which models actually served turns before acting.

Smart routing is exactly the kind of feature where an unverified assumption
becomes an invisible, systemic behaviour change. Hence: measure first.

## Instrumentation

`scripts/model_routing_telemetry.py` (read-only; opens the telemetry DB with
`mode=ro`) aggregates the profile-local turn database into a per-model profile.

```bash
python scripts/model_routing_telemetry.py                 # operator summary
python scripts/model_routing_telemetry.py --as-json       # machine readable
python scripts/model_routing_telemetry.py --window-days 7 # trailing window
python scripts/model_routing_telemetry.py --write         # persist derived report
```

Output: `derived/model-routing/latest.json` (schema `model-routing-telemetry.v1`,
git-ignored derived output — rebuild with `--write`).
Design/evidence record: this file, `references/model-routing-evidence.md`.
Tests: `tests/test_model_routing_telemetry.py` (11 cases).

### Metrics and why each was chosen

| Field | Meaning | Why |
|---|---|---|
| `error_rate_percent` | API errors ÷ API **requests** | Per-turn rates exceed 100% when a turn retries |
| `ms_per_1k_input_tokens` | Wall time normalised by workload | Raw latency is workload-confounded (see below) |
| `completion_rate_percent` | turns with `outcome='complete'` | Separates latency leaks from correctness leaks |
| `avg_tool_calls` / `avg_rounds` | agentic loop depth | Heavy-loop models suit different tasks than one-shot models |
| `sufficient_evidence` | `turns >= 5` | Thin samples are listed but never ranked |

### The confound this instrumentation exists to correct

Raw average turn latency **cannot** be compared across models here. In the
observed data `gpt-5.6-sol` averaged ~265k input tokens per turn while
`kimi-k2.7-code` averaged ~49k. The heavier model will look slower even if it is
faster per unit of work. `ms_per_1k_input_tokens` is the comparable figure, and
even it does not adjust for task difficulty. Treat all cross-model comparisons as
directional until a controlled A/B exists.

## Baseline (2026-08-28, 243 turns, all-time)

Ranked rows only (`turns >= 5`):

| Model | Provider | Turns | Err% | Turn s | Avg in-tok | ms/1k |
|---|---|---:|---:|---:|---:|---:|
| gpt-5.6-sol | openai-codex | 87 | 8.6 | 665.8 | 265,132 | 2511 |
| gpt-5.6-terra | openai-codex | 54 | 12.0 | 618.7 | 224,462 | 2757 |
| stealth/ox-alpha | openrouter | 27 | 5.7 | 220.0 | 56,086 | 3922 |
| claude-opus-5 | anthropic | 17 | 2.6 | 243.5 | 87,576 | 2780 |
| kimi-k3 | ollama-cloud | 17 | **0.0** | 172.0 | 87,423 | **1967** |
| kimi-k3 | kimi-coding | 15 | 14.3 | 561.0 | 125,299 | 4478 |
| deepseek-v4-pro:0813 | ollama-cloud | 5 | 0.0 | 1124.4 | 170,991 | 6576 |
| kimi-k2.7-code | ollama-cloud | 5 | **0.0** | 67.7 | 49,050 | **1381** |
| gpt-5.3-codex-spark | openai-codex | 5 | 2.3 | 425.9 | 92,734 | 4593 |
| gpt-5.6-luna | openai-codex | 5 | 21.1 | 739.3 | 138,404 | 5342 |

**Same model, different provider:** `kimi-k3` shows 0.0% via `ollama-cloud`
versus 14.3% via `kimi-coding`. Provider identity — not just model identity —
belongs in any routing key.

## Requested model coverage

| Requested | Telemetry status | Routing readiness |
|---|---|---|
| Luna (`gpt-5.6-luna`) | 5 turns, **21.1% err** | Ranked; worst observed error rate |
| Terra (`gpt-5.6-terra`) | 54 turns, 12.0% err | Adequate evidence |
| Sol (`gpt-5.6-sol`) | 87 turns, 8.6% err | Best-evidenced model |
| Opus 5 (`claude-opus-5`) | 17 turns, 2.6% err | Adequate evidence |
| Kimi 3 (`kimi-k3`) | 17 (ollama-cloud) + 15 (kimi-coding) | Split by provider — keep separate |
| MiniMax 3 (`minimax-m3`) | **2 turns** | **Insufficient** — needs ≥5 |
| GLM 2 | **0 turns** | **Not observed at all** — see below |

⚠️ **GLM 2 has never run in this workspace.** No `glm*` model appears in any of
the 243 recorded turns, and no `zai`/GLM provider is present in the credential
pool (`copilot`, `openai-codex`, `anthropic`, `ollama-cloud`, `nvidia`,
`openrouter`, `kimi-coding`). Including it in a routing policy today would route
traffic to an unconfigured, unmeasured provider. It needs credentials plus a
deliberate sampling period before it can be a routing target.

MiniMax 3 is configured and working but at 2 turns is below the ranking
threshold. Both are "gather evidence", not "route to".

## What is deliberately NOT decided here

- Whether `smart_model_routing` should be enabled at all.
- Task-class taxonomy (what counts as "cheap" work).
- Cost weighting — **no pricing data is in telemetry**; cost claims would be
  fabricated. Any cost-aware policy needs a price table supplied separately.
- Whether cheap models preserve output quality. Telemetry measures latency,
  errors, and completion — **not correctness**. A fast, reliable, wrong answer
  is invisible to every metric in this report.

## Interaction with the active fallback chain

A fallback chain is now configured (`anthropic/claude-opus-5` →
`ollama-cloud/kimi-k3`). Fallback is **reactive** (fires on primary failure, turn-scoped) whereas smart
routing is **proactive** (picks a model up front). They compose, but enabling
both at once makes attribution ambiguous: a turn served by a non-primary model
could be either mechanism.

**Therefore:** let the fallback chain accumulate a clean baseline first. Enabling
smart routing simultaneously would contaminate the very evidence needed to judge
it.

## Recommended sequence

1. **Now — passive collection.** Fallback chain live; smart routing stays off.
   Re-run the report periodically; `--write` snapshots it to `derived/`.
2. **After ~1 week / ≥30 new turns** — re-run and check: did fallback fire, and
   did the openai-codex error rate change? Get MiniMax 3 above 5 turns; decide
   whether GLM 2 gets credentials and a sampling period.
3. **Then — propose a policy** with explicit task classes and a named model per
   class, each backed by ≥5 turns of evidence, for operator approval.
4. **Only then — enable**, with a defined rollback (`smart_model_routing.enabled:
   false`) and a re-measurement window.

## Verification performed

- `python -m unittest discover -s tests` → **422 tests, 0 failures** (was 411).
- `model_routing_telemetry.py --write` → exit 0, wrote
  `derived/model-routing/latest.json`.
- Read-only guarantee asserted by test (`test_database_is_opened_read_only`).

## Open questions for the operator

1. Should GLM 2 be credentialed and sampled, or dropped from scope?
2. Is there a price table to supply? Without one, "cheaper" cannot be measured.
3. What quality signal should gate cheap-model routing, given telemetry cannot
   see correctness?
