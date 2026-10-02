"""Inputs and outputs of the portfolio simulator (Phase 15, layer B).

A ``SimDecision`` is what the trading workflow decided on one day, *without* a quantity:
side, entry order, stop, targets and the portfolio manager's keep-ratio. The simulator
sizes it from the simulated equity at fill time, so the same decisions can be replayed
with any starting capital or risk setting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Side = Literal["long", "short", "flat"]
OrderType = Literal["market", "limit", "stop"]
ExitReason = Literal["tp", "sl", "time_stop", "reverse", "end_of_test"]
SkipReason = Literal[
    "flat",
    "invalid_levels",
    "short_not_allowed",
    "already_open",
    "max_positions",
    "below_one_lot",
    "no_buying_power",
    "gap_invalidated",
    "order_expired",
    "no_bars",
]

ENGINE_VERSION = "v1"


class SimDecision(BaseModel, frozen=True):
    symbol: str
    currency: str
    asset_type: str = "stock"  # stock | forex | crypto | metal | index
    decided_on: date  # the analysis date; entry is possible from the next bar dated after it
    side: Side
    order_type: OrderType = "market"
    order_price: Decimal | None = None
    order_valid_bars: int | None = Field(default=None, ge=1)
    stop: Decimal | None = None
    targets: list[Decimal] = Field(default_factory=list)
    size_fraction: Decimal = Field(default=Decimal(1), ge=0, le=1)
    time_stop_bars: int | None = Field(default=None, ge=1)
    lot_size: Decimal = Field(default=Decimal(1), gt=0)
    ref: str = ""  # run id of the trading run that decided it (or "baseline:…")
    rating: str | None = None


IntrabarRule = Literal["stop_first", "target_first"]
ScaleOut = Literal["first_target", "equal_split"]


class SimConfig(BaseModel, frozen=True):
    initial_capital: Decimal = Field(default=Decimal(100_000), gt=0)
    base_currency: str = "THB"
    risk_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.5"))
    max_position_weight: Decimal | None = Field(default=Decimal("0.20"), gt=0, le=1)
    max_open_positions: int = Field(default=5, ge=1, le=50)
    # notional / margin; anything not listed trades unlevered
    leverage: dict[str, Decimal] = Field(default_factory=lambda: {"forex": Decimal(10)})
    compounding: bool = True  # False: size every trade from the initial capital
    # asset types that may be sold short (Thai cash equities cannot)
    shortable: frozenset[str] = frozenset({"forex"})
    commission_pct: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("0.02"))  # per side, of notional
    min_commission: Decimal = Field(default=Decimal(0), ge=0)  # per side, base currency
    slippage_bps: Decimal = Field(default=Decimal(5), ge=0, le=Decimal(500))  # market / stop fills
    intrabar_rule: IntrabarRule = "stop_first"
    scale_out: ScaleOut = "first_target"
    default_order_valid_bars: int = Field(default=5, ge=1)

    def leverage_for(self, asset_type: str) -> Decimal:
        return self.leverage.get(asset_type, Decimal(1))


class Trade(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    ref: str
    decided_on: date
    entry_at: datetime
    entry_price: Decimal
    exit_at: datetime
    exit_price: Decimal  # unit-weighted average over partial exits
    units: Decimal
    stop: Decimal
    exit_reason: ExitReason
    gap: bool = False  # the exit filled at a gapped open, beyond the level
    fees: Decimal  # base currency, both sides
    pnl: Decimal  # base currency, after fees
    r_multiple: Decimal | None  # gross of fees, in units of the initial risk
    bars_held: int


class Skipped(BaseModel):
    symbol: str
    ref: str
    decided_on: date
    reason: SkipReason
    detail: str = ""


class EquityPoint(BaseModel):
    day: date
    equity: Decimal
    cash: Decimal  # equity minus the margin held by open positions
    gross_exposure: Decimal  # sum of open notionals / equity
    open_positions: int


class SimResult(BaseModel):
    engine_version: str = ENGINE_VERSION
    config: SimConfig
    trades: list[Trade]
    skipped: list[Skipped]
    equity_curve: list[EquityPoint]
    final_equity: Decimal

    @model_validator(mode="after")
    def _sorted(self) -> SimResult:
        self.trades.sort(key=lambda t: (t.exit_at, t.symbol))
        return self
