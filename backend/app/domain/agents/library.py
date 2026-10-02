"""Expert role library: role → title, versioned prompt, tools, tier (F5).

Pattern from ``../FinRobot/finrobot/agents/agent_library.py`` (roles as
data, not code) with profiles adapted from FinRobot's equity agents
(company overview, valuation, risks) and TradingAgents' analysts. Prompts
live in ``prompts/<role>.v<N>.md`` so the exact version used is recorded
with each run.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.domain.ports import Tier

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

RoleName = Literal[
    "research_lead",
    "equity_analyst",
    "macro_strategist",
    "technical_analyst",
    "sentiment_analyst",
    "valuation_specialist",
    "risk_officer",
    # trading workflow (P10) — a fixed pipeline, never user-selectable in the research planner
    "trade_market_analyst",
    "trade_news_analyst",
    "trade_fundamentals_analyst",
    "trade_sentiment_analyst",
    "trade_macro_analyst",
    "trade_bull_researcher",
    "trade_bear_researcher",
    "trade_research_manager",
    "trade_trader",
    "trade_aggressive_debator",
    "trade_conservative_debator",
    "trade_neutral_debator",
    "trade_portfolio_manager",
    "trade_report_writer",
    "trade_reflector",
    # portfolio advisor (P12) — also a fixed pipeline, not offered by the research planner
    "pf_risk_officer",
    "pf_allocation_strategist",
    "pf_advisor",
]
PIPELINE_ROLE_PREFIXES = ("trade_", "pf_")
DEFAULT_ROLE: RoleName = "research_lead"


class RoleSpec(BaseModel):
    name: RoleName
    title: str
    version: str = "v1"
    tools: list[str] | None = None  # None = every enabled tool
    tier: Tier = "deep"

    @property
    def prompt_id(self) -> str:
        return f"{self.name}.{self.version}"


LIBRARY: dict[str, RoleSpec] = {
    spec.name: spec
    for spec in [
        # v2 = prompts ported from the references (prompts/THIRD_PARTY.md); v1 kept for old runs
        RoleSpec(name="research_lead", title="Research Lead", version="v2"),
        RoleSpec(name="equity_analyst", title="Equity Analyst", version="v2"),
        RoleSpec(name="macro_strategist", title="Macro Strategist"),
        RoleSpec(name="technical_analyst", title="Technical Analyst", version="v2"),
        RoleSpec(name="sentiment_analyst", title="Sentiment Analyst", version="v2"),
        RoleSpec(name="valuation_specialist", title="Valuation Specialist", version="v2"),
        RoleSpec(name="risk_officer", title="Risk Officer", version="v2"),
        RoleSpec(
            name="trade_market_analyst",
            title="Market Analyst",
            version="v2",
            tier="quick",
            tools=["get_prices", "get_market_snapshot", "compute_indicators"],
        ),
        RoleSpec(
            name="trade_news_analyst",
            title="News Analyst",
            version="v2",
            tier="quick",
            tools=[
                "get_news",
                "get_global_news",
                "get_macro",
                "get_prediction_markets",
                "get_economic_calendar",
            ],
        ),
        RoleSpec(
            name="trade_fundamentals_analyst",
            title="Fundamentals Analyst",
            version="v2",
            tier="quick",
            tools=["get_fundamentals", "get_statements", "get_insider_activity", "compute_comps"],
        ),
        RoleSpec(
            name="trade_sentiment_analyst",
            title="Sentiment Analyst",
            version="v2",
            tier="quick",
            tools=["get_social_sentiment", "get_news"],
        ),
        RoleSpec(
            name="trade_macro_analyst",
            title="Macro Analyst",
            version="v2",
            tier="quick",
            tools=["get_macro", "get_global_news", "get_prediction_markets", "get_economic_calendar"],
        ),
        RoleSpec(name="trade_bull_researcher", title="Bull Researcher", version="v2"),
        RoleSpec(name="trade_bear_researcher", title="Bear Researcher", version="v2"),
        RoleSpec(name="trade_research_manager", title="Research Manager", version="v2"),
        # v3: horizon, time stop, invalidation; v4: pending limit/stop orders;
        # v5: flat is allowed on a marginal setup even when the plan leans a side, and
        # confidence must be calibrated to actual win rate, not thesis conviction
        # (live/backtest showed frequent stop-outs from low-quality forced entries)
        RoleSpec(name="trade_trader", title="Trader", version="v5"),
        RoleSpec(
            name="trade_aggressive_debator", title="Aggressive Risk Debator", version="v2", tier="quick"
        ),
        RoleSpec(
            name="trade_conservative_debator", title="Conservative Risk Debator", version="v2", tier="quick"
        ),
        RoleSpec(name="trade_neutral_debator", title="Neutral Risk Debator", version="v2", tier="quick"),
        RoleSpec(name="trade_portfolio_manager", title="Portfolio Manager", version="v2"),
        RoleSpec(name="trade_report_writer", title="Report Writer", tier="quick"),
        RoleSpec(name="trade_reflector", title="Trade Reflector", tier="quick"),
        RoleSpec(
            name="pf_risk_officer",
            title="Portfolio Risk Officer",
            tools=[
                "get_market_snapshot",
                "compute_returns_risk",
                "get_fundamentals",
                "get_news",
                "get_macro",
            ],
        ),
        RoleSpec(
            name="pf_allocation_strategist",
            title="Allocation Strategist",
            tools=[
                "get_market_snapshot",
                "get_fundamentals",
                "compute_comps",
                "get_news",
                "list_sector_symbols",
            ],
        ),
        RoleSpec(name="pf_advisor", title="Portfolio Advisor"),
    ]
}


def get_role(name: str | None) -> RoleSpec:
    return LIBRARY.get(name or DEFAULT_ROLE) or LIBRARY[DEFAULT_ROLE]


@cache
def load_profile(prompt_id: str) -> str:
    return (PROMPTS_DIR / f"{prompt_id}.md").read_text(encoding="utf-8").strip()


def role_profile(role: RoleSpec) -> str:
    return load_profile(role.prompt_id)
