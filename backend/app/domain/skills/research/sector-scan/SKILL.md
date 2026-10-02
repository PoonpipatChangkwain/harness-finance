---
name: sector-scan
description: Scan one sector (SET or US) — list its stocks, compare performance and risk, rank them in code, and surface the key news. Use when the user asks "which bank stocks are strongest", "สแกนกลุ่มพลังงาน", sector leaders/laggards, or a sector overview.
category: research
role: equity_analyst
inputs: [sector, market]
tools: [list_sector_symbols, compare_performance, compare_fundamentals, get_market_snapshot, get_news]
---

# Sector Scan

Answer in the user's language. Rankings come from `compare_performance` / `compare_fundamentals`, never from your own sorting.

## Checklist
```
- [ ] 1. Resolve sector + market
- [ ] 2. Symbols in the sector
- [ ] 3. Performance + risk ranking (computed)
- [ ] 4. Valuation / quality comparison (computed)
- [ ] 5. News for the leaders and laggards
- [ ] 6. Summary
```

## Step 1 — Sector
Market: TH for SET (Thai names / ".BK"), US otherwise. If the sector name is unclear, call `list_sector_symbols` without `sector` to see the available names and pick the closest.

## Step 2 — Symbols
Call `list_sector_symbols(market, sector)`. Keep at most 10 symbols (the largest / best known first).

## Step 3 — Performance
Call `compare_performance(symbols, range="6mo")` (and `"1y"` if the user wants a longer view). Use its `ranking` as-is.

## Step 4 — Fundamentals
Call `compare_fundamentals(symbols)` with pe, pb, roe, net_margin, dividend_yield. Missing values stay missing.

## Step 5 — News
Call `get_news` for the top 2 and bottom 1 by return (they can run in the same turn).

## Step 6 — Output
1. **Sector read** (2–3 sentences)
2. **Ranking table**: rank | symbol | return | volatility | max drawdown (from the tool)
3. **Valuation table** from `compare_fundamentals`
4. **Leaders / laggards**: why, using the news
5. **Data gaps** (symbols with no data)
