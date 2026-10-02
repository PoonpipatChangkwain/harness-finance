---
name: compare-companies
description: Side-by-side comparison of 2–10 companies — valuation, profitability, leverage, growth and price performance in computed tables with rankings. Use when the user asks to compare companies, "เทียบ A กับ B", peer tables, or which of several stocks is cheaper/stronger.
category: data_processing
role: equity_analyst
inputs: [symbols]
tools: [search_symbols, compare_fundamentals, compare_performance, get_company_profile]
---

# Compare Companies

Answer in the user's language. Tables and rankings come from the compare tools; you only interpret them.

## Checklist
```
- [ ] 1. Resolve tickers
- [ ] 2. Fundamentals table (computed rankings)
- [ ] 3. Performance table (computed ranking)
- [ ] 4. Context (what each company does)
- [ ] 5. Verdict per question asked
```

## Step 1 — Tickers
If the user gave company names, resolve them with `search_symbols` first. Keep the same market for everything when possible.

## Step 2 — Fundamentals
Call `compare_fundamentals(symbols, metrics=[...])` choosing metrics that fit the question (pe, forward_pe, pb, ev_ebitda, roe, net_margin, debt_to_equity, revenue_growth, dividend_yield). Lower-is-better metrics (P/E, P/B, EV/EBITDA, D/E) are ranked ascending by the tool.

## Step 3 — Performance
Call `compare_performance(symbols, range="1y")` (same turn as step 2).

## Step 4 — Context
`get_company_profile` for companies the user may not know (at most 3).

## Step 5 — Output
1. **One-line answer** to what the user asked (cheapest / highest quality / best momentum)
2. **Fundamentals table** exactly from the tool, with ranks
3. **Performance table** exactly from the tool
4. **Trade-offs**: 2–4 bullets
5. **Missing data** per company
