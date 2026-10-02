"""Structured outputs of research agents (T5)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.agents.library import RoleName
from app.domain.models.coerce import coerce_optional_float, coerce_percent_field
from app.domain.models.decision import Decision, Rating


class ResearchPlan(BaseModel):
    """What the ``plan`` node decides before the agent loop starts."""

    skill: str | None = Field(
        default=None, description="Skill name from the list, or null for a free-form answer"
    )
    role: RoleName = Field(default="research_lead", description="Expert role that fits the question best")
    symbols: list[str] = Field(default_factory=list, description="Tickers mentioned or implied, as written")
    language: str = Field(default="th", description="Language of the question: th, en, …")
    title: str = Field(default="", max_length=80, description="Session title, at most 6 words, same language")
    reason: str = Field(default="", description="One sentence: why this skill/role")


# -- trading workflow (P10, T5). Named differently from ``ResearchPlan`` above (which is the
# research-chat scenario picker) to avoid the name collision contracts.md §8 would otherwise
# have with the already-shipped P07 schema.


def _prices(value: Any) -> Any:
    """A list of LLM-written prices: drop anything that is not one number ("15%", "150-160")."""
    if isinstance(value, str | int | float):
        value = [value]
    if not isinstance(value, list):
        return value
    return [p for p in (coerce_optional_float(v) for v in value) if p is not None]


def _bar_count(value: Any) -> Any:
    """ "16", 16.4, "16 bars" → 16; anything unreadable → None (the horizon default applies)."""
    if isinstance(value, str):
        value = value.strip().split(" ")[0]
    number = coerce_optional_float(value)
    if isinstance(number, int | float) and not isinstance(number, bool):
        return max(1, round(number))
    return None


# Field descriptions double as output instructions in structured mode (ported from
# ../TradingAgents/tradingagents/agents/schemas.py, Apache-2.0).
_HOLD_RULE = (
    "Conflicting arguments alone are not a reason to Hold: commit to the stronger side, sized by how "
    "decisively it wins. Choose Hold only when the evidence is still balanced after weighing, or too "
    "thin to support a call."
)
_PRICE_RULE = "Absolute price in the instrument's quote currency (e.g. 189.5), never a percentage or a range."


class TradeResearchPlan(BaseModel):
    """``research_manager``'s synthesis of the bull/bear debate."""

    stance: Rating = Field(
        description=f"Exactly one of Buy / Overweight / Hold / Underweight / Sell. {_HOLD_RULE}"
    )
    thesis: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the debate, ending with which "
            "arguments led to the recommendation, then concrete steps for the trader (sizing relative to "
            "a standard allocation)."
        )
    )
    key_risks: list[str] = Field(default_factory=list, description="Risks that would break the thesis")
    catalysts: list[str] = Field(default_factory=list, description="Events that would move the price")
    decision: Decision


StopBasis = Literal["atr", "level"]
TradeSide = Literal["long", "short", "flat"]
OrderType = Literal["market", "limit", "stop"]


def _order_type(value: Any) -> Any:
    """ "Sell Limit", "buy_stop", "LIMIT" → limit / stop; anything unreadable → market."""
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    for kind in ("limit", "stop"):
        if kind in text:
            return kind
    return "market"


