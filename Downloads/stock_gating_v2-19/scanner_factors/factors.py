"""
factors.py — The 6 scanner factors (dual-strategy, regime-aware) + earnings.

Every factor branches on the active strategy engine AND the current macro
regime, derived from the 0-100 Composite Macro Score:

  BULL REGIME      (macro_score >= 70) — reward upside, block shorts
  SIDEWAYS REGIME  (40 <= macro_score < 70) — high selectivity, cap mid scores
  BEAR REGIME      (macro_score < 40) — reward downside, block longs

Each factor returns {score 0-100, raw, detail}. A "BLOCKED" factor score of 0
drives the composite down so the offending direction lands in the lowest tier
(STRONG SHORT) — see _scan_one in run_scanner.py for the composite math.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import data_utils as du

TREND = "Trend-Following"
MEAN_REVERSION = "Mean Reversion"

# ── regime classifier (single source of truth) ────────────────────────────────
BULL = "BULL REGIME"
SIDEWAYS = "SIDEWAYS REGIME"
BEAR = "BEAR REGIME"


def regime_of(macro_score) -> str:
    """Map a 0-100 macro composite to BULL / SIDEWAYS / BEAR regime."""
    if macro_score is None:
        return SIDEWAYS                # safest default if macro is unavailable
    if macro_score >= 70:
        return BULL
    if macro_score >= 40:
        return SIDEWAYS
    return BEAR


def _bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0):
    """Return (sma, upper_band, lower_band, last_z_score) over the close series."""
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = sma + n_std * std
    lower = sma - n_std * std
    last = float(close.iloc[-1])
    last_sma = float(sma.iloc[-1])
    last_std = float(std.iloc[-1]) if std.iloc[-1] > 0 else 1.0
    z = (last - last_sma) / last_std
    return float(last_sma), float(upper.iloc[-1]), float(lower.iloc[-1]), z


def _rsi(close: pd.Series, period: int = 14) -> float:
    """14-day RSI of the close series; returns 50.0 if insufficient data."""
    s = du.rsi(close, period=period).dropna()
    return float(s.iloc[-1]) if len(s) else 50.0


def _blocked(reason: str, raw: float = 0.0) -> dict:
    """Return a regime-blocked factor result: score 0 + tagged detail string."""
    return {"score": 0.0, "raw": float(raw),
            "detail": f"[BLOCKED: {reason}]"}


# ── Factor 1: Momentum (regime-aware) ─────────────────────────────────────────
def momentum(df: pd.DataFrame, strategy: str = TREND,
             macro_score: float | None = None) -> dict:
    """
    TREND          : score reflects upward momentum.
        BULL  -> unrestricted (reward EMA10>EMA50 + breakout strength)
        SIDE  -> capped at 70 unless top-tier RS (cap applied externally)
        BEAR  -> BLOCKED: shorts only. score=0 for any long setup.

    MEAN_REVERSION : score reflects a buyable pullback OR shortable bounce
        depending on regime.
        BULL  -> shallow dip: RSI<=40 OR price touches 20-SMA -> long reward
        SIDE  -> standard oversold: RSI<=30 OR price <= lower BB
        BEAR  -> extreme capitulation: RSI<=20 AND price 3+ std below 20-SMA
    """
    if df.empty or "Close" not in df or len(df) < 55:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    close = df["Close"].dropna()
    regime = regime_of(macro_score)
    ema10 = du.ema(close, 10)
    ema50 = du.ema(close, 50)
    cur = float(close.iloc[-1])
    e10 = float(ema10.iloc[-1])
    e50 = float(ema50.iloc[-1])
    rsi = _rsi(close)
    sma20, _, lower_bb, z20 = _bollinger(close, 20, 2.0)

    if strategy == MEAN_REVERSION:
        # MEAN_REVERSION expresses long signals via HIGH scores and short
        # signals via LOW scores. The composite then lands in the right
        # Directional Bias tier (80+ = STRONG LONG, 0-19 = STRONG SHORT).
        if regime == BULL:
            # Shorts BLOCKED. Long-only: shallow dip RSI<=40 OR price <= 20-SMA
            # Detect a "bounce-to-short" setup -> if present, block it.
            overbought_bounce = rsi >= 70 or cur >= sma20 + 2 * (cur - sma20)
            if overbought_bounce and rsi >= 70:
                return _blocked(BULL, raw=-rsi)
            shallow_dip = rsi <= 40 or cur <= sma20
            if shallow_dip:
                # tighter RSI / closer to SMA -> stronger setup
                rsi_part = du.clamp(np.interp(rsi, [25, 40], [100, 60]))
                score = float(rsi_part)
                detail = (f"BULL shallow-dip long · RSI {rsi:.0f}"
                          f"{' (at 20-SMA)' if cur<=sma20 else ''}")
            else:
                # No dip and not overbought-blocked: park at neutral (HOLD/CASH).
                # The old value of 35 incorrectly fell into WATCH SHORT tier,
                # which is the wrong direction for a long strategy in a bull.
                score = 50.0
                detail = (f"BULL: no dip yet, no setup (RSI {rsi:.0f}, price "
                          f"{(cur/sma20-1)*100:+.1f}% vs 20-SMA)")
            raw = -rsi
        elif regime == BEAR:
            # BOTH directions allowed under BEAR — opposite extremes:
            # Longs require capitulation: RSI<=20 OR 2.5+σ below 20-SMA
            #   (loosened from the original RSI<=20 AND z<=-3, which an audit
            #    showed never fires in real data — see findings #15)
            # Shorts trigger on bounces: RSI>=50 OR price touched 20-SMA from below
            extreme_long = (rsi <= 20) or (z20 <= -2.5)
            bounce_short = (rsi >= 50) or (cur >= sma20 and z20 > -0.5)
            if extreme_long:
                score = 100.0
                detail = (f"BEAR capitulation long · RSI {rsi:.0f}, "
                          f"{z20:.1f}σ vs 20-SMA")
                raw = -z20
            elif bounce_short:
                # short setup -> low score (drives composite into SHORT tier)
                short_strength = du.clamp(np.interp(rsi, [50, 70], [40, 0]))
                score = float(short_strength)
                detail = (f"BEAR bounce-to-short · RSI {rsi:.0f}"
                          f"{' (at 20-SMA from below)' if cur>=sma20 else ''}")
                raw = rsi  # higher RSI = stronger short signal
            else:
                score = du.clamp(np.interp(z20, [-3.0, 0.0], [50, 25]))
                detail = (f"BEAR: no extreme (RSI {rsi:.0f}, "
                          f"{z20:.1f}σ vs 20-SMA)")
                raw = -z20
        else:  # SIDEWAYS
            # Both directions allowed at standard thresholds:
            # Longs:  RSI<=30 OR price <= lower BB
            # Shorts: RSI>=70 OR price >= upper BB (computed below)
            _, upper_bb, _, _ = _bollinger(close, 20, 2.0)
            oversold = rsi <= 30 or cur <= lower_bb
            overbought = rsi >= 70 or cur >= upper_bb
            if oversold:
                score = du.clamp(np.interp(rsi, [20, 30], [100, 70]))
                detail = (f"SIDEWAYS oversold long · RSI {rsi:.0f}"
                          f"{' (at lower BB)' if cur<=lower_bb else ''}")
                raw = -rsi
            elif overbought:
                score = du.clamp(np.interp(rsi, [70, 80], [30, 0]))
                detail = (f"SIDEWAYS overbought short · RSI {rsi:.0f}"
                          f"{' (at upper BB)' if cur>=upper_bb else ''}")
                raw = rsi
            else:
                score = 50.0
                detail = f"SIDEWAYS neutral (RSI {rsi:.0f})"
                raw = -rsi
        return {"score": round(float(score), 1),
                "raw": round(float(raw), 3), "detail": detail}

    # ── TREND engine ──
    if regime == BEAR:
        # Longs blocked in BEAR. The factor's score is 0 (-> low composite ->
        # STRONG SHORT tier, which is the correct direction in a bear regime).
        return _blocked(BEAR, raw=-1.0)

    gap_pct = (e10 - e50) / cur * 100
    if e10 > e50:
        score = du.clamp(70 + gap_pct * 8, 70, 100)
    else:
        score = du.clamp(50 + gap_pct * 8, 0, 50)
    detail = f"EMA10 {'>' if e10>e50 else '<'} EMA50 (gap {gap_pct:+.1f}%)"

    if regime == SIDEWAYS:
        # cap at 70 — A+ relative strength can still override via RS factor,
        # so the composite isn't permanently neutered, just demanded high RS.
        if score > 70:
            score = 70.0
            detail += " · SIDEWAYS cap (needs A+ RS to push higher)"

    return {"score": round(float(score), 1), "raw": round(float(gap_pct), 3),
            "detail": detail}


# ── Factor 2: Volume Surge (regime-aware) ─────────────────────────────────────
def volume_surge(df: pd.DataFrame, strategy: str = TREND,
                 macro_score: float | None = None) -> dict:
    """
    5d/20d average-volume ratio. Higher ratio -> higher score in BOTH engines.
    Regime gates:
      TREND + BEAR  -> longs blocked (score 0)
      MR    + BULL  -> shorts blocked is N/A (MR scores express long bias here)
    The TREND-side block is what matters for the regime rules.
    """
    if df.empty or "Volume" not in df or len(df) < 21:
        return {"score": 50.0, "raw": 1.0, "detail": "insufficient data"}

    regime = regime_of(macro_score)
    if strategy == TREND and regime == BEAR:
        return _blocked(BEAR)

    vol = df["Volume"].dropna()
    avg5 = float(vol.iloc[-5:].mean())
    avg20 = float(vol.iloc[-20:].mean())
    if avg20 == 0:
        return {"score": 50.0, "raw": 1.0, "detail": "zero 20d volume"}

    ratio = avg5 / avg20
    score = du.clamp(np.interp(ratio, [0.7, 2.0], [0, 100]))
    note = "exhaustion" if strategy == MEAN_REVERSION else "confirmation"
    # SIDEWAYS cap for TREND breakouts
    if strategy == TREND and regime == SIDEWAYS and score > 70:
        score = 70.0
        note += ", SIDEWAYS cap"
    return {"score": round(score, 1), "raw": round(ratio, 2),
            "detail": f"5d/20d volume {ratio:.2f}x ({note})"}


# ── Factor 3: Relative Strength vs SPY (regime-aware) ─────────────────────────
def relative_strength(df: pd.DataFrame, spy_close: pd.Series,
                      strategy: str = TREND,
                      macro_score: float | None = None) -> dict:
    """
    20-day stock return minus 20-day SPY return (pp).
      TREND          : outperformance -> high score (=> long).
      MEAN_REVERSION : extreme UNDERperformance -> high score (=> long via fade).
    BEAR + TREND blocks longs entirely. RS is the one factor that ESCAPES the
    SIDEWAYS cap, so an A+ RS leader can still earn a high composite (the
    other factors cap at 70 under SIDEWAYS, but RS does not).
    """
    if df.empty or "Close" not in df or len(df) < 21 or len(spy_close) < 21:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    regime = regime_of(macro_score)
    if strategy == TREND and regime == BEAR:
        return _blocked(BEAR)

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
        # -10pp -> 0 ; +10pp -> 100. RS is NOT capped under SIDEWAYS — this is
        # the "A+ relative strength override" the spec calls for.
        score = du.clamp(np.interp(rs, [-10, 10], [0, 100]))
        detail = f"20d RS vs SPY {rs:+.1f}pp"
        raw = rs

    return {"score": round(score, 1), "raw": round(float(raw), 2),
            "detail": detail}


# ── Factor 4: Range Proximity (regime-aware) ──────────────────────────────────
def range_proximity(df: pd.DataFrame, strategy: str = TREND,
                    macro_score: float | None = None) -> dict:
    """
    TREND          : reward proximity to 52-week HIGH.
    MEAN_REVERSION : reward proximity to 52-week LOW.
    Regime gates:
      TREND + BEAR -> longs blocked
      SIDEWAYS     -> cap TREND breakouts at 70 (no breakout-buy near highs)
    """
    if df.empty or "Close" not in df or len(df) < 60:
        return {"score": 50.0, "raw": 0.0, "detail": "insufficient data"}

    regime = regime_of(macro_score)
    if strategy == TREND and regime == BEAR:
        return _blocked(BEAR)

    close = df["Close"].dropna()
    window = close.iloc[-252:] if len(close) > 252 else close
    cur = float(close.iloc[-1])
    hi = float(window.max())
    lo = float(window.min())

    if strategy == MEAN_REVERSION:
        if lo == 0:
            return {"score": 50.0, "raw": 0.0, "detail": "zero 52w low"}
        prox = cur / lo
        score = du.clamp(np.interp(prox, [1.0, 1.40], [100, 0]))
        detail = f"{(prox-1)*100:.0f}% above 52w low"
        raw = -prox
    else:
        if hi == 0:
            return {"score": 50.0, "raw": 0.0, "detail": "zero 52w high"}
        prox = cur / hi
        score = du.clamp(np.interp(prox, [0.70, 1.0], [0, 100]))
        detail = f"{prox*100:.0f}% of 52w high"
        if regime == SIDEWAYS and score > 70:
            score = 70.0
            detail += " · SIDEWAYS cap"
        raw = prox

    return {"score": round(score, 1), "raw": round(float(raw), 3),
            "detail": detail}


# ── Factor 5: Short Interest ──────────────────────────────────────────────────
def short_interest(ticker: str, strategy: str = TREND,
                   macro_score: float | None = None) -> dict:
    """
    MoM change in short interest.
      TREND          : DECLINING shorts -> high score (capitulation of bears).
      MEAN_REVERSION : ELEVATED / rising shorts -> high score (squeeze fuel).
    Under TREND+BEAR longs are blocked.
    """
    if strategy == TREND and regime_of(macro_score) == BEAR:
        return _blocked(BEAR)

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
def options_flow(ticker: str, df: pd.DataFrame, strategy: str = TREND,
                 macro_score: float | None = None) -> dict:
    """
    IV percentile + put/call open-interest ratio.
      TREND          : low IV + call-heavy OI = calm, favorable -> high score.
      MEAN_REVERSION : high IV + put-heavy OI = peak fear -> high score.
    Under TREND+BEAR longs are blocked.
    """
    if strategy == TREND and regime_of(macro_score) == BEAR:
        return _blocked(BEAR)

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
