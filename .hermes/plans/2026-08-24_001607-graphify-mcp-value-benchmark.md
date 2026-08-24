# Graphify MCP Value Benchmark Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make a go/no-go decision on retaining the local read-only Graphify MCP route by measuring correctness, time-to-verified-evidence, context cost, reliability, and operational safety against direct source navigation.

**Architecture:** Use one immutable, source-verified question set and run it through four routes: direct source navigation, direct Graphify CLI, raw local MCP protocol, and fresh Hermes sessions with the existing seven-tool allowlist. The report will score each answer against primary-source evidence and record Graphify freshness and MCP-capability evidence before and after every run.

**Tech Stack:** Python 3.11 standard library, `unittest`, existing `graphifyy[mcp]==0.9.45` isolated `uvx` runtime, `hermes mcp`, local `graphify-out/graph.json`.

---

## Current evidence and scope

- The current pilot is local stdio only, has no authentication or remote endpoint, and reports `7 selected` Graphify tools.
- `hermes mcp test graphify` connected in 2.265 seconds and advertised ten server tools. The three PR tools remain outside the selected set.
- The graph is fresh: `python scripts/graphify_freshness.py` returned `status: fresh` with no issues.
- The existing Graphify-native benchmark estimates a 5.0x graph-context reduction (about 19,802 tokens/query versus a 99,066-token naive corpus). It does **not** measure MCP client overhead, answer correctness, source verification, or end-to-end agent usefulness, so it is not a go/no-go result.
- Preserve the existing unrelated changes in `state/workflows/WF-1000.json` through `WF-1003.json`; no benchmark task should touch them.

## Pre-registered decision rule

Promote the MCP route beyond the pilot only if all safety/reliability gates pass and the route creates material user-facing value:

1. **Safety gate (all required):** local stdio only; no credentials; freshness passes before and after every run; the active Hermes session exposes only the seven selected Graphify analysis tools; no PR, write, shell, filesystem, refresh, remote, or external-account capability is callable through this server.
2. **Evidence-quality gate:** at least 90% of structural cases locate the pre-registered authoritative source or exact graph relation, with every accepted answer cross-checked in the listed source. It must not be more than five percentage points worse than the direct-source baseline.
3. **Reliability gate:** zero silent wrong answers; no more than one recoverable transport/tool failure across 54 warm calls; every no-path or incomplete-graph result must explicitly recommend source fallback rather than imply absence.
4. **Value gate:** on structural-navigation cases, the MCP route must improve median **time to verified evidence** (tool result plus required source cross-check) by at least 25% in at least two of three categories, or reduce evidence payload by at least 3x without reducing evidence quality.
5. **Operational gate:** the warm-session P95 transport/tool latency must be acceptable for interactive use (target: below 1 second); cold-start cost is recorded separately and must not be represented as per-query overhead.

If a safety, source-verification, or reliability gate fails, remove or disable the pilot rather than broadening its tools. If safety passes but the value gate fails, retain direct source navigation and do not spend more effort on MCP integration.

## Task 1: Create a source-verified benchmark corpus

**Objective:** Pre-register questions and gold evidence before recording comparative results.

**Files:**
- Create: `tests/fixtures/graphify_mcp_cases.json`
- Create: `tests/fixtures/graphify_mcp_cases.schema.json`

**Steps:**
1. Curate 18 questions from current workspace sources, split into six topology/navigation cases, six symbol/neighbor/path cases, and six negative or boundary cases.
2. For every case, record a stable `case_id`, question, allowed Graphify operation (`query_graph`, `get_node`, `get_neighbors`, `shortest_path`, `get_community`, `god_nodes`, or `graph_stats`), expected node/edge behavior, primary-source path, source line range or unique proof pattern, and the required safe fallback behavior.
3. Include cases known to need source fallback—for example, a relationship not represented as a Graphify path despite a source-level invocation—so “no path found” is scored as an honest limitation rather than a failure to hallucinate.
4. Avoid questions whose answer is solely a generated graph artifact or a mutable workflow state JSON document; Graphify’s AST graph is not authoritative for those claims.
5. Validate fixture shape and unique IDs through a schema and deterministic unit tests. Do not change the questions after observing results; additions require a new corpus version.

**Verification:** A test fails on duplicate IDs, unavailable MCP operation names, paths outside the workspace, absent proof patterns, or a case without a declared fallback policy.

## Task 2: Build a deterministic transport and direct-route harness

**Objective:** Measure the graph routes separately so server/protocol cost is not confused with agent reasoning cost.

**Files:**
- Create: `scripts/graphify_mcp_benchmark.py`
- Create: `tests/test_graphify_mcp_benchmark.py`

**Steps:**
1. Follow the established `scripts/vector_memory_benchmark.py` pattern: return a JSON-serializable report from a testable `run_benchmark()` function, record per-call times with `time.perf_counter_ns()`, and calculate mean, P50, and P95 without external services.
2. Implement four named adapters with a common result schema: `direct_source`, `graphify_cli`, `raw_mcp`, and `hermes_session` (the last is populated by the controlled agent trial in Task 4, not faked by the script).
3. For `raw_mcp`, launch the version-pinned `uvx --from graphifyy[mcp]==0.9.45` stdio server against the absolute `graphify-out/graph.json` path, initialize once, and call only the operation named by each benchmark case. Record process startup separately from warm tool-call latency.
4. For `direct_source`, use deterministic workspace search plus the source proof in the case file; report the files and lines inspected. For `graphify_cli`, issue the equivalent native Graphify query/path command against the same graph.
5. Run each case once cold and three times warm per route. Persist raw timings and raw returned evidence, not just aggregates, so percentiles and scoring can be audited.

