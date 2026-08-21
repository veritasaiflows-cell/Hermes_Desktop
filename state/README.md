# State layer

`state/` is the workspace control plane: current workflow truth, routing inputs,
operator overrides, lane coordination, and generated workflow summaries.

## Authority and contents

- `active_workflows.json` — authoritative active-workflow queue.
- `workflow_alias_index.json` — authoritative workflow alias mapping.
- `workflow-control-overrides.json` — operator pause, halt, and gate controls.
- `ACTIVE_WORKFLOWS.md` and `WORKFLOW_ALIAS_INDEX.md` — human-readable mirrors.
- `concurrent-lane-register.sqlite` — durable lane coordination state.
- `workflow-routing-index.json` and `workflows/` — generated routing surfaces.

Do not store the canonical database, retrieval indexes, source evidence, or scratch
files here. The canonical database is `canonical/efficiens.db`; retrieval indexes
belong under `vector/indexes/`.

## Retention

Do not clear this directory as a unit. Preserve authoritative inputs and lane
state. Generated routing artifacts may be refreshed through
`scripts/workflow_router.py`, but should be changed only by their owning command.
