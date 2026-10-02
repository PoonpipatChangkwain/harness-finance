# Third-party prompt sources

The `*.v2.md` role prompts are adapted from the open-source projects below. "Adapted" means:
tool names mapped to this project's tools, the output mapped to this project's structured
schemas, and this project's rules added (numbers only from tools / compute, the LLM proposes a
stop *basis* and never a size, no external tools in decision steps). Wording that is not
affected by those changes is kept as in the original. `*.v1.md` files are earlier rewrites kept
so the `prompt_id` recorded with old runs still resolves. This file is not loaded into any prompt.

## TradingAgents — Apache License 2.0

Source: https://github.com/TauricResearch/TradingAgents (`tradingagents/agents/`), local copy
`../TradingAgents`. Licensed under the Apache License, Version 2.0
(http://www.apache.org/licenses/LICENSE-2.0). These files were modified from the originals.

| Our prompt | Original |
|---|---|
| `trade_market_analyst.v2.md` | `analysts/market_analyst.py` |
| `trade_news_analyst.v2.md` | `analysts/news_analyst.py` |
| `trade_fundamentals_analyst.v2.md` | `analysts/fundamentals_analyst.py` |
| `trade_sentiment_analyst.v2.md` | `analysts/sentiment_analyst.py` (`_build_system_message`) + `schemas.py` (`SentimentReport`) |
| `trade_macro_analyst.v2.md` | analyst preamble shared by `analysts/*.py` (role text is our own) |
| `trade_bull_researcher.v2.md`, `trade_bear_researcher.v2.md` | `researchers/bull_researcher.py`, `researchers/bear_researcher.py` |
| `trade_research_manager.v2.md` | `managers/research_manager.py` + `schemas.py` (`ResearchPlan`) |
| `trade_trader.v2.md`, `trade_trader.v3.md`, `trade_trader.v4.md` (v3 adds our horizon / event-risk / time-stop rules, v4 our pending limit/stop orders) | `trader/trader.py` + `schemas.py` (`TraderProposal`) |
| `trade_reflector.v1.md` | `graph/reflection.py` (`Reflector._system_prompt`) |
| `trade_aggressive_debator.v2.md`, `trade_conservative_debator.v2.md`, `trade_neutral_debator.v2.md` | `risk_mgmt/*_debator.py` |
| `trade_portfolio_manager.v2.md` | `managers/portfolio_manager.py` + `schemas.py` (`PortfolioDecision`) + `context.py` (portfolio not provided) |
| `technical_analyst.v2.md` (indicator guide), `sentiment_analyst.v2.md` (best practices) | `analysts/market_analyst.py`, `analysts/sentiment_analyst.py` |

Also ported into code: `context.py` (`report_or_absent`, `opponent_argument_or_opening`) in
`domain/workflows/trading.py`, and the `schemas.py` field descriptions in `domain/agents/schemas.py`.

## FinRobot — Apache License 2.0

Copyright © 2024-2026 AI4Finance Foundation. This product includes software developed by
AI4Finance Foundation. Source: https://github.com/AI4Finance-Foundation/FinRobot
(`finrobot_equity/core/src/modules/equity_agents/`), local copy `../FinRobot`. Licensed under the
Apache License, Version 2.0. These files were modified from the originals. "FinRobot" and
"AI4Finance" are trademarks of AI4Finance Foundation; this project is not affiliated with it.

| Our prompt | Original |
|---|---|
| `equity_analyst.v2.md` | `company_overview_agent.py`, `competitor_analysis_agent.py`, `investment_overview_agent.py` |
| `risk_officer.v2.md` | `risks_agent.py` (`RISKS_PROMPT`) |
| `valuation_specialist.v2.md` | `valuation_overview_agent.py` |

## Dexter — MIT License

Source: https://github.com/virattt/dexter, local copy `../dexter`. MIT License.

| Our prompt | Original |
|---|---|
| `research_lead.v2.md` ("How you think") | `SOUL.md` (condensed) |
| `harness/runtime/prompts.py` (Behavior section, final-answer rules 6, 8, 9) | `src/agent/channels.ts` (CLI profile), `src/agent/prompts.ts` |
