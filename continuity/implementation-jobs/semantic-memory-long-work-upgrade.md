# Disciplined Long-Work and Semantic Retrieval Upgrade Plan

## Goal

Make long implementations efficiently resumable across models and sessions while improving semantic retrieval precision, freshness, and context cost.

## Governing architecture

- `state/implementation-jobs/*.json` is authoritative machine state for implementation jobs and phases.
- `continuity/implementation-jobs/*.md` is the stable human plan and rationale.
- `scripts/concurrent_lane_manager.py` remains the write-lease and collision authority.
- Acceptance commands execute as argv arrays and emit bounded receipts with before/after source fingerprints.
- Pickup packets contain only current phase, accepted-phase summaries, blockers, stop lines, and exact next commands.
- Exact state determines where work stands; semantic retrieval explains why. Semantic results never advance state.

## Phase P0 — Implementation contract control plane

**Objective:** Add validated job contracts, dependency-ordered phases, executed acceptance receipts, explicit blocking, closeout, and bounded pickup packets.

**Files:**
- `scripts/implementation_job.py`
- `tests/test_implementation_job.py`
- `references/implementation-contract.md`
- `references/script-index.md`
- `state/README.md`

**Acceptance:**
- `uv run --with pytest python -m pytest tests/test_implementation_job.py -q`

## Phase P1 — Section-level semantic indexing and registry v2

**Objective:** Index Markdown by heading section with exact line citations and introduce metadata-rich retrieval source entries while retaining v1 compatibility.

**Files:**
- `scripts/vector_memory_index.py`
- `scripts/workspace_index.py`
- `scripts/retrieval_refresh.py`
- `vector/retrieval-sources.json`
- focused index/retrieval tests

**Acceptance:**
- focused workspace/vector/retrieval tests pass
- existing v1 manifests remain supported
- section results preserve path, heading, line range, hash, family, and authority

## Phase P2 — Selective Wiki, continuity, and plan retrieval

**Objective:** Add only high-signal generated sources with review-only authority and freshness gating; add weak-result abstention and bounded result defaults.

**Approved generated sources:**
- Wiki index, decisions, gaps, and selected system synthesis
- workflow continuity notes
- active implementation plans

**Acceptance:**
- stale Wiki sources cannot enter a fresh index
- authoritative and review-only results remain distinguishable
- duplicate source/section results are removed

## Phase P3 — Incremental refresh and batched queries

**Objective:** Reuse unchanged embeddings by content hash and provide a multi-query path that reuses the in-process query cache.

**Acceptance:**
- unchanged sections are not re-embedded
- removed sections disappear
- changed sections refresh atomically
- batched output preserves one bounded packet per query

## Phase P4 — Startup/status integration and closeout

**Objective:** Surface active implementation jobs and deterministic pickup commands in workspace status, refresh affected derived surfaces, and run independent review.

**Acceptance:**
- focused tests pass
- full test suite passes with nonzero test count
- `python scripts/run_checks.py --skip-smoke` passes
- implementation-job validation and pickup pass
- lane register validates with no collisions or expired leases
- final source fingerprint is current
- independent review reports no security or logic blockers

## Efficiency constraints

- Do not add an ANN/vector database at current corpus size.
- Do not scan arbitrary workspace files; use the approved registry.
- Do not load full transcripts for pickup.
- Limit pickup semantic context to a few verified sections.
- Reuse existing SQLite, lane, routing, fingerprint, and test infrastructure.
- Keep phases serial when they touch shared indexer files; parallelize only non-overlapping read-only review.

## Stop lines

- No phase may be accepted without executed commands and stable source fingerprints.
- No semantic result may be treated as authoritative current state.
- No stale Wiki page may be indexed as fresh.
- No commit may include work outside the registered implementation scope.
