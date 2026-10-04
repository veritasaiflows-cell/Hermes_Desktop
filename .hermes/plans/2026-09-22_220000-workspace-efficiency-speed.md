# Workspace Efficiency & Speed — Findings and Implementation Plan

> **For Hermes:** Execute phase by phase. Each phase has its own gate; do not start the next until the gate is met. Phases 0 and 3 need operator approval.

**Goal:** Restore a green correctness gate, cut local gate/test wall time by at least 50%, and reduce time lost to agent turns, without weakening any safety gate.

**Evidence window:** turn telemetry 2026-09-08 → 2026-09-22 (70 turns, 14 days), live runs on 2026-09-22.

---

## 1. Measured findings (ranked by impact)

| # | Finding | Evidence | Impact |
|---|---|---|---|
| F1 | **The correctness gate fails on time, not on bugs.** Full suite: **708 passed, 0 failed**. It took 412s (cold) and 340s (warm), but `run_checks.py` has `PYTEST_TIMEOUT_SECONDS = 240`. | A2-full `last_status: error` ("pytest timeout after 240s", 78% done). `workspace_status` shows `correctness_unavailable`. `tmp/` has 5 `pytest-timeout-*.log` files. | Correctness proof is **permanently stale**. The feedback loop can't evaluate the 6 pending candidates because they need a green baseline. |
| F2 | **One test file uses 56% of suite time.** `test_implementer_canary_harness.py` = 187s of 334s. The next file is `test_workspace_status.py` at 35s (10.5%). | `--durations=0` aggregated per file. Individual tests take 5–26s each. Each one builds a git sandbox and runs many `git` subprocesses. | This file is the main reason F1 happens. |
| F3 | **Starting a process is very slow on this host.** Base Python `-I -S -c pass` takes 0.455s, `cmd /c exit` 0.478s and `git --version` 0.465s. The hermes venv Python takes **1.15s** (about 2.5× base). A normal Windows host takes about 0.03–0.05s. | Min-of-5 benchmarks. McAfee (`mc-fw-host`, 1.18 GB) is the active real-time scanner; Defender is off. | Every gate, test subprocess and git call pays this. It's the root cause of F4 and a large part of F2. |
| F4 | **`workspace_status.py` got about 15× slower:** 14 gates, 29.3s total (1.1–3.5s each), 35.8s wall. The 2026-08 baseline was about 2.0s. | Each gate's `elapsed_ms`. Gates run serially on purpose (`scripts/workspace_status.py:160`: "avoid shared SQLite and artifact races"). | Each gate is about 1.2s of process startup plus a little real work. A11 runs this every 4 hours. |
| F5 | **Memory pressure.** 16 GB RAM with about 1.0 GB free. Edge (2.26 GB), McAfee (1.18 GB), node ×9 (1.1 GB), WSL (0.7 GB), 10 Python processes. | `Get-Process` grouped by name. | Paging slows every process start and makes F3 worse. |
| F6 | **Agent turns are serial and spend a lot of time waiting.** API work/wall = **1.00** (fully serial). Tool work/wall = 1.07 (almost no parallelism). Calls per round = 1.38. | `turn_metrics` over 14 days: 5.65 h of turn time. `process_manage` waits = **1021s**, the largest tool-time item. `terminal` = 917s (max 304s). | Mostly waiting on long foreground/background commands, which is the slow suite (F1/F2) again. |
| F7 | **Errors now cost latency, not correctness.** API error rate is **1.97%**, down from 9.6% in 2026-08. However, the 5 turns with errors averaged **1430s vs 203s** for clean turns (7×). All errors fall in the **100–150k-token** context bucket (6.6% error rate there, 0% in every other bucket). 68 of 70 turns ended `complete`. | Token-bucket and provider group-by. Errors are spread across anthropic (7/88), nvidia (2/23), kimi (1/64) and mixed. ollama-cloud had 0/365 but mostly ran smaller or cached contexts, so this is not a clean comparison. | Low volume, so the confidence is low. It matches the earlier context-size pattern. |
| F8 | **Top tool-error signal is `terminal_error`** (13 occurrences, priority 104, rank 1), then `web_extract_error` (15 raw) and `skill_manage_error` (5). | `derived/feedback-evaluation/latest.json`, `tool_error_categories_json`. | One confirmed cause: the terminal shell has **`TMPDIR` unset**. `$TMPDIR/x` becomes `/x` and writes to the MSYS root (reproduced this session). |
| F9 | **Storage bloat inside the workspace.** `graphify-out/` = 151 MB (**17 generations**, ~9 MB each). `source/graphify-candidates/` = 153 MB (17 snapshots). `derived/` = 184 MB, of which 186 MB is video renders (`friesian-scene` 117 MB, etc.). Total: 6,114 files. | `du`, `find`. | Tree-walking gates (organization 3.5s, wiki, fingerprint) and scanner I/O slow down as file count grows. |
| F10 | **Feedback backlog:** 6 candidates in `baseline_recorded`, 10 rejected, 1 accepted. | canonical `tasks`. | Blocked by F1 (no green baseline within the time budget). |
| F11 | **Gateway not running** when checked (`gateway_running: false`). The jobs did run today, so it is intermittent. | `cronjob list` warning. | If it stays down, A1–A19 stop firing. Needs a check but isn't a speed issue. |

