"""
run_scanner.py — PAGE 2: Custom Watchlist Scanner (+ S&P 500 scan for Page 1).

Scores each ticker with 6 factors driven by the active Strategy Engine
(Trend-Following or Mean Reversion), assigns a qualitative status, and applies
an earnings-proximity override.

`scan_universe()` runs the same factor logic across the S&P 500 sample so
Page 1 can surface a "Broad Market Top 5".
"""

from __future__ import annotations
import concurrent.futures
from datetime import datetime

import numpy as np
import pandas as pd

import data_utils as du
from scanner_factors import factors

TREND = factors.TREND
MEAN_REVERSION = factors.MEAN_REVERSION

# 6 technical/institutional factors — INSTITUTIONAL FLOW WEIGHTED model.
# The composite is the weighted sum of factor scores (not a simple average),
# rounded to the nearest integer. P/E and the 52-week range position are
# DECOUPLED — display only, NOT scored.
FACTOR_WEIGHTS = {
    "Options Flow":      0.30,   # institutional options/volatility flow
    "Volume Surge":      0.25,   # big-money volume
    "Momentum":          0.15,   # price speed
    "Relative Strength": 0.10,   # market leadership
    "Short Interest":    0.10,   # squeeze fuel
    "Range Proximity":   0.10,   # chart position
}

# Plain-English display labels for the 6 factors (with their weight).
FACTOR_DISPLAY_LABELS = {
    "Options Flow":      "Options Flow (30%)",
    "Volume Surge":      "Big Money Volume (25%)",
    "Momentum":          "Price Speed (15%)",
    "Relative Strength": "Market Leader (10%)",
    "Short Interest":    "Squeeze Fuel (10%)",
    "Range Proximity":   "Chart Position (10%)",
}

# Directional Bias mapping — guides LONG / SHORT trade placement.
STATUS_TIERS = [
    (80, "🟢 STRONG LONG",  "#22e08a"),
    (65, "🟢 LEAN LONG",    "#7fd98a"),
    (50, "🟡 HOLD / CASH",  "#f5c344"),
    (35, "🟠 WATCH SHORT",  "#ff9442"),
    (20, "🔴 LEAN SHORT",   "#ff7a6c"),
    (0,  "🔴 STRONG SHORT", "#ff5d6c"),
]

def classify_status(score: float) -> tuple[str, str]:
    for threshold, label, color in STATUS_TIERS:
        if score >= threshold:
            return label, color
    return STATUS_TIERS[-1][1], STATUS_TIERS[-1][2]


