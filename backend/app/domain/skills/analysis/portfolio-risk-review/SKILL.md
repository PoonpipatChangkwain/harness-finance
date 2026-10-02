---
name: portfolio-risk-review
description: Risk review of the user's saved portfolio — concentration, sector/currency exposure, volatility, drawdown, beta, VaR/CVaR, correlation and which holdings drive the risk. No rebalance proposal. Use when the user asks to check portfolio risk, "ตรวจความเสี่ยงพอร์ต", diversification, or for the weekly portfolio report.
category: analysis
role: risk_officer
inputs: [portfolio]
tools: [get_portfolio_analytics, get_market_snapshot, compute_returns_risk, get_fundamentals, get_news]
---

# Portfolio Risk Review

Answer in the user's language. Every number comes from a tool result; the portfolio analytics are computed ground truth — quote them, never recompute them. Do **not** propose trades, target weights or share counts (that is the advisor workflow's job).

## Checklist
```
- [ ] 1. Portfolio analytics
- [ ] 2. Concentration + exposure
- [ ] 3. Market risk
- [ ] 4. Risk drivers (holding level)
- [ ] 5. Data gaps
- [ ] 6. Summary
```

## Step 1 — Analytics
If a PORTFOLIO block is already in the message, use it. Otherwise call `get_portfolio_analytics` once per selected portfolio. Note `as_of`, base currency and every warning.

## Step 2 — Concentration and exposure
HHI, largest weight, top-3 weight, effective N; sector, currency and market exposure. Flag any single name above 20% and any sector above 40% (state the numbers).

## Step 3 — Market risk
Annualized volatility, max drawdown, beta vs the benchmark, 1-day VaR/CVaR 95%. Explain in plain words what each means for this book (e.g. "a typical bad day loses about X%").

## Step 4 — Risk drivers
Compare each holding's contribution to risk with its weight; name the holdings whose risk share is much larger than their weight and any highly correlated pairs (correlation > 0.8). For the top 1-3 drivers you may call `get_market_snapshot` or `get_news` to explain *why* (trend, event), citing the tool result.

## Step 5 — Data gaps
List symbols without price/history/FX and what that leaves out.

## Step 6 — Output
1. **Risk level**: low / moderate / high with the 2-3 numbers that justify it
2. **Table**: metric | value | reading
3. **Top risk drivers** with numbers
4. **What to watch** (observable tripwires)
5. Short disclaimer: information, not investment advice
