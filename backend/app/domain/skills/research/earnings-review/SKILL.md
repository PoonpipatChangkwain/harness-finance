---
name: earnings-review
description: Review a company's latest reported results versus the prior year/quarter and expectations — growth, margins, beat/miss, and what moved the stock. Use when the user asks about earnings, งบ, quarterly/annual results, "สรุปงบ", or a post-earnings reaction.
category: research
role: equity_analyst
inputs: [symbol]
tools: [get_statements, get_fundamentals, get_news, get_analyst_estimates, get_market_snapshot, compute_returns_risk]
---

# Earnings Review

Answer in the user's language. Every number you quote must come from a tool result and keep its provenance id.

## Checklist
```
- [ ] 1. Latest statements (quarterly AND annual)
- [ ] 2. Growth and margins (from the statements, not estimated)
- [ ] 3. News around the report date
- [ ] 4. Expectations (analyst estimates, if available)
- [ ] 5. Market reaction (snapshot)
- [ ] 6. Beat / miss verdict + key issues
```

## Step 1 — Statements
Call `get_statements` twice in the same turn: `period="quarterly"` and `period="annual"`. Extract revenue, gross profit, operating income, net income, EPS, operating cash flow, capex, total debt, cash.
If a line item is missing, say it is missing — do not back it out from other numbers.

## Step 2 — Growth and margins
Growth (YoY / QoQ) and margins are ratios of numbers you already have. Only state a growth rate or margin that is present in a tool result (e.g. `revenue_growth`, `profitMargins` from `get_fundamentals`) or that is a direct comparison of two reported line items you show side by side (show both raw values so the reader can see them).
Never compound, annualize or project numbers yourself.

## Step 3 — News
Call `get_news` for the symbol. Pick the items published around the report date: guidance changes, one-offs, management commentary.

## Step 4 — Expectations
Call `get_analyst_estimates`. If it is unavailable (common for SET stocks), write that consensus is not available — never invent consensus figures.

## Step 5 — Reaction
Call `get_market_snapshot` for the % change 1d/5d/1m and where the price sits vs SMA50/200.
Optionally `compute_returns_risk` with `range="3mo"` for context.

## Step 6 — Output
1. **Headline**: beat / miss / in line (only if expectations exist; otherwise "no consensus available")
2. **Results table**: line item | latest | prior | change (only reported values)
3. **Drivers**: 3–5 bullets from statements + news
4. **Market reaction**: from the snapshot
5. **What to watch next quarter**
6. **Data gaps**: anything unavailable
