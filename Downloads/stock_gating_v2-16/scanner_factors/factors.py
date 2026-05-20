"""
factors.py — The 6 scanner factors (dual-strategy) + earnings overlay.

Every factor branches on the active strategy engine:

  TREND       — reward strength, momentum, proximity to highs, calm positioning
  MEAN_REVERSION — reward capitulation, oversold extremes, fear, squeeze setups

Each factor returns {score 0-100, raw, detail}. `raw` is the value used for
cross-sectional ranking in run_scanner.py.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import data_utils as du

TREND = "Trend-Following"
MEAN_REVERSION = "Mean Reversion"


# ── Factor 1: Momentum ────────────────────────────────────────────────────────
def momentum(df: pd.DataFrame, strategy: str = TREND) -> dict:
    """
    TREND          : 100 if 10-EMA > 50-EMA, else scaled by the gap.
    MEAN_REVERSION : 100 if price is extended >10% BELOW the 50-EMA (capitulation),
                     0 if at/above it.
    """
    if df.empty or "Close" not in df or len(df) < 55:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    close = df["Close"].dropna()
    ema10 = du.ema(close, 10)
    ema50 = du.ema(close, 50)
    cur = float(close.iloc[-1])
    e10 = float(ema10.iloc[-1])
    e50 = float(ema50.iloc[-1])

    if strategy == MEAN_REVERSION:
        # how far below the 50-EMA is price? extension in %
        ext = (cur - e50) / e50 * 100  # negative = below
        # -10% or lower -> 100 ; 0% (at EMA) -> 0
        score = du.clamp(np.interp(ext, [-10, 0], [100, 0]))
        if ext < -10:
            score = 100.0
        detail = f"Price {ext:+.1f}% vs 50-EMA (capitulation setup)"
        raw = -ext  # more negative extension -> higher raw
    else:
        # trend: EMA10 above EMA50 = uptrend
        gap_pct = (e10 - e50) / cur * 100
        if e10 > e50:
            score = du.clamp(70 + gap_pct * 8, 70, 100)
        else:
            score = du.clamp(50 + gap_pct * 8, 0, 50)
        detail = f"EMA10 {'>' if e10>e50 else '<'} EMA50 (gap {gap_pct:+.1f}%)"
        raw = gap_pct

    return {"score": round(float(score), 1), "raw": round(float(raw), 3),
            "detail": detail}


# ── Factor 2: Volume Surge ────────────────────────────────────────────────────
def volume_surge(df: pd.DataFrame, strategy: str = TREND) -> dict:
    """
    5d/20d average-volume ratio. Higher ratio -> higher score in BOTH engines:
      TREND          : volume confirms the move.
      MEAN_REVERSION : volume spike on a downtrend signals seller exhaustion.
    """
    if df.empty or "Volume" not in df or len(df) < 21:
        return {"score": 50.0, "raw": 1.0, "detail": "insufficient data"}

    vol = df["Volume"].dropna()
    avg5 = float(vol.iloc[-5:].mean())
    avg20 = float(vol.iloc[-20:].mean())
    if avg20 == 0:
        return {"score": 50.0, "raw": 1.0, "detail": "zero 20d volume"}

    ratio = avg5 / avg20
    score = du.clamp(np.interp(ratio, [0.7, 2.0], [0, 100]))
    note = "exhaustion" if strategy == MEAN_REVERSION else "confirmation"
    return {"score": round(score, 1), "raw": round(ratio, 2),
            "detail": f"5d/20d volume {ratio:.2f}x ({note})"}


# ── Factor 3: Relative Strength vs SPY ────────────────────────────────────────
def relative_strength(df: pd.DataFrame, spy_close: pd.Series,
                      strategy: str = TREND) -> dict:
    """
    20-day stock return minus 20-day SPY return (pp).
      TREND          : outperformance -> high score.
      MEAN_REVERSION : extreme UNDERperformance -> high score.
    """
    if df.empty or "Close" not in df or len(df) < 21 or len(spy_close) < 21:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    close = df["Close"].dropna()
    stock_ret = (close.iloc[-1] / close.iloc[-21] - 1) * 100
    spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-21] - 1) * 100
    rs = float(stock_ret - spy_ret)

    if strategy == MEAN_REVERSION:
        # -15pp (deep underperformance) -> 100 ; +5pp -> 0
        score = du.clamp(np.interp(rs, [-15, 5], [100, 0]))
        detail = f"20d RS vs SPY {rs:+.1f}pp (underperformance reward)"
        raw = -rs
    else:
        # -10pp -> 0 ; +10pp -> 100
        score = du.clamp(np.interp(rs, [-10, 10], [0, 100]))
        detail = f"20d RS vs SPY {rs:+.1f}pp"
        raw = rs

    return {"score": round(score, 1), "raw": round(float(raw), 2),
            "detail": detail}


# ── Factor 4: Range Proximity ─────────────────────────────────────────────────
def range_proximity(df: pd.DataFrame, strategy: str = TREND) -> dict:
    """
    TREND          : price / 52-week HIGH  (closer to high -> high score).
    MEAN_REVERSION : price / 52-week LOW   (closer to low  -> high score).
    """
    if df.empty or "Close" not in df or len(df) < 60:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    close = df["Close"].dropna()
    window = close.iloc[-252:] if len(close) > 252 else close
    cur = float(close.iloc[-1])
    hi = float(window.max())
    lo = float(window.min())

    if strategy == MEAN_REVERSION:
        if lo == 0:
            return {"score": 50.0, "raw": 0.0, "detail": "zero 52w low"}
        prox = cur / lo  # 1.0 = at the low, higher = further above
        # at the low (1.0) -> 100 ; 1.40 (40% above low) -> 0
        score = du.clamp(np.interp(prox, [1.0, 1.40], [100, 0]))
        detail = f"{(prox-1)*100:.0f}% above 52w low"
        raw = -prox
    else:
        if hi == 0:
            return {"score": 50.0, "raw": 0.0, "detail": "zero 52w high"}
        prox = cur / hi
        score = du.clamp(np.interp(prox, [0.70, 1.0], [0, 100]))
        detail = f"{prox*100:.0f}% of 52w high"
        raw = prox

    return {"score": round(score, 1), "raw": round(float(raw), 3),
            "detail": detail}


# ── Factor 5: Short Interest ──────────────────────────────────────────────────
def short_interest(ticker: str, strategy: str = TREND) -> dict:
    """
    MoM change in short interest.
      TREND          : DECLINING shorts -> high score (capitulation of bears).
      MEAN_REVERSION : ELEVATED / rising shorts -> high score (squeeze fuel).
    """
    cur = prior = None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).get_info()
        cur = info.get("sharesShort")
        prior = info.get("sharesShortPriorMonth")
    except Exception:
        pass

    if not cur or not prior or prior == 0:
        return {"score": 50.0, "raw": 0.0, "status": "unavailable",
                "detail": "short interest data unavailable"}

    pct_change = (cur - prior) / prior * 100  # negative = shorts declining

    if strategy == MEAN_REVERSION:
        # rising shorts -> squeeze potential. +30% -> 100 ; -30% -> 0
        score = du.clamp(np.interp(pct_change, [-30, 30], [0, 100]))
        direction = "rising (squeeze fuel)" if pct_change > 0 else "declining"
        raw = pct_change
    else:
        # declining shorts -> bullish. -30% -> 100 ; +30% -> 0
        score = du.clamp(np.interp(pct_change, [-30, 30], [100, 0]))
        direction = "declining (bullish)" if pct_change < 0 else "rising"
        raw = -pct_change

    return {"score": round(score, 1), "raw": round(float(raw), 1), "status": "ok",
            "detail": f"Short interest {direction} {pct_change:+.0f}% MoM"}


# ── Factor 6: Options Flow & Volatility ───────────────────────────────────────
def options_flow(ticker: str, df: pd.DataFrame, strategy: str = TREND) -> dict:
    """
    IV percentile + put/call open-interest ratio.
      TREND          : low IV + call-heavy OI = calm, favorable -> high score.
      MEAN_REVERSION : high IV + put-heavy OI = peak fear -> high score.
    """
    opt = du.get_options_metrics(ticker)
    pc_ratio = opt.get("pc_oi_ratio")
    avg_iv = opt.get("avg_iv")

    iv_pctile = None
    if avg_iv is not None and not df.empty and "Close" in df and len(df) > 60:
        rv = df["Close"].pct_change().rolling(20).std() * np.sqrt(252)
        rv = rv.dropna()
        if len(rv) > 30:
            iv_pctile = float(np.mean(rv < avg_iv) * 100)

    if pc_ratio is None and iv_pctile is None:
        return {"score": 50.0, "raw": 0.0, "status": "unavailable",
                "detail": "options data unavailable"}

    if strategy == MEAN_REVERSION:
        # high IV -> high score
        iv_score = du.clamp(iv_pctile) if iv_pctile is not None else 50.0
        # put-heavy (high P/C ratio) -> fear -> high score: 0.5->0, 1.5->100
        pc_score = (du.clamp(np.interp(pc_ratio, [0.5, 1.5], [0, 100]))
                    if pc_ratio is not None else 50.0)
    else:
        # low IV percentile -> favorable -> high score
        iv_score = du.clamp(100 - iv_pctile) if iv_pctile is not None else 50.0
        # call-heavy (low P/C ratio) -> bullish: 0.5->100, 1.5->0
        pc_score = (du.clamp(np.interp(pc_ratio, [0.5, 1.5], [100, 0]))
                    if pc_ratio is not None else 50.0)

    score = round((iv_score + pc_score) / 2, 1)
    bits = []
    if iv_pctile is not None:
        bits.append(f"IV pctile {iv_pctile:.0f}%")
    if pc_ratio is not None:
        bits.append(f"P/C OI {pc_ratio:.2f}")
    return {"score": score, "raw": pc_ratio or 0.0, "status": "ok",
            "iv_percentile": round(iv_pctile, 1) if iv_pctile is not None else None,
            "pc_oi_ratio": pc_ratio,
            "detail": " · ".join(bits)}



# ── Risk Overlay: Earnings Proximity ──────────────────────────────────────────
def earnings_proximity(ticker: str) -> dict:
    """Days until next earnings. Within ~5 trading days => HOLD flag."""
    ed = du.get_earnings_date(ticker)
    if ed is None:
        return {"next_earnings": None, "trading_days_away": None,
                "flag": False, "detail": "earnings date unavailable"}

    today = pd.Timestamp.today().normalize()
    ed_norm = pd.Timestamp(ed).normalize()
    cal_days = (ed_norm - today).days
    trading_days = int(round(cal_days * 5 / 7)) if cal_days >= 0 else cal_days

    flag = 0 <= cal_days <= 7  # ~5 trading days
    return {
        "next_earnings": ed_norm.strftime("%Y-%m-%d"),
        "trading_days_away": trading_days,
        "calendar_days_away": cal_days,
        "flag": flag,
        "detail": f"Earnings in ~{trading_days} trading days" if cal_days >= 0
                  else "Earnings date in past / TBC",
    }
