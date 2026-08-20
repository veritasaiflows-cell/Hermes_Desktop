# Memory graph layer

Store relationships and traversable connections here, including entities, task dependencies, ownership, decision impact, source-to-claim links, project structure, historical transitions, contradictions, and supersession.

The graph explains relationships. It is not authoritative when it conflicts with canonical SQL or primary source evidence.

## Implementation

The durable graph is stored in the canonical database (`relationships` table in
`canonical/schema.sql`) and operated through `scripts/graph_memory.py`:

```bash
python scripts/graph_memory.py add_edge --subject-type entities --subject-id <id> \
  --predicate owns --object-type tasks --object-id <id> --confidence 0.9
python scripts/graph_memory.py neighbors --subject-type entities --subject-id <id>
python scripts/graph_memory.py path --start-type entities --start-id <a> --end-type tasks --end-id <b>
python scripts/graph_memory.py affected --object-type entities --object-id <id>
python scripts/graph_memory.py validate
```

The authoritative routing and write contract is `references/graph-memory.md`.

## Record fields

- stable relationship ID
- subject and object IDs
- relationship type
- provenance reference
- confidence
- created and observed timestamps
- supersession or validity interval
