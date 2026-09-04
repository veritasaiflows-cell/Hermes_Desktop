# Script index

Authoritative routing surface for the `scripts/` layer. This index answers
"what does this script do, how do I run it, and where is its behavior
documented" without opening source. It **points to** owning references rather
than re-stating their rules (per `GOVERNANCE.md`).

Each entry lists: purpose, primary entrypoint/arguments, test anchor, and the
owning reference. Side effects and invariants live in the owning reference or
the script's own docstring — this index is a map, not a duplicate.

## Automation jobs (A1–A13)

Owned by `references/automation-layer.md`.

| Script | Purpose | Test anchor |
|---|---|---|
| `cron_wiki_regen.py` | A1 — daily wiki freshness regen | `test_cron_wrappers.py` |
| `cron_health_check.py` | A2 — fast operational gate (routing + wiki + alias + cron registration) | `test_cron_wrappers.py` |
| `cron_test_gate.py` | A2-full — full correctness gate (`run_checks.py --skip-smoke --record-telemetry`) | `test_cron_wrappers.py` |
| `cron_routing_cache_sweep.py` | A3 — evict expired/signature-mismatched routing cache rows | `test_cron_routing_cache_sweep.py` |
| `cron_archive_stale_workflows.py` | A4 — flag terminal workflows >30d, archive capsules (`--check-only`) | `test_archive_stale_workflows.py` |
| `cron_routing_refresh.py` | A5 — regenerate routing index + capsules hourly | `test_cron_wrappers.py` |
| `cron_alias_sweep.py` | A6 — report aliases pointing to non-existent workflows | `test_cron_alias_sweep.py` |
| `cron_queue_hygiene.py` | A7 — remove terminal workflows >30d from authoritative queue | `test_queue_hygiene.py` |
| `cron_telemetry_harvest.py` | A8 — feedback/evaluation refresh, surface pending-review IDs | `test_feedback_evaluation_loop.py` |
| `cron_claim_drift_check.py` | A9 — monitor expiring claims, tampered runs, stale replays | `test_claim_drift_check.py` |
| `cron_graph_freshness.py` | A10 — graph orphan/duplicate sweep + coverage drift | `test_freshness_cron.py` |
| `workspace_status.py` | A11 — single-command JSON operating brief | `test_workspace_status.py` |
| `cron_retrieval_refresh.py` | A12 — refresh exact + semantic indexes from approved manifest | `test_cron_retrieval_refresh.py` |
| `cron_canonical_integrity.py` | A13 — verify canonical SQLite integrity (read-only) | `test_freshness_cron.py` |
| `cron_registration_validator.py` | Verify every cron wrapper resolves to a repo script | `test_cron_registration_validator.py` |

## Agent qualification (canary) harnesses

| Script | Purpose | Arguments | Test anchor | Owning reference |
|---|---|---|---|---|
| `researcher_canary_harness.py` | Deterministic, tool-free canary qualification for a read-only Researcher Bot (model attribution, no fallback, exact JSON oracle) | `validate-fixtures/run/verify` + `--manifest --run-dir --timeout` | `test_researcher_canary_harness.py` | `references/researcher-canary-runbook.md` |
| `implementer_canary_harness.py` | Deterministic, evidence-only canary qualification for a bounded, tool-using Implementer Bot (diff/scope/frozen-hash/signature + RED→GREEN oracle) | `validate-fixtures/run/verify` + `--manifest --run-dir --timeout` | `test_implementer_canary_harness.py` | `references/implementer-canary-runbook.md` |

## Workflow entrypoints

| Script | Purpose | Arguments | Test anchor | Owning reference |
|---|---|---|---|---|
| `product_research_workflow.py` | WF-1000 — product research ingestion + candidate scoring | `catalog` + `--database --top-n --min-viability-score --holdout-fraction --adversarial-fraction --dry-run --lane-id --lane-owner --lane-register` | `test_product_research_workflow.py` | `references/workflow-product-research.md` |
| `workflow_router.py` | Workflow ownership routing + control-plane validator | `selector` + `--all --answer --validate --write-index --write-capsules --index-path --no-cache --aliases --status` | `test_workflow_router.py` | `references/workflow-routing-control-plane.md` |
| `workflow_runner.py` | Mandatory read-only control-plane preflight for workflow entrypoints | (library; imported by workflow CLIs) | — | `references/workflow-routing-control-plane.md` |
| `concurrent_lane_manager.py` | Concurrent lane + write-lease manager | `plan/lease/start/complete/status` subcommands | `test_concurrent_lane_manager.py` | `references/concurrent-lane-control-plane.md` |
| `helper_agent_router.py` | Deterministic admission gate for bounded helper-agent spawns (read-only labeling contract; write mode requires leased covering lane) | `admit` + `--request --project-root` | `test_helper_agent_router.py`, `test_helper_agent_router_hardening.py` | `references/operating-procedures.md` |
| `implementation_job.py` | Long-work job/phase contract, executed acceptance receipts, and bounded pickup packets | `validate/status/pickup/phase-start/phase-accept/phase-block/close` | `test_implementation_job.py` | `references/implementation-contract.md` |

