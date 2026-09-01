# Model Routing Efficiency and Role-Lane Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Reduce expensive implementation-context use while assigning stable, explicit model roles: Terra for coding, Luna for bounded cheap agentic work, Sol for final QA, Opus 5 for escalation, and Kimi/GLM for availability fallback.

**Architecture:** Use a stable Terra parent for each implementation session and route other work through explicit lanes rather than switching the live parent. Use Kimi and GLM only for reactive provider failures; use fresh, read-only Sol and Opus sessions for review/escalation so they do not disturb Terra prompt-cache continuity. Qualify all low-cost lanes against fixed tasks before enabling them globally.

**Tech Stack:** Hermes Agent CLI v0.20.5, profile-scoped Hermes configuration, `hermes fallback`, `hermes config`, `hermes profile`, local SQLite telemetry (`turn-metrics.sqlite`, `state.db`, `canonical/efficiens.db`), Python 3.11, `unittest`.

---

## Scope, authority, and non-goals

- This document is a plan only. It does not authorize or apply model, provider, fallback, profile, credential, permission, or system-behavior changes.
- Do not hand-edit `C:\Users\Veritas\AppData\Local\hermes\config.yaml`; use the supported Hermes CLI after explicit approval for every production-profile change.
- Preserve the pre-existing unrelated workspace changes:
  - Modified: `state/workflows/WF-1000.json` through `state/workflows/WF-1004.json`
  - Untracked: `parse_tvcmall_nuxt.py`, `scripts/wf_commerce_browser.sh`, supplier-search JSON artifacts, and `wf1000_public_supplier_search.json`
- Keep `smart_model_routing` disabled. The intended design is explicit role routing, not per-turn automatic switching.
- Treat "Ollama GLM3" as the currently configured exact identifier `ollama-cloud/glm-5.3`; confirm the identifier before any write.
- Do not treat a configured fallback list as evidence that a fallback executed. Test primary-fallback activation deliberately in a canary.

## Current verified baseline

### Live configuration

- Primary route: `openai-codex / gpt-5.6-sol`
- Current fallback order: Terra → GLM 5.3 → Opus 5 → Kimi K3 → GLM 5.3 Flash
- Delegation currently inherits the parent model and has the following cost-exposure limits:
  - `delegation.max_iterations: 250`
  - `delegation.max_concurrent_children: 10`
  - `delegation.max_summary_chars: 24000`
- Compression threshold: `250000` tokens.

### Evidence to preserve

- `scripts/model_routing_telemetry.py` and `tests/test_model_routing_telemetry.py` provide read-only aggregation of `turn-metrics.sqlite`.
- `derived/model-routing/latest.json` is the historical routing snapshot; do not treat it as a fresh execution record without regenerating it.
- `C:\Users\Veritas\AppData\Local\hermes\state.db` has aggregate per-model/per-task usage including `estimated_cost_usd`, `actual_cost_usd`, `cost_status`, and `cost_source`. Current zero-valued `included`/`unknown` cost rows do not establish marginal dollar price.
- `canonical/efficiens.db` has `run_metrics.acceptance_status` and verification fields, but model attribution and session/turn linkage are incomplete. The current telemetry cannot reliably calculate premium tokens per accepted implementation change without new correlation data.

### Observed operational signals

| Route | Observed role signal | Planning implication |
|---|---|---|
| `openai-codex/gpt-5.6-terra` | 58 observed turns; 98.3% completion | Best current candidate for stable implementation parent |
| `openai-codex/gpt-5.6-sol` | 118 observed turns; 6.71% API-request error rate | Mature evidence for final QA/review lane |
| `openai-codex/gpt-5.6-luna` | 5 observed turns; 21.05% API-request error rate | Qualify before broad automated delegation despite promising completion |
| `ollama-cloud/kimi-k3` | 18 observed turns; 0% API-request error rate | First fallback and compression candidate |
| `ollama-cloud/glm-5.3` | 9 observed turns; 0% API-request error rate but lower completion | Second fallback pending qualification |
| `anthropic/claude-opus-5` | 34 observed turns | Explicit challenger/escalation only; not a routine fallback |

These are operational observations across mixed workloads, not intrinsic model-quality or price rankings.

### Auxiliary workload priority

All-time aggregate entries in `state.db` show the following observed auxiliary load:

| Task | API calls | Input tokens | Cached input tokens | Plan consequence |
|---|---:|---:|---:|---|
| `background_review` | 883 | 42,167,322 | 92,958,050 | Highest auxiliary optimization opportunity; qualify carefully because it affects memory/skill review behavior |
| `compression` | 75 | 888,004 | 0 | Material context-reduction lever; use a large-context candidate |
| `approval` | 1,314 | 684,981 | 6,924 | Preserve a known strong/safety-qualified route |
| `title_generation` | 67 | 18,155 | 640 | Safe smoke-test target, but not a material savings source |

