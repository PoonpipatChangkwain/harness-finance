"""Portfolio simulator for backtests (Phase 15, layer B): pure, Decimal, no LLM (F1)."""

from app.domain.compute.backtest.baseline import sma_cross
from app.domain.compute.backtest.engine import simulate
from app.domain.compute.backtest.metrics import buy_and_hold, performance
from app.domain.compute.backtest.models import (
    ENGINE_VERSION,
    EquityPoint,
    SimConfig,
    SimDecision,
    SimResult,
    Skipped,
    Trade,
)

__all__ = [
    "ENGINE_VERSION",
    "EquityPoint",
    "SimConfig",
    "SimDecision",
    "SimResult",
    "Skipped",
    "Trade",
    "buy_and_hold",
    "performance",
    "simulate",
    "sma_cross",
]
