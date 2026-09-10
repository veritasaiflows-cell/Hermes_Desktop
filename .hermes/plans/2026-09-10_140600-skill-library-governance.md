# Skill Library Governance & Tailoring Plan

**Goal:** Align the Hermes skill library (111 installed: 43 agent / 57 bundled / 9 hub) with Efficiens' actual work classes, enforcing the GOVERNANCE.md single-owner and design standards for governance, efficiency, and routing speed.

**Architecture:** Skills are the procedure layer; profile-local skills have no Git history, so Efficiens-coupled skills migrate to trusted repo-local `.hermes/skills/` (precedent: `researcher-workflow`, `mcp-integration-governance`, `mcp-pilot-evaluation`). Profile-local edits use Curator backup + mutation ledger as the audit trail.

**Evidence base (2026-09-10):** `hermes curator usage/status`, hub `skills check` (all up to date), diff of duplicate pair, grep surveys for coverage gaps, lane register (no active lanes), `workspace_status.py --fast` (degraded: wiki stale_freshness only, `changed_sources: []` — pre-existing, no trust impact on this work), `run_checks.py --skip-smoke` exit 0.

---

## Key findings

1. **Duplicate skill**: `workflow-opportunity-audit` (activity 75) vs `workflow-loop-opportunity-audit` (activity 44) — same check stack; the duplicate uniquely owns the business-vs-operational efficiency disambiguation, feedback `recommendations`/`signal_trends` fields, canonical candidate recount, and the decision-integrity reference.
2. **Overlap clusters**: readiness/audit ×4 (`workspace-harness-readiness`, `workspace-skill-audit-readiness`, `workflow-status-audit`, `hermes-runtime-bootstrap`); refresh ×2 (`workspace-refresh-governance` vs `-operations`).
3. **Metadata debt**: `script-documentation-and-graphify-audit` has no `version:`; 7 workspace skills lack change history; several descriptions lack "Use when…" triggers. `workflow-opportunity-audit` frontmatter said 1.0.0 while its change history was at v1.1.0.
4. **Governance gaps**: 15 foreground-created skills curator-unmanaged; curator consolidation OFF; last curator run 10d ago.
5. **Coverage gaps**: multi-agent fleet dispatch (admission gate → lease → spawn → canary qualification → review → verdict) has scripts + references but no skill; WF-1004 `gate_counterfactual.py` has no skill.
6. **Index bloat**: 62/111 skills never used, nearly all bundled/hub — per-session routing-index cost.
7. **Stale**: `workflow-a-phase3-readiness` (11d; WF-1000 re-scoped to proof lab).

---

## Tasks

### Task 1: Merge duplicate audit pair — EXECUTED this session
- Survivor: `software-development/workflow-opportunity-audit` (profile-local).
- Folded in from duplicate: efficiency-disambiguation gate, feedback recommendations/signal-trends, canonical candidate recount, decision-integrity contract; references `feedback-candidate-decision-integrity.md`, `model-routing-efficiency-review.md`.
- Version 1.0.0 → 1.2.0 (fixes frontmatter/history drift); description now a proper trigger.
- Duplicate archived via `hermes curator archive`.
- Proof: curator backup `2026-09-10T21-08-51Z`, ledger IDs, post-merge `skill_view` read-back.

### Task 2: Create `multi-agent-fleet-operations` project skill — EXECUTED this session
- Create: `.hermes/skills/multi-agent-fleet-operations/SKILL.md` (repo-local, Git-tracked).
- Routes to owners, does not duplicate: admission mechanics → `references/operating-procedures.md`; role matrix/qualification order → `references/multi-agent-fleet-governance.md`; harnesses → `references/researcher-canary-runbook.md` / `implementer-canary-runbook.md`.
- Owns: end-to-end dispatch sequence (request JSON → `helper_agent_router.py admit` → lane lease → spawn → untrusted-output handling → verdict recording → lane closeout with proofs).
- Verify: appears in `hermes skills list` as project skill; `run_checks.py --skip-smoke` exit 0.

