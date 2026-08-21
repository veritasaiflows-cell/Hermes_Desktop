# Temporary scratch space

`tmp/` is for disposable, per-run files that are safe to delete after the process
using them has exited.

Appropriate contents include:

- query packets such as `vector-memory-query.json`;
- short-lived exports and intermediate transforms;
- one-time benchmark or diagnostic output pending review.

Do not place canonical state, source evidence, workflow control state, or live
retrieval indexes here. Durable rebuildable retrieval indexes belong under
`vector/indexes/`; useful generated artifacts belong under `derived/` or their
own documented artifact directory.

## Retention

Review scratch output after each run. Delete it when no longer useful, or promote
it to the correct governed layer with provenance. Except for this README, the
contents of `tmp/` are intentionally ignored by git.
