"""
factors.py — The 6 scanner factors (dual-strategy, regime-aware) + earnings.

Every factor branches on the active strategy engine AND the current macro
regime, derived from the 0-100 Composite Macro Score:

  BULL REGIME      (macro_score >= 70) — reward upside, block shorts
  SIDEWAYS REGIME  (40 <= macro_score < 70) — high selectivity, cap mid scores
  BEAR REGIME      (macro_score < 40) — reward downside, block longs

Each factor returns {score 0-100, raw, detail}. A "BLOCKED" factor score of 0
drives the composite down so the offending direction lands in the lowest tier
(AVOID / SHORT) — see _scan_one in run_scanner.py for the composite math.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import data_utils as du

TREND = "Trend-Following"
MEAN_REVERSION = "Mean Reversion"

# ── Time Horizon system ──────────────────────────────────────────────────────
# Switches lookback windows across the engine. Swing is the original/default
# behavior (tactical 1-4 week trades). Long-Term stretches every responsive
# window to its multi-month equivalent for position trading. Same math, same
# composite formula, same regime rules — only the lookback periods change.
#
# Signals NOT in this table (Range Proximity 52w, Short Interest, Options Flow
# IV-pct 252d, spot VIX Level, VIX Term Structure ratio, Credit Spreads
# Z-score, Mega-Cap Rotation, Factor Crowding 60d corr) are intentionally
# horizon-independent — they're either point-in-time readings or already use
# long lookbacks.
SWING = "Swing Trade System"
LONG_TERM = "Long-Term System"

LOOKBACKS = {
    SWING: {
        "momentum_trend_short": 10,    # TREND momentum: short EMA
        "momentum_trend_long":  50,    # TREND momentum: long EMA
        "momentum_mr_window":   20,    # MR momentum: SMA + Bollinger window
        "volume_short":          5,    # Volume Surge: short avg window
        "volume_long":          20,    # Volume Surge: long avg window
        "rs_window":            20,    # Relative Strength: lookback days
        "sector_breadth_sma":   50,    # Sector Breadth: SMA window
        "vix_momentum":         20,    # VIX Momentum: ROC window
    },
    LONG_TERM: {
        "momentum_trend_short":  50,
        "momentum_trend_long":  200,
        "momentum_mr_window":    50,
        "volume_short":          20,
        "volume_long":           60,
        "rs_window":             60,
        "sector_breadth_sma":   200,
        "vix_momentum":          60,
    },
}


def lookback(horizon: str, key: str) -> int:
    """Resolve a lookback period for the active horizon. Falls back to SWING
    defaults for unknown horizons rather than raising — a single mistyped
    horizon string should not break a scan."""
    table = LOOKBACKS.get(horizon, LOOKBACKS[SWING])
    return table.get(key, LOOKBACKS[SWING][key])

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


def _realized_vol_pct(close: pd.Series, window: int = 20) -> float:
    """20-day annualized realized volatility, expressed as a percentage.

    Used to normalize momentum / relative-strength signals so high-beta
    names (semis, biotech, meme) don't systematically dominate top scores.
    A 5% move in NVDA is unremarkable; a 5% move in JNJ is a thunderclap.
    Vol normalization scales those equivalently.

    Returns the percent value (e.g. 35.0 = 35% annualized vol). Floored at
    5% to prevent division explosions on extremely placid names; capped at
    150% to prevent volatility-collapse anomalies from zeroing factor scores.
    """
    if len(close) < window + 1:
        return 25.0  # neutral default — typical large-cap annual vol
    daily_ret = close.pct_change().dropna().tail(window)
    if len(daily_ret) < window // 2:
        return 25.0
    daily_std = float(daily_ret.std())
    if not np.isfinite(daily_std) or daily_std <= 0:
        return 25.0
    annualized = daily_std * np.sqrt(252) * 100
    return float(np.clip(annualized, 5.0, 150.0))


def _blocked(reason: str, raw: float = 0.0) -> dict:
    """Return a regime-blocked factor result: score 0 + tagged detail string."""
    return {"score": 0.0, "raw": float(raw),
            "detail": f"[BLOCKED: {reason}]"}


# ── Factor 1: Momentum (regime-aware) ─────────────────────────────────────────
def momentum(df: pd.DataFrame, strategy: str = TREND,
             macro_score: float | None = None,
             horizon: str = SWING) -> dict:
    """
    TREND          : score reflects upward momentum.
        BULL  -> unrestricted (reward EMA-short > EMA-long + breakout strength)
        SIDE  -> capped at 70 unless top-tier RS (cap applied externally)
        BEAR  -> BLOCKED: shorts only. score=0 for any long setup.

    MEAN_REVERSION : score reflects a buyable pullback OR shortable bounce
        depending on regime.
        BULL  -> shallow dip: RSI<=40 OR price touches MR-SMA -> long reward
        SIDE  -> standard oversold: RSI<=30 OR price <= lower BB
        BEAR  -> extreme capitulation: RSI<=20 AND price 3+ std below MR-SMA

    Horizon controls the EMA + Bollinger window sizes (SWING uses 10/50/20;
    LONG_TERM uses 50/200/50). Needs at least `momentum_trend_long + 5` bars
    of history; insufficient data returns neutral 50.
    """
    short_window = lookback(horizon, "momentum_trend_short")
    long_window  = lookback(horizon, "momentum_trend_long")
    mr_window    = lookback(horizon, "momentum_mr_window")
    min_bars     = long_window + 5

    if df.empty or "Close" not in df or len(df) < min_bars:
        return {"score": 50.0, "raw": 0.0,
                "detail": f"insufficient data (need {min_bars}+ bars)"}

    close = df["Close"].dropna()
    regime = regime_of(macro_score)
    ema_short = du.ema(close, short_window)
    ema_long  = du.ema(close, long_window)
    cur = float(close.iloc[-1])
    es = float(ema_short.iloc[-1])
    el = float(ema_long.iloc[-1])
    rsi = _rsi(close)
    sma_mr, _, lower_bb, z_mr = _bollinger(close, mr_window, 2.0)

    if strategy == MEAN_REVERSION:
        # MEAN_REVERSION expresses long signals via HIGH scores and short
        # signals via LOW scores. The composite then lands in the right
        # Conviction Tier (80+ = HIGH CONVICTION, 0-34 = AVOID / SHORT).
        if regime == BULL:
            # Shorts BLOCKED. Long-only: shallow dip RSI<=40 OR price <= MR-SMA
            # Detect a "bounce-to-short" setup -> if present, block it.
            overbought_bounce = rsi >= 70 or cur >= sma_mr + 2 * (cur - sma_mr)
            if overbought_bounce and rsi >= 70:
                return _blocked(BULL, raw=-rsi)
            shallow_dip = rsi <= 40 or cur <= sma_mr
            if shallow_dip:
                # tighter RSI / closer to SMA -> stronger setup
                rsi_part = du.clamp(np.interp(rsi, [25, 40], [100, 60]))
                score = float(rsi_part)
                detail = (f"BULL shallow-dip long · RSI {rsi:.0f}"
                          f"{f' (at {mr_window}-SMA)' if cur<=sma_mr else ''}")
            else:
                # No dip and not overbought-blocked: park at neutral (HOLD/CASH).
                # The old value of 35 incorrectly fell into CAUTION tier,
                # which is the wrong direction for a long strategy in a bull.
                score = 50.0
                detail = (f"BULL: no dip yet, no setup (RSI {rsi:.0f}, price "
                          f"{(cur/sma_mr-1)*100:+.1f}% vs {mr_window}-SMA)")
            raw = -rsi
        elif regime == BEAR:
            # BOTH directions allowed under BEAR — opposite extremes:
            # Longs require capitulation: RSI<=20 OR 2.5+σ below 20-SMA
            #   (loosened from the original RSI<=20 AND z<=-3, which an audit
            #    showed never fires in real data — see findings #15)
            # Shorts trigger on bounces: RSI>=50 OR price touched MR-SMA from below
            extreme_long = (rsi <= 20) or (z_mr <= -2.5)
            bounce_short = (rsi >= 50) or (cur >= sma_mr and z_mr > -0.5)
            if extreme_long:
                score = 100.0
                detail = (f"BEAR capitulation long · RSI {rsi:.0f}, "
                          f"{z_mr:.1f}σ vs {mr_window}-SMA")
                raw = -z_mr
            elif bounce_short:
                # short setup -> low score (drives composite into SHORT tier)
                short_strength = du.clamp(np.interp(rsi, [50, 70], [40, 0]))
                score = float(short_strength)
                detail = (f"BEAR bounce-to-short · RSI {rsi:.0f}"
                          f"{f' (at {mr_window}-SMA from below)' if cur>=sma_mr else ''}")
                raw = rsi  # higher RSI = stronger short signal
            else:
                score = du.clamp(np.interp(z_mr, [-3.0, 0.0], [50, 25]))
                detail = (f"BEAR: no extreme (RSI {rsi:.0f}, "
                          f"{z_mr:.1f}σ vs {mr_window}-SMA)")
                raw = -z_mr
        else:  # SIDEWAYS
            # Both directions allowed at standard thresholds:
            # Longs:  RSI<=30 OR price <= lower BB
            # Shorts: RSI>=70 OR price >= upper BB (computed below)
            _, upper_bb, _, _ = _bollinger(close, mr_window, 2.0)
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
        # AVOID / SHORT tier, which is the correct direction in a bear regime).
        return _blocked(BEAR, raw=-1.0)

    gap_pct = (es - el) / cur * 100
    # Volatility normalization — divide the EMA gap by realized vol so that
    # high-beta semis don't systematically out-score low-beta utilities just
    # because they move further in absolute terms. A 25%-annualized-vol stock
    # is the neutral baseline (factor unchanged); a 50%-vol name gets its
    # gap halved, a 12.5%-vol name gets it doubled. The score scale (0-100,
    # tier thresholds at 65/80) is preserved by construction.
    vol_pct = _realized_vol_pct(close)
    vol_scaler = 25.0 / vol_pct        # neutral baseline = 25% annualized
    gap_pct_norm = gap_pct * vol_scaler

    if es > el:
        score = du.clamp(70 + gap_pct_norm * 8, 70, 100)
    else:
        score = du.clamp(50 + gap_pct_norm * 8, 0, 50)
    detail = (f"EMA{short_window} {'>' if es>el else '<'} EMA{long_window} "
              f"(gap {gap_pct:+.1f}%, vol-adj {gap_pct_norm:+.1f}%, "
              f"σ {vol_pct:.0f}%)")

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
                 macro_score: float | None = None,
                 horizon: str = SWING) -> dict:
    """
    Short / long average-volume ratio. Higher ratio -> higher score in both
    engines. Window sizes come from the active horizon:
      SWING     : 5-day / 20-day  (tactical volume change)
      LONG_TERM : 20-day / 60-day (sustained accumulation/distribution)

    Regime gates:
      TREND + BEAR  -> longs blocked (score 0)
      MR    + BULL  -> shorts blocked is N/A (MR scores express long bias here)
    """
    short_win = lookback(horizon, "volume_short")
    long_win  = lookback(horizon, "volume_long")
    min_bars  = long_win + 1

    if df.empty or "Volume" not in df or len(df) < min_bars:
        return {"score": 50.0, "raw": 1.0,
                "detail": f"insufficient data (need {min_bars}+ bars)"}

    regime = regime_of(macro_score)
    if strategy == TREND and regime == BEAR:
        return _blocked(BEAR)

    vol = df["Volume"].dropna()
    avg_short = float(vol.iloc[-short_win:].mean())
    avg_long  = float(vol.iloc[-long_win:].mean())
    if avg_long == 0:
        return {"score": 50.0, "raw": 1.0,
                "detail": f"zero {long_win}d volume"}

    ratio = avg_short / avg_long
    score = du.clamp(np.interp(ratio, [0.7, 2.0], [0, 100]))
    note = "exhaustion" if strategy == MEAN_REVERSION else "confirmation"
    if strategy == TREND and regime == SIDEWAYS and score > 70:
        score = 70.0
        note += ", SIDEWAYS cap"
    return {"score": round(score, 1), "raw": round(ratio, 2),
            "detail": f"{short_win}d/{long_win}d volume {ratio:.2f}x ({note})"}


# ── Factor 3: Relative Strength vs SPY (regime-aware) ─────────────────────────
def relative_strength(df: pd.DataFrame, spy_close: pd.Series,
                      strategy: str = TREND,
                      macro_score: float | None = None,
                      horizon: str = SWING) -> dict:
    """
    Stock return minus SPY return (pp) over the active horizon's RS window
    (SWING = 20 days, LONG_TERM = 60 days).
      TREND          : outperformance -> high score (=> long).
      MEAN_REVERSION : extreme UNDERperformance -> high score (=> long via fade).
    BEAR + TREND blocks longs entirely. RS is the one factor that ESCAPES the
    SIDEWAYS cap, so an A+ RS leader can still earn a high composite (the
    other factors cap at 70 under SIDEWAYS, but RS does not).
    """
    rs_win = lookback(horizon, "rs_window")
    min_bars = rs_win + 1

    if (df.empty or "Close" not in df
            or len(df) < min_bars or len(spy_close) < min_bars):
        return {"score": 50.0, "raw": 0.0,
                "detail": f"insufficient data (need {min_bars}+ bars)"}

    regime = regime_of(macro_score)
    if strategy == TREND and regime == BEAR:
        return _blocked(BEAR)

    close = df["Close"].dropna()
    stock_ret = (close.iloc[-1] / close.iloc[-(rs_win + 1)] - 1) * 100
    spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-(rs_win + 1)] - 1) * 100
    rs = float(stock_ret - spy_ret)

    if strategy == MEAN_REVERSION:
        # -15pp (deep underperformance) -> 100 ; +5pp -> 0
        score = du.clamp(np.interp(rs, [-15, 5], [100, 0]))
        detail = f"{rs_win}d RS vs SPY {rs:+.1f}pp (underperformance reward)"
        raw = -rs
    else:
        # -10pp -> 0 ; +10pp -> 100. RS is NOT capped under SIDEWAYS — this is
        # the "A+ relative strength override" the spec calls for.
        # Volatility normalization: high-beta names produce big RS-vs-SPY
        # numbers just from their inherent vol, not necessarily real alpha.
        # Scale by realized vol so a 10pp RS on JNJ (low vol) outranks a 10pp
        # RS on SMCI (high vol) — that's actual outperformance vs noise.
        vol_pct = _realized_vol_pct(close)
        vol_scaler = 25.0 / vol_pct
        rs_norm = rs * vol_scaler
        score = du.clamp(np.interp(rs_norm, [-10, 10], [0, 100]))
        detail = (f"{rs_win}d RS vs SPY {rs:+.1f}pp "
                  f"(vol-adj {rs_norm:+.1f}, σ {vol_pct:.0f}%)")
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
        # MR Range Proximity — true range-position metric.
        # Previously this used `prox = cur / lo` (% above 52w low) capped at
        # [1.0, 1.40], which had a real bug: the formula mapped "20% above
        # low" the same as "20% above low and only 10% of the way through
        # the range" (a wide-range stock where 20% above is still oversold).
        # The trade log MR validation showed this produced bad SHORTs at
        # mid-range positions (50% of range) because the formula didn't
        # distinguish "mid-range" from "overbought."
        #
        # New formula: range_position = (price - low) / (high - low) * 100
        # gives a clean 0-100 scale.
        #
        # MR scoring is U-shaped:
        #   - Position near 0% (deeply oversold) → HIGH score (LONG candidate)
        #   - Position near 100% (deeply overbought) → LOW score (SHORT candidate)
        #   - Position 30-70% (mid-range) → ~50 (neutral, no MR signal)
        #
        # This means mid-range names won't passively drift below the SHORT
        # threshold of 35 — they'll get pinned near 50 by this factor, which
        # makes other factors (Volume Surge, Options Flow, etc.) need to be
        # genuinely bearish to push the composite below 35.
        if hi <= lo:
            return {"score": 50.0, "raw": 0.0, "detail": "degenerate 52w range"}
        range_pos_pct = (cur - lo) / (hi - lo) * 100
        # U-curve: 100 at pos=0, ~50 at pos=50, 0 at pos=100
        # Linear inversion: score = 100 - position. Simple and interpretable.
        # The trade log validation showed cleanly oversold (0% pos) and
        # cleanly overbought (>90% pos) are the only regimes where MR works.
        score = du.clamp(100.0 - range_pos_pct)
        detail = f"{range_pos_pct:.0f}% of 52w range (0=low, 100=high)"
        raw = -range_pos_pct / 100  # signed for downstream consumers
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
