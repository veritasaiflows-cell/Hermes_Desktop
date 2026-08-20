# SQL canonical layer

This layer is the authoritative structured state for the workspace.

- `schema.sql` is the baseline schema for a new SQLite database.
- Preserve history with append-only `events` and explicit `version_history` records.
- Include stable identifiers, timestamps, provenance, confidence, status, scope, and supersession data where applicable.
- Do not silently overwrite important records.
- Validate derived or retrieved claims against this layer and the original source layer.
- Use `claims` for explicit, evidence-level assertions with optional source hashes, validity windows, authority class, and invalidation history.
- Use `relationships` for asserted graph edges between canonical records (dependencies, ownership, impact, contradictions, supersession). Edges link records by identifier and never re-state facts; the graph contract is `references/graph-memory.md`.
- Enable foreign-key enforcement on every connection with `PRAGMA foreign_keys = ON`; SQLite does not persist this setting per database file.

## Access utility

Use `canonical/db.py` instead of opening the database directly:

```python
from canonical.db import CanonicalDB

with CanonicalDB() as db:
    provenance_id = db.add_provenance(
        source_type="manual",
        source_ref="operator-note-001",
        confidence=1.0,
    )
    entity_id = db.insert(
        "entities",
        {"entity_type": "project", "name": "Example"},
        provenance_id=provenance_id,
    )
    db.update(
        "entities",
        entity_id,
        {"status": "archived"},
        provenance_id=provenance_id,
    )
```

`insert()` enables foreign keys, uses WAL mode, inserts transactionally, records UTC timestamps and provenance, and raises `DuplicateRecordError` instead of overwriting. Use `with db.transaction():` for a multi-record all-or-nothing write set; individual inserts defer their commits until that outer transaction succeeds.

`update()` runs as one `BEGIN IMMEDIATE` transaction: it snapshots the prior row into `version_history`, appends a `record.updated` event, then applies the mutation. A failed mutation rolls back the row change, audit records, and automatic provenance together. Repeated updates receive sequential per-record version numbers. Every update resolves one audit provenance ID, even when the target table has no `provenance_id` column; where that column exists, the target row, version record, and event share it. Primary keys and `created_at` are immutable; ambiguous or null provenance changes are rejected. `provenance`, `events`, and `version_history` are immutable evidence/audit tables and cannot be updated through this API; record a new provenance row instead.

Both write paths run `PRAGMA integrity_check` after committed writes. `workflow_runs` records completed workflow-run keys and their result payloads so a workflow can return a replay result instead of duplicating identical canonical writes.

The schema has been syntax-checked against an in-memory SQLite database, and `efficiens.db` has passed `PRAGMA integrity_check`. Record counts are operational state: query the database directly rather than treating this document as a current count report.

### Commerce seed workflow (Phase 1)

Use `scripts/product_research_workflow.py` to prove an end-to-end write path:

```bash
# Always allowed: local analysis with no canonical write.
python scripts/product_research_workflow.py catalog.csv --dry-run --top-n 5

# Only after the router reports active/no-blockers and a running lane is leased.
python scripts/product_research_workflow.py catalog.csv \
  --database canonical/efficiens.db --top-n 5 \
  --lane-id WF-1000::product-research --lane-owner agent-main
```

This script reads a local CSV catalog and records `product_candidate` entities,
their workflow metrics, and workflow events in the canonical database. The CLI
performs a mandatory read-only route/lease/write-surface preflight before a
canonical write; the lower-level Python function remains available to the
isolated test harness.