No aggregate rows were recorded for `memory_query_rewrite` or `web_extract`; treat their routing as capability tests rather than demonstrated savings.

## Target role architecture

| Role | Route | Allowed purpose | Explicit restriction |
|---|---|---|---|
| Main coding parent | `openai-codex/gpt-5.6-terra` | New implementation sessions, final edits, deterministic verification ownership | Never switch an established Terra session for a one-off review or delegation task |
| Cheap agentic worker | `openai-codex/gpt-5.6-luna` | Bounded discovery, source inventory, test discovery, narrow reproduction, scoped audits, low-risk text auxiliaries | No cross-cutting refactors, architecture ownership, final verification, or unresolved multi-cycle debugging by default |
| Compression | `ollama-cloud/kimi-k3` | Long-context session compression after fidelity qualification | Do not assign Luna first: Codex 5.6 routes have a 272K cap close to the current 250K threshold |
| Final QA | `openai-codex/gpt-5.6-sol` | Fresh, read-only diff/test/acceptance review after deterministic checks | Sol reports defects; Terra retains repair ownership |
| Challenger | `anthropic/claude-opus-5` | Explicit escalation, architectural challenge, security review, or Terra/Sol disagreement | Never an automatic availability fallback or default routine reviewer |
| Availability fallback 1 | `ollama-cloud/kimi-k3` | Primary/provider rate-limit, 5xx, or connection failure | Not a substitute for QA disagreement or quality escalation |
| Availability fallback 2 | `ollama-cloud/glm-5.3` | Secondary availability failure | Remain second until completion/rework evidence improves |
| Terminal availability fallback | `ollama-cloud/glm-5.3-flash` | Last-resort availability continuity | Not a coding owner or review model |

## Pre-registered promotion and rollback rules

### Promotion rule

Promote a candidate lane only when a fixed benchmark and a canary show all of the following:

1. All deterministic tests for the task class pass.
2. Sol QA finds no increase in material defects or repair cycles relative to the Terra/Sol baseline.
3. API errors, retries, and fallback activations remain within the agreed baseline tolerance.
4. The measured route produces a meaningful reduction in Terra/Sol input tokens per accepted, fully-tested change. Suggested initial target: at least 25% on task classes where the lane is intended to save premium context.
5. The resulting session/turn/model attribution is sufficient to audit the claim; no cost claim is made solely from zero-valued `included` or `unknown` ledger rows.

### Immediate rollback triggers

Rollback the most recent isolated configuration change if any occurs:

- a safety or approval decision is handled by an unqualified cheap route;
- a fallback model silently becomes the implementation owner for a high-risk task;
- compression loses required constraints, test outcomes, open blockers, or file paths;
- a canary causes a deterministic test regression, recurring context-limit failure, or material increase in rework;
- a model/provider route produces unexpected paid fallback behavior.

### Rollback method

Before each approved production change, record the exact affected `hermes config get <key>` and `hermes fallback list` output in the experiment record. Restore only the changed keys through `hermes config set` and the supported `hermes fallback` manager. Do not create or copy plaintext configuration backups that could duplicate credentials without separate approval.

## Task 1: Freeze a reproducible baseline

**Objective:** Capture current routing, usage, and acceptance evidence before any canary configuration is changed.

**Files:**
- Read: `scripts/model_routing_telemetry.py`
- Read: `tests/test_model_routing_telemetry.py`
- Read: `references/model-routing-evidence.md`
- Read: `canonical/schema.sql`
- Create at runtime: `derived/model-routing/experiments/<run-id>-baseline.json`
- Create at runtime: `derived/model-routing/experiments/<run-id>-baseline.md`

**Steps:**
1. Run the deterministic workspace gate and record the existing Git baseline without staging unrelated changes.
2. Run the existing telemetry script with both all-time and seven-day windows.
3. Query `state.db` read-only for usage grouped by model/provider/task, including cache reads, token counts, cost status, and actual/estimated cost fields.
4. Query `canonical/efficiens.db` read-only for `run_metrics` grouped by model attribution, verification result, acceptance status, and retries.
5. Save a timestamped experiment record that names source databases, the query window, current primary/fallback configuration, and all limitations.
6. Run `hermes fallback list` and preserve its output in the experiment record.

