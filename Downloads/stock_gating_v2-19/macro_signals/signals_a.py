"""
Macro signals 1-3: VIX Level, VIX Term Structure, Market Breadth.
Each compute() returns: {name, score (0-100), detail, status, ...metrics}
"""

import numpy as np
import pandas as pd
import data_utils as du


# ── 1. VIX Level ──────────────────────────────────────────────────────────────
def vix_level() -> dict:
    name = "VIX Level"
    try:
        closes = du.get_close_series("^VIX", days=400)
        if len(closes) < 60:
            return _err(name, "VIX history unavailable")

        closes = closes.iloc[-252:] if len(closes) > 252 else closes
        current = float(closes.iloc[-1])

        percentile = float(np.mean(closes < current) * 100)
        score = 100 - percentile  # low VIX -> high score

        if current < 15:
            score += 5
        if current > 30:
            score -= 10
        score = du.clamp(score)

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "current_vix": round(current, 2),
            "percentile": round(percentile, 1),
            "detail": f"VIX {current:.1f} — {percentile:.0f}th pctile of 1yr",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 2. VIX Term Structure ─────────────────────────────────────────────────────
def vix_term_structure() -> dict:
    name = "VIX Term Structure"
    try:
        vix = du.get_close_series("^VIX", days=30)
        vix3m = du.get_close_series("^VIX3M", days=30)
        if len(vix) == 0 or len(vix3m) == 0:
            return _err(name, "VIX/VIX3M unavailable")

        v = float(vix.iloc[-1])
        v3 = float(vix3m.iloc[-1])
        if v3 == 0:
            return _err(name, "VIX3M is zero")

        ratio = v / v3
        score = du.clamp(np.interp(ratio, [0.85, 1.15], [100, 0]))
        regime = "Contango (calm)" if ratio < 1.0 else "Backwardation (stress)"

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "ratio": round(ratio, 3), "vix": round(v, 2), "vix3m": round(v3, 2),
            "detail": f"VIX/VIX3M {ratio:.3f} — {regime}",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 3. Market Breadth ─────────────────────────────────────────────────────────
def market_breadth(watchlist: list | None = None) -> dict:
    """
    Watchlist Breadth: % of the ACTIVE WATCHLIST trading above its 200-day SMA.
    >80% -> 100 score, <30% -> 0. Measures trend participation in your names.
    """
    name = "Watchlist Breadth"
    try:
        if not watchlist:
            watchlist = du.DEFAULT_WATCHLIST
        data = du.get_bulk_history(tuple(watchlist), days=320)
        above = counted = 0
        for t, df in data.items():
            if df.empty or "Close" not in df:
                continue
            closes = df["Close"].dropna()
            if len(closes) < 200:
                continue
            sma200 = closes.rolling(200).mean().iloc[-1]
            if pd.notna(sma200):
                counted += 1
                if closes.iloc[-1] > sma200:
                    above += 1

        if counted == 0:
            return _err(name, "No breadth data")

        pct = above / counted * 100
        score = du.clamp(np.interp(pct, [30, 80], [0, 100]))

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "pct_above_200sma": round(pct, 1), "sample_size": counted,
            "detail": f"{pct:.0f}% of {counted} watchlist names above 200d SMA",
        }
    except Exception as e:
        return _err(name, str(e))


def _err(name, msg):
    return {"name": name, "score": 50.0, "status": "error",
            "detail": f"Error: {msg}", "error": msg}
