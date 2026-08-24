---
name: mcp-pilot-evaluation
title: Evaluate an MCP pilot against direct navigation
description: "Use when deciding whether an MCP pilot beats direct tools."
version: 1.1.0
updated: 2026-08-23
tags:
  - mcp
  - benchmarking
  - evaluation
  - least-privilege
  - source-verification
related_skills:
  - mcp-integration-governance
---

# MCP Pilot Evaluation

## Purpose

Decide whether an MCP integration materially improves an agent workflow over
existing direct source, CLI, SQL, graph, or API navigation. Measure the real
route to verified evidence—not only connectivity, raw latency, or a vendor token
estimate.

Use `mcp-integration-governance` first. This skill evaluates a bounded capability
contract; it never authorizes broader tools, credentials, or transport.

## Scope and explicit non-scope

In scope:

- immutable source-verified case corpora;
- direct, raw MCP, and actual client/agent route comparison;
- cold/warm timing, evidence quality, reliability, payload, and fallback scoring;
- a mechanical retain/extend/disable decision.

Out of scope:

- changing the capability contract during measurement;
- using a benchmark to authorize writes, credentials, remote transport, or new
  profiles without separate approval;
- treating Graphify or another derived index as proof of workspace state.

## Efficiens authority boundary

For `HermesWorkspace`, primary source and canonical SQL remain authoritative.
The durable graph adapter locates asserted record relationships. Graphify MCP is
a derived code-navigation route only. Every accepted Graphify result must be
cross-checked in its declared source; no-path and no-result responses require
source fallback.

## Required inputs

Before measuring, require:

- a written governance contract with named allowed and excluded operations;
- a fresh, diagnosed artifact and retained semantic-extraction caveats;
- a deterministic direct-source/CLI baseline;
- a versioned immutable case corpus with primary-source proof for each case;
- pre-registered decision thresholds and final outcome vocabulary;
- server-advertised and client-selected capability evidence;
- a rollback owner and action;
- current repository status and a source revision **plus dirty-tree/source
  fingerprint** when the workspace is uncommitted.

Do not repair unrelated workspace-control failures as part of the benchmark.
Record their trust impact and keep workflow/canonical questions on their owning
routes.

## Evaluation procedure

### 1. Pre-register and version the corpus

The corpus must declare its schema/version. Each case must declare:

- stable ID and category;
- natural-language question;
- exactly one permitted MCP operation and arguments;
- expected behavior (`locator`, `relationship`, `aggregate`, or expected
  absence);
- authoritative source path and stable proof pattern/range;
- mandatory source fallback.

Cover normal navigation, relationships, and negative/boundary behavior. Do not
silently alter a used corpus after observing route results. If a workspace fix
changes expected behavior—such as a formerly missing path becoming an explicit
cited edge—preserve the old corpus and create a new version before the next run.

### 2. Separate advertisement, selection, and invocation

Record independently:

1. **Advertised tools** — raw server `list_tools()` or `hermes mcp test`.
2. **Selected tools** — `hermes mcp list`/configuration plus the actual fresh
   client registry.
3. **Invoked tools** — operations present in the retained raw run records.

The advertised set may be broader than the selected allowlist. Do not call
excluded operations to test exclusion; client selection is the authorization
evidence. The invoked set must be a subset of the selected set.

### 3. Measure deterministic routes

Run the same corpus through:

- direct source/CLI navigation;
- raw MCP cold calls when startup overhead matters;
- raw MCP warm calls in one initialized session;
- the actual Hermes/client route before a product decision.

Record startup separately from tool-call latency. Restrict raw stdio child
environments to non-secret process variables. Preserve raw outputs, exceptions,
timeouts, and protocol-level `is_error` results.

### 4. Use the workspace harness without overclaiming it

The current Graphify primitives are:

