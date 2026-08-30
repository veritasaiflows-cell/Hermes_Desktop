# I built an AI dropshipping system. It told me to buy nothing.

**A case study in agents that can say no.**

Status: internal draft, not published. Every number below is reproducible from
this repository. Sources are named inline.

---

## Summary

I built an autonomous product-research system to find dropshipping products
worth selling. It evaluated 30 real supplier leads across 5 platforms —
Doba, TopDawg, Wholesale2B, CJdropshipping, and Spocket — and recommended
**none of them**.

The production shortlist is empty. That is not a failure state. It is the
system working.

```
$ python scripts/top_opportunities_report.py canonical/efficiens.db --as-json
[]
```

Total spent evaluating 30 products: **$0.00**, against an approved sample
budget of $250.

Most AI agent demos show you what the agent *did*. This one is about what it
*refused to do*, and why that turned out to be the more valuable capability.

---

## Why an empty result is the interesting result

An agent that always produces output is easy to build. Ask a language model
for the "top 3 dropshipping opportunities" and it will always give you three.
It will give you three whether or not the underlying data supports three,
because producing an answer is what it was trained to do.

The failure mode is not that the agent is stupid. The failure mode is that
**a confident answer and a correct answer are indistinguishable in the output
format.** You get a ranked list either way.

So I built the system to be able to return nothing, and then measured what
that cost me and what it saved me.

---

## The counterfactual: what a naive agent would have shipped

Claiming "my system is careful" is an assertion. To convert it into a number,
I replayed the exact same evidence corpus through two decision policies:

- **Gated** — the real launch policy, every hard gate enforced.
- **Ungated** — a reconstruction of typical naive-agent behavior: rank by
  whatever margin signal is available, take the top 3.

Reproduce with:

```
$ python scripts/gate_counterfactual.py
```

| | Gated (actual) | Ungated (naive) |
|---|---|---|
| Leads evaluated | 30 | 30 |
| Rankable on available signal | — | 10 |
| **Selected for launch** | **0** | **3** |
| Spend authorized | $0 | 3 product samples |

The naive agent is not paralysed. It ships three products. Here they are, with
what the evidence actually said about them:

| Lead | Naive margin | Cost | Retail | What the gates found |
|---|---|---|---|---|
| `W2B-PET-001` LED Pet Safety Collar | 62.5% | $5.74 | $15.32 | **$14.68 below the $30 retail floor**; sizing-return risk; electronics review required |
| `W2B-HOME-001` 6 Watch Valet | 60.0% | $12.49 | $31.23 | Evidence contract incomplete — supplier identity, shipping, delivery, returns, and demand all unverified |
| `TD-PET-001` Cat Self Groomer | 55.2% | $8.79 | $19.61 | **Out of stock at verification time**; $10.39 below the retail floor |

---

## The finding that surprised me

**All three naive picks pass the margin gate.**

62.5%, 60.0%, and 55.2% all clear the 55% minimum. If I had built the obvious
version of this system — the one that checks profit margin, because margin is
what everyone checks — it would have shipped all three.

What actually stopped them was mundane and unglamorous:

- **Retail floor.** Two products cannot be sold at a price anyone would pay.
  A 62.5% margin on a $15.32 product is $9.58 of gross profit per unit, before
  any advertising. The margin *percentage* looks great and the *dollars* do
  not survive customer acquisition.
- **Inventory.** One product was out of stock at the moment of verification.
  A margin-only agent has no opinion about stock, so it would have built a
  storefront around an item that could not be shipped.
- **Evidence completeness.** One product had no disqualifying number at all —
  it simply had too many unknowns to justify spending money on.

The lesson generalizes past commerce: **the gate that catches the most
dangerous errors is rarely the gate you thought to build.** Margin was the
obvious risk. Retail floor and stock state were the real ones.

---

## What actually caught the 30 leads

The full attribution table, produced by the same command:

| Count | Gate |
|---|---|
| 18 | Incomplete evidence contract |
| 6 | Gross margin below 55% at best supplier tier |
| 3 | Retail price below policy floor |
| 2 | Inventory not in stock |
| 1 each | Sizing-return risk, electronics review, bulk, fragility, breakage risk, safety-sensitive electronics, unvalidated performance claim, and four further margin variants |

