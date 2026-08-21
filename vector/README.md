# Vector layer

Use this layer for semantic recall, similar prior tasks, related documents, prior reasoning,
user preferences, and reusable patterns.

Vector results are retrieval hints, not proof. Validate important claims against canonical
records or source material before relying on them.

This workspace ships a concrete SQLite+FTS5 semantic-memory adapter in
`scripts/vector_memory_index.py`.

- Semantic index: `vector/indexes/vector-memory.sqlite`
- Exact full-text index: `vector/indexes/workspace-index.sqlite`
- Approved source manifest: `vector/retrieval-sources.json`
- Query packet: `tmp/vector-memory-query.json`

## Canonical flow

1. Refresh both indexes from the version-controlled approved source manifest:

```bash
python scripts/retrieval_refresh.py
```

The A12 no-agent cron runs the same contract every six hours. Update the
manifest—not the generated databases—when the approved retrieval scope changes.

2. Query the memory layer (default hybrid retrieval via `memory_search`):

```bash
python scripts/vector_memory_index.py memory_search --query "what did we decide" --packet tmp/vector-memory-query.json
```

Use `query --retrieval-mode full_text` for lexical-only fallback when needed.

3. Open the returned source paths (`source_path`, `citation`) from the machine-readable payload
   before treating a result as truth.

## Required fields in every indexed record

Every indexed result should retain and preserve:

- source or canonical record ID
- source path or URI
- citation / line range
- source hash
- source family
- authority class
- retrieval mode
- indexed timestamp
- content hash
