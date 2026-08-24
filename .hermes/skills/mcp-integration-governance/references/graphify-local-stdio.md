# Graphify local stdio MCP pilot for Efficiens

## Scope and authority

Use Graphify only as a local, read-only code-navigation service for
`HermesWorkspace`. The artifact at `graphify-out/graph.json` is derived,
rebuildable, and Git-ignored. It cannot establish workflow state, ownership,
approval, canonical records, or consequential absence. Verify important results
in primary source; use the durable graph adapter for asserted record
relationships.

## Workspace preflight

From the workspace root, capture—not silently repair—the current trust state:

```bash
python scripts/workspace_status.py
graphify check-update .
python scripts/graphify_freshness.py
hermes mcp list
hermes mcp test graphify
```

Graphify use requires `graphify_freshness.py` to report `fresh`. An unrelated
routing or workflow-health warning does not make Graphify authoritative and must
be reported separately. Preserve all existing uncommitted and user-owned work.

Validate the exact server runtime Hermes will spawn. For this pilot, the proven
launcher shape is:

```text
uvx --from graphifyy[mcp]==0.9.45 graphify-mcp <absolute-workspace-path>/graphify-out/graph.json
```

Do not assume `python -m graphify.serve` works in the Hermes or project
interpreter. Do not modify either interpreter to make it work.

## Refresh ownership and ordering

Do not run `update`, `extract`, `cluster-only`, or `label` against the accepted
`graphify-out/` while that graph can be served by MCP. Graphify can replace
`graph.json` before workspace reconciliation and acceptance complete, and MCP
hot reload can expose that unaccepted candidate. Classifying drift before a
writer lock also permits concurrent source changes to enter the published
baseline.

A safe writer must acquire its lock before fingerprinting, build from an
immutable source snapshot into an isolated candidate directory, validate the
candidate without changing the accepted artifact, re-check the source
fingerprint, and atomically promote the complete accepted artifact set only
after every gate passes. The current A18 code-only writer does not yet satisfy
that contract and must remain paused. Leave the graph honestly stale rather than
using the in-place commands below as a fallback.

Within a future isolated candidate workflow, `graphify update .` refreshes code
structure without an LLM. When prose or other semantic sources changed, use the
version-pinned Ollama extra and a local model against the immutable candidate
source snapshot:

The commands below document stage ordering only. Do not execute them from the
live workspace root. A staging implementation must direct every Graphify and
workspace-owned command to the isolated candidate workspace/artifact paths.

```bash
uvx --from "graphifyy[ollama]==0.9.45" \
  graphify extract . --backend ollama --model <installed-model> \
  --max-concurrency 1 --token-budget 4000 --api-timeout 900
```

A local Ollama run needs no real credential. It can take many minutes; run it as
a tracked process and retain timeout, omitted-file, and source-attribution
warnings. Freshness proves tracked artifact/source parity and required code
coverage, not semantic completeness of every document.

After extraction, run `graphify cluster-only .`; run `graphify label` with the
same pinned runtime/model only when named-community quality is required. The
**last candidate writer** must be followed by the workspace-owned reconciliation
and acceptance sequence against that same isolated candidate:

```bash
python scripts/graphify_gate_edges.py
graphify diagnose multigraph --graph graphify-out/graph.json --json
python scripts/graphify_freshness.py --write-baseline
python scripts/graphify_freshness.py
```

`graphify_gate_edges.py` statically extracts `DEFAULT_GATES` from
`scripts/workspace_status.py` and writes cited `runs_gate` edges. Any later
`update`, `extract`, `cluster-only`, or `label` can rewrite `graph.json`, so run
the reconciliation again before candidate acceptance.

After candidate freshness and the MCP contract pass, require an unchanged source
fingerprint and atomically promote the complete artifact set. If any stage fails,
discard the candidate and leave the previously accepted graph and baseline
untouched. Sequentially copying a few files back after failure is not atomic
rollback and is not sufficient.

Never write a baseline after failed diagnostics, missing gate edges, pending
`needs_update`, incomplete code coverage, or unresolved artifact-path safety
checks.

## Hermes configuration contract

Use Hermes-supported controls only:

- `hermes mcp add` for a custom local server;
- `hermes mcp configure graphify` for the named allowlist;
- `hermes mcp list` for configured/selected status;
- `hermes mcp test graphify` for transport and server advertisement;
- `hermes mcp remove graphify` for rollback.

Do not hand-edit profile configuration. For a catalog server, prefer the inline
MCP setup/consent surface when available. Graphify is a custom local server, so
record the exact version-pinned command and absolute graph path.

Start a fresh Hermes session after add/remove/filter changes. Do not assume
`/reload-mcp` exists; use a desktop reload action only when the current command
palette exposes it.

## Selected allowlist

Select exactly these seven Graphify read operations:

- `query_graph`
- `get_node`
- `get_neighbors`
- `shortest_path`
- `get_community`
- `god_nodes`
- `graph_stats`

The Graphify server currently advertises three additional PR operations:
`list_prs`, `get_pr_impact`, and `triage_prs`. Keep them unselected. Also exclude
all shell, filesystem, git, SQL, graph-refresh, external-account, publication,
spend, and mutation capabilities. Pass no credentials, OAuth grants, HTTP
headers, or remote endpoint.

`hermes mcp test graphify` may legitimately report all ten advertised tools while
`hermes mcp list` reports `7 selected`. That split is expected. Authorization is
the fresh-session selected registry, not raw advertisement. Never invoke an
excluded PR operation merely to test exclusion.

## Acceptance checks

1. Freshness is `fresh` before and after behavior checks.
2. `hermes mcp list` reports the configured server enabled with seven selected
   tools; `hermes mcp test graphify` connects and records the advertised set.
3. A fresh Hermes session exposes only the seven selected `mcp_graphify_*`
   operations.
4. One locator query and one relationship query succeed.
5. The regression query from `workspace_status.py` to
   `graphify_freshness.py` returns a one-hop `runs_gate [EXTRACTED]` path.
6. That path is verified against the literal `DEFAULT_GATES` declaration and the
   `graphify_gate_edges.py` source-line citation.
7. Any no-path/no-result case falls back to direct source and never establishes
   absence.
8. Excluded tools are absent from client selection; they are not invoked.
9. Latest semantic omissions and community-label caveats are retained in the
   pilot evidence.

## Rollback

Run `hermes mcp remove graphify`, verify the server is absent with
`hermes mcp list`, and start a fresh session. Rollback removes only the Hermes
server entry; it does not delete `graphify-out/`, modify another profile, or
change workspace source/canonical state.
