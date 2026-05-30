"""
trade_plan.py — Decision-support trade plan for signal-based projections.

When the scanner produces a TRADABLE signal, traders need to know:
  - Where to set a protective stop (loss limit)
  - Where to take initial profits (target)
  - How many shares to buy with a fixed dollar amount
  - Whether the risk/reward ratio justifies taking the trade

This module computes those projections. It does NOT enforce them — the
backtest still uses its 20-day mechanical exit. The trade plan is
displayed on Page 2 alongside each signal as decision support.

Design choices:

  Stop methodology: ATR-based, NOT fixed-%.
    Reasoning: a 7% stop is too tight for high-volatility names like RIVN
    (whose daily ATR is ~6%) and too loose for stable names like AAPL
    (whose daily ATR is ~1%). ATR scales the stop to the stock's own
    volatility, producing comparable risk profiles across the watchlist.

  ATR window: 14 trading days. Conventional choice — long enough to
    smooth noise, short enough to react to regime changes.

  Strategy-specific multipliers:
    TREND LONG: 2.0×ATR stop, 3.0×ATR target → 1.5:1 reward/risk
    MR LONG:    1.5×ATR stop, 2.0×ATR target → 1.33:1 reward/risk
      (tighter on MR because mean-reverters that fail tend to fail fast)

  Position sizing: $10,000 equal-dollar per trade.
    Matches the backtest sizing. A more sophisticated alternative would
    be "$200 risk per trade" → size = $200 / stop_distance_per_share,
    but that's a different model. Equal-dollar is consistent with how
    the backtest computes results and how Page 4 reports P&L.

Honest caveats:
  - Stop levels assume no gap. Real fills on gap-down opens will be worse.
  - ATR snapshots current volatility; regime shifts blow through it.
  - These are recommendations, not enforced limits. Manual broker setup
    required to actually use them.
"""
from __future__ import annotations
from typing import Literal

import pandas as pd

import data_utils as du


# Default position size — matches run_portfolio_backtest.DEFAULT_TARGET_DOLLARS
DEFAULT_POSITION_DOLLARS = 10_000.0

# ATR multipliers by strategy/direction
_MULTIPLIERS = {
    # (strategy, direction) → (stop_atr_mult, target_atr_mult)
    ("trend", "LONG"):  (2.0, 3.0),
    ("trend", "SHORT"): (2.0, 3.0),
    ("mr",    "LONG"):  (1.5, 2.0),
    ("mr",    "SHORT"): (1.5, 2.0),
}


def compute_atr(history: pd.DataFrame, window: int = 14) -> float | None:
    """Compute the most recent ATR(window) value from an OHLC history.

    True Range for each day = max of:
      - High - Low
      - |High - prev_Close|
      - |Low - prev_Close|

    ATR = simple moving average of True Range over `window` days.
    (Wilder's smoothing is the textbook version; SMA is a common simplification
     that's close enough for stop placement.)

    Returns the latest ATR in price units, or None if data insufficient.
    """
    if history is None or history.empty:
        return None
    needed = {"High", "Low", "Close"}
    if not needed.issubset(history.columns):
        return None
    if len(history) < window + 1:
        return None
    df = history[["High", "Low", "Close"]].dropna().tail(window + 5).copy()
    if len(df) < window + 1:
        return None
    df["prev_close"] = df["Close"].shift(1)
    df["tr1"] = df["High"] - df["Low"]
    df["tr2"] = (df["High"] - df["prev_close"]).abs()
    df["tr3"] = (df["Low"]  - df["prev_close"]).abs()
    df["TR"]  = df[["tr1", "tr2", "tr3"]].max(axis=1)
    atr = df["TR"].tail(window).mean()
    if atr is None or pd.isna(atr) or atr <= 0:
        return None
    return float(atr)


def build_trade_plan(ticker: str,
                      entry_price: float,
                      strategy: Literal["trend", "mr"],
                      direction: Literal["LONG", "SHORT"],
                      position_dollars: float = DEFAULT_POSITION_DOLLARS,
                      history: pd.DataFrame | None = None,
                      ) -> dict | None:
    """Build a complete trade plan for a signal.

    Args:
        ticker: stock symbol (used only for fetching history if not provided)
        entry_price: the price at which the trade would be entered (current price)
        strategy: "trend" or "mr" — different ATR multipliers per strategy
        direction: "LONG" or "SHORT" — determines stop above/below entry
        position_dollars: target $ per trade (default $10K to match backtest)
        history: optional pre-fetched OHLC history. If None, fetched here.

    Returns:
        Dict with:
          - entry (float)
          - stop (float)              — protective stop price
          - target (float)             — first profit target
          - risk_per_share (float)     — entry → stop distance
          - reward_per_share (float)   — entry → target distance
          - risk_pct (float)           — stop % from entry
          - reward_pct (float)         — target % from entry
          - rr_ratio (float)           — reward / risk
          - shares (int)               — position size in shares
          - position_dollars (float)   — actual notional
          - max_loss_dollars (float)   — total $ at risk if stopped out
          - max_gain_dollars (float)   — total $ gained at first target
          - atr (float)                — ATR(14) used
          - method (str)               — short description
        None if data insufficient (no ATR available, bad entry price, etc.)
    """
    if entry_price is None or entry_price <= 0:
        return None

    if history is None:
        try:
            history = du.get_history(ticker, days=60)
        except Exception:
            history = None
    atr = compute_atr(history, window=14)
    if atr is None:
        return None

    key = (strategy.lower(), direction.upper())
    if key not in _MULTIPLIERS:
        return None
    stop_mult, target_mult = _MULTIPLIERS[key]

    risk_per_share = stop_mult * atr
    reward_per_share = target_mult * atr

    if direction.upper() == "LONG":
        stop = entry_price - risk_per_share
        target = entry_price + reward_per_share
    else:  # SHORT
        stop = entry_price + risk_per_share
        target = entry_price - reward_per_share

    # Guard against pathological values: stop below zero on penny stocks
    # would be nonsense. Cap at $0.01.
    if stop <= 0:
        stop = 0.01
        risk_per_share = entry_price - stop

    risk_pct    = (risk_per_share   / entry_price) * 100
    reward_pct  = (reward_per_share / entry_price) * 100
    rr_ratio    = reward_per_share / risk_per_share if risk_per_share > 0 else 0

    shares = int(position_dollars // entry_price) if entry_price > 0 else 0
    actual_notional = shares * entry_price
    max_loss   = shares * risk_per_share
    max_gain   = shares * reward_per_share

    return {
        "entry":              round(entry_price, 2),
        "stop":               round(stop, 2),
        "target":             round(target, 2),
        "risk_per_share":     round(risk_per_share, 2),
        "reward_per_share":   round(reward_per_share, 2),
        "risk_pct":           round(risk_pct, 2),
        "reward_pct":         round(reward_pct, 2),
        "rr_ratio":           round(rr_ratio, 2),
        "shares":             shares,
        "position_dollars":   round(actual_notional, 2),
        "max_loss_dollars":   round(max_loss, 2),
        "max_gain_dollars":   round(max_gain, 2),
        "atr":                round(atr, 2),
        "method":             f"{stop_mult}×ATR(14) stop, "
                              f"{target_mult}×ATR(14) target",
    }
