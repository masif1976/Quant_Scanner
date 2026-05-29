"""
run_backtest.py — PAGE 3: Visual Backtest & Audit.

Recomputes the daily scanner score and status over the last 252 trading days
for a single ticker, using whichever Strategy Engine is active AND whichever
Macro Regime was in effect on each historical date.

METHODOLOGY
-----------
The backtest uses the SAME institutional flow weighting as the live scanner
(Options Flow 30%, Volume Surge 25%, Momentum 15%, RS 10%, SI 10%, Range 10%)
so it honestly audits the strategy you're actually running.

Short Interest and Options Flow are only available as a *current* point-in-time
snapshot from yfinance — there is no free historical series — so they cannot be
replayed day by day. The backtest pins each to neutral 50 every day, which still
preserves their weights' contribution to the composite.

Regime awareness: a daily macro-score series is fetched from the same engine
that drives Page 1's history chart, and the same regime-blocking rules from
scanner_factors/factors.py are applied per-day. Blocked days are sentineled with
composite=-1 and labeled as "❌ BLOCKED BY REGIME".
"""

from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_utils as du
from scanner_factors import factors
# Import the live scanner's strategy-specific weights directly so the backtest
# automatically tracks any future weight changes — single source of truth.
from run_scanner import TREND_WEIGHTS, MR_WEIGHTS

TREND = factors.TREND
MEAN_REVERSION = factors.MEAN_REVERSION

# Factors with no historical series: pinned to neutral 50 each day.
NEUTRAL_FACTOR = 50.0
BT_NEUTRAL = ("Options Flow", "Short Interest")
BT_REPLAYED = ("Momentum", "Volume Surge", "Relative Strength", "Range Proximity")


def _weights_for(strategy: str) -> dict:
    """Match the LIVE scanner's strategy-specific weighting for an honest
    audit — the backtest scores each historical day with the same model the
    live scanner would apply today."""
    return MR_WEIGHTS if strategy == MEAN_REVERSION else TREND_WEIGHTS

# Conviction Tiers — imported from run_scanner so the backtest stays in
# lock-step with the live scanner's bucketing. If the tiers ever change
# again, the backtest picks it up automatically.
from run_scanner import STATUS_TIERS, normalize_tier_label  # noqa: E402

FWD_WINDOW = 20  # forward-return horizon in trading days


def _status(score: float) -> tuple[str, str]:
    if score is None or (isinstance(score, float) and (np.isnan(score) or score < 0)):
        return "❌ BLOCKED BY REGIME", "#7d8aa5"
    for threshold, label, color in STATUS_TIERS:
        if score >= threshold:
            return label, color
    return STATUS_TIERS[-1][1], STATUS_TIERS[-1][2]


def _clip(x):
    return np.clip(x, 0, 100)


# ── per-day factor series (vectorised), strategy-aware ────────────────────────
def _momentum_series(close: pd.Series, strategy: str) -> pd.Series:
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    if strategy == MEAN_REVERSION:
        # extension below 50-EMA: -10% or lower -> 100, 0% -> 0
        ext = (close - ema50) / ema50 * 100
        score = np.interp(ext, [-10, 0], [100, 0])
    else:
        gap_pct = (ema10 - ema50) / close * 100
        above = ema10 > ema50
        score = np.where(above,
                         _clip(70 + gap_pct * 8),
                         _clip(50 + gap_pct * 8))
    return pd.Series(_clip(score), index=close.index)


def _volume_series(volume: pd.Series) -> pd.Series:
    avg5 = volume.rolling(5).mean()
    avg20 = volume.rolling(20).mean()
    ratio = avg5 / avg20.replace(0, np.nan)
    score = np.interp(ratio.fillna(1.0), [0.7, 2.0], [0, 100])
    return pd.Series(_clip(score), index=volume.index)


def _rel_strength_series(close: pd.Series, spy: pd.Series,
                         strategy: str) -> pd.Series:
    stock_ret = close.pct_change(20) * 100
    spy_ret = spy.reindex(close.index).ffill().pct_change(20) * 100
    rs = (stock_ret - spy_ret).fillna(0)
    if strategy == MEAN_REVERSION:
        score = np.interp(rs, [-15, 5], [100, 0])
    else:
        score = np.interp(rs, [-10, 10], [0, 100])
    return pd.Series(_clip(score), index=close.index)


def _range_series(close: pd.Series, strategy: str) -> pd.Series:
    if strategy == MEAN_REVERSION:
        rolling_low = close.rolling(252, min_periods=60).min()
        prox = close / rolling_low
        score = np.interp(prox.fillna(1.2), [1.0, 1.40], [100, 0])
    else:
        rolling_high = close.rolling(252, min_periods=60).max()
        prox = close / rolling_high
        score = np.interp(prox.fillna(0.85), [0.70, 1.0], [0, 100])
    return pd.Series(_clip(score), index=close.index)


