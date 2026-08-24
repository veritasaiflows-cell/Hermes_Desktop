# Graphify MCP maintenance automation

## Objective

Keep the local Graphify pilot observable without allowing a stale or partially built graph to become trusted or MCP-visible.

## Current safe state

| ID | Schedule | Behavior | Mutation boundary | State |
|---|---|---|---|---|
| A15 | hourly at :20 | Read-only freshness monitor | none | active |
| A16 | daily 07:25 | Validate exact Hermes config, live tools, raw MCP schemas, and `graph_stats` | none | active; expected to alert while upstream exposes `project_path` |
| A17 | Sunday 12:00 | Query PyPI for a newer stable Graphify release | none; never runs an updater | active |
| A18 | nightly 02:10 | Read freshness and fail closed when stale | none | paused; writer implementation intentionally withheld |

A18 does **not** run `graphify update`, semantic extraction, baseline writes, artifact replacement, or rollback. Its stale result is `atomic_publication_not_activated`. This is deliberate: sequential replacement of selected files cannot satisfy the complete-set atomic publication requirement.

## Confirmed blockers

1. The configured stock Graphify 0.9.45 MCP server hot-reloads `graph.json` in place.
2. Every advertised upstream tool schema exposes `project_path`, so the command-line graph path is not a fixed data boundary.
3. The current top-level `graphify-out/` layout has no atomic complete-set selector.
4. A marker file is not a cross-process kernel lock and cannot safely coordinate readers and writers.
5. Changing the Hermes MCP command to a governed facade is a configuration/system-behavior change and requires explicit operator approval plus a fresh session.

## Required activation design

Do not resume A18 until all of the following are implemented and accepted:

1. Immutable same-volume generations under `graphify-out/generations/<generation>/`.
2. A verified, Git-visible and non-ignored source snapshot that rejects links, reparse points, and special files.
3. Candidate-only extraction, edge reconciliation, structural diagnosis, baseline generation, freshness, and real MCP checks.
4. A Merkle hash and acceptance record for the complete candidate artifact tree.
5. A pre-publication live-source fingerprint recheck.
6. A single atomic pointer replacement selecting one complete immutable generation.
7. A fixed-project MCP facade that resolves the pointer once at startup, exposes only approved tools, and rejects `project_path`.
8. A fresh-session smoke test after the Hermes config is explicitly approved and changed.
9. Kernel-backed reader/writer coordination and crash-recovery tests.
10. A18 remaining paused until the above rollout proof is reviewed.

## Current verification

```bash
python -m pytest tests/test_graphify_cron_automation.py -q
python scripts/cron_registration_validator.py
python scripts/script_doc_validator.py
python scripts/cron_graphify_artifact_monitor.py
python scripts/cron_graphify_mcp_contract.py
python scripts/cron_graphify_version_advisory.py
python scripts/cron_graphify_code_refresh.py
```

Expected current outcomes:

- A15 alerts while the legacy artifact is stale.
- A16 alerts with `project_path_exposed` against stock Graphify 0.9.45.
- A17 reports a newer release only when PyPI has one and never applies it.
- A18 exits non-zero with `atomic_publication_not_activated` and `writer_executed=false`.

## Stop lines

- Do not mutate the MCP-served top-level graph in place.
- Do not implement publication as a hard-coded artifact list plus sequential `os.replace` calls.
- Do not baseline a candidate against mutable live sources.
- Do not treat a timestamp marker as a lock.
- Do not resume A18 or change the Hermes MCP configuration without explicit operator approval.
- Do not claim Graphify freshness while the configured live server still points at the stale legacy artifact.