**What's healthy (no action):** routing index fresh, 0 hard failures, A15/A18 graphify deadlock resolved (both `ok`), tool error diagnostics dropped = 0, 18 of 19 cron jobs `ok`.

---

## 2. Recommendations (data → fix)

| Priority | Recommendation | Addresses | Expected gain |
|---|---|---|---|
| **P0** | Split the suite into a fast tier and a slow tier, and give the gate a budget that matches reality | F1, F2, F10 | Gate turns green today. Fast tier ≈ 150s. |
| **P0** | Add workspace and Hermes paths to McAfee on-access scan exclusions (operator action) | F3, F4, F2 | Largest single lever. Expect process spawn to drop toward ~0.1s, and gates and tests to speed up 2–5×. **Verify with benchmarks; don't assume.** |
| **P1** | Run the canary-harness tests in parallel (`pytest-xdist`, `-n auto`), keeping the other files serial if they share SQLite | F2 | 187s → ~40–60s on a 16-thread i7-14650HX |
| **P1** | Cut git subprocess count in the canary harness fixtures: build one template repo per class (`setUpClass`) and copy it, instead of running `git init/add/commit` per test | F2, F3 | Roughly halves per-test cost |
| **P1** | Run the side-effect-free `workspace_status` gates in-process, or in parallel with a read-only allow-list | F4 | 29s → under 8s |
| **P2** | Set retention for graphify generations/candidates (keep 3) and move video renders out of `derived/` | F9 | ~400 MB and ~thousands of files less to walk and scan |
| **P2** | Export `TMPDIR` in the terminal environment | F8 | Removes one known class of `terminal_error` |
| **P2** | Lower the compaction threshold below 100k tokens for long sessions | F7 | Targets the only bucket that has errors |
| **P3** | Free RAM: close idle Edge tabs, stop WSL/Docker when not needed, clean up orphaned node/python processes | F5 | ~2–3 GB back |
| **P3** | Review and decide on the 6 pending feedback candidates once the gate is green | F10 | Closes the loop |

**Deliberately NOT recommended:**
- Raising the timeout alone. It hides the growth trend: test count grew and the suite now takes 340–412s.
- Parallelizing all status gates blindly. Serial order exists to avoid SQLite and artifact races.
- Provider changes. Error volume is too low (10 errors in 14 days) to name a cause.

---

## 3. Implementation plan

### Phase 0 — Host baseline and scanner exclusions (operator, ~15 min)

**Objective:** Remove the per-process tax that affects everything else.

1. Record the baseline (save it to `derived/perf-baseline/2026-09-22.json`):
   ```python
   # min-of-5 for: base python -I -S, venv python, git --version, cmd /c exit
   # plus: workspace_status.py wall, pytest -q wall
   ```
   Current values: 0.455 / 1.154 / 0.465 / 0.478s; status 35.8s; pytest 340–412s.
