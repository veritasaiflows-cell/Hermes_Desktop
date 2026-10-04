# Tools and learning — add only what closes a known gap
Internal recommendation, not installation or spending approval. Owner: WF-1006.

## Recommendation
No new paid tool is needed for the current diagnostic/training stage. Use the installed coaching and four SMB skills, existing Excel/document tooling and deterministic tests. The next technical choice is **one automation runner**, chosen from the diagnosed stack—not several platforms at once.

| Priority | Tool/capability | Why it belongs | Gate before adding |
|---|---|---|---|
| Now | Existing Hermes coaching + Excel and report template | Practice discovery, compare task options, quantify baseline, explain human review | Synthetic only; preserve real rehearsal evidence |
| Now | Existing Git/pytest and synthetic case inventory | Reproducible validation and explicit expected/actual separation | Eight cases are teaching coverage, not a production quality benchmark |
| Next, Microsoft-centric candidate | Power Automate | Official docs support workflows across apps/services; useful candidate to assess when the actual environment is Microsoft-based | Verify exact product/plan, connectors, permissions, costs, approval design and sandbox; Excel alone is insufficient evidence |
| Next, cross-system candidate | n8n | Compare as a visual automation runner; Cloud or self-hosted deployment options | Choose hosting/security/operator ownership first. Self-hosting adds maintenance; it does not prove privacy or security |
| Later, repeated model/prompt comparisons | Promptfoo | Test cases, assertions, prompt/model comparisons and CI/CLI evaluation | Approved provider/data/budget; local eval runner still sends requests directly to configured LLMs |
| Before live use | Credential isolation, approved client sandbox and least-privilege accounts | Protect clients and make access/rollback reviewable | Separate policy and implementation approval; no secrets in this shared repository |
| Defer | CRM, vector database, paid observability and broad connector bundles | Not required to practice or deliver the diagnostic design | Add only after a measured need, support owner and explicit authorization |

## Learning order and concrete proof
1. Explain the quote-example task table without naming vendors: what AI interprets, what rules check and what people decide.
2. Walk through each evaluation case; explain why missing/ambiguous input must not become invented facts and why repeated events must not repeat actions.
3. Recompute the economics exercise, including the negative-review case. Explain capacity versus cash.
4. Read one runner's beginner documentation based on a chosen synthetic stack. Draw the proposed flow on paper; do not create accounts or connect a mailbox yet.
5. For a separately approved sandbox build, require input validation, schema-constrained output, evidence validation, exception handling, human review, duplicate-event protection and manual fallback. Exact baseline, cost cap, expiry, data boundaries, tests and independent QA must be in its own scope.

## Sources and limits
Official pages retrieved for this preparation on 2026-10-04 UTC (2026-10-03 Arizona); captures in derived/smb-ai-diagnostic-alignment/tool-source-captures.json:
- Microsoft Power Automate overview: https://learn.microsoft.com/en-us/power-automate/getting-started
- n8n deployment selection: https://docs.n8n.io/choose-n8n/
- Promptfoo evaluation introduction: https://www.promptfoo.dev/docs/intro/

These establish general product functions, not tenant entitlements, verified integration, current price quotations or compliance. The selection preferences above are recommendations. No vendor was installed, purchased or connected, and no paid evaluation ran.