**18 of 30 leads — 60% — died from incomplete evidence, not bad economics.**
They were not rejected because the numbers were bad. They were rejected
because the numbers *were not there*, and the system refuses to guess.

This is the single most transferable idea in the project. Most agent failures
in production are not arithmetic errors. They are **confident interpolation
over missing inputs.**

---

## Two things the system caught about itself

The gates do not only point outward at suppliers. Two examples from building
this:

**1. A supplier's own profit badge was treated as advisory, not fact.**
TopDawg's catalog displays an estimated profit per product. The policy
records supplier margin claims as `supplier_claim_unknown` unless the basis is
`landed_cost`, and recomputes economics from row-level costs. The displayed
badge never became an input to a launch decision.

**2. The freshness harness refused to let me claim a green build.**
While writing this case study, the workspace's correctness proof went stale.
The commit hash had not changed, but the source fingerprint had — because I had
edited files since the last verified test run. The startup gate reported
`correctness_stale` and declined to keep asserting "verified."

The same thing happened to the wiki layer. Four pages aged past their weekly
freshness window, and `publish` refused to republish them — correctly, because
it could not confirm they still matched their sources. Fixing that required
building a `reattest` path that re-verifies source hashes before refreshing a
timestamp, and **refuses when a source has genuinely changed.** It refused on
the first run, correctly, because I had just edited one of the declared source
files.

An agent stack that cannot fail its own audit is not trustworthy. It is just
quiet.

---

## What this is worth

I want to be precise here, because the temptation to overclaim is exactly the
thing this project exists to resist.

**What I can prove:**
- 30 leads evaluated, 0 selected, $0 spent — from on-disk evidence records.
- 3 products a margin-only agent would have shipped, all of which fail at
  least one non-margin gate.
- 60% of rejections driven by missing evidence rather than bad economics.

**What I cannot prove:**
- That those 3 products would have lost money. They would have been *bad
  decisions on the available evidence*, which is a different and weaker claim
  than "they would have failed."
- Any revenue figure. **This system has generated $0 in revenue.** The $250
  unspent is avoided cost, not earnings, and the codebase has a regression
  test asserting that the report never conflates the two.
- That gates like these are worth paying for. That is a hypothesis, and it
  needs buyer conversations rather than more code.

---

## Why I am not fixing the dropshipping business

The obvious move, staring at an empty shortlist, is to loosen the gates until
something passes. Drop the margin floor to 45%. Waive the retail floor. Ship
something.

That would destroy the only asset the project produced.

The control plane behind this — roughly 19,000 lines of workflow logic under
13,000 lines of tests, with policy gates, staged authority, lane-safe
concurrent writes, evidence-age enforcement, and claim-drift detection — is
domain-neutral. Commerce was one instance of a general problem: **a workflow
where "unknown" must be a hold, not a guess.**

The code that stops a supplier from lying to me about margin percentages is a
vendor-claim verification engine. I built it to protect $250. The same logic
protects six-figure procurement decisions with no architectural change — only
a different policy file.

So the store stays a proof lab, and the machine becomes the product.

---

## Reproduce it

```bash
python scripts/gate_counterfactual.py            # the counterfactual baseline
python scripts/gate_counterfactual.py --as-json  # machine-readable
python -m unittest tests.test_gate_counterfactual -v
python scripts/top_opportunities_report.py canonical/efficiens.db --as-json
```

Evidence records:

- `derived/research/wf1000-supplier-discovery-2026-08-29.json` — 30 leads
- `derived/research/wf1000-topdawg-verification-2026-08-29.json` — per-tier costs
- `derived/research/wf1000-doba-verification-2026-08-29.json` — live retail
- `derived/research/wf1000-cj-zendrop-public-review-2026-08-29.json` — public review

Policy: `state/commerce-launch-policy.json`

---

## Open questions

- Does anyone pay for agent trust infrastructure, or do they only care after a
  bad outcome? Unvalidated.
- Is the empty-shortlist story credible to buyers, or does it read as failure?
  Untested outside this document.
- Would open-sourcing the control plane build more credibility than it costs
  in future licensing? Genuinely unresolved.

---

*Draft. No pricing, no outreach, no publication. Numbers reproduce from the
commands above at the commit that introduced this file.*
