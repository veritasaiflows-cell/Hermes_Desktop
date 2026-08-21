# Memory routing contract — SQL, graph, and vector execution path

Authoritative owner of retrieval-route selection for this workspace. It governs when to use exact structured retrieval, durable graph traversal, local semantic retrieval, or a bounded hybrid, using concrete commands.

## Scope and adapter availability

Hermes native memory stores approved durable facts and user preferences. The canonical SQLite layer stores authoritative structured state. Vector retrieval is a derived recall layer and must remain source-grounded.

The workspace adapters named below are concrete and executable:

- exact full-text route: `scripts/workspace_index.py`
- graph route: `scripts/graph_memory.py` (affected / path / neighbors / validate)
- semantic route: `scripts/vector_memory_index.py memory_search`
- metadata route: `scripts/vector_memory_index.py memory_get`

Verify the approved source registry/profile before invoking. Do not create placeholder indexes or scan arbitrary files merely to satisfy this contract.

## Authority rule

Authoritative source files, current records, and explicitly designated canonical tables outrank all retrieval indexes.

Generated SQL indexes, vector indexes, summaries, and caches are routing layers unless explicitly marked canonical.

A retrieval result is not proof by itself. Open the cited source or canonical record before making a consequential claim.

## Route selection

### Use SQL or full-text search first when the request contains:

- Exact names, identifiers, paths, dates, or statuses
- Structured fields
- Current task or workflow state
- Counts, metrics, timestamps, or version numbers
- Exact keyword or phrase lookup
- Filtering, sorting, joins, or aggregation
- A known record type or table

SQL is preferred because it is deterministic, fast, and auditable for structured questions.

### Use semantic (vector) search first when the request contains:

- A vague or conceptual question
- A paraphrase of prior information
- “What did we decide about…?”
- “Why did we…?”
- Similar past work, lessons, failures, or solutions
- Related notes without known filenames or identifiers
- Natural-language concepts that may appear under different wording

Vector search is preferred when it retrieves meaning rather than requiring exact vocabulary.
Execute via:

```bash
python scripts/vector_memory_index.py memory_search \
  --index vector/indexes/vector-memory.sqlite \
  --query "your natural-language question"
```

### Use graph retrieval first when the request is about relationships

Use the durable graph layer when the request asks how records relate:

- “What depends on X?”
- “Who owns Y?”
- “What did Z affect or contradict?”
- “How are A and B connected?”
- Dependency chains, ownership, impact, or supersession questions

Execute via the graph adapter:

```bash
python scripts/graph_memory.py affected --object-type entities --object-id <id>
python scripts/graph_memory.py path --start-type entities --start-id <a> --end-type tasks --end-id <b>
python scripts/graph_memory.py neighbors --subject-type entities --subject-id <id>
```

Graph edges are asserted relationships with provenance. They are recall aids,
not proof: verify the cited canonical record before making a consequential
claim. The full graph contract is owned by `references/graph-memory.md`.

### Use hybrid retrieval only when both are necessary

Use SQL plus semantic search when the request contains an exact anchor and requires surrounding reasoning or context.
Execute both routes explicitly and then merge results:

```bash
python scripts/workspace_index.py query \
  --index vector/indexes/workspace-index.sqlite \
  --query "exact anchor"

python scripts/vector_memory_index.py memory_search \
  --index vector/indexes/vector-memory.sqlite \
  --query "same question"
```

Examples:

- SQL identifies the exact project; semantic search retrieves related decisions and lessons.
- SQL filters records by date or status; semantic search finds relevant explanations.
- Semantic search finds likely source documents; SQL verifies metadata, freshness, status, or ownership.

Do not use hybrid retrieval by default. Use it only when each route contributes different information.

## Default routing algorithm

1. Parse the request for exact identifiers, fields, dates, and structured filters.
2. If strong structured anchors exist, run:

   - `python scripts/workspace_index.py query --index vector/indexes/workspace-index.sqlite --query "..."`

3. If the request is about relationships (dependencies, ownership, impact, supersession), run:

   - `python scripts/graph_memory.py affected --object-type <type> --object-id <id>`
   - `python scripts/graph_memory.py path --start-type <type> --start-id <a> --end-type <type> --end-id <b>`
   - `python scripts/graph_memory.py neighbors --subject-type <type> --subject-id <id>`

4. If no strong anchors exist, run:

   - `python scripts/vector_memory_index.py memory_search --index vector/indexes/vector-memory.sqlite --query "..."`

5. If the first route returns no useful result, run the fallback route as bounded secondary pass.
6. If multiple routes are used, deduplicate by source path and record ID.
7. Open every high-confidence candidate and verify freshness.
8. For each selected result, validate against source with `memory_get`:

   ```bash
   python scripts/vector_memory_index.py memory_get \
     --index vector/indexes/vector-memory.sqlite \
     --source-path "/absolute/path/to/source.md"
   ```

