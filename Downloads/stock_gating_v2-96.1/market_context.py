"""
market_context.py — Market-distance-from-peak indicators for Page 1.

Why this exists:
  The existing macro composite measures regime via 7 signals (VIX, credit
  spreads, breadth, etc.) but doesn't directly measure "how far has SPY
  traveled from its last all-time high?" — which is a distinct piece of
  context.

  A market that just hit ATH is a different posture than a market that's
  been trading sideways below ATH for 6 months. Both might score the same
  on the composite, but the implications for positioning differ.

  This module computes a single, focused metric:
    - Days since SPY's most recent all-time high (within history we have)
    - Current drawdown from that high

  Used on Page 1 below the composite gauge as a small contextual indicator.
  Not part of the scoring pipeline — purely informational.

Honest limitations:
  - "All-time high" here means "highest close in the last ~5 years of data
    we have on disk" — not the actual ATH if SPY made one before then.
    For practical purposes this is fine; the most recent multi-year high is
    what matters for current positioning.
  - Uses adjusted close to handle SPY's dividends/splits consistently.
  - If yfinance fails (rate limits, "Invalid Crumb" issues), the indicator
    is hidden rather than showing stale or misleading data.
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pandas as pd

try:
    import streamlit as st
    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False

import data_utils as du


def _cached(ttl: int = 3600):
    """st.cache_data if available, simple in-process cache otherwise."""
    if _HAS_STREAMLIT and hasattr(st, "cache_data"):
        return st.cache_data(ttl=ttl, show_spinner=False)
    import time
    def deco(fn):
        cache = {}
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            if key in cache:
                v, t = cache[key]
                if now - t < ttl:
                    return v
            v = fn(*args, **kwargs)
            cache[key] = (v, now)
            return v
        return wrapper
    return deco


@_cached(ttl=3600)
def get_spy_ath_context(lookback_years: int = 5) -> dict | None:
    """Compute SPY distance-from-all-time-high context.

    Args:
        lookback_years: how far back to consider for "all-time high". 5 years
                        is the practical default — older highs are rarely
                        relevant to current positioning, and SPY had multiple
                        secular tops we'd lump together unhelpfully.

    Returns:
        dict with:
          - current_price:  most recent SPY close (float)
          - ath_price:      highest close in lookback window (float)
          - ath_date:       date of the ATH (pd.Timestamp)
          - drawdown_pct:   current % below ATH (always ≤ 0; 0 = at ATH)
          - days_since_ath: trading days since the ATH date (int)
          - at_ath:         True if drawdown < 0.5% (treat near-ATH as at-ATH)
          - status:         "ok" | "stale" | "error"
        None if SPY data couldn't be fetched at all.

    Edge cases handled:
      - SPY data missing entirely → returns None
      - Empty history → returns None
      - All values NaN → returns None
      - Date arithmetic issues (very fresh ATH on first trading day of window)
        → days_since_ath = 0
    """
    try:
        days = lookback_years * 365 + 30  # convert to calendar days w/ buffer
        df = du.get_history("SPY", days=days)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None

    closes = df["Close"].dropna()
    if closes.empty:
        return None

    # ATH = highest close in the window
    ath_price = float(closes.max())
    ath_date = closes.idxmax()
    current_price = float(closes.iloc[-1])
    current_date = closes.index[-1]

    if ath_price <= 0 or current_price <= 0:
        return None

    drawdown_pct = (current_price - ath_price) / ath_price * 100  # ≤ 0

    # Trading-days-since-ATH: use the index because it's daily trading data
    try:
        days_since = closes.index.get_loc(current_date) - closes.index.get_loc(ath_date)
    except (KeyError, ValueError):
        days_since = 0

    # "At ATH" if within 0.5% — accounts for intraday drift on the ATH day
    at_ath = abs(drawdown_pct) < 0.5

    return {
        "current_price": current_price,
        "ath_price": ath_price,
        "ath_date": ath_date,
        "drawdown_pct": drawdown_pct,
        "days_since_ath": int(days_since),
        "at_ath": at_ath,
        "status": "ok",
    }


def format_ath_caption(ctx: dict | None) -> str | None:
    """Build a one-line caption summarizing SPY ATH context. Returns None
    if the data isn't available — caller should hide the section entirely
    rather than show "ATH: unknown" or similar.

    Examples of what this returns:
      "SPY at all-time high ($612.40 · 0 days since last ATH)"
      "SPY −2.3% from ATH (12 trading days · last ATH 2026-05-12 at $612.40)"
      "SPY −18.7% from ATH (98 trading days · last ATH 2026-01-15 at $612.40)"
    """
    if not ctx or ctx.get("status") != "ok":
        return None
    drawdown = ctx["drawdown_pct"]
    ath_price = ctx["ath_price"]
    ath_date = ctx["ath_date"]
    days_since = ctx["days_since_ath"]
    current = ctx["current_price"]

    if ctx.get("at_ath"):
        return (f"SPY at all-time high (${current:.2f} · "
                f"{days_since} days since last ATH)")

    return (f"SPY {drawdown:+.2f}% from ATH ({days_since} trading days · "
            f"last ATH {ath_date.strftime('%Y-%m-%d')} at ${ath_price:.2f})")


# ── gap-down / panic detector ────────────────────────────────────────────────
GAP_DOWN_THRESHOLD_PCT = -5.0   # daily change of -5% or worse → flag


def is_gap_down(quote: dict, threshold_pct: float = GAP_DOWN_THRESHOLD_PCT) -> bool:
    """True if the quote's daily change is bad enough to flag.

    Used to overlay a 🚨 badge on Page 1 sparkline cards when something
    has happened to a ticker overnight or intraday that the user should
    notice before placing a trade.

    Args:
        quote: dict from data_utils.get_live_quotes() — expected fields
               are 'change_pct' (float) and 'status' ('ok' if usable).
        threshold_pct: how bad the move must be. Default -5% catches
               news-driven moves while ignoring normal daily volatility.

    Returns False on missing/invalid data (no false alarms).

    Why -5% specifically:
      - Real news (downgrades, earnings misses, guide-downs) commonly
        produces 5-15% gaps
      - Normal daily volatility for large-caps is 1-2% standard deviation;
        a 5% move is 2.5+ sigma which is rare enough to be informative
      - Tech and biotech can have routine 3-4% days, so going below -5%
        avoids constant false positives on volatile names
    """
    if not quote or quote.get("status") != "ok":
        return False
    chg = quote.get("change_pct")
    if chg is None:
        return False
    try:
        return float(chg) <= threshold_pct
    except (TypeError, ValueError):
        return False
