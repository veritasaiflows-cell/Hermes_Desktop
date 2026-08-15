# Efficiens workspace

This workspace is organized around a layered evidence and memory architecture.

## Directories

- `source/` — original evidence and preserved inputs
- `canonical/` — authoritative SQLite schema and structured state
- `graph/` — relationships and dependencies
- `vector/` — semantic retrieval indexes and metadata
- `derived/` — summaries and generated artifacts
- `telemetry/` — run instrumentation and improvement signals

## Operating rules

`AGENTS.md` contains the active Hermes project instructions. `agent.md` preserves the original identity document.

The canonical SQL baseline is `canonical/schema.sql`. Apply it to a new database only after confirming the target path; preserve existing databases and history.
