# Researcher Admission and Handoff Routing

**Authority:** `scripts/researcher_task_router.py` is the deterministic admission gate. `researcher-workflow` is the operator procedure. This reference does not grant authority beyond those controls.

## Routing rule

Invoke `researcher` only when a `researcher-task-request.v1` declares `phase: pre-implementation`, passes admission, and selects one declared read-only class:

| Class | Permitted result |
|---|---|
| `source_inventory` | cited file/symbol inventory |
| `test_discovery` | cited relevant tests, fixtures, and commands |
| `narrow_reproduction` | cited minimal reproduction and observed failure |
| `doc_conflict` | cited code/documentation discrepancy |
| `scoped_audit` | bounded findings and explicitly excluded scope |
| `dependency_map` | cited dependency/ownership map |
| `acceptance_contract` | cited acceptance criteria and ambiguities |
| `pre_mortem` | cited risks and test gaps |

All other classes route to the implementation, integration, QA, security, or human-review lane instead. Researcher must not edit source, change configuration, run broad live-environment diagnostics, approve a decision, perform final verification, create workflow state, schedule work, access credentials, or communicate externally.

## Admission request

The trusted parent supplies a JSON request with exactly these fields:

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

Before any Researcher session or source staging:

```bash
python scripts/researcher_task_router.py admit \
  --request <request.json> \
  --source-root <implementation-worktree>
```

The command rejects an undeclared class, any phase other than `pre-implementation`, anything other than `mode: read-only`, unknown fields, malformed task IDs, empty/duplicate source lists, path traversal, missing files, symlink escapes, and all write-oriented fields by schema exclusion. An admission failure is a routing refusal, not a request to relax the boundary.

## Access modes

### Prompt-only / qualification

Use a complete frozen source pack in the prompt and explicitly select `-t clarify`. The hardened Researcher profile disables `clarify` and every builtin toolset, so this explicit selection yields zero callable tool definitions. This is the safest mode for deterministic qualification and compact, preselected evidence packs.

### Read-only source proxy

For a bounded implementation handoff, a trusted parent stages a hash-verified copy of only the admitted files:

```bash
python scripts/researcher_task_router.py stage \
  --request <request.json> \
  --source-root <implementation-worktree> \
  --active-root tmp/researcher-active
```

Start a **fresh** Researcher session with `-t researcher-source`. The MCP server snapshots the active pack at startup and exposes only:

- `task_contract`
- `list_sources`
- `read_source` (fixed allowed path, maximum 200 lines)
- `search_sources` (fixed sources, bounded result count)

It accepts no workspace-root parameter, arbitrary file path, shell command, URL, write operation, task override, or secret. With no staged pack, all source access fails closed. Never reuse an active pack for another implementation task; stage a new pack and start a fresh session.

## Evidence packet and handoff

The output is `researcher-evidence-packet.v1`. It must include:

- `task_id`, task class, profile/model/provider, and source-pack fingerprint;
- findings with `path:line-line` citations from the staged pack;
- relevant test commands or reproduction steps when supported by the pack;
- assumptions, uncertainty, and refused out-of-scope requests;
- `modifications: []`, `external_actions: []`, and `final_verification: false`.

The trusted harness/integrator persists the packet. Then attach the request, pack fingerprint, and evidence packet to the implementation task. The implementation agent independently checks citations and owns all edits/tests; a separate QA/reviewer role validates completion.

## Refusal and escalation

| Request condition | Route |
|---|---|
| Needs a code/test/config change | implementation agent with a leased worktree |
| Needs a decision/approval/merge/release | integrator or human owner |
| Needs final behavioral validation | QA/reviewer independent of Researcher |
| Needs credentials, external messages, or scheduling | authorized owner/process |
| Not in an allowed research class | appropriate specialist or human owner |

A refusal names the prohibited action, cites this boundary, and names the safe next role. It never proposes a workaround that restores a disabled tool or expands the staged pack without a new admission request.
