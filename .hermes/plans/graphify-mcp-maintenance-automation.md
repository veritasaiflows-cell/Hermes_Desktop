# Graphify MCP maintenance automation

## Objective

Keep the local Graphify pilot observable without allowing a stale or partially built graph to become trusted or MCP-visible.

## Current safe state

| ID | Schedule | Behavior | Mutation boundary | State |
|---|---|---|---|---|
| A15 | hourly at :20 | Read-only freshness monitor | none | active |
| A16 | daily 07:25 | Validate exact Hermes facade config, live tools, closed raw schemas, and `graph_stats` | none | active; passes only after the facade configuration is verified |
| A17 | Sunday 12:00 | Query PyPI for a newer stable Graphify release | none; never runs an updater | active |
| A18 | nightly 02:10 | Read freshness; perform one isolated promotion only with `--promote-once` | immutable candidate generation + atomic selector | schedule paused; one-shot path implemented |

A18 does **not** run `graphify update`, semantic extraction, or any writer against the
served legacy or selected artifact. An unflagged stale invocation returns
`explicit_promotion_required`. The explicitly authorized `--promote-once` path
builds an isolated code-only candidate, validates its complete artifact set, and
uses a single pointer replacement with rollback on failed post-promotion freshness.

## Confirmed blockers

1. The configured stock Graphify 0.9.45 server is still live until the approved Hermes configuration swap is verified.
2. Stock upstream schemas expose `project_path`, so upstream stays behind the fixed facade.
3. The generation/pointer layout, acceptance hash, kernel lock, snapshot, and rollback primitives are implemented and covered by regression tests.
4. The remaining operational step is an isolated candidate promotion followed by the approved Hermes facade configuration and fresh-session smoke check.

## Implemented activation design

The implementation requires all of the following before it selects a generation:

1. Immutable same-volume generations under `graphify-out/generations/<generation>/`.
2. A verified, Git-visible and non-ignored source snapshot that rejects links, reparse points, and special files.
3. Candidate-only extraction, edge reconciliation, structural diagnosis, baseline generation, freshness, and real MCP checks.
4. A Merkle hash and acceptance record for the complete candidate artifact tree.
5. A pre-publication live-source fingerprint recheck.
6. A single atomic pointer replacement selecting one complete immutable generation.
7. A fixed-project MCP facade that resolves the pointer once at startup, exposes only approved tools, and rejects `project_path`.
8. A fresh-session smoke test after the Hermes config is explicitly approved and changed.
9. Kernel-backed reader/writer coordination and crash-recovery tests.
10. A18's recurring schedule remaining paused after the one-shot rollout proof.

## Current verification

```bash
python -m pytest tests/test_graphify_cron_automation.py -q
python scripts/cron_registration_validator.py
python scripts/script_doc_validator.py
python scripts/cron_graphify_artifact_monitor.py
python scripts/cron_graphify_mcp_contract.py
python scripts/cron_graphify_version_advisory.py
python scripts/cron_graphify_code_refresh.py --promote-once
```

Expected current outcomes:

- A15 alerts while no selected artifact is fresh.
- A16 alerts until Hermes is verified to launch the fixed facade; then it reports seven closed-schema tools.
- A17 reports a newer release only when PyPI has one and never applies it.
- A18 exits non-zero with `explicit_promotion_required` unless its explicit one-shot flag is present.

## Stop lines

- Do not mutate the MCP-served top-level graph in place.
- Do not implement publication as a hard-coded artifact list plus sequential `os.replace` calls.
- Do not baseline a candidate against mutable live sources.
- Do not treat a timestamp marker as a lock.
- Do not resume A18 or change the Hermes MCP configuration without explicit operator approval.
- Do not claim Graphify freshness while the configured live server still points at the stale legacy artifact.
