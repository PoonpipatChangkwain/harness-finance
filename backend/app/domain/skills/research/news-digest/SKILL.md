---
name: news-digest
description: Digest recent news for a stock or the market — group items by theme, rate importance, and explain likely impact. Use when the user asks "มีข่าวอะไร", "what's the news on X", "summarize headlines", or wants a catch-up on recent events.
category: research
role: research_lead
tools: [get_news, get_global_news, web_search, web_fetch, get_market_snapshot]
---

# News Digest

Answer in the user's language. Cite each news item by its source and date. This skill needs no calculation; any price or % change comes from the compute tool `get_market_snapshot`, never from a headline.

## Checklist
```
- [ ] 1. Gather: symbol news + market news
- [ ] 2. Deduplicate and group by theme
- [ ] 3. Rate importance + direction
- [ ] 4. Price context
- [ ] 5. Digest
```

## Step 1 — Gather
- A symbol was given: `get_news(symbol)` and `get_global_news(market=…)` in the same turn.
- No symbol: `get_global_news` for the relevant market (and a focused topic if the user named one).
- Thai company news is thin in global feeds: add one `web_search` in Thai or English (e.g. "PTT ข่าวล่าสุด").
- Use `web_fetch` only for the 1–2 items whose details change the conclusion.

## Step 2 — Themes
Group items into themes (pattern from FinRobot's news integrator): **earnings / guidance**, **corporate action** (M&A, dividends, buybacks), **regulation / policy**, **macro**, **product / operations**, **management**, **market flow / ratings**. Merge duplicates reported by several sources.

## Step 3 — Importance
For each theme give importance **high / medium / low** and direction **positive / negative / mixed** for the stock or market, with one line of reasoning.

## Step 4 — Price context
If a symbol was given, call `get_market_snapshot` and relate the % changes to the dates of the main items (without claiming causation you cannot show).

## Step 5 — Output
1. **Top line** (1–2 sentences)
2. **Themes** — for each: importance, direction, 1–3 items (source, date, one-line gist)
3. **What to watch**
4. **Coverage gaps** (e.g. few Thai-language sources)