- `scripts/graphify_mcp_benchmark.py`;
- `tests/fixtures/graphify_mcp_cases.json` and its schema;
- `tests/test_graphify_mcp_benchmark.py`;
- `scripts/graphify_freshness.py`.

Inspect `python scripts/graphify_mcp_benchmark.py --help` before use and report
only routes it actually implements. Its `graphify-mcp-benchmark.v1` output is
transport/direct-route evidence, not a final go/no-go artifact unless it also
contains freshness before/after, source and graph fingerprints, selected-tool
evidence, verified MCP answers, actual Hermes-session results, scoring, and a
mechanical decision.

Keep incomplete/raw runs in `tmp/`. Promote only complete, validated decision
artifacts to `derived/benchmarks/graphify-mcp/` under `derived/README.md`.

### 5. Verify every result against source

For each result, record:

- source-proof and relationship correctness;
- time to verified evidence, including the source cross-check;
- cold startup and warm call latency separately;
- output/context size and calls made;
- failures, fallbacks, stale refusals, and silent-wrong-answer count;
- artifact freshness before and after.

A retrieval result without source proof is a locator, not a correct answer.

### 6. Run the actual client arm safely

Do not use raw MCP results as a substitute for Hermes behavior. Use fresh,
otherwise-comparable sessions and record the actual selected registry. Do not
modify the active default profile merely for experimental isolation. Creating or
altering temporary profiles requires explicit operator approval, supported
Hermes commands, an exact teardown action, and read-back verification.

### 7. Score and decide mechanically

Apply thresholds registered before results. At minimum require:

- no security or authority regression;
- zero silent wrong answers;
- source-proof quality no worse than the direct baseline within the registered
  tolerance;
- bounded transport/tool failures and honest fallbacks;
- material improvement in verified-evidence time or evidence payload.

Use exactly one final status:

- `promote`
- `extend_pilot`
- `keep_direct_source`
- `disable`

Do not promote from one favorable latency or context statistic.

## Expected output

Emit a machine-readable report plus a short human summary containing:

- corpus schema/hash and source revision/fingerprint;
- graph digest and freshness before/after;
- exact server command/transport and no-auth status;
- advertised, selected, invoked, and excluded sets;
- raw route results, primary-source proof, and failures;
- threshold calculations and one mechanical outcome;
- rollback/next action and interpretation limits.

## Failure handling and rollback

Abort scoring on stale/unavailable artifacts, corpus drift, forbidden selected
operations, missing source proof, protocol evidence loss, or an unverified client
registry. Preserve the failed raw evidence and classify the result as
`extend_pilot`, `keep_direct_source`, or `disable` according to the pre-registered
rule—never “fix” thresholds after observing data.

Rollback uses the governance contract: remove/disable the server through Hermes,
verify with `hermes mcp list`, and use a fresh session. Benchmark teardown must
not delete source evidence or alter unrelated workspace/profile state.

## Verification checklist

- [ ] Corpus version/hash is fixed before route results.
- [ ] Expected behaviors still match current source and graph semantics.
- [ ] Artifact is fresh before and after.
- [ ] Advertised, selected, and invoked sets are independently recorded.
- [ ] Cold startup and warm calls are separate.
- [ ] Exceptions and protocol errors count as failures.
- [ ] Every accepted result has primary-source proof.
- [ ] Actual Hermes/client behavior is measured before promotion.
- [ ] Thresholds and final status are explicit.
- [ ] Raw and final artifacts are stored in governed layers.

## References

- `references/raw-stdio-benchmark-contract.md` — Efficiens two-stage transport
  and final decision-report contract.
- `mcp-integration-governance` — capability, authority, activation, and rollback
  contract.

## Change history

- v1.0.0 — Added immutable corpus, cold/warm raw MCP, source proof, and decision
  requirements.
- v1.1.0 — Aligned with the Efficiens Graphify harness, corpus versioning after
  graph fixes, selected-vs-advertised evidence, governed artifact placement,
  fresh-session client measurement, and exact decision vocabulary.
