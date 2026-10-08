# Phase 0 closeout — split disposition

Historical status of the additional repair round: **closed BLOCKED; checkpoint_pending; no commit or hook deployment**. The warning corrective delta passed independent Opus QA and focused tests, but the exact-candidate full verification exceeded its existing 360-second test deadline. The failed gate and rejected commit-gate source remain preserved. The later reduced qualification and administrative closeout supersede this as current status: see `references/efficiency-phase0-reduced-closeout.md` and the separately operator-approved checkpoint/push exception in `references/efficiency-phase0-checkpoint.md`. This record does not accept Phase 0b or relabel its historical failure.

## Authority and authorship

Randall directed on 2026-10-07: "Proceed to close out phase 0. Use Opus 5.5 for QA and GPT6.1 Sol as a helper lane. Approve bot model bindings as needed." After the two-round stop was explicitly preserved, he approved "another repair round" (Telegram message 669). This record authorizes ONE additional corrective round, executed by the Governor, not additional helper writes or a reset of the failed-round history.

Current Governor/corrective author: `openai-codex/gpt-6-astra`. Original implementation author: `anthropic/claude-opus-5-5`. Original reference-document contributor for the commit gate: `openai-codex/gpt-6.1-sol`. Both previous independent QA round-2 records verified Astra as the effective reviewer and rejected acceptance. Those historical reports remain immutable.

Opus QA is scoped to Astra's new warning corrective delta and this split disposition. It is NOT independent re-review of Opus's original implementation. The prior independent Astra baseline review remains the evidence for unchanged original warning code; the Governor reconciles the combined evidence rather than claiming a new full-source Opus pass. Any blocking corrective review ends this additional round.

Sol ran a gated, tool-free, read-only Senior Engineer advisory on the already-failed changes: session `20261007_062907_a080bc`, effective route `openai-codex/gpt-6.1-sol` verified from the runtime session record. It declined implementation ownership, recommended deferring the index-consumption design, and identified Windows Job Object containment as a separately specified requirement. Advice is not acceptance and no helper wrote code.

## Selected split

1. **Warning-only guard:** correct the filesystem preflight exception boundary, disclose `examined_count` and `truncated` in compact status, and align threshold/sampling documentation. Regression coverage exercises resolution PermissionError, symlink-loop RuntimeError, marker-probing OSError, both existing healthy and hard-failure main exits, and a real helper/parser with a capped synthetic Git response. Governor observed genuine RED for the missing fail-soft boundary and compact keys, then 42 focused tests plus 9 subtests green.
2. **Commit gate:** retain `qa_round2_failed` on the original lane. No new commit-gate source fixes or installation in this round. Defer architecture and implementation to Phase 0b instead of introducing an unproven transaction/containment protocol. Do not commit the rejected source with the warning checkpoint.
3. Preserve the original live `.git/hooks/pre-commit`. Baseline SHA-256: `0b05f27cbcc3b5ee766c5f1b39a3862c6e1bc8cc3937a756822463727ab8a8c4`. A normal local checkpoint, if QA and both isolated candidate tests and the ordinary live hook pass, contains only accepted warning paths and this disposition. No bypass, push, or live hook replacement.

## Phase 0b acceptance prerequisites (not authorized implementation)

