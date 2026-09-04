# Frozen task brief: canary-003-discount-code-guard

Fixture-only. This is the exact, bounded task brief handed to the candidate
Implementer Bot. It is embedded verbatim into the run prompt by the harness;
it is not itself graded.

## Objective

`src/pricing.py::price_after_discount` raises `AttributeError` when
`code` is `None`. Fix it so a missing code means no discount (full
price), while every existing discount behavior is preserved exactly.

## Required outcome

- `tests/test_pricing.py` must pass in full (it is frozen; do not
  edit it).
- The only file you may change is `src/pricing.py`.
- Do not change the function's name, its parameter signature, or add new
  parameters.
- Do not touch any file outside this task's repo root.

## Acceptance command

```
python -m unittest tests.test_pricing -v
```
