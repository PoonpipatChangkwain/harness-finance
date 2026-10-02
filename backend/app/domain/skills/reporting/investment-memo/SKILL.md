---
name: investment-memo
description: Draft a buy-side investment memo (long or short) with variant view, falsifiable thesis, bear/base/bull scenarios, catalysts, risks with tripwires and monitoring KPIs — as markdown in the answer. Use when the user asks to "write a memo", "investment thesis", "pitch this stock", "เขียน memo", or a long/short write-up.
category: reporting
role: equity_analyst
inputs: [symbol, direction]
tools: [get_statements, get_fundamentals, get_company_profile, get_market_snapshot, get_news, get_analyst_estimates, get_insider_activity, compute_dcf, compute_comps, compute_returns_risk, memory_search]
---

# Investment Memo

Ported from Dexter's `write-memo` skill. Output is **markdown in the final answer** (no files). The memo may only use data gathered in this run (the scratchpad) — every number carries its provenance id; scenario returns and probability-weighted figures must come from compute tools or be written as explicit assumptions.

Read [memo-style.md](memo-style.md) and follow [memo-template.md](memo-template.md) exactly.

## Checklist
```
- [ ] 1. Frame the trade (direction, horizon, conviction, variant view)
- [ ] 2. Gather data (parallel)
- [ ] 3. Bear / base / bull scenarios (driver-based)
- [ ] 4. Valuation anchor (compute_dcf / compute_comps)
- [ ] 5. Draft every template section
- [ ] 6. Self-critique pass
```

## Step 1 — Frame
Ticker (required), direction long/short (default long if unclear, say so), horizon (default 12 months), conviction (default medium). If the user gave no variant view, derive a candidate after step 2 from where the data diverges from consensus, and label it as a draft.

## Step 2 — Gather (same turn where possible)
`get_statements` (annual and quarterly), `get_fundamentals`, `get_company_profile`, `get_market_snapshot`, `get_news`, `get_analyst_estimates`, `get_insider_activity` (US), and `memory_search(namespace="user_prefs")` for the user's risk tolerance or constraints.

## Step 3 — Scenarios
For bear / base / bull specify revenue growth, margin, exit multiple, probability (sum 100%). These are **assumptions** — label them. Price targets per scenario must come from a compute tool: run `compute_comps` for the multiple context and `compute_dcf` for the base case. If a scenario target cannot be computed with the tools, state the assumption and do not print a derived number.

## Step 4 — Anchor
`compute_dcf` base case (+ its sensitivity grid) and `compute_comps`. Use them as cross-checks, not as the whole thesis.

## Step 5 — Draft
Fill every section of the template. Thesis bullets: **[claim with number]** — evidence with a number. *Wrong if [observable].*

## Step 6 — Self-critique (mandatory)
1. The variant view contradicts something specific (consensus number, price-implied growth)
2. Every thesis bullet is falsifiable
3. Numbers behind every adjective, each with a provenance id
4. The bear case reads as strong as the bull case
5. Probabilities sum to 100%
6. Every risk has an observable tripwire
7. No invented consensus — write "not available" instead
