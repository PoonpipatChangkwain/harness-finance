---
name: portfolio-weakness
description: Find the weak spots of the user's saved portfolio — holdings whose contribution to risk is high while fundamentals or momentum are weak, plus concentration and diversification gaps. Use when the user asks "หาจุดอ่อนพอร์ต", which holding is the weakest, what drags the portfolio, or what to review first.
category: analysis
role: equity_analyst
inputs: [portfolio]
tools: [get_portfolio_analytics, get_fundamentals, get_market_snapshot, compute_returns_risk, compute_comps, get_news]
---

# Portfolio Weakness Scan

Answer in the user's language. Portfolio numbers come from `get_portfolio_analytics` (ground truth); company numbers come from tools. Never estimate a number. Do not give share counts or order instructions.

## Checklist
```
- [ ] 1. Portfolio analytics
- [ ] 2. Shortlist by risk
- [ ] 3. Fundamentals + momentum of the shortlist
- [ ] 4. Structural gaps
- [ ] 5. Ranked weaknesses
```

## Step 1 — Analytics
Use the PORTFOLIO block in the message if present, otherwise call `get_portfolio_analytics`. Keep the weights, P&L %, risk contribution and sector of every holding.

## Step 2 — Shortlist
Pick at most 5 holdings where risk contribution > weight, or with the largest unrealized loss, or the largest weight. Stocks only for fundamentals (forex/crypto/commodity: momentum only).

## Step 3 — Evidence (one turn, parallel calls)
For each shortlisted stock: `get_fundamentals` (margins, growth, leverage, valuation) and `get_market_snapshot` (trend vs SMA50/200, RSI, 3-month change). Use `compute_comps` when a peer comparison decides the case. `get_news` only for a holding with an unexplained move.

## Step 4 — Structural gaps
Concentration (effective N, top-3), sector or currency over-exposure, no defensive assets, too much or too little cash — from the analytics numbers.

## Step 5 — Output
1. **Ranked weaknesses** (most important first): holding or issue | evidence with numbers | why it matters
2. **Structural gaps** with numbers
3. **Questions to decide** (e.g. "is the thesis for X still intact?") — not orders
4. Short disclaimer: information, not investment advice