**Verification:**
- `python -m unittest tests.test_model_routing_telemetry -v`
- `python scripts/model_routing_telemetry.py --window-days 7`
- `python scripts/model_routing_telemetry.py`
- `git status --short --branch`

## Task 2: Add accepted-change correlation to the benchmark surface

**Objective:** Make the proposed efficiency metric auditable rather than inferring accepted changes from unrelated aggregate data.

**Files:**
- Create: `tests/fixtures/model_routing_benchmark_cases.json`
- Create: `scripts/model_routing_benchmark.py`
- Create: `tests/test_model_routing_benchmark.py`
- Modify: `scripts/model_routing_telemetry.py`
- Modify: `tests/test_model_routing_telemetry.py`
- Create at runtime: `derived/model-routing/experiments/<run-id>-results.json`
- Create at runtime: `derived/model-routing/experiments/<run-id>-results.md`

**Steps:**
1. Define a small, versioned fixture set of six to eight representative cases: source inventory, test discovery, narrow reproduction, documentation lookup, narrow code audit, mechanical patch, final QA, and long-context compression fidelity.
2. For each case, define acceptance criteria, deterministic validation command, expected evidence, allowed toolset, role owner, maximum repair cycles, and whether a fresh session is mandatory.
3. Give each experiment a stable `experiment_id`, workspace revision, model/provider, session/turn identifier, case identifier, and timestamp.
4. Extend the report schema so it can join the experiment record to the relevant `run_metrics` acceptance and verification outcome without storing raw prompts, credentials, or secrets.
5. Retain raw numeric measures: uncached input, cached input, output/reasoning tokens where available, elapsed time, API errors, retries, tool rounds, fallback use, deterministic test result, Sol QA outcome, and repair count.
6. Add synthetic tests proving that an unlinked/missing acceptance record is reported as `unattributed`, not counted as an accepted change.

**Verification:**
- `python -m unittest tests.test_model_routing_benchmark -v`
- `python -m unittest tests.test_model_routing_telemetry -v`
- `python scripts/model_routing_benchmark.py --help`
- Assert that the report rejects duplicate case IDs, missing validation commands, missing model/session identifiers, and missing acceptance linkage.

## Task 3: Build an isolated role-routing canary

**Objective:** Qualify Luna, Kimi, and fallback behavior without altering the default production profile.

**Files:**
- Create at runtime: a dedicated non-default Hermes profile, e.g. `routingcanary`
- Create at runtime: `derived/model-routing/experiments/<run-id>-canary-config.md`
- Create at runtime: `derived/model-routing/experiments/<run-id>-canary-results.{json,md}`

**Steps:**
1. Obtain explicit approval before creating any profile or changing any Hermes configuration.
2. Create a dedicated canary profile using the supported profile command; do not modify the active default profile.
3. Configure only the canary profile with the candidate role assignments, one independent change at a time.
4. Record every changed config key and the `hermes fallback list` result before and after the change.
5. Run the fixed benchmark corpus in fresh sessions. Keep primary-fallback failure testing separate from quality testing so a fallback does not contaminate task attribution.
6. Test the fallback chain under a controlled recoverable failure and preserve direct activation evidence rather than relying on the configured list.
7. Destroy or retain the canary profile only after recording its disposition; do not leave an undocumented alternate route active.

**Verification:**
- `hermes profile create --help` confirms the supported creation interface before use.
- `hermes config check`
- `hermes fallback list`
- Benchmark corpus passes its deterministic tests.
- The experiment report includes an explicit `fallback_executed: true|false|unknown` field based on actual evidence.

## Task 4: Qualify Luna as a bounded worker, not a broad implementation owner

**Objective:** Determine whether Luna can reduce Terra-context use on cheap independent work without raising rework.

**Files:**
- Modify: `tests/fixtures/model_routing_benchmark_cases.json`
- Modify: `scripts/model_routing_benchmark.py`
- Modify: `tests/test_model_routing_benchmark.py`
- Create at runtime: `derived/model-routing/experiments/<run-id>-luna-worker.{json,md}`

**Steps:**
1. Run Luna only on fresh, bounded leaf tasks: source inventory, test discovery, narrow reproduction, documentation lookup, and simple local audit.
2. Keep Terra responsible for final design, edits, and verification during the benchmark.
3. Compare Luna output with the pre-registered acceptance requirements and Sol review findings.
4. Test low-risk auxiliary candidates separately: `title_generation` first, then `memory_query_rewrite` and `web_extract` if their actual usage appears.
5. Do not route `approval`, vision, MCP routing, complex decomposition, or final verification to Luna in this phase.
6. Promote Luna only if the pre-registered promotion gate passes and its reliability sample is no longer thin.

