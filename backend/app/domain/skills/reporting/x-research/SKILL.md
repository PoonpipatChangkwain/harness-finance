---
name: x-research
description: X/Twitter public sentiment research — real-time perspectives, expert opinions and community discourse on a stock, sector or event, grouped into sourced themes. Use when the user asks "what are people saying on X/Twitter about…", "check X for", or wants public opinion from X specifically.
category: reporting
role: sentiment_analyst
inputs: [symbol_or_topic]
requires: [x_api]
tools: [get_social_sentiment, web_search, get_news]
---

# X Research

Ported from Dexter's `x-research` skill. Only offered when an X API token is configured. X posts reach you through `get_social_sentiment` (X is one of its sources); stance counts are computed in code. Answer in the user's language.

## Research loop

### 1. Decompose
Turn the question into 2–3 angles: the core ticker/cashtag, bullish signal, bearish signal, news-linked posts.

### 2. Execute
- `get_social_sentiment(symbol, lookback_days=1 or 7)` — pick the window by how time-sensitive the topic is.
- For a topic without a ticker, use `web_search` with "site:x.com <topic>" to find notable posts.
- If results are noisy or sparse, change the window once; do not loop more than twice (tools are limited per run).

### 3. Cross-check
`get_news` for the same symbol so claims seen on X are checked against reported news.

### 4. Synthesize
Group findings by theme (bullish, bearish, neutral, news/catalysts):

```
### [Theme]
[1–2 sentence summary]
- @username: "[key quote]" — [link]
```

End with **Overall sentiment**: tone, confidence, the computed stance counts, and any divergence between retail voices and news.

## Output
1. Query summary: what was searched and the time window
2. Sentiment themes with sourced quotes
3. Overall sentiment (with stance counts from the tool)
4. Caveats: X sentiment is not a reliable predictor; vocal-minority bias; short window
