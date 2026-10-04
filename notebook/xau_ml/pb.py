"""pybroker wiring: features as indicators, models (walk-forward + pretrained), executions.

* Base-timeframe features are registered as ``pybroker.indicator`` from the same
  functions as ``features.BASE_FEATURES``; higher-timeframe context columns (already
  merged on close time) are registered as custom columns.
* Walk-forward: pybroker's split is a ROLLING window; ``wf_train_fn`` turns it into an
  EXPANDING one by training on every labelled row from TRAIN start up to the window's last
  train bar, then purging the last ``time_stop + 1`` rows (their labels reach into the
  window's test bars). pybroker already leaves ``lookahead`` bars between train and test.
* Trading rules = the system's: market entry at the next open (``buy_delay=1``), stop
  1R, target 1.5R (2.5R counter-trend for the ``ct25`` set), time stop via ``hold_bars``
  exiting at the close, one position at a time, 1% risk per trade.
  pybroker checks stops from the bar AFTER the fill, while the harness also checks the
  entry bar â€” the R-replica in ``sim.py`` follows the harness; compare both.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import lightgbm as lgb
import numpy as np
import pandas as pd
import pybroker as pyb
from pybroker import ExecContext, PriceType, StrategyConfig
from pybroker.common import FeeMode

from xau_ml import features as F
from xau_ml.config import BARS_PER_YEAR, COST_PRICE, INITIAL_CASH, RISK_PCT, SYMBOL, TRAIN, Horizon, LabelSet, ModelParams
from xau_ml.model import Calibrator, fit_lgbm

LEVERAGE = 50.0  # CFD account: 1% risk on a ~$10 stop needs notional above cash


def pb_frame(frame: pd.DataFrame, htf_cols: list[str], extra: tuple[str, ...] = ()) -> pd.DataFrame:
    cols = ["date", "open", "high", "low", "close", "volume", *htf_cols, "no_entry", *extra]
    out = frame[cols].copy()
    out.insert(1, "symbol", SYMBOL)
    return out


def register_indicators() -> list[pyb.Indicator]:
    """Every base feature + ATR14 as a pybroker indicator (same function as the predictor)."""

    def wrap(fn: Callable[[F.Arrays], np.ndarray]) -> Callable:
        def ind(data) -> np.ndarray:
            a = {"open": data.open, "high": data.high, "low": data.low, "close": data.close,
                 "volume": data.volume, "date": data.date}
            return fn(a)

        return ind

    inds = [pyb.indicator(name, wrap(fn)) for name, fn in F.BASE_FEATURES.items()]
    inds.append(pyb.indicator("atr14", wrap(F._atr14)))
    return inds


def config(tf: str) -> StrategyConfig:
    return StrategyConfig(
        initial_cash=INITIAL_CASH,
        enable_fractional_shares=True,
        round_fill_price=False,
        fee_mode=FeeMode.PER_SHARE,
        fee_amount=COST_PRICE / 2,  # charged on entry and on exit
        leverage=LEVERAGE,
        buy_delay=1,
        sell_delay=1,
        exit_on_last_bar=True,
        bars_per_year=BARS_PER_YEAR[tf],
    )


# --- models -------------------------------------------------------------------------------


class WFModel:
    def __init__(self, booster: lgb.Booster, cal: Calibrator, cols: list[str]):
        self.booster, self.cal, self.cols = booster, cal, cols

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.cal(self.booster.predict(X[self.cols]))


def wf_train_fn(symbol, train_data, test_data, *, frame, side, hz, cols, log, mp=None):
    """Expanding-window training for one pybroker walk-forward window."""
    mp = mp or ModelParams()
    train_end = pd.Timestamp(train_data["date"].max())
    rows = frame[(frame["date"] >= pd.Timestamp(TRAIN[0])) & (frame["date"] <= train_end)]
    rows = rows[rows[f"y_{side}"].notna() & (rows["no_entry"] == 0)].iloc[: -(hz.time_stop + 1)]  # purge
    cal_n = int(len(rows) * 0.25)
    fit_rows, cal_rows = rows.iloc[: -(cal_n + hz.time_stop + 1)], rows.iloc[-cal_n:]
    booster = fit_lgbm(fit_rows[cols], fit_rows[f"y_{side}"].to_numpy(), hz, mp)
    cal = Calibrator.fit("platt", booster.predict(cal_rows[cols]), cal_rows[f"y_{side}"].to_numpy())
    log.append({"side": side, "train_from": rows["date"].min(), "train_to": rows["date"].max(),
                "rows": len(rows), "test_from": test_data["date"].min(), "test_to": test_data["date"].max(),
                "rounds": booster.num_trees()})
    return WFModel(booster, cal, cols), cols


def predict_fn(model, df: pd.DataFrame) -> np.ndarray:
    return model.predict(df)


def register_wf_model(name, inds, **kw) -> pyb.ModelSource:
    return pyb.model(name, wf_train_fn, indicators=inds, predict_fn=predict_fn, **kw)


def register_pretrained(name, inds, obj: WFModel) -> pyb.ModelSource:
    def load(symbol, train_start_date, train_end_date):
        return obj, obj.cols

    return pyb.model(name, load, indicators=inds, predict_fn=predict_fn, pretrained=True)


def frozen_backtest(strategy: pyb.Strategy, frame: pd.DataFrame, start, end, tf: str, pad_bars: int = 300):
    """Backtest pretrained (frozen) models over exactly ``[start, end]``.

    pybroker only loads models for a window that has train rows, so the run gets a dummy
    train slice of ``pad_bars`` bars just before ``start`` (the loader ignores it â€” the
    models are already fitted) and ``train_size`` is solved so testing begins at ``start``.
    """
    dates = frame["date"]
    k = int(np.searchsorted(dates.to_numpy(), np.datetime64(pd.Timestamp(start))))
    s = dates.iloc[k - pad_bars]
    window = dates[(dates >= s) & (dates <= pd.Timestamp(end))].reset_index(drop=True)
    n, look = len(window), 1
    res = n - 1 - look
    target = int(window.searchsorted(pd.Timestamp(start)))
    best = None
    for ts in np.linspace(0.001, 0.999, 20000):
        tl, tsl = int(res * ts), int(res * (1 - ts))
        test_start = n - look - tl - tsl - 1 + tl + look
        if test_start >= target and (best is None or test_start < best[1]):
            best = (ts, test_start)
    ts, test_start = best
    result = strategy.backtest(start_date=s, end_date=end, timeframe=tf, train_size=float(ts), lookahead=look)
    return result, window.iloc[test_start]


# --- executions ---------------------------------------------------------------------------


def _open(ctx: ExecContext, side: int, hz: Horizon, tp_r: float) -> None:
    R = hz.sl_atr * float(ctx.indicator("atr14")[-1])
    if not np.isfinite(R) or R <= 0:
        return
    shares = Decimal(str(round(float(ctx.total_equity) * RISK_PCT / R, 4)))
    if side == 1:
        ctx.buy_shares = shares
        ctx.buy_fill_price = PriceType.OPEN
        ctx.sell_fill_price = PriceType.CLOSE  # time-stop exit
    else:
        ctx.sell_shares = shares
        ctx.sell_fill_price = PriceType.OPEN
        ctx.buy_fill_price = PriceType.CLOSE
    ctx.stop_loss = R
    ctx.stop_profit = tp_r * R
    ctx.hold_bars = hz.time_stop


def _tp(ctx: ExecContext, side: int, ls: LabelSet) -> float:
    if ls.ct_tp_r is None:
        return ls.tp_r
    trend = float(ctx.d1_trend[-1]) if np.isfinite(ctx.d1_trend[-1]) else 0.0
    return ls.ct_tp_r if trend == -side else ls.tp_r


def _flat(ctx: ExecContext) -> bool:
    return ctx.long_pos() is None and ctx.short_pos() is None


def _can_enter(ctx: ExecContext) -> bool:
    """Flat, and not next to a data hole (``labels.data_holes``: the trade would be priced
    across weeks of missing bars, or the features straddle them)."""
    return _flat(ctx) and float(ctx.no_entry[-1]) == 0.0


def model_exec(hz: Horizon, ls: LabelSet, bull: str, bear: str, tau: float, m: float):
    def fn(ctx: ExecContext) -> None:
        if not _can_enter(ctx):
            return
        pb, pr = float(ctx.preds(bull)[-1]), float(ctx.preds(bear)[-1])
        if pb >= tau and pb - pr >= m and pb > pr:
            _open(ctx, 1, hz, _tp(ctx, 1, ls))
        elif pr >= tau and pr - pb >= m and pr > pb:
            _open(ctx, -1, hz, _tp(ctx, -1, ls))

    return fn


def signal_exec(hz: Horizon, ls: LabelSet):
    """Trade a precomputed custom column ``ml_signal`` (+1/−1/0, decided at that bar's
    close by the nested walk-forward) with the system's entry/exit rules."""

    def fn(ctx: ExecContext) -> None:
        if not _can_enter(ctx):
            return
        s = float(ctx.ml_signal[-1])
        if s in (1.0, -1.0):
            _open(ctx, int(s), hz, _tp(ctx, int(s), ls))

    return fn


