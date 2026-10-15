# Forward A/B — XAUUSD intraday v0.5 vs gates (paper, no orders)

- start (server clock, pre-registered): **2026-10-05 00:00**
- last run: 2026-10-15 17:19 server · runs: 76
- bars: 15m 2026-07-27 .. 2026-10-15 17:00
- candidates logged: 704 · LLM actions: {'half': 549, 'skip': 128, 'take': 27}
- LLM: gpt-oss:120b via ollama_cloud, anonymised packet (no date / price), temperature 0

LLM answers are committed when the bar closes (see `decided_at` / `late_minutes` in `candidates.jsonl`); outcomes are scored only after the 16-bar window closes.

| arm | resolved | pending | skipped | win% | PF | exp R | total R | R / unit risk | max DD % |
|---|---|---|---|---|---|---|---|---|---|
| ml_only | 77 | 1 | 0 | 33.8 | 0.912 | -0.054 | -4.166 | -0.054 | 13.739 |
| calendar_rule | 76 | 1 | 4 | 34.2 | 0.933 | -0.041 | -3.131 | -0.041 | 12.836 |
| trend_rule | 3 | 0 | 674 | 33.3 | 0.719 | -0.192 | -0.575 | -0.192 | 1.027 |
| llm_veto | 78 | 1 | 10 | 33.3 | 0.964 | -0.011 | -0.881 | -0.022 | 7.606 |
| llm_binary | 78 | 1 | 10 | 33.3 | 0.884 | -0.073 | -5.708 | -0.073 | 14.7 |

Judge after ~100 resolved trades per arm. Win-rate SE at n=100 is about ±5 pp; differences inside that band are noise.
