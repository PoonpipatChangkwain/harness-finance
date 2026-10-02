---
name: technical-analysis
description: Technical read of a stock, index, forex pair or crypto — trend, momentum, volatility, support/resistance and signal summary from computed indicators. Use when the user asks about chart, trend, RSI/MACD, support/resistance, "เทคนิค", entry/exit levels or momentum.
category: analysis
role: technical_analyst
inputs: [symbol]
tools: [get_market_snapshot, compute_indicators, compute_returns_risk, get_prices]
---

# Technical Analysis

Answer in the user's language. Every level and indicator value must come from a tool (the snapshot is ground truth).

## Checklist
```
- [ ] 1. Snapshot (trend + momentum + volatility)
- [ ] 2. Extra indicators if needed
- [ ] 3. Risk profile
- [ ] 4. Support / resistance
- [ ] 5. Signal summary
```

## Step 1 — Snapshot
Call `get_market_snapshot`. Read: close vs SMA20/50/200 (trend), RSI14 (momentum; >70 overbought, <30 oversold), MACD vs signal and histogram sign, Bollinger position, ATR14 (volatility), % change 1d/5d/1m/3m, 52-week high/low.

## Step 2 — More indicators (optional)
`compute_indicators` with e.g. `["stoch", "obv"]` or a different `period` when the question needs it. Do not recompute what the snapshot already has.

## Step 3 — Risk
`compute_returns_risk(range="1y")` for volatility, max drawdown and beta.

## Step 4 — Support / resistance
Use only levels that exist in tool outputs: 52-week high/low, SMA50/200, Bollinger bands, and swing highs/lows visible in `recent_closes`. If you need a longer swing history call `get_prices(range="6mo")` and cite the specific bar (date + value) you use as a level. Do not invent Fibonacci or pivot levels.

## Step 5 — Output
1. **Signal**: bullish / bearish / neutral + confidence (low/medium/high) and why
2. **Indicator table**: indicator | value | reading
3. **Levels**: support and resistance with where each came from
4. **Risk**: volatility, drawdown, ATR-based stop distance *as a range from the tool value* (e.g. "1–2× ATR14 = …" only if ATR is shown)
5. **What would change the view** (observable triggers)
6. Short disclaimer: technical signals are not a recommendation