**Verification:**
- Each case’s deterministic command passes after Terra incorporates only accepted Luna findings.
- The report states whether Luna saved premium parent input, increased rework, or simply displaced usage without a net benefit.
- Sol QA receives the final diff and test evidence without the full Luna transcript unless needed for defect investigation.

## Task 5: Qualify Kimi compression and optimize retention before changing the summarizer broadly

**Objective:** Reduce retained expensive context while preserving implementation-critical state.

**Files:**
- Modify: `tests/fixtures/model_routing_benchmark_cases.json`
- Modify: `scripts/model_routing_benchmark.py`
- Modify: `tests/test_model_routing_benchmark.py`
- Create at runtime: `derived/model-routing/experiments/<run-id>-compression.{json,md}`

**Steps:**
1. Create long-context cases that contain acceptance criteria, changed paths, failed and passed test outputs, unresolved blockers, explicit user constraints, and rollback facts.
2. Compare the current compression behavior against Kimi-generated compression using the same source transcript.
3. Score summaries for required-fact retention, unsupported assertion rate, token reduction, latency, and ability of a fresh Terra session to resume work correctly.
4. Canary `compression.tail_mode: lean` separately from the compression-model change so its effect is measurable.
5. Keep the current 250K threshold initially; benchmark threshold changes only after model and tail-mode behavior are understood.
6. Do not use Luna for this lane until it demonstrates reliable behavior near the highest expected compression input size.

**Verification:**
- A deterministic fidelity test fails if any required constraint, file path, test result, open blocker, or authority limit is omitted or contradicted.
- Terra can correctly continue from a compressed context and pass the original task’s validation command.
- The report distinguishes summarizer cost from the parent context saved on later turns.

## Task 6: Address background review as the dominant auxiliary workload

**Objective:** Reduce high-volume auxiliary activity without weakening durable memory/skill quality.

**Files:**
- Modify: `scripts/model_routing_telemetry.py`
- Modify: `tests/test_model_routing_telemetry.py`
- Create at runtime: `derived/model-routing/experiments/<run-id>-background-review.{json,md}`

**Steps:**
1. Add reporting by auxiliary task so `background_review` volume and its model/provider attribution remain visible after every experiment.
2. Measure useful review yield: accepted memory/skill improvements, no-op reviews, rejected suggestions, and correction/rework caused by a review.
3. First test a lower review cadence or manual milestone review; do not change route and cadence in the same run.
4. If the yield remains adequate, qualify Luna or Kimi for the review lane in a canary before changing the default profile.
5. Keep all memory/skill writes traceable and subject to existing approval/authority policy.

**Verification:**
- Reports show review volume, yield, route, and no-op rate.
- A low-yield reduction does not impair later retrieval or cause missed durable facts in the fixed evaluation cases.
- No memory/skill update is attributed to a route without provenance.

## Task 7: Apply the approved default-profile configuration in reversible increments

**Objective:** Move to the target role architecture only after the canary qualifies it.

**Files:**
- Modify: profile-scoped Hermes configuration through supported CLI only: `C:\Users\Veritas\AppData\Local\hermes\config.yaml`
- Create at runtime: `derived/model-routing/experiments/<run-id>-production-change.md`

**Steps:**
1. Obtain explicit approval for each production configuration change and record the exact prior values.
2. Set the primary parent route to Terra. Start a new coding session afterwards; do not switch the existing implementation session midstream.
3. Use the interactive `hermes fallback` manager to make the primary availability order Kimi K3 → GLM 5.3 → GLM 5.3 Flash. Verify with `hermes fallback list`.
4. Remove Sol and Opus from automatic fallback routing. Keep them available only through explicit fresh sessions or approved task-level routing.
5. Configure Luna for the qualified low-risk auxiliary slots and, only after its worker benchmark passes, as the delegation provider/model.
6. Apply delegation cost fences as one isolated update: initial target `max_iterations: 50`, `max_concurrent_children: 3`, and `max_summary_chars: 12000`. Keep `subagent_auto_approve: false`.
7. Apply Kimi compression and `tail_mode: lean` only if their independent canaries pass.
8. Keep `approval`, vision, and other safety/complexity-adjacent routes unchanged until separately qualified. Do not assume `auto` is cost-predictable.
9. Run `hermes config check`, `hermes fallback list`, and the full fixed benchmark suite after each isolated configuration increment.

**Verification:**
- `hermes config check`
- `hermes fallback list`
- `python -m unittest tests.test_model_routing_telemetry tests.test_model_routing_benchmark -v`
- `python scripts/model_routing_telemetry.py --window-days 7`
- The production-change report records exactly one change group, its expected effect, actual evidence, and rollback instruction.