### Task 3: Migrate Efficiens-coupled profile skills to `.hermes/skills/` (next session)
- Candidates (highest coupling first): `workflow-control-plane`, `workflow-status-audit`, `workspace-harness-readiness`, `workspace-skill-audit-readiness`, `concurrent-lane-control-plane`, `memory-retrieval-readiness`, `prompt-workspace-governance`, `workflow-activation`, `workflow-routing-refresh`, `hermes-runtime-bootstrap`, `workspace-refresh-*`, `workspace-telemetry-loop`, `workspace-checkpoint-commits`, `workflow-a-phase3-readiness`, `workflow-opportunity-audit`, `vector-memory-benchmarking`, `script-documentation-and-graphify-audit`, `agentic-workspace-architecture`.
- Procedure per skill: copy to `.hermes/skills/<name>/` → verify load → `hermes curator archive <name>` (profile copy) → commit. One batch per session, 3–5 skills, to keep diffs reviewable.
- Risk: routing flapping during migration — mitigate by keeping descriptions identical.
- **Decision needed:** whether profile copies are archived (single source) or kept as installed mirrors. Plan assumes archive.

### Task 4: Prune never-used bundled/hub skills from the routing index
- `hermes skills opt-out <name>` for clearly irrelevant bundled: `apple-notes`, `apple-reminders`, `findmy`, `imessage`, `openhue`, `touchdesigner-mcp`, `baoyu-infographic`, `songwriting-and-ai-music`, `manim-video`, `procedural-3d-animation`, `p5js`, `popular-web-designs`, `ascii-art`, `ascii-video`, `songsee`, `youtube-content`, `gif-search`, plus zero-use productivity set (`notion`, `obsidian`, `xlsx`, `docx`, `pdf`, `powerpoint`, `airtable`, `box`, `google-workspace`, `maps`, `meeting-action-items`, `weekly-review-planning`, `teams-meeting-pipeline`, `product-price-monitor`, `session-librarian` review first — it has activity 2).
- `hermes skills uninstall` hub zero-use: `sketch`, `pretext`, `openhue`, `touchdesigner-mcp`, `weights-and-biases`, `serving-llms-vllm` (keep `llama-cpp`, `evaluating-llms-harness` — plausible ML use).
- Verify: `hermes curator usage` count of never-used drops from 62; routing index visibly smaller.
- Risk: opting out a skill later needed — reversible via `opt-in` / reinstall.

### Task 5: Metadata standard pass
- Add `version:` to `script-documentation-and-graphify-audit`; add change-history sections where missing (`workflow-activation`, `hermes-runtime-bootstrap`, `workflow-a-phase3-readiness`, `workspace-telemetry-loop`, `agentic-workspace-architecture`, `vector-memory-benchmarking`).
- Rewrite weak descriptions into "Use when…" triggers (`workspace-refresh-governance`, `workspace-checkpoint-commits`, `vector-memory-benchmarking`).
- Fold into Task 3 migration edits where applicable (single touch per skill).

### Task 6: Curator lifecycle wiring
- `hermes curator adopt --all-unmanaged` (15 skills).
- Enable consolidation pass (currently prune-only): opt in per `hermes curator` config so overlaps surface automatically.
- Verify: `hermes curator status` shows 0 unmanaged; next run summary includes consolidation review.

### Task 7: `agent-trust-audit` runbook skill (small)
- `.hermes/skills/agent-trust-audit/SKILL.md`: WF-1004 counterfactual-gate runbook (`scripts/gate_counterfactual.py --as-json`), honest-baseline reporting rule (avoided cost ≠ earned revenue), no new tooling per WF-1004 blocker.
- Trigger: "Use when running or reporting the WF-1004 agent trust audit."

### Task 8: Retire `workflow-a-phase3-readiness`
- WF-1000 is re-scoped as a proof lab; the phase-rollout trigger is stale (11d unused).
- Action: `hermes curator archive workflow-a-phase3-readiness` after confirming no script/test references (grep first).

---

## Explicitly deferred
- WF-1001/1002/1003 draft-generation skill — workflows are `route_only` pending storefront/ad-channel connectors; create at first connector approval.
- Readiness/audit 4-skill cluster consolidation — needs body-level diff; first pass is trigger/boundary clarification, not merge.
- Hub installs — nothing relevant found; revisit quarterly via `hermes skills search`.

## Validation
- `python scripts/run_checks.py --skip-smoke` exit 0 after repo changes.
- `hermes skills list` shows new project skills; archived skills absent from routing index.
- `hermes curator ledger --skill workflow-opportunity-audit` records the merge.
- Commit: `GIT_LANE_OWNER=agent-main git commit` scoped to `.hermes/plans/`, `.hermes/skills/` only — pre-existing dirty `state/workflows/*.json` preserved untouched.

## Known debt carried (not this plan's scope)
- Wiki `stale_freshness` hard failure (generated 2026-09-05, weekly window) — owner: `scripts/cron_wiki_regen.py`.
- `correctness_unavailable` warning.
- Pre-existing modified `state/workflows/WF-1000..1004.json`.