**Verification:** Unit tests use a temporary fixture graph and a fake MCP transport for timing/scoring logic; tests assert cold and warm stats are distinct, percentiles are correct, errors are retained, and no adapter is silently substituted for another.

## Task 3: Add freshness, capability, and source-evidence checks

**Objective:** Make every benchmark result traceable and safe to interpret.

**Files:**
- Modify: `scripts/graphify_mcp_benchmark.py`
- Modify: `tests/test_graphify_mcp_benchmark.py`

**Steps:**
1. Execute `python scripts/graphify_freshness.py` before the first call and after the last call; abort scoring if either report is not `fresh`.
2. Capture `hermes mcp list` and `hermes mcp test graphify` into report metadata. Distinguish the server’s advertised ten tools from Hermes’s seven selected tools; count only selected tools as exposed capability.
3. Assert that the selected set exactly equals the seven approved graph-reading tools. Treat any selected PR tool or unapproved capability as a safety failure.
4. For each candidate answer, independently load the declared primary source and validate the case’s proof pattern before marking it correct. A graph answer without source verification is “locator only,” never a correct final answer.
5. Record graph digest/freshness report, Graphify package version, MCP command, no-auth/local-stdio configuration, source revision/fingerprint, corpus schema version, timestamp, and validation outcome in the report.

**Verification:** Tests cover stale/unavailable graph refusal, a missing proof pattern, a server-advertised-but-unselected PR tool, a selected forbidden tool, and a no-path result that must carry a source-fallback outcome.

## Task 4: Run the controlled Hermes end-to-end trial

**Objective:** Measure whether the MCP tools improve an actual Hermes answer, rather than only raw protocol performance.

**Files:**
- Create: `docs/benchmarking/graphify-mcp-runbook.md`
- Create at runtime: `derived/benchmarks/graphify-mcp/<run-id>.json`

**Steps:**
1. Use two temporary, non-default Hermes profiles configured through supported `hermes config` commands: a source-only control and an otherwise-identical Graphify profile. Do not edit the active default profile by hand and do not expose any additional server tools.
2. Pin the same model, prompt template, tool budget, workspace revision, and benchmark corpus for both arms. Randomize case order and run fresh sessions so results are not contaminated by prior graph context.
3. Require every final answer to identify the specific primary file and supporting location; route-specific prompts must not name expected node labels or paths.
4. Execute three warm repetitions for each of the 18 cases in both arms. A separate five-repetition cold-start test measures connection/discovery cost but is excluded from warm query latency.
5. Have a reviewer score outputs blind to route using the pre-registered source evidence. Record correct locator, correct source-proof claim, time to verified evidence, payload size, calls made, fallback use, failures, and unsafe behavior.

**Verification:** The runbook contains the exact profile setup, fresh-session commands, run ordering seed, collection format, teardown command, and a proof that temporary profiles/configuration were removed after the trial.

## Task 5: Generate an auditable comparison report

**Objective:** Produce a decision-ready artifact rather than a single favorable latency number.

**Files:**
- Modify: `scripts/graphify_mcp_benchmark.py`
- Create at runtime: `derived/benchmarks/graphify-mcp/<run-id>.json`
- Create at runtime: `derived/benchmarks/graphify-mcp/<run-id>.md`

**Steps:**
1. Emit both JSON and Markdown reports. Follow `derived/README.md`: include source record identifiers/paths, generation time, generator/version, schema version, freshness assumptions, and validation status.
2. Report results by route and category: correct-source rate, correct-relation rate, no-path/fallback behavior, P50/P95 cold and warm latencies, time to verified evidence, input/output payload sizes, tool failures, and selected-tool safety findings.
3. Present the current 5.0x Graphify-native context estimate as a separate contextual metric, never as the MCP benefit claim.
4. Apply the decision rule mechanically and end with exactly one status: `promote`, `extend_pilot`, `keep_direct_source`, or `disable`—with each failed threshold cited.

**Verification:** Unit tests use fixed synthetic measurements to prove each decision outcome and ensure a stale/failing safety gate cannot result in `promote`.

## Task 6: Validate and decide

**Objective:** Confirm the harness works and make the MCP decision from evidence.

**Files:**
- Test: `tests/test_graphify_mcp_benchmark.py`
- Test: `tests/test_graphify_freshness.py`
- Test: `tests/test_workspace_status.py`

**Steps:**
1. Run the targeted unit tests, then the full project suite required by the workspace’s current validation contract.
2. Run the Graphify freshness gate immediately after benchmark collection.
3. Review the JSON and Markdown report against the pre-registered decision rule; inspect all mismatches and all no-path cases in primary source before deciding.
4. Keep the pilot scoped to the existing read-only seven-tool allowlist regardless of outcome. Do not commit, push, change credentials, add HTTP transport, or expand tool scope unless separately requested by the operator.

**Verification commands:**
- `python -m unittest tests.test_graphify_mcp_benchmark -v`
- `python -m unittest tests.test_graphify_freshness tests.test_workspace_status -v`
- `python scripts/graphify_freshness.py`
- `hermes mcp list`
- `hermes mcp test graphify`
- `python -m unittest discover -s tests -v`

## Risks and interpretation limits

- The graph may omit valid source-level relationships. Such gaps are a recall/completeness metric, not evidence that a source dependency does not exist.
- Comparing only raw tool latency would overstate the value: final answers must still pay for source verification.
- Agent runs can vary with model behavior. Pinning model/prompt/tool budget, randomizing order, and retaining raw transcripts reduces—but does not eliminate—this variance.
- The 18-case suite is a pilot sample, not a universal claim. If it passes, retain a short shadow-mode review over several real structural-navigation tasks before treating it as a default route.
