---
name: mcp-integration-governance
title: Govern local MCP integrations and least-privilege pilots
trigger: Use when piloting local MCP tools with least privilege.
description: Use when piloting local MCP tools with least privilege.
version: 1.2.0
updated: 2026-08-23
tags:
  - mcp
  - model-context-protocol
  - local-tools
  - least-privilege
  - governance
  - read-only
related_skills:
  - mcp-pilot-evaluation
---

# MCP Integration Governance

## Purpose

Safely introduce an MCP server as a bounded agent tool surface. Default to a
local, read-first pilot for code graphs, indexes, reports, or other derived
artifacts. This skill governs configuration and capability scope; use
`mcp-pilot-evaluation` to decide whether the pilot earns continued use.

## Scope and explicit non-scope

In scope:

- local stdio servers with a fixed artifact boundary;
- named read-only tool allowlists;
- discovery, source-verified behavior checks, rollback, and pilot handoff.

Out of scope unless separately approved:

- generic shell, filesystem, SQL, git, refresh/build, publication, spend, or
  external-account access;
- credentials, OAuth grants, remote HTTP transport, or mutation tools;
- treating an MCP result as workflow authority or canonical state.

## Efficiens workspace authority boundary

For `HermesWorkspace`, preserve this routing order:

1. primary source and canonical SQL establish exact current truth;
2. the durable `graph/` adapter locates asserted record relationships;
3. vector and Graphify artifacts are derived routing aids;
4. Graphify MCP is for code orientation and structural navigation only.

Never use Graphify to establish workflow state, ownership, approval, canonical
records, or a consequential negative claim. A missing node or path means “fall
back to source,” not “the relationship does not exist.”

## Core rules

1. **Use local stdio first.** Prefer one client-spawned subprocess over shared
   HTTP. Do not bind a listener or add credentials without separate approval.
2. **Fix the data boundary.** Pass an absolute path to one intended derived
   artifact; do not expose an arbitrary workspace root or generic query engine.
3. **Allowlist, never merely blacklist.** Select the smallest named set of
   read-only tools that proves the hypothesis.
4. **Separate advertised, selected, and invoked tools.** Raw server discovery may
   advertise more than the client selects. Only the client-selected registry is
   the callable authority; do not invoke excluded tools as a test.
5. **Keep authority separate.** Treat each MCP result as a locator. Verify
   consequential results in primary source or canonical records.
6. **Use supported client controls.** Configure through the host’s documented
   commands or consent surface; never hand-edit host configuration.
7. **Do not pass secrets by default.** A local read-only pilot should need no API
   keys, OAuth grants, HTTP headers, or credential environment variables.
8. **Honor generated-artifact ownership.** Refresh derived artifacts only with
   owner commands, diagnose before rebaselining, and preserve unrelated work.

## Required inputs and preconditions

Before configuration, record:

- the server name, hypothesis, owner, and rollback action;
- the absolute artifact path and its workspace containment;
- artifact structure, freshness gate, and unresolved extractor warnings;
- the version-pinned server launcher visible to the client runtime;
- exact allowed and excluded tool names;
- the client’s existing MCP server list and current selected-tool evidence;
- repository status and any concurrent or user-owned changes.

For Efficiens, run `python scripts/workspace_status.py` and classify any trust
failure before writes. Do not “repair” unrelated workflow/routing state merely to
make an MCP pilot appear healthy. Graphify use itself requires a fresh
`python scripts/graphify_freshness.py` result.

## Procedure

### 1. Write the capability contract first

Define:

- server name and transport;
- version-pinned launcher plus absolute artifact path;
- allowed and explicitly excluded operations;
- authority boundary and mandatory source fallback;
- representative acceptance questions;
- rollback command and owner.

For a code graph, the initial allowlist should contain only query, node,
neighbors, path, community, hub, and statistics operations. Exclude PR/GitHub,
write, refresh, filesystem, shell, SQL, and external-service operations.

### 2. Validate the exact runtime and artifact

Test the executable the MCP client will launch, not merely an import in another
Python environment. A tool-managed wrapper or a pinned isolated `uvx` runner is
preferred to modifying the project or Hermes interpreter.

Require the owning freshness and structural diagnostics to pass. Freshness does
not erase semantic-extraction warnings; retain them in pilot evidence.

