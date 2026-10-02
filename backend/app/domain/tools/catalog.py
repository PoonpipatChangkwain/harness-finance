"""Every domain tool, in one list (the harness adds memory + skill + portfolio-lookup tools on top)."""

from __future__ import annotations

from app.domain.tools.analysis import ANALYSIS_TOOLS
from app.domain.tools.base import ToolSpec
from app.domain.tools.calendar import CALENDAR_TOOLS
from app.domain.tools.company import COMPANY_TOOLS
from app.domain.tools.market import MARKET_TOOLS
from app.domain.tools.news import NEWS_TOOLS
from app.domain.tools.portfolio import PORTFOLIO_TOOLS
from app.domain.tools.trading import TRADING_TOOLS


def domain_tools() -> list[ToolSpec]:
    return [
        *MARKET_TOOLS,
        *COMPANY_TOOLS,
        *NEWS_TOOLS,
        *CALENDAR_TOOLS,
        *ANALYSIS_TOOLS,
        *TRADING_TOOLS,
        *PORTFOLIO_TOOLS,
    ]
