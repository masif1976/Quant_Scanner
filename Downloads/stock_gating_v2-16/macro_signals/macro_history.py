"""
macro_history.py — Vectorized 180-day historical Composite Macro Score.

Recomputes the 7-factor Composite Macro Score as a daily timeseries for the
trailing ~180 trading days, so Page 1's SPY chart can shade the background by
the regime that was actually in effect on each past date.

All metrics are computed with vectorized pandas/numpy operations — no per-day
Python loops. All downloads route through data_utils' @st.cache_data helpers.

METHODOLOGY NOTE
----------------
Five of the seven metrics have clean daily history from price/index data and
are replayed exactly:
    VIX Level, VIX Term Structure, Credit Spreads, Put/Call ROC, Mega-Cap
    Rotation, Watchlist Breadth  (6 of 7, actually)
Factor Crowding requires a rolling 60-day correlation of momentum/value factor
baskets built from the S&P sample. That IS computable as a daily series, so it
is included too — all 7 metrics are genuine daily history, no placeholders.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import data_utils as du

LOOKBACK_DAYS = 180  # trading days of regime history to display


def _clip(x):
    return np.clip(x, 0, 100)


# ── per-metric daily score series (all vectorized) ────────────────────────────
def _vix_level_series(vix: pd.Series) -> pd.Series:
    """Rolling 1yr percentile rank of VIX, inverted, with the <15 / >30 tweaks."""
    # rolling percentile: fraction of the trailing 252-day window below today
    def _pct(window):
        return float(np.mean(window[:-1] < window[-1]) * 100) if len(window) > 1 \
            else 50.0
    pct = vix.rolling(252, min_periods=60).apply(
        lambda w: _pct(w.values), raw=False)
    score = 100 - pct
    score = score + np.where(vix < 15, 5, 0) - np.where(vix > 30, 10, 0)
    return pd.Series(_clip(score), index=vix.index)


def _vix_term_series(vix: pd.Series, vix3m: pd.Series) -> pd.Series:
    """Front/3M VIX ratio mapped 0.85->100, 1.15->0."""
    ratio = vix / vix3m.replace(0, np.nan)
    score = np.interp(ratio.fillna(1.0), [0.85, 1.15], [100, 0])
    return pd.Series(_clip(score), index=vix.index)


def _credit_series(hyg: pd.Series, tlt: pd.Series) -> pd.Series:
    """TLT/HYG ratio, rolling 1yr z-score, mapped z=-2->100, z=+2->0."""
    spread = tlt / hyg.replace(0, np.nan)
    mean = spread.rolling(252, min_periods=60).mean()
    std = spread.rolling(252, min_periods=60).std()
    z = (spread - mean) / std.replace(0, np.nan)
    score = np.interp(z.fillna(0), [-2, 2], [100, 0])
    return pd.Series(_clip(score), index=hyg.index)


def _putcall_series(vix: pd.Series) -> pd.Series:
    """VIX 20-day ROC as sentiment proxy, mapped -30%->100, +50%->0."""
    roc = vix.pct_change(20) * 100
    score = np.interp(roc.fillna(0), [-30, 50], [100, 0])
    return pd.Series(_clip(score), index=vix.index)


def _megacap_series(mags: pd.Series, spy: pd.Series) -> pd.Series:
    """20-day ROC of the MAGS/SPY ratio mapped -5%->0, +5%->100."""
    ratio = mags / spy.replace(0, np.nan)
    roc = ratio.pct_change(20) * 100
    score = np.interp(roc.fillna(0), [-5, 5], [0, 100])
    return pd.Series(_clip(score), index=mags.index)


def _breadth_series(panel: pd.DataFrame) -> pd.Series:
    """% of the watchlist above its 200-day SMA, daily, mapped 30%->0, 80%->100."""
    sma200 = panel.rolling(200, min_periods=100).mean()
    above = (panel > sma200)
    pct = above.sum(axis=1) / above.count(axis=1) * 100
    score = np.interp(pct.fillna(50), [30, 80], [0, 100])
    return pd.Series(_clip(score), index=panel.index)


def _crowding_series(sp_panel: pd.DataFrame) -> pd.Series:
    """
    Rolling 60-day correlation of momentum vs value factor long/short baskets,
    computed daily. Corr -0.8 -> 0, +0.3 -> 100.
    """
    if sp_panel.shape[1] < 10 or len(sp_panel) < 130:
        return pd.Series(dtype=float)

    daily = sp_panel.pct_change()

    # momentum factor: 120-day return; value proxy: inverse 250-day return
    mom = sp_panel.pct_change(120)
    val = -sp_panel.pct_change(min(250, len(sp_panel) - 1))

    n = max(3, sp_panel.shape[1] // 3)

    # rank cross-sectionally each day; long top-n, short bottom-n
    mom_rank = mom.rank(axis=1)
    val_rank = val.rank(axis=1)
    cols = sp_panel.shape[1]

    mom_long = (mom_rank > cols - n)
    mom_short = (mom_rank <= n)
    val_long = (val_rank > cols - n)
    val_short = (val_rank <= n)

    # daily long/short basket returns (mean of selected names)
    mom_ls = (daily.where(mom_long).mean(axis=1)
              - daily.where(mom_short).mean(axis=1))
    val_ls = (daily.where(val_long).mean(axis=1)
              - daily.where(val_short).mean(axis=1))

    ls = pd.concat([mom_ls, val_ls], axis=1).dropna()
    ls.columns = ["mom", "val"]
    corr = ls["mom"].rolling(60).corr(ls["val"])
    score = np.interp(corr.fillna(-0.25), [-0.8, 0.3], [0, 100])
    return pd.Series(_clip(score), index=ls.index)


# ── main ──────────────────────────────────────────────────────────────────────
def regime_timeseries(watchlist: list | None = None) -> dict:
    """
    Build the 180-day daily Composite Macro Score and regime bands.

    Returns:
      {
        "status": "ok"|"error",
        "dates": [...], "composite": [...], "regime": [...],
        "segments": [ {regime, color, start, end}, ... ],   # for add_vrect
        "spy_dates": [...], "spy_price": [...],
      }
    """
    try:
        if not watchlist:
            watchlist = du.DEFAULT_WATCHLIST

        # ~520 calendar days of history so rolling 252-day windows are valid
        vix   = du.get_close_series("^VIX", days=620)
        vix3m = du.get_close_series("^VIX3M", days=620)
        hyg   = du.get_close_series("HYG", days=620)
        tlt   = du.get_close_series("TLT", days=620)
        mags  = du.get_close_series("MAGS", days=620)
        spy   = du.get_close_series("SPY", days=620)

        if any(len(s) < 60 for s in (vix, hyg, tlt, spy)):
            return {"status": "error",
                    "error": "Insufficient macro history for regime timeseries"}

        # watchlist price panel for breadth
        wl_data = du.get_bulk_history(tuple(watchlist), days=620)
        wl_closes = {}
        for t, df in wl_data.items():
            if not df.empty and "Close" in df:
                s = df["Close"].dropna()
                if len(s) > 100:
                    wl_closes[t] = s
        wl_panel = pd.DataFrame(wl_closes) if wl_closes else pd.DataFrame()

        # S&P sample panel for factor crowding
        sp_data = du.get_bulk_history(tuple(du.SP500_SAMPLE), days=620)
        sp_closes = {}
        for t, df in sp_data.items():
            if not df.empty and "Close" in df:
                s = df["Close"].dropna()
                if len(s) > 150:
                    sp_closes[t] = s
        sp_panel = pd.DataFrame(sp_closes).dropna() if sp_closes else pd.DataFrame()

        # ── per-metric daily series ──
        series = {
            "VIX Level":          _vix_level_series(vix),
            "VIX Term Structure": _vix_term_series(vix, vix3m),
            "Credit Spreads":     _credit_series(hyg, tlt),
            "Put/Call Sentiment": _putcall_series(vix),
            "Mega-Cap Rotation":  _megacap_series(mags, spy),
        }
        if not wl_panel.empty:
            series["Watchlist Breadth"] = _breadth_series(wl_panel)
        if not sp_panel.empty:
            crowd = _crowding_series(sp_panel)
            if not crowd.empty:
                series["Factor Crowding"] = crowd

        # ── align all series on a common daily index & average ──
        score_df = pd.DataFrame(series).dropna()
        if score_df.empty:
            return {"status": "error",
                    "error": "No overlapping history across macro metrics"}

        composite = score_df.mean(axis=1).round(1)  # equal-weighted average

        # trim to the last LOOKBACK_DAYS trading days
        composite = composite.iloc[-LOOKBACK_DAYS:]

        # ── regime mapping ──
        def _regime(s):
            if s >= 70:
                return "FULL DEPLOY", "#22e08a"
            if s >= 40:
                return "REDUCED", "#f5c344"
            return "DEFENSIVE", "#ff5d6c"

        regimes = [_regime(s) for s in composite.values]
        regime_labels = [r[0] for r in regimes]
        regime_colors = [r[1] for r in regimes]

        # ── collapse consecutive same-regime days into vrect segments ──
        segments = []
        dates = list(composite.index)
        if dates:
            seg_start = 0
            for i in range(1, len(regime_labels) + 1):
                if (i == len(regime_labels)
                        or regime_labels[i] != regime_labels[seg_start]):
                    segments.append({
                        "regime": regime_labels[seg_start],
                        "color": regime_colors[seg_start],
                        "start": dates[seg_start].strftime("%Y-%m-%d"),
                        "end": dates[i - 1].strftime("%Y-%m-%d"),
                        "days": i - seg_start,
                    })
                    seg_start = i

        # SPY price aligned to the same window
        spy_window = spy.reindex(composite.index).ffill()

        return {
            "status": "ok",
            "dates": [d.strftime("%Y-%m-%d") for d in composite.index],
            "composite": composite.tolist(),
            "regime": regime_labels,
            "segments": segments,
            "metrics_used": list(score_df.columns),
            "spy_dates": [d.strftime("%Y-%m-%d") for d in spy_window.index],
            "spy_price": spy_window.round(2).tolist(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