- Bind lane-path checking, candidate proof and Git's consumed commit tree to one immutable candidate, or demonstrate serialization through consumption for an explicitly supported command matrix. An endpoint recheck is not a transaction guarantee.
- Reproduce using disposable real commits: ordinary/initial, amend, alternate-index and any claimed partial/`-a` modes; synchronized mutations during receipt validation and after fresh proof, plus resulting committed tree and untouched unrelated staging. Upstream Git v2.53.0 `builtin/commit.c` corroborates that as-is preparation commits/releases its index lock; it does not itself prove behavior of the installed Git-for-Windows binary.
- Reject Windows traversal, drive/UNC/device/ADS forms, aliases and collisions before archive writes; verify containment and outside sentinel preservation.
- Strip `PYTHONOPTIMIZE`, define supported interpreter flags, prove failing application assertions remain active in actual children, and invalidate old receipts by protocol version.
- Retain and verify executable modes on supported platforms; state and enforce Windows limits rather than promising untested POSIX semantics.
- Normalize or reject noncanonical absolute lane paths before owner exemptions; include `.` and repeated separators, drive-relative and prefix-sibling cases.
- A single supervisor deadline must cover acquisition, materialization, collection, copying, tests, verification and receipt handling as specified. Contain descendants before they execute, clean up exceptional later launches, and verify no descendants remain. Specify bounded cleanup grace separately; do not claim taskkill/psutil polling is race-free containment.
- Reconcile four implemented partitions against the historical two-batch plan; add machine-readable outcome accounting and external-state isolation evidence. Correct overstrong docs at their source before deployment.
- A fresh independent architecture decision, scoped lane plan, tests and QA are required. This closeout is not that approval.

## Model bindings and provenance

The operator's requested Opus route is temporarily added to the QA registry for this job, invoked with an explicit provider/model pin, checked against the actual session identity, then removed. Existing Astra QA primary, Sonnet fallback, Sol Implementer and Sol Senior Engineer bindings remain unchanged. No other profile files, credentials, permissions, qualification or lane exceptions are modified.

The original `phase0-commit-gate-20261006` and `phase0-uncommitted-guard-20261006` failed attempts remain recorded. New corrective lanes carry retry count 2 to preserve the stop history; closing the warning corrective lane does not relabel the rejected historical attempt. Three expired agent-main side lanes were blocked with `operator_closeout_handoff`, with changes preserved and no claim of acceptance.

Evidence root: `derived/efficiency-phase0/2026-10-07/`; original reviews: `derived/efficiency-phase0/2026-10-06/qa-*-r2/`. `closeout/baseline.json` records HEAD `383782e5c54adb84aecf5b77620d0ea48a3fbbd2`, all 22 pre-existing changed paths and hashes; `closeout/baseline/` preserves their bytes. These ignored artifacts supplement this tracked narrative, not replace it.

## Final additional-round result and Governor reconciliation

**QA:** `PASS_WITH_NOTES`, no blocking findings, on the Astra corrective delta only. Session `20261007_065703_443d8b`; requested and runtime-verified effective route `anthropic/claude-opus-5-5`; driver exit 0, empty problems. The lane author was independently read by the driver as `openai-codex/gpt-6-astra`. The request's `pre-implementation` phase is the admission schema label for read-only spawns, not a claim that code was not yet written. Opus marked original warning findings 1/2/3 resolved. Low notes about wording/import style and additional optional coverage remain non-blocking; the four warning implementation/doc files were not changed after the reviewed freeze; this factual closeout was updated by the Governor after verification.

**Binding restoration:** the temporary Opus route was removed and the roster regenerated. Registry before/after SHA-256 is `3fabfc7f10c9027162cdcbdd002c1a45620bbf8d18f493be90e3cc8a0c05a98a`; the dispatch-time temporary registry hash was `0252152a9a2d79c63b199497c2768705539e1c200b6535ec1093e9372e58d4c1`. Roster before/after hash is `76e647ff27595cc117f5a3348367bb0b6cbf54735b965bc64726ccb1932ca918`. `closeout/binding-restoration.json` records these comparisons. No profile-local configuration changes were made.

**Candidate isolation:** Git stash `7aee444bd99e7f1967606707b5921bed1b9370ca` preserved the 16 excluded paths, with raw byte backups and per-path hashes in `closeout/shelved.json` and `closeout/shelved-raw/`. Only the four warning implementation/doc paths, this narrative, and two owner-regenerated wiki manifests remained. The exact staged candidate tree was `3f343add8d36b2b637f97fbd28e17029ec7be1b2`; all staged blob IDs were compared with Git-filtered working files before execution. No rejected commit-gate implementation or unrelated binding/test changes were included.

