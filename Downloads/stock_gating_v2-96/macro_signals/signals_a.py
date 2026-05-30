"""
Macro signals 1-3: VIX Level, VIX Term Structure, Sector Breadth.
Each compute() returns: {name, score (0-100), detail, status, ...metrics}
"""

import numpy as np
import pandas as pd
import data_utils as du


# 11 Select Sector SPDR ETFs — broad-market sector breadth basket
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
               "XLP", "XLRE", "XLU", "XLV", "XLY")


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


# ── 3. Sector Breadth ─────────────────────────────────────────────────────────
def sector_breadth(watchlist: list | None = None,
                   horizon: str = "Swing Trade System") -> dict:
    """
    Sector Breadth: of the 11 SPDR sector ETFs, how many are trading STRICTLY
    above their N-day SMA (N selected by horizon — SWING=50, LONG_TERM=200).
    Normalized linearly to 0-100 as (count / 11) * 100.

    This replaces the old Watchlist-Breadth metric, which was sensitive to
    which tickers the user picked. The 11 sector ETFs span the broad market
    consistently across all watchlists — same macro signal for every user.

    The `watchlist` parameter is accepted (and ignored) for API compatibility
    with the previous market_breadth signature.
    """
    name = "Sector Breadth"
    # late-import to avoid circular deps (factors imports data_utils which
    # is imported by macro_signals too)
    try:
        from scanner_factors.factors import lookback
        sma_window = lookback(horizon, "sector_breadth_sma")
    except ImportError:
        sma_window = 50  # safe default

    try:
        # Fetch enough history for the chosen SMA window to be fully populated.
        # 50-SMA needs ~120d to be safe; 200-SMA needs ~280d.
        history_days = max(280, sma_window + 50)
        data = du.get_bulk_history(SECTOR_ETFS, days=history_days)

        above = counted = 0
        details = []
        for t in SECTOR_ETFS:
            df = data.get(t, pd.DataFrame())
            if df.empty or "Close" not in df:
                continue
            closes = df["Close"].dropna()
            if len(closes) < sma_window:
                continue
            sma = closes.rolling(sma_window).mean().iloc[-1]
            last = closes.iloc[-1]
            if pd.notna(sma):
                counted += 1
                if last > sma:                       # STRICT '>' per spec
                    above += 1
                    details.append(f"{t}+")
                else:
                    details.append(f"{t}-")

        if counted == 0:
            return _err(name, "No sector ETF data available")

        # Normalize linearly per spec: (count / 11) * 100.
        # Use `counted` (not literal 11) so the score still computes if 1-2
        # ETFs failed to load — degrades gracefully rather than throwing.
        pct_above = above / counted * 100
        score = int(round(pct_above))

        return {
            "name": name, "score": score, "status": "ok",
            "count_above_sma": above,
            "count_total": counted,
            "pct_above_sma": round(pct_above, 1),
            "sma_window": sma_window,
            "detail": f"{above}/{counted} sector ETFs above {sma_window}-SMA "
                      f"({pct_above:.0f}%)",
            "sectors": details,
        }
    except Exception as e:
        return _err(name, str(e))


def _err(name, msg):
    return {"name": name, "score": 50.0, "status": "error",
            "detail": f"Error: {msg}", "error": msg}
