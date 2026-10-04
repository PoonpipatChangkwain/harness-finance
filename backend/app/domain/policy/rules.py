"""First policy rule set (phase-09 §9.6), reused by trading (P10) and portfolio (P12).

Every rule reads pre-computed numbers off ``proposal``/``context`` — it never
computes a weight or a risk amount itself (that is ``domain/compute``'s job,
per F1/F3: a rule only compares numbers that already have provenance). A
field the proposal does not provide is treated as *unknown*, and an unknown
value fails a ``block`` rule closed (see each rule's docstring) rather than
silently passing a gate it cannot evaluate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.policy.base import PolicyResult, Severity


def _get(d: dict[str, Any], key: str) -> Any | None:
    return d.get(key)


@dataclass
class MaxPositionWeight:
    """``proposal.position_weight`` (0..1, post-trade) must not exceed ``limit``."""

    limit: float = 0.20
    id: str = "max_position_weight"
    description: str = "Position weight after the trade must stay within the per-symbol cap."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        weight = _get(proposal, "position_weight")
        if weight is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="position_weight was not provided",
                values={"limit": self.limit},
            )
        passed = weight <= self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"position weight {weight:.2%} {'within' if passed else 'exceeds'} cap {self.limit:.2%}",
            values={"weight": weight, "limit": self.limit},
        )


@dataclass
class MaxSectorWeight:
    """``proposal.sector_weight`` (0..1, post-trade) must not exceed ``limit``."""

    limit: float = 0.40
    id: str = "max_sector_weight"
    description: str = "Sector weight after the trade must stay within the cap."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        weight = _get(proposal, "sector_weight")
        if weight is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="sector_weight was not provided",
                values={"limit": self.limit},
            )
        passed = weight <= self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"sector weight {weight:.2%} {'within' if passed else 'exceeds'} cap {self.limit:.2%}",
            values={"weight": weight, "limit": self.limit},
        )


_OPEN_ACTIONS = frozenset({"buy", "sell", "open_long", "open_short"})


@dataclass
class RequireStopLoss:
    """An action that opens/adds risk (see ``_OPEN_ACTIONS``) must carry ``proposal.stop_loss``."""

    id: str = "require_stop_loss"
    description: str = "A new position must declare a stop-loss."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        action = str(_get(proposal, "action") or "").lower()
        if action not in _OPEN_ACTIONS:
            return PolicyResult(
                rule_id=self.id,
                passed=True,
                severity=self.severity,
                message=f"action {action!r} does not open risk; stop-loss not required",
            )
        stop_loss = _get(proposal, "stop_loss")
        passed = stop_loss is not None
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message="stop-loss present" if passed else "no stop-loss declared for an opening trade",
            values={"stop_loss": stop_loss},
        )


@dataclass
class MaxRiskPerTrade:
    """``proposal.risk_amount / context.equity`` must not exceed ``limit`` (default 2%)."""

    limit: float = 0.02
    id: str = "max_risk_per_trade"
    description: str = "Risk of a single trade must stay within a fraction of equity."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        risk_amount = _get(proposal, "risk_amount")
        equity = _get(context, "equity")
        if risk_amount is None or not equity:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="risk_amount or equity was not provided",
                values={"limit": self.limit},
            )
        fraction = risk_amount / equity
        passed = fraction <= self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"risk {fraction:.2%} {'within' if passed else 'exceeds'} cap {self.limit:.2%} of equity",
            values={"fraction": fraction, "limit": self.limit},
        )


@dataclass
class CashSufficient:
    """``proposal.cash_required`` must not exceed ``context.cash_available``."""

    id: str = "cash_sufficient"
    description: str = "The account must have enough cash to fund the trade."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        required = _get(proposal, "cash_required")
        available = _get(context, "cash_available")
        if required is None or available is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="cash_required or cash_available was not provided",
            )
        passed = required <= available
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=(f"cash required {required} {'<=' if passed else '>'} available {available}"),
            values={"required": required, "available": available},
        )


@dataclass
class ValidSymbol:
    """``proposal.symbol_valid`` — set by the caller after ``normalize_symbol`` succeeded."""

    id: str = "valid_symbol"
    description: str = "The symbol must have normalized successfully."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        valid = _get(proposal, "symbol_valid")
        passed = bool(valid)
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message="symbol is valid" if passed else "symbol did not normalize",
        )


@dataclass
class MinConfidence:
    """``proposal.confidence`` (0..1) must reach ``limit`` (fail-safe default 0.6, per T5)."""

    limit: float = 0.6
    id: str = "min_confidence"
    description: str = "Decision confidence must reach the minimum threshold."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        confidence = _get(proposal, "confidence")
        if confidence is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="confidence was not provided",
                values={"limit": self.limit},
            )
        passed = confidence >= self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"confidence {confidence:.2f} {'meets' if passed else 'below'} minimum {self.limit:.2f}",
            values={"confidence": confidence, "limit": self.limit},
        )


@dataclass
class MinRiskReward:
    """An opening trade's ``proposal.risk_reward`` (at the first target, computed) must exceed ``limit``.

    The first target is the one most likely to be hit, so it is the one that must pay for the
    risk. No target (R:R unknown) fails closed; an action that opens no risk passes.
    """

    limit: float = 1.0
    id: str = "min_risk_reward"
    description: str = "Reward to the first target must exceed the risk to the stop by the minimum ratio."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        action = str(_get(proposal, "action") or "").lower()
        if action not in _OPEN_ACTIONS:
            return PolicyResult(
                rule_id=self.id,
                passed=True,
                severity=self.severity,
                message=f"action {action!r} does not open risk; R:R not required",
            )
        rr = _get(proposal, "risk_reward")
        if rr is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="no target beyond entry: R:R unknown",
                values={"limit": self.limit},
            )
        passed = rr > self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"R:R {rr:.2f} at the first target {'exceeds' if passed else 'does not exceed'} "
            f"minimum {self.limit:.2f}",
            values={"risk_reward": rr, "limit": self.limit},
        )


@dataclass
class CounterTrendRiskReward:
    """A trade against the daily trend (``proposal.counter_trend``) needs a stricter R:R floor.

    Evidence (2026-10, XAUUSD intraday backtest): fading an RSI/Bollinger extreme against an
    established daily trend loses far more often than a with-trend trade at the same R:R
    (23% win rate in a trending month vs ~50% in a ranging one) — the higher timeframe running
    through the position is a real, distinct risk that the base ``min_risk_reward`` floor
    does not price in on its own. Not counter-trend, or an action that opens no risk: passes.
    """

    limit: float = 2.5
    id: str = "counter_trend_risk_reward"
    description: str = "A counter-trend trade's R:R at the first target must clear a stricter minimum."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        if not _get(proposal, "counter_trend"):
            return PolicyResult(
                rule_id=self.id, passed=True, severity=self.severity, message="not counter-trend"
            )
        action = str(_get(proposal, "action") or "").lower()
        if action not in _OPEN_ACTIONS:
            return PolicyResult(
                rule_id=self.id,
                passed=True,
                severity=self.severity,
                message=f"action {action!r} does not open risk; R:R not required",
            )
        rr = _get(proposal, "risk_reward")
        if rr is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="counter-trend trade with no target beyond entry: R:R unknown",
                values={"limit": self.limit},
            )
        passed = rr > self.limit
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"counter-trend R:R {rr:.2f} {'exceeds' if passed else 'does not exceed'} "
            f"the stricter minimum {self.limit:.2f}",
            values={"risk_reward": rr, "limit": self.limit},
        )


@dataclass
class ValidPendingOrder:
    """A limit/stop entry must pass its computed check (``proposal.order_error`` is ``None``).

    ``compute.trade.order`` sets the error when the order is on the wrong side of the
    price, too far from it in ATR, or cannot be checked. A market order passes.
    """

    id: str = "valid_pending_order"
    description: str = "A pending entry must sit on the right side of the price and within reach of it."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        order = str(_get(proposal, "order") or "market")
        if order == "market":
            return PolicyResult(rule_id=self.id, passed=True, severity=self.severity, message="market order")
        error = _get(proposal, "order_error")
        return PolicyResult(
            rule_id=self.id,
            passed=error is None,
            severity=self.severity,
            message=str(error) if error else f"{order.replace('_', ' ')} is valid",
            values={"order": order, "distance_atr": _get(proposal, "order_distance_atr")},
        )


@dataclass
class NoHighImpactEventSoon:
    """No high-impact scheduled release within ``hours`` of entry.

    Reads ``proposal.hours_to_next_high_impact`` (from the economic calendar; ``None`` =
    none in the window) and ``proposal.event_calendar_available``. An unavailable
    calendar fails the rule — it cannot vouch for a quiet window. Default ``warn``: a
    human approves every trade and may want to trade the release deliberately.
    """

    hours: float = 12.0
    id: str = "no_high_impact_event_soon"
    description: str = "No high-impact economic release within the guard window after entry."
    severity: Severity = "warn"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        if not _get(proposal, "event_calendar_available"):
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="economic calendar unavailable: event risk not checked",
                values={"hours": self.hours},
            )
        nxt = _get(proposal, "hours_to_next_high_impact")
        passed = nxt is None or nxt > self.hours
        where = "outside" if passed else "inside"
        message = (
            f"no high-impact release within {self.hours:g}h"
            if nxt is None
            else f"next high-impact release in {nxt:g}h ({where} the {self.hours:g}h guard)"
        )
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=message,
            values={"hours_to_next": nxt, "hours": self.hours},
        )


@dataclass
class NoShortIfDisallowed:
    """A short (negative quantity or ``action in {"sell_short","open_short"}``) needs
    ``context.allow_short``."""

    id: str = "no_short_if_disallowed"
    description: str = "Shorting is blocked unless the account explicitly allows it."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        action = str(_get(proposal, "action") or "").lower()
        quantity = _get(proposal, "quantity")
        is_short = action in {"sell_short", "open_short"} or (quantity is not None and quantity < 0)
        if not is_short:
            return PolicyResult(rule_id=self.id, passed=True, severity=self.severity, message="not a short")
        allowed = bool(_get(context, "allow_short"))
        return PolicyResult(
            rule_id=self.id,
            passed=allowed,
            severity=self.severity,
            message="short allowed for this account" if allowed else "shorting is not allowed",
        )


@dataclass
class MinCashWeight:
    """(P12) ``proposal.cash_weight`` (0..1, after the rebalance) must reach ``limit``."""

    limit: float = 0.0
    id: str = "min_cash"
    description: str = "Cash left after the rebalance must stay above the minimum weight."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        weight = _get(proposal, "cash_weight")
        if weight is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="cash_weight was not provided",
                values={"limit": self.limit},
            )
        passed = weight >= self.limit - 1e-9
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"cash {weight:.2%} {'meets' if passed else 'below'} minimum {self.limit:.2%}",
            values={"weight": weight, "limit": self.limit},
        )


@dataclass
class NoSellList:
    """(P12) No leg in ``proposal.sells`` (symbols sold) may be in ``context.no_sell``."""

    id: str = "no_sell_list"
    description: str = "Symbols the user marked as never-sell must not be sold."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        sells = _get(proposal, "sells")
        if sells is None:
            return PolicyResult(
                rule_id=self.id, passed=False, severity=self.severity, message="sells was not provided"
            )
        protected = {str(s).upper() for s in (_get(context, "no_sell") or [])}
        hit = sorted(s for s in sells if str(s).upper() in protected)
        return PolicyResult(
            rule_id=self.id,
            passed=not hit,
            severity=self.severity,
            message=f"sells protected symbols: {', '.join(hit)}" if hit else "no protected symbol sold",
            values={"violations": hit},
        )


@dataclass
class MaxTurnover:
    """(P12) ``proposal.turnover`` (gross traded value / portfolio value) must not exceed ``limit``."""

    limit: float = 0.30
    id: str = "max_turnover"
    description: str = "Gross traded value of one rebalance must stay within the turnover cap."
    severity: Severity = "block"

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult:
        turnover = _get(proposal, "turnover")
        if turnover is None:
            return PolicyResult(
                rule_id=self.id,
                passed=False,
                severity=self.severity,
                message="turnover was not provided",
                values={"limit": self.limit},
            )
        passed = turnover <= self.limit + 1e-9
        return PolicyResult(
            rule_id=self.id,
            passed=passed,
            severity=self.severity,
            message=f"turnover {turnover:.2%} {'within' if passed else 'exceeds'} cap {self.limit:.2%}",
            values={"turnover": turnover, "limit": self.limit},
        )


DEFAULT_RULES: tuple[Any, ...] = (
    MaxPositionWeight(),
    MaxSectorWeight(),
    RequireStopLoss(),
    MaxRiskPerTrade(),
    CashSufficient(),
    ValidSymbol(),
    MinConfidence(),
    NoShortIfDisallowed(),
)