class TraderProposal(BaseModel):
    """The trader's proposal; ``domain/compute/risk.py`` turns ``stop_basis`` into a price and
    sizes the position — the LLM proposes a basis, never a final number (F1)."""

    side: TradeSide = Field(
        description=(
            "long / short / flat. Buy or Overweight → long; Underweight or Sell → short "
            "(flat for a cash stock)"
        )
    )
    entry_zone: list[float] = Field(
        default_factory=list, description=f"[low, high] or a single price. {_PRICE_RULE}"
    )
    order_type: OrderType = Field(
        default="market",
        description=(
            "market = enter now at the current price; limit = wait for a better price (buy below / sell "
            "above the current price, e.g. a pullback into a zone); stop = enter on a breakout (buy above "
            "/ sell below the current price)"
        ),
    )
    order_price: float | None = Field(
        default=None,
        description=f"Required for limit/stop: the price the pending order waits at. {_PRICE_RULE}",
    )
    order_valid_bars: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        description="Cancel the pending order if not filled within this many bars (empty = horizon default)",
    )
    stop_basis: StopBasis = Field(
        default="atr", description="atr (with atr_multiple) or level (with stop_level)"
    )
    atr_multiple: float | None = Field(
        default=2.0, description="Used when stop_basis == 'atr', typically 1.5-3"
    )
    stop_level: float | None = Field(
        default=None,
        description=(
            f"Used when stop_basis == 'level': a support/resistance level from the reports. {_PRICE_RULE}"
        ),
    )
    target_levels: list[float] = Field(default_factory=list, description=f"1-3 targets. {_PRICE_RULE}")
    horizon: Literal["intraday", "swing", "position"] = "swing"
    time_stop_bars: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        description=(
            "Exit if neither stop nor target is hit within this many bars of the horizon's timeframe "
            "(e.g. 16 fifteen-minute bars = 4 hours)"
        ),
    )
    invalidation: str = Field(
        default="",
        description=(
            "The observable condition that proves the thesis wrong before the stop is hit "
            "(e.g. 'a 1-hour close back below the broken 208.50 resistance')"
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "The case for this side, anchored in the analysts' reports and the research plan. 2-4 sentences."
        ),
    )
    decision: Decision
    proposed_units: float | None = Field(
        default=None, description="Ignored: size is always computed. Recorded only to flag a mismatch."
    )

    _stop = field_validator("stop_level", "atr_multiple", "proposed_units", "order_price", mode="before")(
        coerce_optional_float
    )
    _bars = field_validator("time_stop_bars", "order_valid_bars", mode="before")(_bar_count)
    _order = field_validator("order_type", mode="before")(_order_type)
    _levels = field_validator("entry_zone", "target_levels", mode="before")(_prices)


class TargetWeight(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    target_weight: float = Field(ge=0, le=1, description="Fraction of the whole portfolio, 0-1")
    rationale: str = ""

    _w = field_validator("target_weight", mode="before")(coerce_percent_field)


class RebalanceProposal(BaseModel):
    """``propose_rebalance`` (P12): the LLM proposes *target weights and reasons only*; shares,
    money and post-trade metrics are computed by the internal ``rebalance_plan`` tool (F1)."""

    targets: list[TargetWeight] = Field(description="Every holding to keep plus any new symbol, 0-1 each")
    new_symbols: list[str] = Field(default_factory=list, description="Tickers to add that are not held yet")
    cash_target: float = Field(default=0.0, ge=0, le=1, description="Cash weight after the rebalance, 0-1")
    decision: Decision
    summary: str = Field(default="", description="2-4 sentences: why this allocation")

    _c = field_validator("cash_target", mode="before")(coerce_percent_field)


class PortfolioDecision(BaseModel):
    """``portfolio_manager``'s final call; ``final_size_pct`` is re-derived from compute after
    any adjustment, never taken from the LLM as-is (F1)."""

    rating: Rating = Field(
        description=f"Exactly one of Buy / Overweight / Hold / Underweight / Sell. {_HOLD_RULE}"
    )
    decision: Decision
    final_size_pct: float = Field(
        ge=0, le=1, description="Fraction 0-1 of the trader's computed position to keep (1 = as computed)"
    )
    adjustments: str = Field(
        default="",
        description=(
            "Investment thesis: the evidence that decided it, which risk points changed the plan, "
            "what would change the call"
        ),
    )
    summary: str = Field(
        default="",
        description=(
            "Executive summary: the call and how to act on it (entry, sizing, key risk levels, horizon). "
            "2-4 sentences."
        ),
    )

    _f = field_validator("final_size_pct", mode="before")(coerce_percent_field)