def _regime_series(macro_history: dict | None,
                   index: pd.DatetimeIndex) -> pd.Series:
    """
    Return a daily regime label series aligned to `index`. Falls back to
    SIDEWAYS when no macro history is available (so the backtest still runs).
    """
    if not macro_history or macro_history.get("status") != "ok":
        return pd.Series(factors.SIDEWAYS, index=index)
    hist_dates = pd.to_datetime(macro_history.get("dates", []))
    hist_scores = macro_history.get("composite", [])
    if len(hist_dates) == 0 or len(hist_scores) == 0:
        return pd.Series(factors.SIDEWAYS, index=index)
    score_s = pd.Series(hist_scores, index=hist_dates).reindex(index).ffill().bfill()
    # vectorised regime mapping
    out = pd.Series(factors.SIDEWAYS, index=index)
    out[score_s >= 70] = factors.BULL
    out[score_s < 40] = factors.BEAR
    return out


def run(ticker: str, strategy: str = TREND,
        macro_history: dict | None = None) -> dict:
    """Build the 252-day backtest for one ticker under the active strategy.

    `macro_history` is the dict returned by macro_signals.macro_history.
    regime_timeseries(); the backtest applies the same per-day regime
    blocking and SIDEWAYS cap as the live scanner.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        return {"status": "error", "error": "No ticker provided", "ticker": ticker}

    hist = du.get_history(ticker, days=620)
    spy = du.get_close_series("SPY", days=620)

    if hist.empty or "Close" not in hist or len(hist) < 120:
        return {"status": "error", "ticker": ticker,
                "error": f"Insufficient price history for {ticker}"}
    if len(spy) < 60:
        return {"status": "error", "ticker": ticker,
                "error": "SPY history unavailable for relative strength"}

    close = hist["Close"].dropna()
    volume = (hist["Volume"].dropna() if "Volume" in hist
              else pd.Series(1.0, index=close.index))

    f_mom = _momentum_series(close, strategy)
    f_vol = _volume_series(volume)
    f_rs  = _rel_strength_series(close, spy, strategy)
    f_rp  = _range_series(close, strategy)

    factor_df = pd.DataFrame({
        "Momentum":          f_mom,
        "Volume Surge":      f_vol,
        "Relative Strength": f_rs,
        "Range Proximity":   f_rp,
    }).dropna()

    # ── per-day regime classification ──
    regimes = _regime_series(macro_history, factor_df.index)

    # ── apply regime rules (mirrors scanner_factors/factors.py logic) ──
    bear_mask = (regimes == factors.BEAR)
    sideways_mask = (regimes == factors.SIDEWAYS)

    if strategy == TREND:
        # TREND + BEAR: all factors blocked (will be sentinelled below).
        # TREND + SIDEWAYS: cap non-RS factors at 70.
        for col in ("Momentum", "Volume Surge", "Range Proximity"):
            factor_df.loc[sideways_mask, col] = np.minimum(
                factor_df.loc[sideways_mask, col], 70.0)
    # MR rules are direction-mixed (handled by the underlying _momentum_series
    # branches which already differ per strategy); we don't double-apply caps.

    # ── composite using STRATEGY-SPECIFIC institutional weights ──
    # Replayed factors weighted at their live values; SI + Options pinned to 50
    # (no free historical series). Pulling weights from the active strategy
    # keeps the backtest a faithful audit of the live scanner.
    w = _weights_for(strategy)
    composite = (
        w["Momentum"]          * factor_df["Momentum"]
      + w["Volume Surge"]      * factor_df["Volume Surge"]
      + w["Relative Strength"] * factor_df["Relative Strength"]
      + w["Range Proximity"]   * factor_df["Range Proximity"]
      + w["Short Interest"]    * NEUTRAL_FACTOR
      + w["Options Flow"]      * NEUTRAL_FACTOR
    ).clip(0, 100).round(1)

    # ── regime sentinel for hard-blocked days ──
    # TREND + BEAR -> longs blocked (composite = -1, will show "❌ BLOCKED")
    if strategy == TREND:
        composite[bear_mask] = -1.0

    display = pd.DataFrame({"Close": close, "Score": composite,
                             "Regime": regimes}).dropna(subset=["Close", "Score"])
    if len(display) > 252:
        display = display.iloc[-252:]

    display["Status"] = display["Score"].apply(lambda s: _status(s)[0])
    display["Color"] = display["Score"].apply(lambda s: _status(s)[1])

    fwd_ret_full = (close.shift(-FWD_WINDOW) / close - 1) * 100
    display["FwdRet20"] = fwd_ret_full.reindex(display.index)

    segments = _build_segments(display)
    perf = _performance_table(display)

    best_idx = display["Score"].idxmax()
    worst_idx = display["Score"].idxmin()
    best = _signal_point(display, best_idx)
    worst = _signal_point(display, worst_idx)

    n_blocked = int((display["Score"] < 0).sum())

    return {
        "status": "ok", "ticker": ticker, "strategy": strategy,
        "timestamp": datetime.now().isoformat(),
        "dates": [d.strftime("%Y-%m-%d") for d in display.index],
        "price": display["Close"].round(2).tolist(),
        "score": display["Score"].tolist(),
        "status_label": display["Status"].tolist(),
        "status_color": display["Color"].tolist(),
        "regime": display["Regime"].tolist(),
        "segments": segments,
        "performance": perf,
        "best_signal": best, "worst_signal": worst,
        "current_score": float(display["Score"].iloc[-1]),
        "current_status": display["Status"].iloc[-1],
        "fwd_window": FWD_WINDOW,
        "weights": {
            "replayed_factors": list(BT_REPLAYED),
            "neutral_factors": list(BT_NEUTRAL),
            "neutral_value": NEUTRAL_FACTOR,
            "live_weights": w,
        },
        "n_days": len(display),
        "n_blocked_days": n_blocked,
        "regime_aware": macro_history is not None,
    }


def _signal_point(display: pd.DataFrame, idx) -> dict:
    return {
        "date": idx.strftime("%Y-%m-%d"),
        "score": float(display.loc[idx, "Score"]),
        "status": display.loc[idx, "Status"],
        "price": round(float(display.loc[idx, "Close"]), 2),
        "fwd_ret": _safe_round(display.loc[idx, "FwdRet20"]),
    }


def _build_segments(display: pd.DataFrame) -> list[dict]:
    """Collapse consecutive same-status days into shaded background spans."""
    segments = []
    if display.empty:
        return segments
    statuses = display["Status"].tolist()
    colors = display["Color"].tolist()
    dates = list(display.index)
    seg_start = 0
    for i in range(1, len(statuses) + 1):
        if i == len(statuses) or statuses[i] != statuses[seg_start]:
            segments.append({
                "status": statuses[seg_start], "color": colors[seg_start],
                "start": dates[seg_start].strftime("%Y-%m-%d"),
                "end": dates[i - 1].strftime("%Y-%m-%d"),
                "days": i - seg_start,
            })
            seg_start = i
    return segments


def _performance_table(display: pd.DataFrame) -> dict:
    """
    Average 20-day forward return by directional bias.
    LONG group  = HIGH CONVICTION + TRADABLE
    SHORT group = CAUTION + AVOID / SHORT
    """
    all_labels = ["🟢 HIGH CONVICTION", "🟢 TRADABLE", "🟡 NEUTRAL",
                  "🟠 CAUTION", "🔴 AVOID / SHORT"]
    buckets = {}
    for label in all_labels:
        mask = (display["Status"] == label) & display["FwdRet20"].notna()
        rets = display.loc[mask, "FwdRet20"]
        buckets[label] = {
            "days": int(mask.sum()),
            "avg_fwd_ret": _safe_round(rets.mean()) if len(rets) else None,
            "win_rate": (round(float((rets > 0).mean()) * 100, 1)
                         if len(rets) else None),
        }

    # LONG signals vs SHORT signals
    long_mask = (display["Status"].isin(
        ["🟢 HIGH CONVICTION", "🟢 TRADABLE"])
        & display["FwdRet20"].notna())
    short_mask = (display["Status"].isin(
        ["🟠 CAUTION", "🔴 AVOID / SHORT"])
        & display["FwdRet20"].notna())
    long_rets = display.loc[long_mask, "FwdRet20"]
    short_rets = display.loc[short_mask, "FwdRet20"]

    long_avg = _safe_round(long_rets.mean()) if len(long_rets) else None
    short_avg = _safe_round(short_rets.mean()) if len(short_rets) else None
    # edge: a good system has LONG fwd-returns above SHORT fwd-returns
    edge = (round(long_avg - short_avg, 2)
            if long_avg is not None and short_avg is not None else None)

    return {
        "buckets": buckets,
        "long_avg": long_avg, "long_days": int(long_mask.sum()),
        "short_avg": short_avg, "short_days": int(short_mask.sum()),
        "long_vs_short_edge": edge,
    }


def _safe_round(x, nd: int = 2):
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return None
        return round(float(x), nd)
    except Exception:
        return None


if __name__ == "__main__":
    import json
    print(json.dumps(run("NVDA", TREND), indent=2, default=str)[:1500])
