# Frozen task brief: canary-002-optional-name-format

Fixture-only. This is the exact, bounded task brief handed to the candidate
Implementer Bot. It is embedded verbatim into the run prompt by the harness;
it is not itself graded.

## Objective

`src/name_fmt.py::format_display_name` raises `AttributeError` when
either name is `None`. Fix it so a missing name is treated as an empty
string, while every existing formatting behavior is preserved exactly.

## Required outcome

- `tests/test_name_fmt.py` must pass in full (it is frozen; do not
  edit it).
- The only file you may change is `src/name_fmt.py`.
- Do not change the function's name, its parameter signature, or add new
  parameters.
- Do not touch any file outside this task's repo root.

## Acceptance command

```
python -m unittest tests.test_name_fmt -v
```