# ── Tranche / Position-Sizing engine ──────────────────────────────────────────
def calculate_tranche_action(macro_score: float, stock_score: float) -> dict:
    """
    Pair the Page-1 Macro Score with an individual stock's 0-100 score to
    produce a tactical tranche directive.

    Returns {"action": str, "color": hex}.

    Tranche logic:
      Stock 80-100 + Macro 70-100 -> 🟢 TRANCHE 3 (MAX LONG)
      Stock 80-100 + Macro 40-69  -> 🟢 TRANCHE 2 (MID LONG · A+ RS)
                                     (reachable only via the A+ RS override —
                                     SIDEWAYS cap holds other factors at 70)
      Stock 65-79                 -> 🟢 TRANCHE 1 (PILOT LONG)
      Stock 50-64                 -> 🟡 HOLD CORE / CASH
      Stock 35-49                 -> 🟠 WATCH / NO TRADE
      Stock 20-34                 -> 🔴 TRANCHE 2 (LEAN SHORT)
      Stock 0-19                  -> 🔴 TRANCHE 3 (MAX SHORT)

    Overriding rules:
      1. Macro Score < 40 (BEAR REGIME) -> all LONG actions forced to
         "❌ RISK OFF: COLD CASH".
      2. Macro Score >= 70 (BULL REGIME) -> all SHORT actions forced to
         "❌ SHORT BLOCKED: BULL REGIME".

    The composite-builder uses `stock_score = -1` as a sentinel for rows
    blocked by `_composite` — this function short-circuits on it so the
    action cell agrees with the BLOCKED status label.
    """
    GREEN, YELLOW, RED = "#22e08a", "#f5c344", "#ff5d6c"
    s = stock_score

    # short-circuit on the -1 blocked-row sentinel (set in _composite)
    if s is None or s < 0:
        return {"action": "❌ BLOCKED BY REGIME", "color": "#7d8aa5"}

    macro_defensive = macro_score is not None and macro_score < 40
    macro_bull = macro_score is not None and macro_score >= 70

    if s >= 80:
        if macro_score is not None and macro_score >= 70:
            action, color, is_long = "🟢 TRANCHE 3 (MAX LONG)", GREEN, True
        else:  # macro 40-69: score 80+ implies A+ RS override (SIDEWAYS cap
               # holds non-RS factors at 70, so reaching 80 requires top RS).
            action, color, is_long = "🟢 TRANCHE 2 (MID LONG · A+ RS)", GREEN, True
    elif s >= 65:
        action, color, is_long = "🟢 TRANCHE 1 (PILOT LONG)", GREEN, True
    elif s >= 50:
        action, color, is_long = "🟡 HOLD CORE / CASH", YELLOW, False
    elif s >= 35:
        # Aligned with the Directional Bias "🟠 WATCH SHORT" tier — this is a
        # caution zone, not a deploy-shorts zone. The action is wait-and-see.
        action, color, is_long = "🟠 WATCH / NO TRADE", "#ff9442", False
    elif s >= 20:
        action, color, is_long = "🔴 TRANCHE 2 (LEAN SHORT)", RED, False
    else:
        action, color, is_long = "🔴 TRANCHE 3 (MAX SHORT)", RED, False

    # Overriding rule 1 — BEAR REGIME kills all LONG deployment
    if macro_defensive and is_long:
        action, color = "❌ RISK OFF: COLD CASH", "#7d8aa5"

    # Overriding rule 2 — BULL REGIME kills all SHORT deployment
    if macro_bull and not is_long and s < 50:
        action, color = "❌ SHORT BLOCKED: BULL REGIME", "#7d8aa5"

    return {"action": action, "color": color}