9. Merge verified evidence and rank by authority, freshness, exactness, and confidence.
10. Return the answer with explicit freshness and degraded-retrieval markers.

Never treat the highest similarity score as the correct answer.

## Workspace adapters

When available, use:

- `scripts/workspace_index.py`
  - Exact workspace and full-text retrieval
  - Structured document, heading, alias, metadata, and freshness lookup
  - Database: `vector/indexes/workspace-index.sqlite`

- `scripts/vector_memory_index.py`
  - Semantic/vector retrieval with source-grounded citations
  - Database: `vector/indexes/vector-memory.sqlite`
  - Query packet: `tmp/vector-memory-query.json`

- `memory_search` (script command)
  - Execute: `python scripts/vector_memory_index.py memory_search`
  - Default mode: `hybrid`
  - Providers: local Ollama embeddings (`--embedding-provider ollama`, default `nomic-embed-text:latest`)
  - Output: `tmp/vector-memory-query.json` by default for machine-readable retrieval packets

- `memory_get` (script command)
  - Execute: `python scripts/vector_memory_index.py memory_get --source-path <path>`
  - Returns indexed metadata for an explicit source path to support citation checks

Use the configured source registry and source profiles. Do not scan arbitrary files or index secrets, credentials, configuration stores, private runtime data, or excluded temporary content.

## Vector retrieval requirements

Every useful vector result should preserve:

- Source path
- Exact line range or citation
- Source hash where available
- Source family
- Authority class
- Retrieval mode
- Embedding provider and model
- Similarity score
- Freshness state
- Warnings

Vector search is a recall mechanism. It does not establish:

- Current truth
- Authority
- Approval
- Completion
- Correctness
- Freshness

If semantic embeddings are unavailable, fall back to approved full-text or local lexical retrieval and mark the result as degraded.

Do not claim degraded retrieval is equivalent to semantic retrieval.

## SQL retrieval requirements

SQL results may be used directly only when the table is explicitly designated canonical.

Otherwise, SQL is a locator and filtering layer. After a result:

1. Open the referenced source record or file.
2. Check freshness and validation state.
3. Confirm that the source has not drifted.
4. Use the source as the basis for the answer.

If SQL conflicts with the source, trust the source, report the conflict, and classify the index as stale or inconsistent.

Do not silently repair source truth to match an index.

## Freshness and fail-closed behavior

For current or consequential questions:

- Require fresh source validation where available.
- Reject or clearly label stale index results.
- Check source hashes and timestamps.
- Prefer direct source inspection when freshness cannot be proven.
- Never convert a missing result into a negative conclusion.
- Never convert a stale result into a current fact.

If retrieval quality is insufficient, say so and identify the missing evidence. Use `memory_search` with `--retrieval-mode full_text` for deterministic fallback.

## Memory writes

Write structured information to SQL only when a defined schema and authority owner exist.

Write durable narrative information to the approved memory source, then index it for retrieval.

Use vector memory as a derived representation of source content. Do not write the same fact independently into multiple memory layers unless one record clearly points to the other.

Store:

- Durable decisions
- Confirmed preferences
- Reusable lessons
- Important task state
- Failure patterns
- Source references
- Resume instructions

Do not store every conversation turn, raw transcripts, secrets, credentials, or unbounded tool output.

Preserve history. Prefer append-only events, versioning, and supersession over silent overwrites.

## Retrieval observability

For meaningful queries, record:

- Route selected
- Reason for route selection
- Source profile
- Query latency
- Fallbacks
- Top sources
- Source-open confirmation
- Stale or conflicting results
- User corrections
- Retrieval success or failure

Improve routing based on measured source accuracy, citation correctness, freshness, first-pass usefulness, false positives, and abstention quality.

Do not optimize only for speed, result count, or similarity score.

## Final retrieval rule

Use this sequence:

`classify -> choose SQL, graph, or vector -> fallback only if needed -> open source -> verify -> answer`

SQL finds exact structure.

Graph finds relationships.

Vector search finds meaning.

The source record establishes truth.

## Change history

- v1 — Installed as the authoritative workspace memory-routing contract when Hermes native memory was enabled.
- v1.1 — Replaced routing placeholders with executable commands (`memory_search`, `memory_get`) and local-ollama-backed hybrid retrieval.
- v1.2 — Added the graph route (`scripts/graph_memory.py`) for relationship-shaped questions; routing order is now SQL → graph → vector.
- v1.3 — Added the graph branch to the default routing algorithm and the adapter list; title now covers SQL, graph, and vector.
