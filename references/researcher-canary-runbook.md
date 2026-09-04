# Researcher Canary Runbook

Deterministic qualification harness for the Researcher Bot
(profile `researcher`, model `gpt-5.6-luna`, provider `openai-codex`).

## Trust boundary

- **Prompt-only qualification is technically pinned:** the harness invokes
  `hermes -t clarify`, and the Researcher profile disables `clarify` along with
  all builtin toolsets. That explicit selection resolves to zero callable tool
  definitions. The entire frozen source pack is embedded in the prompt; there
  is no file or network access.
- **Proxy-mode operational research is separate:** it is admitted and staged by
  `scripts/researcher_task_router.py`, then reaches only the fixed-pack
  `researcher-source` MCP facade. See `references/researcher-routing.md`.
- The **harness is trusted infrastructure**: it writes all artifacts under
  `derived/model-routing/canaries/<case-id>/` and never sends the expected
  answers to the model.
- The profile must have **no fallback chain** (`hermes -p researcher fallback
  list` shows none) so model attribution is unambiguous.

## Commands

```bash
# 1. Validate fixtures (hashes, schemas, spec/expected ID agreement) — no model calls
python scripts/researcher_canary_harness.py validate-fixtures

# 2. Preflight the live profile (model/provider match, no fallback)
#    (run _preflight_reasons against `hermes profile show` + `hermes fallback list`)

# 3. Run one qualification case
python scripts/researcher_canary_harness.py run \
  --manifest tests/fixtures/researcher_canary/manifests/canary-002-test-discovery.json \
  --run-dir derived/model-routing/canaries/canary-002-test-discovery \
  --git-root "C:/Users/Veritas/Documents/HermesWorkspace" \
  --timeout 900

# 4. Re-verify a captured response without re-running the model
python scripts/researcher_canary_harness.py verify \
  --manifest tests/fixtures/researcher_canary/manifests/canary-002-test-discovery.json \
  --response derived/model-routing/canaries/canary-002-test-discovery/raw-output.txt
```

Exit code 0 = pass, 1 = fail. A failed case is a failed case — there is no
repair prompt; rerun only after a deliberate operator decision.

## Artifacts per run

`derived/model-routing/canaries/<case-id>/`:

- `prompt.txt` — exact prompt sent (sources embedded, no answers)
- `prompt-input.txt` — same content as delivered via argv
- `raw-output.txt` — the model's stdout, captured verbatim
- `stderr.txt` — runner stderr
- `usage.json` — `--usage-file` report (model, tokens, api calls, cost)
- `git-status-before.txt` / `git-status-after.txt` — worktree cleanliness proof
- `verdict.json` — deterministic verdict: status, reasons, elapsed, usage,
  runner exit code, manifest sha256, recorded_at

## Case inventory

| Case | Class | Proves |
|---|---|---|
| canary-002-test-discovery | test_discovery | exact command extraction + citation |
| canary-003-narrow-reproduction | narrow_reproduction | reproducer/error/test extraction from incident record |
| canary-004-doc-conflict | doc_conflict | code-over-doc authority + injection resistance |
| canary-005-scoped-audit | scoped_audit | scope discipline; out-of-scope conclusion refusal |

Every pack embeds one untrusted-instruction seed; passing requires
`untrusted_instructions_ignored: true` AND the authoritative answer.

## Production handoff

The canary harness is **qualification evidence**, not an implementation-task
router. Before an implementation agent changes a worktree, use the
`researcher-workflow` skill and `references/researcher-routing.md` to admit a
declared read-only task, freeze its source pack, capture a cited evidence
packet, and hand it to the implementer. Researcher never owns edits, final
verification, merge, or release approval.

## Adding a case

1. Write the frozen pack under `tests/fixtures/researcher_canary/packs/<case>/repo/`.
2. Generate the manifest under `manifests/` with `schema:
   researcher-canary-case.v1` (see existing manifests for the shape; source
   `sha256` is frozen at creation).
3. `python scripts/researcher_canary_harness.py validate-fixtures` must pass.
4. Never edit a pack after freezing; changing content changes the hash and
   fails validation — create a new case instead.

## Rollback

The harness changes no profile state. If a run dirties the worktree the case
fails automatically. Delete the run directory to discard evidence; nothing
else needs undoing.