**Generated-artifact review (D4):** Opus did not review the wiki manifests. The Governor instead performed a deterministic review: owner-command publish against the isolated candidate; all current source hashes matched candidate bytes; all non-hash semantic manifest fields matched HEAD; the LKG was the prior HEAD manifest modulo Git's LF/CRLF checkout conversion. The organization validator returned `ok: true`, no issues. Evidence: `closeout/candidate-manifest-review.json`. After restoration, manifests were refreshed again against the restored live tree; those live generated files are not the tested candidate's manifests and remain uncommitted.

**Required verification FAILED:** the verified runtime interpreter ran `scripts/run_checks.py` against that candidate at the real checkout path. It exited 1 after 366.8 seconds wall time because pytest exceeded the unchanged **360-second** deadline. The captured partial progress reports no test failures, but it has no final outcome summary and is not a pass. Smoke execution and an ordinary commit-hook pass were therefore not established. The ordinary hook was not invoked by a commit; no commit was attempted. A separate `pytest -q --durations=15 tests` timing diagnostic was interrupted by the tool's 420-second outer timeout and produced no completed result; it supplies no additional pass or failure count. No retry, deadline increase, skipped test, framework substitution, or hook bypass was used to obtain acceptance.

**Restoration:** the candidate was unstaged without discarding working changes; the named stash was applied and retained. All 16 shelved files were restored byte-for-byte against their pre-shelve hashes, with only two Git newline projections reapplied from preserved originals. All unchanged noncorrective originals and the live hook also matched the initial baseline. The initial 22 paths are all accounted for, not all expected to have identical hashes: the four authorized warning files contain the reviewed corrections; two manifests were owner-regenerated; the helper audit contains genuine new admission records; the remaining originals are unchanged. This reconciles QA's overbroad request that "all 22 hashes" stay equal—such equality would incorrectly discard the authorized corrections and real audit events. `closeout/restoration-proof.json` contains exact per-path proof. HEAD remains `383782e5c54adb84aecf5b77620d0ea48a3fbbd2`; no staged paths remain.

**D1/D3 evidence:** the Governor read both original round-2 QA records, verified the baseline's 22-path count and hook hash, verified Sol's exported runtime identity, and recorded the operator's actual message 669. These are parent verifications, not claims of Opus having seen those artifacts. The shelving procedure and hash read-back above were actually executed. The scoped source QA verdict is preserved, but its required full verification gate failed; therefore the warning corrective lane is blocked with `full_suite_deadline_exceeded`, not complete. The original failed lanes remain `qa_round2_failed`. The commit-gate corrective scope remains `phase0b_design_required` with no attempted source repair.

## Checkpoint disposition and next decision

`checkpoint_pending` — pending source paths for the warning scope:
- `scripts/workspace_status.py`
- `tests/test_workspace_status.py`
- `references/operating-procedures.md`
- `references/workspace-status-maintenance.md`
- `references/efficiency-phase0-closeout.md`
- `wiki/bootstrap-manifest.json`
- `wiki/.lkg/bootstrap-manifest.json`

Proposed future subject: `workspace-status: make uncommitted-work warning fail soft and disclose sampling`.

Blocker: a complete green exact-candidate repository gate and ordinary unmodified hook are missing. The approved extra repair round ends here, not in another code attempt. Recommend a separately bounded full-suite runtime/runner investigation before any further implementation, followed by a separately accepted Phase 0b architecture. Do not accept the failed gate because no assertion failure appeared in partial output, and do not silently increase the timeout. No source was committed, pushed, installed or discarded; the new closeout narrative itself is also pending checkpoint.

Final full workspace-status check: exit 0, no hard failures, with stale vector/exact-index/Graphify/correctness warnings and a feedback-evaluation warning. Lane validation reported zero active/running lanes, no collisions and no expired leases. Final Git inventory is 23 changed paths, all unstaged (the original 22 plus this narrative). Caution: temporary Git shelving/checkout refreshed file mtimes; the final `aged_count: 0` does not mean the pre-existing uncommitted work was resolved. Byte content was verified and preserved; original filesystem mtimes were not preserved and cannot be truthfully reconstructed from rounded age summaries.
