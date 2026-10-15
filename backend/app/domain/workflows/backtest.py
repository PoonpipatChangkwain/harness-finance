"""Backtest = the trading workflow on many past dates + a portfolio simulation (Phase 15).

Pure pieces the harness runner composes (no I/O here):

- ``BacktestConfig`` — what the user asks for: symbols, date range, cadence, the trading-run
  settings that shape every decision, and the first ``SimConfig`` (capital, risk, costs);
- ``grid`` — analysis dates per symbol from the symbol's *own* trading days, every
  ``cadence`` sessions, never today or later (no outcome to simulate);
- ``cell_decision`` — a finished trading run → the compact decision a cell stores;
- ``sim_decision`` — that stored decision → the simulator's ``SimDecision``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.compute.backtest.models import Side, SimConfig, SimDecision
from app.domain.models.horizon import HorizonName, horizon
from app.domain.models.market import OHLCVBar
from app.domain.models.symbol import normalize_symbol
from app.domain.workflows.trading import AnalystName

MAX_SYMBOLS = 10
MAX_CELLS = 400  # a hard ceiling on LLM spend per backtest, whatever the budget says
DEFAULT_CADENCE: dict[str, int] = {"intraday": 1, "swing": 3, "position": 5}  # trading sessions


class LLMPick(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)


class BacktestConfig(BaseModel):
    name: str = Field(default="", max_length=120)
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    start_date: date
    end_date: date
    horizon: HorizonName = "swing"
    cadence: int | None = Field(default=None, ge=1, le=60, description="Sessions between decisions")
    analysts: list[AnalystName] | None = None
    debate_rounds: int = Field(default=1, ge=1, le=3)
    risk_rounds: int = Field(default=1, ge=1, le=3)
    language: Literal["th", "en"] = "th"
    use_ml: bool = True  # feed the bull/bear ML signal to the desk where a model exists
    use_smc: bool = False  # opt-in Smart Money Concepts block for the desk when no ML signal leads
    llm: LLMPick | None = None
    concurrency: int = Field(default=2, ge=1, le=4)
    budget_usd: float | None = Field(default=None, gt=0)
    baseline: bool = True  # also run the SMA-cross baseline through the same simulator
    sim: SimConfig = Field(default_factory=SimConfig)

    @field_validator("symbols")
    @classmethod
    def _canonical(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            canonical = normalize_symbol(raw).canonical  # raises ValueError on garbage
            if canonical not in out:
                out.append(canonical)
        return out

    @model_validator(mode="after")
    def _dates(self) -> BacktestConfig:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.start_date >= date.today():
            raise ValueError("start_date must be in the past: a backtest needs what happened after")
        return self

    @property
    def sessions_between(self) -> int:
        return self.cadence or DEFAULT_CADENCE[self.horizon]

    @property
    def interval(self) -> str:
        return horizon(self.horizon).interval

    def run_input(self, symbol: str, day: date, backtest_id: str) -> dict[str, Any]:
        """The trading-run input of one cell (no portfolio: the simulator holds the book)."""
        out: dict[str, Any] = {
            "symbol": symbol,
            "analysis_date": day.isoformat(),
            "analysts": self.analysts,
            "debate_rounds": self.debate_rounds,
            "risk_rounds": self.risk_rounds,
            "use_portfolio": False,
            "horizon": self.horizon,
            "language": self.language,
            "use_ml": self.use_ml,
            "use_smc": self.use_smc,
            "backtest_id": backtest_id,
        }
        if self.llm is not None:
            out["llm"] = self.llm.model_dump()
        return out

    def cache_fields(self) -> dict[str, Any]:
        """What shapes a decision (with the symbol, the date and the prompt versions)."""
        return {
            "horizon": self.horizon,
            "analysts": sorted(self.analysts) if self.analysts else None,
            "debate_rounds": self.debate_rounds,
            "risk_rounds": self.risk_rounds,
            "language": self.language,
            "use_ml": self.use_ml,
            "use_smc": self.use_smc,
            "llm": self.llm.model_dump() if self.llm else None,
        }


def grid(
    daily: list[OHLCVBar], start: date, end: date, every: int, *, today: date | None = None
) -> list[date]:
    """Every ``every``-th session of ``daily`` within ``[start, end]``, strictly before today."""
    today = today or date.today()
    days = sorted({b.ts.date() for b in daily if start <= b.ts.date() <= end and b.ts.date() < today})
    return days[::every]


def cell_decision(run_status: str, output: dict[str, Any] | None) -> dict[str, Any]:
    """A finished trading run → what the backtest keeps of it.

    Only an ``approved`` run trades; every other ending (``no_trade``, ``rejected``, a failed
    run) is a flat decision with its reason, so the simulator can count why nothing traded.
    """
    out = output or {}
    state = out.get("state") or {}
    final = state.get("final_decision") or {}
    inst = state.get("instrument") or {}
    outcome = state.get("outcome") or {}
    base = {
        "run_status": run_status,
        "outcome": out.get("status") or outcome.get("status"),
        "reason": outcome.get("reason"),
        "rating": out.get("rating") or final.get("rating"),
        "verdict": out.get("verdict") or final.get("verdict"),
        "asset_type": inst.get("asset_type"),
        "lot_size": inst.get("lot_size"),
        "currency": inst.get("currency"),
    }
    record = state.get("decision_record") or {}
    card = record.get("card") or {}
    if (
        run_status != "completed"
        or base["outcome"] != "approved"
        or card.get("side") not in ("long", "short")
    ):
        return {**base, "side": "flat"}
    approval = state.get("approval") or {}
    fraction = approval.get("final_size_pct", final.get("final_size_pct", 1.0))
    pending = (card.get("order_type") or "market") != "market"
    return {
        **base,
        "side": card["side"],
        "order_type": card.get("order_type") or "market",
        "order_price": card.get("entry") if pending else None,
        "order_valid_bars": card.get("order_valid_bars"),
        "stop": card.get("stop"),
        "targets": list(card.get("targets") or []),
        "size_fraction": fraction,
        "time_stop_bars": card.get("time_stop_bars"),
        "interval": card.get("interval"),
        "entry_reference": card.get("entry"),
        "currency": card.get("currency") or base["currency"],
    }


def _dec(x: Any) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def sim_decision(symbol: str, day: date, decision: dict[str, Any], ref: str) -> SimDecision:
    raw = decision.get("side")
    side: Side = "long" if raw == "long" else "short" if raw == "short" else "flat"
    fraction = min(max(float(decision.get("size_fraction") or 0.0), 0.0), 1.0) if side != "flat" else 1.0
    lot = decision.get("lot_size")
    return SimDecision(
        symbol=symbol,
        currency=str(decision.get("currency") or ""),
        asset_type=str(decision.get("asset_type") or "stock"),
        decided_on=day,
        side=side,
        order_type=decision.get("order_type") or "market",
        order_price=_dec(decision.get("order_price")),
        order_valid_bars=decision.get("order_valid_bars"),
        stop=_dec(decision.get("stop")),
        targets=[Decimal(str(t)) for t in decision.get("targets") or []],
        size_fraction=Decimal(str(fraction)),
        time_stop_bars=decision.get("time_stop_bars"),
        lot_size=Decimal(str(lot)) if lot else Decimal(1),
        ref=ref,
        rating=decision.get("rating"),
    )
