# Efficiens workspace

This workspace is organized around a layered evidence and memory architecture, with a governed separation between bootstrap, references, skills, memory, and artifacts.

## Directories

- `source/` — original evidence and preserved inputs
- `canonical/` — authoritative SQLite schema and structured state
- `graph/` — relationships and dependencies
- `vector/` — semantic retrieval indexes and metadata
- `derived/` — summaries and generated artifacts
- `telemetry/` — run instrumentation and improvement signals
- `references/` — authoritative detailed instructions the lean bootstrap points to

## Bootstrap and governance

- `AGENTS.md` — the active, lean Hermes bootstrap (identity, mission, safety/authority, response contract, startup, routing, pointers). Auto-loaded by Hermes in this directory.
- `GOVERNANCE.md` — authoritative owner of file-organization and single-owner rules.
- `references/operating-procedures.md` — authoritative owner of the full operating loop, memory architecture, learning/telemetry, helper-agent rules, workspace change policy, and failure handling.

Every important rule has one authoritative owner; other files point to it rather than duplicating it.

## Canonical state

The canonical SQL baseline is `canonical/schema.sql`. Apply it to a new database only after confirming the target path; preserve existing databases and history.

## Recovery

The workspace is version-controlled with git. The pre-refactor baseline is preserved in history and any prior file can be recovered with `git checkout <commit> -- <path>`.