2. **Operator:** in McAfee → Real-Time Scanning → Excluded Items, add:
   - `C:\Users\Veritas\Documents\HermesWorkspace\`
   - `C:\Users\Veritas\AppData\Local\hermes\`
   - `C:\Users\Veritas\AppData\Roaming\uv\python\`
   - process `python.exe` under the hermes venv and the uv base
3. Run the same benchmark again.

**Gate:** process spawn at or below 0.15s. If there's no improvement, McAfee isn't the cause. Record that, skip it, and check `mc-fw-host` firewall inspection or the venv `_virtualenv.pth` / `pywin32.pth` hooks (venv adds 0.7s over base).

### Phase 1 — Unblock the correctness gate (P0, ~30 min)

**Files:** `pytest.ini`, `scripts/run_checks.py:37`, `scripts/cron_test_gate.py:35`, `tests/test_implementer_canary_harness.py`, `tests/test_run_checks.py`

1. Register a `slow` marker in `pytest.ini`:
   ```ini
   markers =
       slow: long-running sandbox/git end-to-end tests (canary harness)
   ```
2. Mark the canary-harness test module with `pytestmark = pytest.mark.slow`. If the file is unittest-style, use a module-level `pytestmark`; pytest applies it to unittest classes too.
3. Change `run_checks.py` to run **two passes**, each with its own timeout: `-m "not slow"` (budget 240s) then `-m slow` (budget 300s). Report per-tier counts. The total test count must stay 708, so the test-count equality gate in the feedback loop still holds.
4. Raise `cron_test_gate.py` `TIMEOUT_SECONDS` from 300 to 660 to cover both tiers.
5. Tests first (TDD) in `tests/test_run_checks.py`:
   - two-tier invocation builds both commands
   - a timeout in the slow tier reports `tier=slow` partial evidence, not a generic failure
   - summed counts equal a single-pass count
6. Verify: `python scripts/cron_test_gate.py` exits 0. Then `python scripts/workspace_status.py` → `correctness_unavailable` is gone.

**Gate:** A2-full green on the next 06:00 run. `health.warnings` no longer includes `correctness_unavailable`.

### Phase 2 — Make the suite fast (P1, ~1–2 h)

1. `uv pip install pytest-xdist` into the venv that runs tests (and record it in the project manifest).
2. Run the slow tier with `-n auto --dist loadfile`. **First confirm sandbox isolation:** each test must use its own `tempfile.mkdtemp()` repo and no shared `tmp/` paths. Grep the file for fixed paths before enabling.
3. In `tests/test_implementer_canary_harness.py`, build the base sandbox repo once per class (`setUpClass`) and `shutil.copytree` it per test instead of re-running `git init/add/commit`. Keep every assertion unchanged.
4. Apply the same template approach to `tests/test_workspace_status.py` (35s): check whether its tests spawn the full `workspace_status.py`. If they do, call `main()` in-process with monkeypatched gates.
5. Verify: `python -m pytest -q --durations=15` gives 708 passed. Target: under 150s total wall.

**Gate:** 708 passed and total wall under 150s over 3 consecutive runs, with no flakes.

### Phase 3 — Faster status gates (P1, ~1 h)

**File:** `scripts/workspace_status.py:155-169`

1. Label each of the 14 gates `read_only` or `writes`, based on what each gate script opens for writing (grep for `connect(` without `mode=ro`, `write_text`, `replace(`).
2. Run the `read_only` set in a `ThreadPoolExecutor(max_workers=4)`. Keep `writes` gates serial and in their existing order. Keep the output dict order stable.
3. Tests: a gate marked read-only that writes is caught by a test fixture (compare mtimes on canonical/state before and after). Result ordering is deterministic.
4. Verify: `workspace_status.py` wall-time reported before and after; JSON payload identical except for `elapsed_ms`.

**Gate:** wall under 10s, identical `health` output over 5 runs. **Needs operator approval** because it changes the concurrency contract.

### Phase 4 — Hygiene (P2, ~45 min)

1. **Graphify retention:** check whether `cron_graphify_code_refresh.py` already has a prune option. If not, add `--keep 3` that never deletes the currently selected generation or the previous pointer. Use a dry-run first, then run it.
2. **Video renders:** move `derived/{friesian-scene,friesian-storm,spartan-rider*}` and the untracked `imagine-video/` to `C:\Users\Veritas\Media\renders\`, or add them to `.gitignore` and to the organization gate's skip list. Confirm nothing in `scripts/` references them first (`search_files`).
3. **`tmp/` cleanup:** stop `pytest-timeout-*.log` accumulation by adding a 14-day prune to the A4 weekly sweep.
4. **TMPDIR:** add `export TMPDIR=/c/Users/Veritas/AppData/Local/hermes/cache/scratch` to the terminal shell init (Hermes terminal env config; check the `hermes-agent` skill for the right key).
5. Re-measure: file count and organization gate `elapsed_ms`.

### Phase 5 — Agent-turn efficiency (P2, config)

1. `hermes config set compression.threshold_tokens 90000` (the error bucket starts at 100k). Check the key first with the `hermes-agent` skill.
2. Add these rules to the relevant skill (`workflow-opportunity-audit`):
   - Run the test suite in the background with notify, then keep working instead of blocking on `process_manage wait` (1021s of waits in 14 days).
   - Batch independent reads and searches (current ratio: 1.38 calls per round).
3. Measure after 14 days: tool work/wall should rise above 1.2, `process_manage` time should fall, and the 100–150k bucket should have fewer turns.

### Phase 6 — Close the loop (P3)

1. Once the gate is green, run `feedback_evaluation_loop.py rebaseline` (the cohort grew) and then `evaluate` on the 6 candidates. Decide each one using `references/feedback-candidate-decision-integrity.md`.
2. Check the gateway: `hermes gateway status`. If it's down, run `hermes gateway start` and confirm with `cronjob list`.
3. Two weeks after Phase 2, rerun this audit and compare against the Phase 0 baseline file.

---

## 4. Success metrics

| Metric | Now | Target |
|---|---|---|
| A2-full status | error (timeout) | ok, 7/7 days |
| Full suite wall | 340–412s | under 150s |
| `workspace_status.py` wall | 35.8s | under 10s |
| Python spawn (venv) | 1.15s | under 0.3s |
| Pending feedback candidates | 6 | 0 undecided |
| Workspace file count | 6,114 | under 3,500 |
| Tool parallelism (work/wall) | 1.07 | over 1.2 |

## 5. Risks

- **Scanner exclusions** reduce malware coverage for those paths. They're limited to dev and runtime directories the operator owns. This is the operator's call.
- **xdist** can expose hidden shared state between tests. That's why it's limited to `--dist loadfile` and gated on 3 clean runs.
- **Parallel gates** could race on SQLite. Only proven read-only gates run in parallel, enforced by a test.
- **Low telemetry volume** (70 turns): the F7 and F6 conclusions are directional. Re-verify after 14 days.
