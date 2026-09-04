# Frozen task brief: canary-001-bounded-null-check-fix

Fixture-only. This is the exact, bounded task brief handed to the candidate
Implementer Bot. It is embedded verbatim into the run prompt by the harness;
it is not itself graded.

## Objective

`src/inventory_guard.py::needs_reorder` raises `TypeError` when
`quantity_on_hand` is `None`. Fix it so an unknown on-hand quantity is
treated as needing reorder review (the function must return `True`), while
every existing numeric comparison behavior is preserved exactly.

## Required outcome

- `tests/test_inventory_guard.py` must pass in full (it is frozen; do not
  edit it).
- The only file you may change is `src/inventory_guard.py`.
- Do not change the function's name, its two-parameter signature, or add new
  parameters.
- Do not touch any file outside this task's repo root.

## Acceptance command

```
python -m unittest tests.test_inventory_guard -v
```