### 3. Configure local stdio through supported controls

For a catalog entry, use Hermes’s inline MCP consent/setup surface when
available. For a custom local server, use `hermes mcp add` and
`hermes mcp configure`; do not hand-edit `config.yaml`. Pass only the required
server arguments and artifact path.

### 4. Verify discovery and selection separately

Use:

- `hermes mcp list` for configured status and selected-tool count;
- `hermes mcp test <name>` for transport and server-advertised discovery;
- a **fresh Hermes session** for the actual injected tool registry.

Do not assume `/reload-mcp` exists. A desktop reload action is usable only when
the current command palette actually exposes it. A raw discovery list containing
excluded tools is not itself a failure when the fresh client session exposes only
the named allowlist.

### 5. Verify behavior and authority fallback

Exercise at least:

- one ordinary locator/query;
- one relationship or shortest-path query;
- one primary-source cross-check;
- one safe fallback for an incomplete/no-path result.

Never call excluded operations just to prove they are excluded. Verify exclusion
from client selection/configuration evidence.

### 6. Hand off to evaluation

Once configuration, selection, behavior, and rollback are proven, use
`mcp-pilot-evaluation`. Connection success alone is not promotion evidence.

## Graphify refresh discipline

For the workspace Graphify pilot, `references/graphify-local-stdio.md` owns the
exact sequence. Do not run a Graphify writer against `graph.json` while that
artifact can be served by MCP. Validation after an in-place write is too late:
MCP hot reload can expose the candidate before acceptance, and concurrent source
changes can enter a baseline after the original drift classification.

The required publication invariant is:

`locked immutable source snapshot -> isolated candidate writer -> gate reconciliation -> diagnostics -> candidate freshness -> MCP contract -> atomic promotion`

Acquire the writer lock before the initial source fingerprint and require the
same fingerprint immediately before promotion. Promote the complete accepted
artifact set atomically or leave the prior accepted set untouched. Until a
tested implementation satisfies that invariant, keep automatic writer jobs such
as A18 paused and report the graph as stale; do not fall back to an in-place
refresh. Any candidate `update`, `extract`, `cluster-only`, or `label` run after
gate reconciliation requires reconciliation again before promotion.

## Expected output

Return a compact capability record containing:

- transport, launcher version, and fixed artifact path;
- advertised, selected, invoked, and excluded tool sets;
- artifact freshness and semantic caveats;
- source-verified behavior evidence;
- activation state in a fresh session;
- rollback command and remaining risks.

## Failure handling and rollback

Treat any of these as a pilot blocker: stale or malformed artifact, ambiguous
artifact boundary, launcher/runtime mismatch, inability to enforce a named
allowlist, credentials unexpectedly required, excluded tools selected, or no
source-verification route.

On failure, fall back to direct source/canonical navigation. For Hermes, remove a
custom server with `hermes mcp remove <name>` or disable it through the supported
surface, verify with `hermes mcp list`, and start a fresh session. Do not delete
the underlying artifact or modify unrelated profile configuration as rollback.

## Security and operational pitfalls

- A tool description or model instruction is not an authorization boundary.
- Do not turn refresh/build commands into MCP tools during a read-only pilot.
- Do not substitute remote HTTP for local stdio merely for convenience.
- Do not interpret successful connection as current or complete answers.
- Do not include unrelated post-checkpoint workspace changes in pilot commits.
- Do not broaden scope to “fix” a weak evaluation result.

## References

- `references/graphify-local-stdio.md` — Efficiens Graphify launcher, refresh,
  deterministic gate-edge, allowlist, activation, and rollback contract.
- `mcp-pilot-evaluation` — measured go/no-go decision after governance passes.

## Change history

- v1.0.0 — Added local stdio, least-privilege, freshness, and authority rules.
- v1.1.0 — Aligned with Efficiens authority routing, Graphify gate-edge refresh,
  Hermes advertised-vs-selected semantics, fresh-session activation, and
  explicit failure/rollback output.
- v1.1.1 — Reworded the authority rule to avoid a project-skill scanner false
  positive while preserving the source-verification requirement.
- v1.2.0 — Prohibited in-place publication to the MCP-served graph and required
  locked immutable input, isolated candidate validation, and atomic promotion.