def always_exec(side: int, hz: Horizon, ls: LabelSet):
    def fn(ctx: ExecContext) -> None:
        if _can_enter(ctx):
            _open(ctx, side, hz, _tp(ctx, side, ls))

    return fn


def sma_cross_exec(hz: Horizon, ls: LabelSet):
    def fn(ctx: ExecContext) -> None:
        if not _can_enter(ctx):
            return
        s20, s50 = ctx.indicator("sma20"), ctx.indicator("sma50")
        if len(s20) < 2 or not np.isfinite(s50[-2]):
            return
        if s20[-2] <= s50[-2] and s20[-1] > s50[-1]:
            _open(ctx, 1, hz, _tp(ctx, 1, ls))
        elif s20[-2] >= s50[-2] and s20[-1] < s50[-1]:
            _open(ctx, -1, hz, _tp(ctx, -1, ls))

    return fn


def buy_hold_exec(ctx: ExecContext) -> None:
    if _flat(ctx) and not ctx.session.get("bought"):
        ctx.buy_shares = Decimal(str(round(float(ctx.total_equity) / float(ctx.close[-1]), 4)))
        ctx.buy_fill_price = PriceType.OPEN
        ctx.session["bought"] = True


def sma_indicators() -> list[pyb.Indicator]:
    return [
        pyb.indicator("sma20", lambda data: F.sma(data.close, 20)),
        pyb.indicator("sma50", lambda data: F.sma(data.close, 50)),
    ]


