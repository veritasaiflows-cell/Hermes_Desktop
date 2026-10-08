# Reduced Phase 0 — scope-limited checkpoint and publication

## Operator approval and precedence

On 2026-10-08 UTC, after reduced Phase 0 qualification and its administrative closeout, Randall directly instructed:

> proceed to commit and push

This is the separate checkpoint/publication decision previously required by WF-1300. It narrowly supersedes the no-staging/no-commit/no-push and pinned-HEAD restriction for publishing the accepted warning/tracking/binding scope and the four already-local ancestors to existing `origin/master`. It does not authorize a force push, hook replacement, rejected commit-proof code, Phase 0b promotion/repair, Phase 1/2 execution, profile/configuration changes, another helper or another QA round. All other stop lines, author independence, consumed reviews and repair history remain unchanged.

Starting HEAD: `383782e5c54adb84aecf5b77620d0ea48a3fbbd2`. Verified remote master: `66127dbbee3de6cb399d5d6c3f191f4c5999bb8b`. The outgoing ancestors are `8b49330`, `49ce640`, `c01eca0` and `383782e`; they concern capsule-churn removal, v2 capsule regeneration, genuine architect admission history and existing script-test anchors. A fast-forward push publishes these ancestors as well as the new checkpoint; no history rewrite is authorized.

Owner lane: `phase0-checkpoint-20261008`, agent-main, `openai-codex/gpt-6.1-sol`. This lane is publication bookkeeping, not a new implementation attempt. Generated queue/alias Markdown mirror synchronization and Git index/ref writes are separately authorized owner actions; those forbidden lane surfaces are not granted to helpers.

## Accepted scope and provenance

- Existing informational aged-uncommitted-work warning: `scripts/workspace_status.py`, `tests/test_workspace_status.py`, `references/workspace-status-maintenance.md` and the checkpoint-disposition addition in `references/operating-procedures.md`. Original Opus implementation, independent Astra baseline review, Astra corrective delta and scoped Opus corrective review remain distinct; no new Opus self-review is asserted.
- WF-1300 exact pickup/registration: its continuity note, stable roadmap and pointer, implementation-job contract, queue/alias inputs and generated mirrors/capsules. The receipt and accepted-design history are preserved; overall WF-1300 remains blocked on separately scoped later phases.
- Current operator-approved main/QA bindings, generated WF-1200 roster and corresponding helper-router, QA-driver and fleet-measurement test pins. The existing Sol Implementer binding and historical one-time exception are retained as recorded history, not promoted to general qualification or reused write authority. Main implementation remains with main. Independent QA remains Opus 5.5, with the existing independent Sonnet fallback for Opus-authored work.
- Historical split closeout and current reduced closeout narratives, plus owner-generated wiki manifests for the exact selected source and the existing owner-reattested timestamps on six wiki pages. Every page was compared with HEAD and proved identical after only newline/generated-time normalization; no semantic page content was changed. Ignored proof artifacts supplement the tracked narratives; they are not silently added to Git.

Real Phase 0 owner receipt: `91163fef-29da-4562-a042-38aab60715c7`, completed `2026-10-08T04:22:37.582759Z`. Independent scoped Opus verdict: `PASS_WITH_NOTES`, no blocking findings, actual reviewer `anthropic/claude-opus-5-5`, author `openai-codex/gpt-6.1-sol`, session `20261007_211035_d38e6a`. This review covers the reduced scope/binding/control delta, not all original warning source anew.

Administrative closeout is complete: successor lane `phase0-admin-closeout-20261008` ended at `2026-10-08T05:16:52Z`, before its `2026-10-08T05:20:15Z` lease expiry. The original `phase0-reduced-closeout-20261008` stays blocked with `closeout_cap_expired`; its valid receipt and original deadline are preserved. Final successor readback: `derived/efficiency-phase0/2026-10-08/reduced-closeout/admin-continuation/final-readback.json`.

## Explicit exclusions and verification level

Do not stage the rejected `scripts/commit_proof.py`, `scripts/git_pre_commit.py`, `scripts/run_checks.py` changes, their tests, or the overstrong commit-proof descriptions in `references/automation-layer.md` and `references/script-index.md`. Leave the materializer source core and all Phase 0b source, candidate failures and review artifacts preserved locally and undeployed. Preserve unrelated audit/request records and the advisory scope-reassessment plan outside this implementation checkpoint.

The unchanged installed hook uses the shared working tree and ordinary `run_checks.py --skip-smoke`. Its pass is necessary but is not proof that the partial checkpoint excludes dependencies on dirty rejected code. Therefore separately export exactly the staged index, regenerate its manifests with the owning wiki command, run its full pytest suite, inspect every failure and retain all raw results. The old cron-registration tests compare installed wrappers with absolute live-checkout paths; only those verified export-location mismatches may be classified as manual-export limitations, never silently deselected. Any other failure stops this checkpoint. No automatic tested-equals-committed/publication-system guarantee is claimed.

The first candidate-manifest publish refused the old HEAD pages solely for weekly time staleness. Its failed output is preserved in `checkpoint/attempt1/`. The existing, already owner-reattested pages were then included after the exact time-only comparison; manifests are regenerated by the same owner command. No source/hash fields are hand-edited and no freshness gate is disabled.

Profile-local default model configuration, SOUL, QA configuration, skills, the live lane database, `derived/` evidence and local canonical databases are not part of the source commit or push. No new runtime changes are made here. The exported source is not a deployable Phase 0b controller.

## Checkpoint disposition

Publication is operator-authorized. Its actual outcome is owned by `derived/efficiency-phase0/2026-10-08/checkpoint/final-proof.json`, the exact Git commit/tree and remote-ref readback, not this pre-publication approval record. The proof must record selected and excluded paths, unchanged source/history/hook evidence, real gates and the exact remote SHA; a tool's successful push response alone is not completion.

This tracked record captures consequential approval and scope before publication. Final administrative annotations are Governor bookkeeping, not a new independent QA verdict. The existing acceptance receipt and frozen reviews are not overwritten or re-executed merely to checkpoint them.
