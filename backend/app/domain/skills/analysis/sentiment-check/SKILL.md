---
name: sentiment-check
description: Retail and news sentiment for one symbol — social posts with computed stance counts, news tone, and whether sentiment diverges from price action. Use when the user asks what people think, "กระแส", hype, social/Reddit/StockTwits sentiment, or crowd positioning.
category: analysis
role: sentiment_analyst
inputs: [symbol]
tools: [get_social_sentiment, get_news, get_market_snapshot]
---

# Sentiment Check

Answer in the user's language. Stance counts come from `get_social_sentiment` (computed in code) — never count posts yourself.

## Checklist
```
- [ ] 1. Social posts + stance counts
- [ ] 2. News tone
- [ ] 3. Price action
- [ ] 4. Divergence + interpretation
```

## Step 1 — Social
Call `get_social_sentiment(symbol, lookback_days=7)`.
- `status="unavailable"` means the sources could not be read — say so; it does NOT mean "no one is talking".
- `ok` with zero posts means genuinely quiet.
- Report `stance_counts` exactly (bullish, bearish, neutral, bull_ratio, by_platform).
- Quote 2–4 representative posts (platform + short excerpt).

## Step 2 — News tone
Call `get_news`. Classify the recent headlines as positive / negative / mixed with one line each.

## Step 3 — Price
Call `get_market_snapshot` for % change 5d/1m and RSI14.

## Step 4 — Output
1. **Sentiment verdict**: bullish / bearish / mixed / quiet, with the computed counts
2. **Social table**: platform | posts | stance mix
3. **News tone** bullets
4. **Divergence**: does sentiment agree with price momentum? (e.g. euphoric posts + RSI > 70 = crowded)
5. **Caveats**: small samples, bot/spam risk, Thai stocks have little English-language social coverage