# --- results ------------------------------------------------------------------------------


def trades_in_r(result, frame: pd.DataFrame) -> pd.DataFrame:
    """pybroker trades â†’ R multiples, using the R fixed at the decision bar (bar before entry)."""
    t = result.trades.copy()
    if t.empty:
        return t.assign(r=[], side=[])
    pos = pd.Series(np.arange(len(frame)), index=frame["date"].to_numpy())
    entry_pos = pos.reindex(pd.to_datetime(t["entry_date"]).to_numpy()).to_numpy()
    risk = frame["risk"].to_numpy()[(entry_pos - 1).astype(int)]
    shares = t["shares"].astype(float)
    t["side"] = np.where(t["type"] == "long", "long", "short")
    t["risk"] = risk
    # pybroker's trade pnl is gross; fees (COST_PRICE/2 per share, each side) come off here
    t["r"] = (t["pnl"].astype(float) - shares * COST_PRICE) / (shares * risk)
    return t


def r_metrics(t: pd.DataFrame, result) -> dict:
    """Trade stats in R (net of fees) + drawdown/return from pybroker's equity curve."""
    eq = result.portfolio["equity"].astype(float).to_numpy()
    dd = float(100 * (1 - eq / np.maximum.accumulate(eq)).max()) if len(eq) else 0.0
    ret = float(100 * (eq[-1] / INITIAL_CASH - 1)) if len(eq) else 0.0
    if t.empty:
        return {"trades": 0, "max_dd_pct": dd, "return_pct": ret}
    r = t["r"].to_numpy()
    longs = int((t["side"] == "long").sum())
    return {
        "trades": len(r),
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(r[r > 0].sum() / -r[r < 0].sum()) if (r < 0).any() else np.inf,
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "max_dd_pct": dd,
        "return_pct": ret,
        "long_share": longs / len(r),
        "longs": longs,
        "shorts": len(r) - longs,
    }
