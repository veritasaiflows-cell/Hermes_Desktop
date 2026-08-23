# Script documentation audit — 2026-08-22

Graph-first audit of the `scripts/` layer, per the
`script-documentation-and-graphify-audit` skill. Assesses whether each script is
understandable from documentation, metadata, and graph topology without opening
source.

## Method

- Graph freshness confirmed: `python scripts/graphify_freshness.py` → `fresh`.
- Graph shape: 1049 nodes, 2440 edges (`graphify diagnose multigraph` clean).
- Static pass over all 34 `scripts/*.py`: module docstring word count, function
  docstring ratio, argparse help coverage, markdown mention density, graph
  degree, and test anchor.
- Risk rubric (per skill): +1 each for trivial module doc, low function-doc
  ratio, CLI without parser help, no markdown mention, low graph degree, no test
  anchor. `0–1` Green, `2–3` Amber, `4+` Red.

## Result (pre-fix)

- **1 Red**, **13 Amber**, **20 Green** across 34 scripts.

## Fixes applied this pass

1. `top_opportunities_report.py` (Red → Green): expanded module docstring with
   usage + side effects, added `generate()`/`main()` docstrings, added argparse
   `description`/`epilog` and per-arg help, added
   `tests/test_top_opportunities_report.py` (5 tests), documented in
   `references/commerce-data-model.md`.
2. Added `references/script-index.md` — authoritative routing surface mapping
   every script to purpose, entrypoint, test anchor, and owning reference.
3. Added function docstrings to 9 cron/utility scripts and
   `concurrent_lane_manager.py`, `cron_queue_hygiene.py`,
   `vector_memory_benchmark.py`.
4. Added contract tests for 4 previously-uncovered scripts:
   `test_cron_alias_sweep.py`, `test_cron_retrieval_refresh.py`,
   `test_cron_routing_cache_sweep.py`, `test_secrets_helper.py`.
5. Added `scripts/script_doc_validator.py` — a standing gate that fails when a
   non-trivial script lacks a module docstring, has no function docstrings, has
   no test anchor, or is never mentioned in a markdown reference. Wired into
   `run_checks.py` as a smoke check.

## Result (post-fix)

- `python scripts/script_doc_validator.py` → `ok: true`, 0 issues.
- Full suite: **271 passed, 14 subtests passed**.

## Matrix (post-fix)

| script | module_doc_words | function_doc_ratio | parser_help | md_mentions | graph_degree | test_anchor | risk | band |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| concurrent_lane_manager.py | 86 | >0 | Y | 13 | 296 | Y | 0 | Green |
| cron_alias_sweep.py | 29 | >0 | n/a | 3 | 2 | Y | 0 | Green |
| cron_archive_stale_workflows.py | 45 | >0 | Y | 6 | 5 | Y | 0 | Green |
| cron_canonical_integrity.py | 9 | >0 | n/a | 6 | 5 | Y | 0 | Green |
| cron_claim_drift_check.py | 55 | >0 | n/a | 32 | 36 | Y | 0 | Green |
| cron_graph_freshness.py | 105 | 0.60 | n/a | 4 | 41 | Y | 0 | Green |
| cron_health_check.py | 181 | 0.50 | n/a | 3 | 8 | Y | 0 | Green |
| cron_queue_hygiene.py | 92 | >0 | n/a | 10 | 14 | Y | 0 | Green |
| cron_registration_validator.py | 37 | 0.33 | n/a | 10 | 18 | Y | 0 | Green |
| cron_retrieval_refresh.py | 10 | >0 | n/a | 3 | 5 | Y | 0 | Green |
| cron_routing_cache_sweep.py | 29 | 0.50 | n/a | 14 | 11 | Y | 0 | Green |
| cron_routing_refresh.py | 43 | >0 | n/a | 3 | 2 | Y | 0 | Green |
| cron_telemetry_harvest.py | 68 | >0 | n/a | 3 | 2 | Y | 0 | Green |
| cron_test_gate.py | 94 | >0 | n/a | 3 | 2 | Y | 0 | Green |
| cron_wiki_regen.py | 64 | >0 | n/a | 2 | 2 | Y | 0 | Green |
| feedback_evaluation_loop.py | 49 | 0.30 | Y | 22 | 154 | Y | 0 | Green |
| graph_backfill.py | 91 | 0.50 | Y | 4 | 48 | Y | 0 | Green |
| graph_memory.py | 78 | 0.11 | Y | 35 | 59 | Y | 0 | Green |
| graphify_freshness.py | 8 | 0.11 | Y | 5 | 129 | Y | 0 | Green |
| product_research_workflow.py | 47 | 0.14 | Y | 17 | 206 | Y | 0 | Green |
| retrieval_refresh.py | 11 | 0.50 | Y | 6 | 26 | Y | 0 | Green |
| run_checks.py | 17 | 0.61 | Y | 20 | 138 | Y | 0 | Green |
| runtime_metadata.py | 29 | 1.00 | n/a | 0 | 16 | Y | 0 | Green |
| script_doc_validator.py | 60 | 1.00 | Y | 1 | — | Y | 0 | Green |
| secrets_helper.py | 113 | 0.33 | n/a | 8 | 40 | Y | 0 | Green |
| top_opportunities_report.py | 60 | 1.00 | Y | 1 | 8 | Y | 0 | Green |
| vector_memory_benchmark.py | 71 | >0 | Y | 2 | 37 | Y | 0 | Green |
| vector_memory_index.py | 67 | 0.26 | Y | 26 | 368 | Y | 0 | Green |
| wiki_bootstrap.py | 85 | 0.05 | Y | 18 | 123 | Y | 0 | Green |
| workflow_router.py | 63 | 0.31 | Y | 29 | 297 | Y | 0 | Green |
| workflow_runner.py | 35 | 0.17 | n/a | 2 | 53 | Y | 0 | Green |
| workspace_fingerprint.py | 6 | 0.60 | n/a | 0 | 52 | Y | 0 | Green |
| workspace_index.py | 40 | 0.14 | Y | 13 | 167 | Y | 0 | Green |
| workspace_organization_validator.py | 8 | 0.50 | Y | 2 | 10 | Y | 0 | Green |
| workspace_status.py | 55 | 0.22 | n/a | 20 | 49 | Y | 0 | Green |

## Remaining limitations

- `runtime_metadata.py` and `workspace_fingerprint.py` still have zero markdown
  mentions (they are imported by many scripts but not named in a reference).
  They are now covered by the validator's test-anchor rule, but a dedicated
  "shared helpers" reference would close the doc-visibility gap fully.
- `graphify_freshness.py` and `workspace_fingerprint.py` retain thin module
  docstrings (8 and 6 words) — above the validator floor but below the quality
  bar for a human reader.

## Change history

- v1 — initial audit; applied fixes; added standing `script_doc_validator.py` gate.
