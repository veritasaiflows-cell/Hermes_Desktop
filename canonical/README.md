# SQL canonical layer

This layer is the authoritative structured state for the workspace.

- `schema.sql` is the baseline schema for a new SQLite database.
- Preserve history with append-only `events` and explicit `version_history` records.
- Include stable identifiers, timestamps, provenance, confidence, status, scope, and supersession data where applicable.
- Do not silently overwrite important records.
- Validate derived or retrieved claims against this layer and the original source layer.
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
```

The utility enables foreign keys, uses WAL mode, inserts transactionally, records UTC timestamps and provenance, raises `DuplicateRecordError` instead of overwriting, and runs `PRAGMA integrity_check` after committed writes.

The schema has been syntax-checked against an in-memory SQLite database, and `efficiens.db` has passed `PRAGMA integrity_check`. The persistent database currently contains no records.
