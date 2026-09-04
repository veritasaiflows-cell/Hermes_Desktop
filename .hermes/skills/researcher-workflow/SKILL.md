---
name: researcher-workflow
description: Use when Researcher evidence precedes coding. Gate it.
---

# Researcher Workflow

Use the `researcher` profile only **before** an implementation agent edits a worktree. It is an evidence role, not an implementer, reviewer, approver, or integrator.

## Admission request

The trusted integrator supplies `researcher-task-request.v1`:

```json
{
  "schema": "researcher-task-request.v1",
  "task_id": "issue-123-test-map",
  "task_class": "test_discovery",
  "phase": "pre-implementation",
  "mode": "read-only",
  "objective": "Identify tests affected by the parser change.",
  "source_files": ["src/parser.py", "tests/test_parser.py"],
  "output_schema": "researcher-evidence-packet.v1"
}
```

Only these classes are admissible: `source_inventory`, `test_discovery`, `narrow_reproduction`, `doc_conflict`, `scoped_audit`, `dependency_map`, `acceptance_contract`, and `pre_mortem`.

Reject the task before invoking Researcher if the schema is invalid, `phase` is not exactly `pre-implementation`, `mode` is not exactly `read-only`, a class is undeclared, a source path escapes the supplied source root, or the request asks for writes, approvals, external actions, scheduling, credentials, or final verification.

```bash
python scripts/researcher_task_router.py admit \
  --request <request.json> \
  --source-root <implementation-worktree>
```

## Access modes

- **Prompt-only / qualification:** use the frozen prompt pack and `-t clarify`; the Researcher profile's disabled-toolset policy leaves this invocation with zero callable tool definitions.
- **Proxy mode:** only after admission, stage a source snapshot and start a fresh session with `-t researcher-source`:

```bash
python scripts/researcher_task_router.py stage \
  --request <request.json> \
  --source-root <implementation-worktree> \
  --active-root tmp/researcher-active
```

`researcher-source` exposes only `task_contract`, `list_sources`, `read_source`, and `search_sources`. It loads one hash-verified snapshot at process startup; it accepts neither a workspace root nor arbitrary file paths. No active pack means source reads fail closed.

## Required evidence packet

Require `researcher-evidence-packet.v1` with task/model/profile/source-pack attribution; findings cited as `path:line-line`; relevant tests or reproduction commands; assumptions and uncertainties; refused out-of-scope requests; and `modifications: []`, `external_actions: []`, `final_verification: false`.

## Handoff and verification

1. Attach the immutable request, source-pack fingerprint, and evidence packet to the implementation task.
2. The implementation agent owns the worktree, tests, and changes and independently inspects cited sources.
3. Separate QA/review verifies behavior and acceptance criteria. Researcher cannot approve completion, merge, or release.
4. Treat a source-pack mismatch, unsupported citation, or unverified claim as a failed handoff—not a repair-by-assumption.

## Refusal path

For a prohibited request, return a compact structured refusal: the disallowed action, the relevant boundary, and the safe next role (implementer, integrator, or reviewer). Never suggest an evasion of the configured tool boundary.