## Memory layers

| Script | Purpose | Arguments | Test anchor | Owning reference |
|---|---|---|---|---|
| `graph_memory.py` | Durable graph-memory adapter (add_edge/neighbors/path/affected/validate) | subcommands + `--subject-type --subject-id --predicate --object-type --object-id --confidence --source-ref` | `test_graph_memory.py` | `references/graph-memory.md` |
| `graph_backfill.py` | One-time idempotent backfill of durable graph edges | `--database --state-dir` | `test_graph_backfill.py` | `references/graph-memory.md` |
| `vector_memory_index.py` | SQLite FTS5 + Ollama embedding memory index | `memory_search/memory_get/status` + `--index --query --source-path --embedding-*` | `test_vector_memory_index.py` | `references/memory-routing.md` |
| `workspace_index.py` | Explicit source-grounded SQLite full-text index | `build/query/status` + `--source --index --query` | `test_workspace_index.py` | `references/memory-routing.md` |
| `retrieval_refresh.py` | Refresh exact + semantic indexes from one approved manifest | `--manifest` | — | `references/memory-routing.md` |
| `vector_memory_benchmark.py` | Deterministic benchmark for vector/semantic adapters | `--dataset-size --queries --real-ollama --out` | `test_vector_memory_benchmark.py` | `references/vector-memory-scaling.md` |

## Verification and reporting

| Script | Purpose | Arguments | Test anchor | Owning reference |
|---|---|---|---|---|
| `run_checks.py` | Phase-0 verification runner (tests + smoke + routing + wiki) | `--database --persistent-smoke --skip-smoke --record-telemetry` | `test_run_checks.py` | `references/operating-procedures.md` |
| `workspace_organization_validator.py` | Validate governed directory + retention contract | `--project-root` | `test_workspace_organization_validator.py` | `references/operating-procedures.md` |
| `script_doc_validator.py` | Validate script documentation coverage (module/function docs, test anchor, reference mention) | `--project-root` | `test_script_doc_validator.py` | `references/script-index.md` |
| `graphify_freshness.py` | Deterministic freshness gate for the disposable Graphify artifact | `--project-root --write-baseline` | `test_graphify_freshness.py` | `references/graph-memory.md` |
| `graphify_gate_edges.py` | Reconcile static workspace-status gates into cited derived Graphify edges | `--project-root` | `test_graphify_gate_edges.py` | `references/graph-memory.md` |
| `top_opportunities_report.py` | Ranked, deduplicated opportunity shortlist (read-only) | `database` + `--top-n --min-viability --as-json` | `test_top_opportunities_report.py` | `references/commerce-data-model.md` |
| `feedback_evaluation_loop.py` | Metadata-only feedback→evaluation loop (A8 core) | `refresh/evaluate/decide/status` + `--database --report --decision --reviewer` | `test_feedback_evaluation_loop.py` | `references/automation-layer.md` |
| `wiki_bootstrap.py` | Wiki bootstrap validation + atomic publish | `validate/publish` + `--project-root --manifest-path` | `test_wiki_bootstrap.py` | `references/operating-procedures.md` |

## Shared helpers (no CLI)

| Script | Purpose | Test anchor | Owning reference |
|---|---|---|---|
| `runtime_metadata.py` | Runtime model/provider detection from environment | — | `references/operating-procedures.md` |
| `workspace_fingerprint.py` | Deterministic source fingerprints for correctness telemetry | `test_workspace_fingerprint.py` | `references/operating-procedures.md` |
| `secrets_helper.py` | Secrets resolution (env → keyring → file) for connectors | `test_secrets_helper.py` | `references/automation-layer.md` |

## Change history

- v1 — initial script index covering the workspace `scripts/*.py` entries, mapped to
  owning references and test anchors.
- v1.1 — added the disciplined implementation-job control plane.
