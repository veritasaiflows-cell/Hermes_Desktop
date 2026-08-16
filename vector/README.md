# Vector layer

Use this layer for semantic recall, similar prior tasks, related documents, prior reasoning,
user preferences, and reusable patterns.

Vector results are retrieval hints, not proof. Validate important claims against canonical
records or source material before relying on them.

This workspace ships a concrete SQLite+FTS5 semantic-memory adapter in
`scripts/vector_memory_index.py`.

- Database: `tmp/vector-memory.sqlite`
- Query packet: `tmp/vector-memory-query.json`

## Canonical flow

1. Index approved sources into the memory database (embeddings default to local Ollama):

```bash
python scripts/vector_memory_index.py index --source C:/path/to/approved/root --source C:/path/to/notes.md
```

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