def _percentile_rank(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    items = list(values.items())
    vals = np.array([v for _, v in items], dtype=float)
    if len(vals) == 1 or np.all(vals == vals[0]):
        return {k: 50.0 for k, _ in items}
    return {k: round(float(np.mean(vals < v) * 100), 1) for k, v in items}


def _scan_one(ticker: str, df: pd.DataFrame, spy_close: pd.Series,
              strategy: str, with_options: bool = True,
              macro_score: float | None = None) -> dict:
    """Compute all per-ticker factor results for the given strategy/regime."""
    f_mom = factors.momentum(df, strategy, macro_score=macro_score)
    f_vol = factors.volume_surge(df, strategy, macro_score=macro_score)
    f_rs  = factors.relative_strength(df, spy_close, strategy,
                                       macro_score=macro_score)
    f_rp  = factors.range_proximity(df, strategy, macro_score=macro_score)

    # Short interest & options are slow point-in-time calls; the S&P universe
    # scan skips them (with_options=False) and uses neutral 50 for speed.
    if with_options:
        f_si  = factors.short_interest(ticker, strategy,
                                        macro_score=macro_score)
        f_opt = factors.options_flow(ticker, df, strategy,
                                      macro_score=macro_score)
    else:
        f_si  = {"score": 50.0, "raw": 0.0, "detail": "skipped (universe scan)"}
        f_opt = {"score": 50.0, "raw": 0.0, "detail": "skipped (universe scan)"}

    earn = factors.earnings_proximity(ticker) if with_options else {
        "next_earnings": None, "trading_days_away": None, "flag": False}

    # ── DECOUPLED display-only data (NOT part of the 0-100 composite) ──
    # P/E — yfinance fundamentals. Fetched only in the full scan.
    if with_options:
        fundamentals = du.get_fundamentals(ticker)
    else:
        fundamentals = {"trailing_pe": None, "forward_pe": None,
                        "status": "skipped"}

    # ── 52-week range position (0-100) from the 1-yr OHLCV history ──
    price = None
    range_high = range_low = range_position = None
    if not df.empty and "Close" in df:
        cl = df["Close"].dropna()
        if len(cl):
            price = round(float(cl.iloc[-1]), 2)
            window = cl.iloc[-252:] if len(cl) > 252 else cl
            hi = float(window.max())
            lo = float(window.min())
            range_high = round(hi, 2)
            range_low = round(lo, 2)
            if hi > lo:
                # (Current - 52W Low) / (52W High - 52W Low) * 100
                range_position = round((price - lo) / (hi - lo) * 100, 1)
                range_position = max(0.0, min(100.0, range_position))

    # ── Relative Volume (RVOL) = today's volume / 20-day average volume ──
    rvol = None
    rvol_label = "—"
    if not df.empty and "Volume" in df:
        vol = df["Volume"].dropna()
        if len(vol) >= 21:
            today_vol = float(vol.iloc[-1])
            # 20-day average EXCLUDING today, so RVOL compares vs the baseline
            avg20 = float(vol.iloc[-21:-1].mean())
            if avg20 > 0:
                rvol = round(today_vol / avg20, 2)
                # descriptive plain-English label (no raw number shown)
                if rvol >= 1.2:
                    rvol_label = "Heavy (Institutional)"
                elif rvol >= 0.8:
                    rvol_label = "Normal"
                else:
                    rvol_label = "Quiet (Retail)"

    return {
        "ticker": ticker, "price": price,
        "factors": {
            "Momentum":          f_mom,
            "Volume Surge":      f_vol,
            "Relative Strength": f_rs,
            "Range Proximity":   f_rp,
            "Short Interest":    f_si,
            "Options Flow":      f_opt,
        },
        "earnings": earn,
        "fundamentals": fundamentals,    # decoupled — display only
        "range_high": range_high,        # 52-week high
        "range_low": range_low,          # 52-week low
        "range_position": range_position,  # 0-100 position in the 52w range
        "rvol": rvol,                    # relative volume vs 20d average
        "rvol_label": rvol_label,        # descriptive volume-pace label
        "has_data": not df.empty,
    }


def _composite(raw_results: dict, watchlist: list, rankable_raw: dict) -> list:
    """Assemble final scored rows from raw factor results.

    Regime-blocked factors are mathematically neutralized to 50.0 (instead of
    0.0) so they don't accidentally drive the composite into the SHORT tier.
    The row's composite is then sentineled with -1 so it sorts BELOW all
    valid shorts (which legitimately score 0-19).
    """
    rows = []
    for t in watchlist:
        res = raw_results.get(t, {})
        fr = res.get("factors", {})
        if not fr:
            rows.append(_empty_row(t, res.get("error", "no data")))
            continue

        factor_scores = {}
        is_blocked = False
        block_reason = "❌ BLOCKED BY REGIME"

        for fname in FACTOR_WEIGHTS:
            factor_result = fr.get(fname, {})
            detail_text = factor_result.get("detail", "")

            # Check if the math engine caught a regime violation
            if "[BLOCKED:" in detail_text:
                is_blocked = True
                if "BULL" in detail_text:
                    block_reason = "❌ SHORT BLOCKED: BULL REGIME"
                elif "BEAR" in detail_text:
                    block_reason = "❌ LONG BLOCKED: BEAR REGIME"
                # Mathematically neutralize the factor so it doesn't
                # skew to 0 (Max Short tier)
                factor_scores[fname] = 50.0
            elif fname in rankable_raw:
                factor_scores[fname] = rankable_raw[fname].get(t, 50.0)
            else:
                factor_scores[fname] = factor_result.get("score", 50.0)

        # Composite = INSTITUTIONAL FLOW weighted sum of the 6 factor scores
        composite = int(round(sum(
            FACTOR_WEIGHTS[f] * factor_scores[f] for f in FACTOR_WEIGHTS)))

        # Regime UI override — sentinel composite to -1 so blocked rows sink
        # BELOW valid shorts (composite=0) during sorting
        if is_blocked:
            status_label = block_reason
            status_color = "#7d8aa5"
            composite = -1
        else:
            status_label, status_color = classify_status(composite)

        earn = res.get("earnings", {})
        fund = res.get("fundamentals", {})

        rows.append({
            "ticker": t, "total_score": composite,
            "factor_scores": {k: round(v, 1) for k, v in factor_scores.items()},
            "factor_detail": {k: fr.get(k, {}).get("detail", "—")
                              for k in FACTOR_WEIGHTS},
            "price": res.get("price"),
            "next_earnings": earn.get("next_earnings"),
            "earnings_days_away": earn.get("trading_days_away"),
            "earnings_flag": earn.get("flag", False),
            "status_label": status_label, "status_color": status_color,
            # decoupled display-only fields
            "trailing_pe": fund.get("trailing_pe"),
            "forward_pe": fund.get("forward_pe"),
            "range_high": res.get("range_high"),
            "range_low": res.get("range_low"),
            "range_position": res.get("range_position"),
            "rvol": res.get("rvol"),
            "rvol_label": res.get("rvol_label", "—"),
        })
    return rows


def run(watchlist: list[str], strategy: str = TREND,
        macro_score: float | None = None) -> dict:
    """Scan the custom watchlist with the active strategy engine.

    `macro_score` controls regime-aware factor behavior. When None the factors
    fall back to SIDEWAYS REGIME as a neutral default.
    """
    watchlist = [t.upper().strip() for t in watchlist if t.strip()]
    if not watchlist:
        return {"timestamp": datetime.now().isoformat(), "rows": [],
                "strategy": strategy, "error": "Empty watchlist"}

    spy_close = du.get_close_series("SPY", days=120)
    data = du.get_bulk_history(watchlist, days=400)

    raw_results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(watchlist))) as pool:
        futures = {
            pool.submit(_scan_one, t, data.get(t, pd.DataFrame()),
                        spy_close, strategy, True, macro_score): t
            for t in watchlist
        }
        for fut in concurrent.futures.as_completed(futures):
            t = futures[fut]
            try:
                raw_results[t] = fut.result(timeout=120)
            except Exception as e:
                raw_results[t] = {"ticker": t, "price": None, "factors": {},
                                  "earnings": {}, "has_data": False,
                                  "error": str(e)}

    # cross-sectional ranking for the directional, price-driven factors
    rankable = ["Momentum", "Volume Surge", "Relative Strength",
                "Range Proximity"]
    rankable_raw: dict[str, dict] = {}
    for fname in rankable:
        vals = {t: res["factors"][fname].get("raw", 0.0)
                for t, res in raw_results.items()
                if res.get("factors", {}).get(fname) is not None}
        rankable_raw[fname] = _percentile_rank(vals)

    rows = _composite(raw_results, watchlist, rankable_raw)
    # Sort by composite — blocked stocks have score 0 and naturally fall to
    # the bottom of the table as required by Phase 3.
    rows.sort(key=lambda r: r["total_score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "timestamp": datetime.now().isoformat(),
        "watchlist": watchlist, "strategy": strategy,
        "macro_score": macro_score,
        "factor_weights": FACTOR_WEIGHTS, "rows": rows,
    }


def scan_universe(strategy: str = TREND, top_n: int = 5,
                  macro_score: float | None = None) -> dict:
    """
    DEPRECATED — DO NOT WIRE INTO THE UI.

    Originally ran the factor engine across an S&P 500 sample for a planned
    "Broad Market Top 5" panel. But it skips Short Interest + Options Flow
    (the two highest-weighted factors, 30% + 25% = 55% of the live composite),
    pinning each to neutral 50. The resulting ranking is dominated by the
    remaining 45% of the engine and is materially different from what the
    live custom-watchlist scan would produce on the same tickers.

    Bringing this back honestly requires free historical short-interest and
    options-flow series, which yfinance doesn't provide. Until then, calling
    this function would mislead users. Left in the file for future revival.
    """
    import warnings
    warnings.warn(
        "scan_universe() uses only 45% of the live engine's weights; not "
        "suitable for ranking. Use run(watchlist, strategy, macro_score) "
        "for the full-weight scan.", DeprecationWarning, stacklevel=2)
    universe = du.SP500_SAMPLE
    spy_close = du.get_close_series("SPY", days=120)
    data = du.get_bulk_history(universe, days=400)

    raw_results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_scan_one, t, data.get(t, pd.DataFrame()),
                        spy_close, strategy, False, macro_score): t
            for t in universe
        }
        for fut in concurrent.futures.as_completed(futures):
            t = futures[fut]
            try:
                raw_results[t] = fut.result(timeout=120)
            except Exception:
                raw_results[t] = {"ticker": t, "price": None, "factors": {},
                                  "earnings": {}, "has_data": False}

    rankable = ["Momentum", "Volume Surge", "Relative Strength",
                "Range Proximity"]
    rankable_raw: dict[str, dict] = {}
    for fname in rankable:
        vals = {t: res["factors"][fname].get("raw", 0.0)
                for t, res in raw_results.items()
                if res.get("factors", {}).get(fname) is not None}
        rankable_raw[fname] = _percentile_rank(vals)

    rows = _composite(raw_results, universe, rankable_raw)
    rows = [r for r in rows if r.get("price") is not None]
    rows.sort(key=lambda r: r["total_score"], reverse=True)

    top = []
    for i, r in enumerate(rows[:top_n], 1):
        top.append({
            "rank": i, "ticker": r["ticker"], "score": r["total_score"],
            "price": r["price"], "status_label": r["status_label"],
            "status_color": r["status_color"],
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "strategy": strategy, "universe_size": len(rows), "top": top,
    }


def _empty_row(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker, "total_score": 0.0, "factor_scores": {},
        "factor_detail": {}, "price": None, "next_earnings": None,
        "earnings_days_away": None, "earnings_flag": False, "rank": None,
        "status_label": "❌ NO DATA", "status_color": "#7d8aa5",
        "trailing_pe": None, "forward_pe": None,
        "range_high": None, "range_low": None, "range_position": None,
        "rvol": None, "rvol_label": "—",
        "error": reason,
    }


def get_chart_studies(ticker: str) -> dict:
    """
    Download 1 year of daily OHLCV for `ticker` and compute technical studies
    for the on-demand chart:
      - VWAP (cumulative Volume Weighted Average Price)
      - Standard floor pivots (P, R1, S1, R2, S2) from the last completed month
      - Daily volume bars (color-coded up/down)
      - 50-bin volume-by-price profile + Point of Control (POC)
      - 20-day Rate of Change (ROC) + 9-day EMA signal line

    All values from real yfinance data; returns status="error" on failure.
    """
    try:
        df = du.get_history(ticker.upper(), days=400)
        if df.empty or not {"Open", "High", "Low", "Close"}.issubset(df.columns):
            return {"status": "error", "ticker": ticker,
                    "error": f"No OHLCV history for {ticker}"}

        df = df.dropna().iloc[-252:].copy()
        if len(df) < 30:
            return {"status": "error", "ticker": ticker,
                    "error": "Insufficient history for chart studies"}

        vol = df["Volume"] if "Volume" in df else pd.Series(0.0, index=df.index)

        # ── VWAP — cumulative typical-price * volume / cumulative volume ──
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = vol.cumsum().replace(0, np.nan)
        vwap = (typical * vol).cumsum() / cum_vol
        vwap = vwap.fillna(typical)  # fall back to typical price if no volume

        # ── Standard floor pivots from the last completed month ──
        monthly = df.resample("ME").agg({"High": "max", "Low": "min",
                                         "Close": "last"})
        pivots = {}
        if len(monthly) >= 2:
            prev = monthly.iloc[-2]  # last *completed* month
            hi, lo, cl = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
            p = (hi + lo + cl) / 3
            pivots = {
                "P":  round(p, 2),
                "R1": round(2 * p - lo, 2),
                "S1": round(2 * p - hi, 2),
                "R2": round(p + (hi - lo), 2),
                "S2": round(p - (hi - lo), 2),
            }

        # ── color-coded daily volume bars (green = up day, red = down) ──
        up_day = df["Close"] >= df["Open"]
        vol_colors = ["#22e08a" if u else "#ff5d6c" for u in up_day]

        # ── 50-bin volume-by-price profile + Point of Control ──
        prof_lo = float(df["Low"].min())
        prof_hi = float(df["High"].max())
        profile_prices, profile_volumes, poc_price = [], [], None
        if prof_hi > prof_lo:
            n_bins = 50
            edges = np.linspace(prof_lo, prof_hi, n_bins + 1)
            centers = (edges[:-1] + edges[1:]) / 2
            # assign each day's volume to the bin holding its typical price
            bin_idx = np.clip(
                np.digitize(typical.values, edges) - 1, 0, n_bins - 1)
            buckets = np.zeros(n_bins)
            for i, v in zip(bin_idx, vol.values):
                buckets[i] += float(v)
            profile_prices = [round(c, 2) for c in centers]
            profile_volumes = [float(b) for b in buckets]
            if buckets.max() > 0:
                poc_price = round(float(centers[int(np.argmax(buckets))]), 2)

        # ── 20-day ROC + 9-day EMA signal line ──
        # ROC as a strict PERCENTAGE: pct_change(20) * 100.
        roc = (df["Close"].pct_change(periods=20) * 100).round(2)
        # 9-day EMA of the (already ×100) ROC -> "signal line"
        roc_signal = roc.ewm(span=9, adjust=False).mean().round(2)

        # drop the leading NaNs (first 20 rows) so Plotly never receives NaN —
        # ship the ROC series WITH its own dates so the subplot aligns cleanly
        roc_clean = roc.dropna()
        roc_sig_clean = roc_signal.dropna()

        return {
            "status": "ok", "ticker": ticker.upper(),
            "dates": [d.strftime("%Y-%m-%d") for d in df.index],
            "open": df["Open"].round(2).tolist(),
            "high": df["High"].round(2).tolist(),
            "low": df["Low"].round(2).tolist(),
            "close": df["Close"].round(2).tolist(),
            "volume": [float(v) for v in vol.values],
            "vol_colors": vol_colors,
            "vwap": vwap.round(2).tolist(),
            # ROC series carry their OWN date axis (NaNs already dropped)
            "roc_dates": [d.strftime("%Y-%m-%d") for d in roc_clean.index],
            "roc": roc_clean.tolist(),
            "roc_signal_dates": [d.strftime("%Y-%m-%d")
                                 for d in roc_sig_clean.index],
            "roc_signal": roc_sig_clean.tolist(),
            "pivots": pivots,
            "profile_prices": profile_prices,
            "profile_volumes": profile_volumes,
            "poc": poc_price,
        }
    except Exception as e:
        return {"status": "error", "ticker": ticker, "error": str(e)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(du.DEFAULT_WATCHLIST, TREND), indent=2, default=str))
