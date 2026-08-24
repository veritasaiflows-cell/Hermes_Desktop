# Efficiens Graphify MCP benchmark and decision contract

This reference separates the existing deterministic transport harness from the
larger decision-grade evaluation. Do not present a transport report as a go/no-go
result.

## Workspace inputs

- Harness: `scripts/graphify_mcp_benchmark.py`
- Corpus: `tests/fixtures/graphify_mcp_cases.json`
- Corpus schema: `tests/fixtures/graphify_mcp_cases.schema.json`
- Tests: `tests/test_graphify_mcp_benchmark.py`
- Freshness owner: `scripts/graphify_freshness.py`
- Derived-output contract: `derived/README.md`

The v1 corpus has 18 cases—six each for topology/navigation,
symbol/relationship, and boundary behavior—and covers the seven selected
Graphify operations. Validate its schema, proof patterns, and expected graph
behavior before every new benchmark version.

## Corpus drift rule

A pre-registered corpus is immutable after route results are observed. When a
workspace remediation changes expected behavior, preserve the old corpus and
create a new version/schema before collecting a new run.

In particular, the deterministic `runs_gate` reconciliation means a
`workspace_status.py` to `graphify_freshness.py` shortest-path case should now
expect a cited relationship, not a missing path. A corpus that still expects
`no_path` for that case is stale and must not drive a current decision. Do not
silently flip the result in place after observing it.

## Stage A: deterministic transport evidence

Inspect the live CLI before use:

```bash
python scripts/graphify_mcp_benchmark.py --help
python -m unittest tests.test_graphify_mcp_benchmark -v
python scripts/graphify_freshness.py
```

The current harness exposes `direct_source`, `raw_cold`, `raw_warm`, and `all`
modes. Run `all` only with a corpus version whose expected behaviors have passed
the current drift check. A bounded raw run then uses:

```bash
python scripts/graphify_mcp_benchmark.py \
  --cases <validated-versioned-corpus.json> \
  --mode all --warm-repetitions 3 \
  --out tmp/graphify-mcp-transport.json
```

The checked-in v1 corpus still contains the pre-remediation no-path expectation,
so it is suitable for harness regression/history but not for a current pilot
decision until a new supported corpus version is created.

Require the active Python environment to import the MCP SDK and record its
version. The harness launches the pinned local server against the absolute graph
path and restricts its child environment to non-secret process variables.

The current `graphify-mcp-benchmark.v1` report is transport/direct-route
evidence. It is **not decision-grade** merely because it contains successful
calls or advertised tools. Before promotion, the implementation must also add or
join evidence for:

- artifact freshness before and after;
- source revision plus dirty-tree/source fingerprint;
- graph and corpus digests;
- Hermes-selected tools independently of raw advertisement;
- primary-source verification of MCP answers;
- actual fresh-session Hermes results;
- payload/context measurements;
- registered threshold scoring and one mechanical decision.

Report only routes the current script actually implements. Do not claim a
Graphify CLI route, Hermes-session route, or final scoring phase from a plan or
stub.

## Stage B: decision-grade envelope

A complete decision report should use an envelope equivalent to:

```json
{
  "schema": "mcp-pilot-evaluation.v1",
  "transport_report_schema": "graphify-mcp-benchmark.v1",
  "corpus_schema": "graphify-mcp-cases.vN",
  "corpus_hash": "sha256:...",
  "source_revision": "...",
  "source_fingerprint": "...",
  "graph_hash": "sha256:...",
  "artifact_freshness_before": "fresh",
  "artifact_freshness_after": "fresh",
  "transport": "stdio",
  "authentication": "none",
  "server_command": ["uvx", "--from", "graphifyy[mcp]==0.9.45", "graphify-mcp", "<absolute graph path>"],
  "server_advertised_tools": ["..."],
  "client_selected_tools": ["..."],
  "invoked_tools": ["..."],
  "excluded_tools": ["..."],
  "decision": "promote | extend_pilot | keep_direct_source | disable"
}
```

`server_advertised_tools` is not authorization evidence. For the current
Graphify package it can contain ten operations while Hermes selects seven. The
invoked set must be a subset of the selected set. Do not invoke excluded PR
tools to prove they are excluded.

## Per-case evidence

Retain, at minimum:

```json
{
  "case_id": "stable-id",
  "route": "direct_source | raw_mcp_cold | raw_mcp_warm | hermes_session",
  "operation": "named-operation",
  "startup_ms": 0.0,
  "call_ms": 0.0,
  "time_to_verified_evidence_ms": 0.0,
  "source_path": "authoritative/source",
  "source_line_or_pattern": "...",
  "source_proof_verified": true,
  "relationship_correct": true,
  "fallback_used": false,
  "payload_bytes": 0,
  "protocol_error": null,
  "exception": null,
  "raw_output": "preserved result"
}
```

Cold startup and warm-call latency must stay separate. A protocol result with
`is_error` is a failure even if the SDK did not raise. An MCP output without an
independent source check is a locator, not a verified answer.

## Client evidence and fresh sessions

Capture:

- `hermes mcp list` for enabled/selected status;
- `hermes mcp test graphify` for connection and advertised inventory;
- the actual `mcp_graphify_*` registry in a fresh session.

Raw discovery can be broader than selection. Start a fresh session after MCP
configuration changes; do not assume `/reload-mcp` exists.

Do not modify the active default profile to create an experiment arm. Temporary
profile creation/alteration requires explicit operator approval, supported
Hermes commands, an exact teardown action, and read-back verification.

## Artifact placement

Keep partial transport output in `tmp/`. Promote only a complete, validated JSON
and human summary to `derived/benchmarks/graphify-mcp/<run-id>.*`. Per
`derived/README.md`, include source identifiers, generation timestamp, generator
and versions, schema, freshness assumptions, and validation status. A derived
report is never a competing source of truth.

## Minimum decision checks

1. Corpus version/hash is fixed before results and still matches current source
   and graph semantics.
2. Freshness passes before and after.
3. Selected tools equal the approved seven; invoked tools are a subset.
4. Every accepted answer has source proof.
5. No-path/no-result cases use source fallback rather than asserting absence.
6. Cold and warm measures are separate and protocol errors count as failures.
7. Actual Hermes behavior is measured before promotion.
8. The final decision compares verified-evidence quality/time or payload—not raw
   latency or token estimates alone.
9. Exactly one outcome is emitted: `promote`, `extend_pilot`,
   `keep_direct_source`, or `disable`.

## Failure handling

Abort decision scoring on stale artifacts, corpus drift, forbidden selected
operations, missing source proof, or lost raw evidence. Preserve the failed run
in scratch, use direct source navigation, and apply the pre-registered failure
outcome. Never move thresholds or broaden the MCP scope after observing results.