## Task 8: Operationalize Sol QA and Opus escalation without cache-breaking the main lane

**Objective:** Turn the intended review roles into a repeatable, cost-contained workflow.

**Files:**
- Create: `references/model-routing-review-runbook.md`
- Create: `tests/test_model_routing_review_runbook.py` if the workspace runbook-test convention supports it
- Create at runtime: `derived/model-routing/reviews/<review-id>.md`

**Steps:**
1. Document the fixed review packet: task acceptance criteria, `git diff`, changed-file list, deterministic test/lint/build output, remaining risks, and the requested review question.
2. Run Sol as a fresh, read-only final-QA session after Terra’s deterministic checks pass.
3. Route Sol findings back to Terra for repair; rerun deterministic checks before a targeted Sol re-review.
4. Invoke Opus only for: Terra/Sol material disagreement, architectural/security risk, two failed repair cycles, or no deterministic correctness oracle.
5. Require both reviewers to identify evidence and uncertainty; neither reviewer automatically changes code or configuration.
6. Record reviewer disposition (`approve`, `fix_required`, `escalate`, `inconclusive`) and the final Terra resolution.

**Verification:**
- QA artifacts contain the diff and deterministic outputs, not only a free-form prompt.
- Sol review is absent from the automatic fallback list.
- Opus invocation has a declared escalation trigger and a recorded disposition.
- A completed change has passing deterministic validation and a documented QA disposition.

## Task 9: Run the acceptance review and decide whether to promote, extend, or roll back

**Objective:** Make the routing decision from observed evidence rather than a preferred model narrative.

**Files:**
- Create at runtime: `derived/model-routing/experiments/<run-id>-decision.md`
- Update after evidence is accepted: `references/model-routing-evidence.md`

**Steps:**
1. Compare baseline and canary by task class, not by a blended all-model average.
2. Assess accepted-change rate, deterministic pass rate, Sol defects, repair cycles, API errors, fallback activations, parent input tokens, compression fidelity, and observed cost status.
3. Apply the pre-registered promotion/rollback rules mechanically.
4. Produce exactly one disposition per lane: `promote`, `extend_canary`, `keep_manual`, or `rollback`.
5. Update the evidence reference only with observed values, configuration evidence, and the decision rationale. Do not convert a configured route into a claimed executed route without activation evidence.
6. Re-run the workspace gate and preserve all unrelated changes.

**Verification:**
- `python scripts/workspace_status.py`
- Focused model-routing tests pass.
- `python scripts/run_checks.py` passes when shared telemetry/reference surfaces change.
- The decision artifact cites source paths, timestamps, benchmark IDs, and limitations.

## Risks and open questions

1. **Luna evidence is thin.** Five observed turns are insufficient to justify broad automatic delegation; its observed API-request error rate requires a canary and retry analysis.
2. **GLM is not yet the leading fallback candidate.** Its zero observed API errors do not outweigh its weaker completion evidence; keep it behind Kimi unless new controlled results reverse this.
3. **Cost is not currently comparable across providers.** Codex is marked `included`; Ollama Cloud and some other lanes are `unknown`. Do not publish dollar-savings claims without billing evidence.
4. **`auto` can violate cost expectations.** Existing logs show an auxiliary auto route falling into a paid OpenRouter fallback after a credit error. Explicit provider/model assignment and a documented fallback policy are required for predictable cost control.
5. **Fallback is availability, not quality escalation.** Do not use fallback ordering to encode Sol QA or Opus challenge behavior.
6. **Global delegation is all-or-nothing.** Ordinary delegation has a single global provider/model override; use separate sessions or task-level routing for Sol and Opus rather than repeatedly changing the global delegation model.
7. **Compression quality is a correctness issue.** A cheaper summarizer that loses a blocker or test failure can create more premium rework than it saves.
8. **Runtime/doc drift must be checked.** Use `hermes config check`, CLI help, and the installed runtime’s behavior as the execution authority before applying any command from this plan.

## Completion criteria

This plan is complete when the workspace has a durable, evidence-backed model-routing decision that:

- uses Terra for new implementation-parent sessions;
- uses Luna only for qualified, bounded cheap lanes;
- uses Sol as a documented fresh final-QA lane;
- uses Opus only through documented escalation triggers;
- uses Kimi then GLM for reactive availability fallback;
- measures accepted-change efficiency instead of only raw token totals;
- preserves safety/approval routing until independently qualified;
- includes explicit fallback-execution evidence, promotion gates, and rollback records.
