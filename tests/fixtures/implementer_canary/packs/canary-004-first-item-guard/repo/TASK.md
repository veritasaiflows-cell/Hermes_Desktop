# Frozen task brief: canary-004-first-item-guard

Fixture-only. This is the exact, bounded task brief handed to the candidate
Implementer Bot. It is embedded verbatim into the run prompt by the harness;
it is not itself graded.

## Objective

`src/sequtil.py::first_item` raises `TypeError` when `items` is
`None`. Fix it so a missing sequence returns `None`, while every
existing behavior is preserved exactly.

## Required outcome

- `tests/test_sequtil.py` must pass in full (it is frozen; do not
  edit it).
- The only file you may change is `src/sequtil.py`.
- Do not change the function's name, its parameter signature, or add new
  parameters.
- Do not touch any file outside this task's repo root.

## Acceptance command

```
python -m unittest tests.test_sequtil -v
```
