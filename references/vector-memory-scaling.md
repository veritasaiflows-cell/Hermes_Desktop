# Vector memory scaling: when to move beyond the linear cosine scan

- page_type: decision_reference
- owner: scripts/vector_memory_index.py
- status: current
- authority_boundary: review_only

## Context

`scripts/vector_memory_index.py` stores document embeddings as JSON in SQLite
and, for `semantic` and `hybrid` retrieval, loads **every** embedded row and
computes cosine similarity in Python (`_search_semantic`, `_query_semantic_rows`).
This is O(N) per query in the number of embedded documents.

## Measured behaviour (deterministic, mocked-embedding benchmark)

| Corpus | Hybrid query mean | Hybrid query p95 |
|--------|-------------------|------------------|
| 500 docs | ~8.8 ms | ~13.6 ms |
| 2000 docs | ~35.2 ms | ~53.9 ms |

Latency grows roughly linearly with corpus size (~4x from 500 -> 2000 docs).
Full-text (FTS5/BM25) stays sub-4 ms p95 at 2000 docs and is not the concern.

Real-Ollama embedding of the *query* adds ~130 ms/query; that is now mitigated
by the in-process query-embedding LRU cache (see `query_embedding_cache_stats`).
The remaining scaling risk is the **document-side linear scan**, which the cache
does not address.

## Decision

**Do not refactor to an ANN index yet.** At current and near-term corpus sizes
(low thousands of docs) the linear scan is well within acceptable latency and the
simplicity/auditability of "load rows, score in Python" is worth keeping.

## Trigger to revisit

Move to an approximate-nearest-neighbour index when **any** of these holds:

- embedded document count is projected to exceed **~10,000**, or
- hybrid/semantic p95 query latency exceeds **~150 ms** on the target machine, or
- semantic retrieval becomes a high-QPS hot path (many queries/second).

## Preferred options when the trigger fires (in order)

1. **`sqlite-vec`** extension — keeps the single-file SQLite model, adds a real
   vector index; smallest architectural change, preserves provenance columns.
2. **FAISS** (or `hnswlib`) sidecar index keyed by `memory_id` — faster at large
   scale but adds a second store to keep in sync with the SQLite source of truth.

Whichever is chosen: the SQLite `memory_memories` table remains the authoritative
record; the ANN index is a retrieval accelerator only, rebuilt from it, and must
never become the sole source of a result's provenance.

## Verification hooks

- `python scripts/vector_memory_benchmark.py --dataset-size 2000 --queries 500`
  reproduces the latency table above and reports cache hit/miss stats.
- `tests/test_vector_memory_benchmark.py` smoke-tests the harness and the cache.
