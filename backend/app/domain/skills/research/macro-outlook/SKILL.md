---
name: macro-outlook
description: Macro outlook — US rates, inflation, growth and risk gauges, market-implied event odds, global news, and what they mean for sectors and the Thai market (rates, USD/THB). Use when the user asks about the economy, Fed, interest rates, inflation, recession risk, "ภาพรวมเศรษฐกิจ", or macro impact on stocks.
category: research
role: macro_strategist
tools: [get_macro, get_prediction_markets, get_global_news, get_market_snapshot, compute_returns_risk]
---

# Macro Outlook

Answer in the user's language. Quote only numbers returned by tools, with their provenance ids.

## Checklist
```
- [ ] 1. Rates + inflation + growth + risk series
- [ ] 2. Market-implied odds of key events
- [ ] 3. Global market news
- [ ] 4. Thai market transmission (SET index, USD/THB)
- [ ] 5. Sector implications + scenario view
```

## Step 1 — Macro series
Call `get_macro` with `preset="rates"`, then `preset="inflation"` and `preset="growth"` (they may run in the same turn). Add `preset="risk"` for VIX / dollar index.
If FRED is unavailable, say so and rely on news — never fill numbers from memory.

## Step 2 — Event odds
Call `get_prediction_markets` for the most relevant event (e.g. "Fed rate cut", "recession"). Report the probabilities exactly as returned.

## Step 3 — News
Call `get_global_news` (market="US"; also market="TH" when the user cares about Thailand).

## Step 4 — Thailand transmission
Call `get_market_snapshot` for `^SET.BK` and `USDTHB` (symbol "USDTHB"). Use `compute_returns_risk` on `^SET.BK` for 6-month context if needed.
Explain the channel: US rates → USD → foreign flows into SET → THB; Thai policy rate and inflation if news covers them.

## Step 5 — Output
1. **Regime summary** (2–3 sentences)
2. **Dashboard table**: indicator | latest | change | date
3. **Event odds** from prediction markets
4. **Implications**: which sectors benefit / suffer (banks, property, energy, exporters, tech) and why
5. **Scenarios**: base / upside / downside with the observable trigger for each
6. **Data gaps**
