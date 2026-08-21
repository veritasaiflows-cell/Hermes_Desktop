# Efficiens workspace

This workspace is organized around a layered evidence and memory architecture, with a governed separation between bootstrap, references, skills, memory, and artifacts.

## Directories

- `source/` — original evidence and preserved inputs
- `canonical/` — authoritative SQLite schema and structured state
- `graph/` — relationships and dependencies
- `vector/` — semantic retrieval indexes and metadata
- `derived/` — summaries and generated artifacts
- `telemetry/` — run instrumentation and improvement signals
- `state/` — authoritative workflow control-plane state and generated routing surfaces
- `tmp/` — disposable scratch files and per-run packets; safe to clear when no process is using them
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

## Phase-0 verification

Run a minimal reproducible harness check:

```bash
python scripts/wiki_bootstrap.py validate
python scripts/run_checks.py
python scripts/workflow_router.py WF-1000 --answer summary --validate --write-index
```

The default harness runs smoke activity against a temporary database and copied
control plane. Use `--skip-smoke` to run only the unit tests. A persistent smoke
run is deliberately opt-in and requires both flags:

```bash
python scripts/run_checks.py --persistent-smoke --database canonical/efficiens.db
```

## Concurrent lane control plane

For parallel work without overlapping file edits, use:

```bash
python scripts/concurrent_lane_manager.py plan \
  --parent-job-id WF-1000-PROD \
  --workflow-id WF-1000 \
  --workstream research-pass \
  --owner agent-a \
  --allowed-write derived/results.json

python scripts/concurrent_lane_manager.py lease WF-1000::research-pass --owner agent-a
python scripts/concurrent_lane_manager.py start WF-1000::research-pass
python scripts/concurrent_lane_manager.py complete WF-1000::research-pass --proof derived/proof.json
python scripts/concurrent_lane_manager.py status --validate
```

The lane manager enforces write-surface collisions, leases, status transitions,
and proof-required completion. Workflow CLIs that write canonical data also use
the mandatory routing/lease/write-surface preflight in
`scripts/workflow_runner.py`.
