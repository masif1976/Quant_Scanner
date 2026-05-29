"""
run_portfolio_backtest.py — Watchlist-wide trade simulator.

Replays the scanner against every ticker in the watchlist over a recent
window (default ~3 months), emits paper-trades on signal crossings,
holds them for a fixed period, and reports portfolio-level metrics.

Distinct from run_backtest.py which is a *per-ticker score audit* with
no trade decisions. This module makes trade decisions and reports
win rate, total P&L, and per-trade detail.

DESIGN DECISIONS (locked):

  1. Entry rule:
       - LONG  fires when score crosses INTO TRADABLE (>= 65) from below.
                "Crossing" = yesterday's tier was lower, today's is >= 65.
       - SHORT fires when score crosses INTO AVOID/SHORT (< 35) from above.
     Both require the macro regime to permit the direction (BULL allows
     LONG, BEAR allows SHORT, SIDEWAYS allows both).

  2. Exit rule: fixed 20-day hold. Matches FWD_WINDOW in run_backtest so
     these results are directly comparable to the per-ticker forward-
     return numbers on Page 3.

  3. Position sizing: 100 shares per trade, fixed. Reported in both win-
     rate (count-based) and total $ P&L (dollar-weighted) so neither view
     misleads.

  4. Trades that don't have 20 days of forward data (i.e. signals in the
     last 20 days of the window) are dropped from win-rate stats — they're
     "open" but their outcome is unknown. Counted separately as
     "still open" so the user can see how many were excluded.

  5. Historical scoring uses ONLY the 4 price/volume factors (Momentum,
     Volume Surge, Relative Strength, Range Proximity) re-weighted to sum
     to 1.0. Same limitation as run_backtest — Options Flow + Short
     Interest are only available as current snapshots from yfinance.

  6. Costs/slippage: 0 by default. We mention the gap in the report
     footer rather than baking in an assumption that might not match the
     user's broker.
"""
from __future__ import annotations
from datetime import datetime
import pandas as pd
import numpy as np

import data_utils as du
from scanner_factors import factors
import run_backtest as bt


# ── parameters ────────────────────────────────────────────────────────────────
DEFAULT_LOOKBACK_DAYS = 63          # ~3 months of trading (was 60 / ~2 months)
HOLD_DAYS = bt.FWD_WINDOW           # 20-day hold — match per-ticker backtest

# Position sizing (Tier B audit fix):
# Previously fixed 100 shares — caused systematic distortion where a 100-share
# NVDA position ($21k) carried 2x the exposure of a 100-share MXL ($10k), yet
# both counted equally toward win rate. Equal-dollar sizing makes the
# trade-count win rate economically meaningful: every position represents the
# same dollar exposure, so a 60% win rate actually means "the system makes
# money in 60% of equal-sized bets."
DEFAULT_TARGET_DOLLARS = 10_000     # target $ exposure per trade

# Transaction cost assumption (Tier A audit fix):
# Real-world trading adds spread + commission per round-trip. 7.5 bps per side
# (15 bps round-trip) is a reasonable mid-point for retail brokers in 2026:
#   - Commission: $0 on most brokers (zero-commission era)
#   - Spread (half-spread paid): 3-8 bps on most large-caps, more on smaller
#   - Hidden price impact / slippage: 2-5 bps even on small retail orders
# Net: ~7.5 bps per execution side. Configurable via run() argument.
DEFAULT_COST_BPS_PER_SIDE = 7.5     # 7.5 basis points per side (0.075%)

# Signal thresholds — match the live engine's STATUS_TIERS
LONG_ENTRY_THRESHOLD = 65           # TRADABLE tier lower bound
SHORT_ENTRY_THRESHOLD = 35          # CAUTION/AVOID boundary (entry < 35)

# Filters added after the v1 trade log review surfaced a SHORT-side
# capitulation trap. The v1 log showed:
#   - SHORT in BEAR regime: 33% win rate, -$10,632 (5 catastrophic losers)
#   - SHORT in SIDEWAYS:    70% win rate, +$8,285
# Diagnosis: BEAR-regime shorts entered at oversold lows right before
# the dead-cat bounce — sold the bottom, not the top. The 52-week range
# filter prevents shorting already-deeply-oversold names.
SHORT_MIN_RANGE_POSITION = 40.0     # 52-wk range position (%); skip shorts below this

# Mean Reversion strategy gates (added after MR trade-log audit).
# The MR trade log showed: LONGs only worked when 52w_pos < 2% (n=2 deeply
# oversold winners); SHORTs failed across all mid-range entries (50-65% pos).
# Conclusion: MR is a TAIL-of-distribution strategy — it works at extremes,
# not at mid-range. These gates enforce that selectivity at the signal-
# detection layer (belt) on top of the new range-position-based scoring
# formula (suspenders).
MR_LONG_MAX_RANGE_POSITION = 30.0   # MR LONG requires range_pos < 30 (truly oversold)
MR_SHORT_MIN_RANGE_POSITION = 70.0  # MR SHORT requires range_pos > 70 (truly overbought)

# MR SHORTs are currently DISABLED based on cumulative evidence:
#   - v1 MR trade log (mid-range SHORTs):    n=7, wr=43%, total=-$5,933
#   - Post-gate-fix v2 (GOOGL SHORT 72%):    n=1, wr= 0%, total=-$2,137 closed
#                                            + MXL SHORT 100% paper loss -$2,700
#   - Pattern: MR SHORTs lose money in this market regime regardless of how
#     selective the range gate is. The MXL SHORT at the literal 52-week high
#     (the cleanest "true overbought" trigger possible) went UP 27% in 14
#     trading days. This isn't a gate calibration issue; it's a regime issue.
#     When the broader market is trending up, fighting individual overbought
#     names with MR SHORTs is fighting the tape.
# To re-enable: set this to False. The MR_SHORT_MIN_RANGE_POSITION gate above
# remains in place as a safety net even if you re-enable; this flag is a
# higher-priority override.
MR_SHORTS_DISABLED = True
# LONGs blocked on these fundamental grades. D ("weak but viable") and
# E ("structurally broken") both block — see _GRADE_THRESHOLDS comment in
# run_scanner.py for the D/E split rationale. The block is symmetric: both
# treated identically for trading. Only the visual indicator differs.
LONG_GRADE_BLOCK = {"D", "E"}

# TREND SHORTs are currently DISABLED based on cumulative evidence:
#   - TREND trade log audit (excluding MXL data corruption):
#       n=13, wr=54%, total=-$2,093 — basically breakeven before costs;
#       net loss after costs
#   - LONG side worked: n=8, wr=75%, total=+$12,177
#   - Pattern: TREND SHORTs at mid-range positions (40-80%) on Grade-B
#     mega-caps in SIDEWAYS regime kept getting run over as the broader
#     market trended up. The current 40% range gate prevents capitulation
#     shorts but not "trend resumption" shorts.
#   - Conservative posture: keep what works (LONG breakouts), cut what doesn't
#     (SHORTs that fight the tape).
# To re-enable: set to False. All existing SHORT-side gates (regime,
# range_pos > 40%) remain in place if you re-enable; this is a kill switch
# above them.
TREND_SHORTS_DISABLED = True

# Per-ticker signal cooldown — minimum trading days between consecutive
# signals on the same ticker. Without this, MRNA can fire 3 SHORT signals
# in 14 days, AAPL can fire 2 LONG signals in 4 days, etc — looking like
# diversified bets when they're actually correlated reruns of the same
# signal. With cooldown, each ticker contributes at most one signal per
# 7-trading-day window.
TICKER_SIGNAL_COOLDOWN_DAYS = 7

# Price corruption detection — if any adjacent-day close-to-close move in
# a ticker's score series exceeds this ratio, the data is likely corrupt
# (mixed split-adjusted/unadjusted prices, partial fetches from yfinance
# "Invalid Crumb" failures, etc). Skip the ticker entirely.
# 1.5 = 50% single-day move. Real stocks rarely cross this even on the
# worst earnings days; when they do (rare biotech FDA decisions), getting
# excluded from backtest is a feature not a bug — those days are noise.
PRICE_CORRUPTION_MAX_DAILY_RATIO = 1.5


def _detect_price_corruption(series: pd.DataFrame) -> tuple[bool, str]:
    """Detect data corruption in a ticker's price series.

    Returns:
        (is_corrupt, reason) — is_corrupt True means the series should be
        excluded from the backtest. reason is human-readable description
        suitable for showing in a warning.

    Detection logic:
        Check every adjacent-day Close ratio. If any ratio is outside
        [1/MAX_RATIO, MAX_RATIO] = [0.67, 1.5] with default 1.5, the data
        is suspicious. Real stocks crossing this threshold do exist
        (earnings gaps, FDA approvals, biotech catastrophes) but they're
        rare AND for the few that are real, excluding the ticker from
        backtest is the safer call than letting the phantom move drive
        a 300%+ paper P&L number.

    What this catches:
        - MXL pattern: yfinance returned a mix of split-adjusted and
          non-adjusted prices, producing phantom 4x-5x intraday moves
        - "Invalid Crumb" partial fetches: some date ranges returned data,
          others didn't, the gap between them is a multi-week jump
        - Stock splits: most splits are handled correctly by yfinance's
          adjusted-close, but occasional ones aren't and this catches them
    """
    if series is None or series.empty or "Close" not in series.columns:
        return False, ""
    closes = series["Close"].dropna()
    if len(closes) < 2:
        return False, ""
    # Compute close[t] / close[t-1] for every adjacent pair
    ratios = closes / closes.shift(1)
    ratios = ratios.dropna()
    if len(ratios) == 0:
        return False, ""
    max_ratio = ratios.max()
    min_ratio = ratios.min()
    # Outliers: ratio > MAX_RATIO (price tripled etc) or < 1/MAX_RATIO (collapsed)
    upper_bound = PRICE_CORRUPTION_MAX_DAILY_RATIO
    lower_bound = 1.0 / PRICE_CORRUPTION_MAX_DAILY_RATIO
    if max_ratio > upper_bound:
        bad_date = ratios.idxmax()
        prev_date_loc = closes.index.get_loc(bad_date) - 1
        prev_date = closes.index[prev_date_loc] if prev_date_loc >= 0 else None
        prev_close = closes.loc[prev_date] if prev_date is not None else None
        curr_close = closes.loc[bad_date]
        prev_str = f"${prev_close:.2f}" if prev_close is not None else "?"
        return (True,
                f"phantom price jump on {bad_date.strftime('%Y-%m-%d')}: "
                f"{prev_str} → ${curr_close:.2f} "
                f"({(max_ratio-1)*100:+.0f}% in one day)")
    if min_ratio < lower_bound:
        bad_date = ratios.idxmin()
        prev_date_loc = closes.index.get_loc(bad_date) - 1
        prev_date = closes.index[prev_date_loc] if prev_date_loc >= 0 else None
        prev_close = closes.loc[prev_date] if prev_date is not None else None
        curr_close = closes.loc[bad_date]
        prev_str = f"${prev_close:.2f}" if prev_close is not None else "?"
        return (True,
                f"phantom price collapse on {bad_date.strftime('%Y-%m-%d')}: "
                f"{prev_str} → ${curr_close:.2f} "
                f"({(min_ratio-1)*100:+.0f}% in one day)")
    return False, ""


def _per_ticker_score_series(ticker: str, strategy: str,
                              macro_history: dict | None,
                              history_days: int = 400) -> pd.DataFrame | None:
    """Reuse run_backtest's machinery to produce a daily score series
    for one ticker. Returns DataFrame with index=date, columns=[Score,
    Status, Close, Regime]. None if data fetch fails."""
    close = du.get_close_series(ticker, days=history_days)
    if close is None or len(close) < 60:
        return None
    close.index = pd.to_datetime(close.index)

    spy_close = du.get_close_series("SPY", days=history_days)
    if spy_close is None or spy_close.empty:
        return None
    spy_close.index = pd.to_datetime(spy_close.index)

    # Need volume too. get_history returns OHLCV; fetch fresh.
    hist = du.get_history(ticker, days=history_days)
    if hist is None or hist.empty or "Volume" not in hist:
        return None
    hist.index = pd.to_datetime(hist.index)
    volume = hist["Volume"]

    # Run per-day factor series via run_backtest's helpers
    weights = bt._weights_for(strategy)
    mom  = bt._momentum_series(close, strategy)
    vol  = bt._volume_series(volume)
    rs   = bt._rel_strength_series(close, spy_close, strategy)
    rng  = bt._range_series(close, strategy)

    aligned = pd.concat({
        "Close": close,
        "Momentum": mom,
        "Volume Surge": vol,
        "Relative Strength": rs,
        "Range Proximity": rng,
    }, axis=1).dropna()
    if aligned.empty:
        return None

    # 52-week range position (0-100%) — needed for the SHORT capitulation
    # filter. Computed as: (close - 52w_low) / (52w_high - 52w_low) * 100.
    # min_periods=60 matches the existing _range_series so the series
    # populates with realistic data after about 3 months of history.
    rolling_high = close.rolling(252, min_periods=60).max()
    rolling_low  = close.rolling(252, min_periods=60).min()
    range_pos = ((close - rolling_low) /
                  (rolling_high - rolling_low + 1e-9) * 100).clip(0, 100)
    aligned["RangePos52w"] = range_pos.reindex(aligned.index)

    # Composite uses the SAME weighting model as run_backtest.run():
    # 4 replayable factors at their live weights, plus SI + Options Flow
    # pinned at neutral 50 (the live engine values that yfinance can't
    # replay historically).
    composite = (
        weights["Momentum"]          * aligned["Momentum"]
      + weights["Volume Surge"]      * aligned["Volume Surge"]
      + weights["Relative Strength"] * aligned["Relative Strength"]
      + weights["Range Proximity"]   * aligned["Range Proximity"]
      + weights["Short Interest"]    * bt.NEUTRAL_FACTOR
      + weights["Options Flow"]      * bt.NEUTRAL_FACTOR
    )
    aligned["Score"] = composite.clip(lower=0, upper=100).round(0)

    aligned["Regime"] = bt._regime_series(macro_history, aligned.index)
    aligned["Status"] = aligned["Score"].apply(lambda s: bt._status(s)[0])
    return aligned[["Close", "Score", "Status", "Regime", "RangePos52w"]]


def _detect_signals(series: pd.DataFrame, ticker: str,
                     grade: str | None = None,
                     strategy: str = factors.TREND) -> tuple[list[dict], dict]:
    """Walk a score series chronologically and emit signal dicts for each
    LONG/SHORT entry crossing that PASSES the entry filters.

    Filters are STRATEGY-AWARE — MR has tighter range-position requirements
    than TREND because the MR trade log audit showed MR is a tail-of-
    distribution strategy (only works at extreme oversold/overbought).

    TREND filters:
      LONG:
        - Regime gate: BEAR blocks LONGs (matches live engine)
        - Grade gate: Grade-D and Grade-E names blocked (don't buy weak
                       businesses on technical setups). Grade-A/B/C/N/A all
                       pass. D ("weak but viable") and E ("structurally
                       broken") are both blocked; the visual distinction is
                       informational only.
      SHORT:
        - Regime gate: ONLY SIDEWAYS permits SHORTs. Both BULL (fighting
                       the trend) and BEAR (capitulation trap) are blocked.
                       This INVERTS the v1 rule based on trade-log evidence.
        - Range gate: skip SHORTs when 52-week range position < 40%. Below
                       40% means the name is already deeply oversold, which
                       in the v1 log produced the dead-cat-bounce pattern.

    MR (Mean Reversion) filters — added after MR trade log audit:
      LONG:
        - Range gate: REQUIRE range_pos < 30 (must be genuinely oversold).
                       The MR trade log showed both winning LONGs were at
                       0-1% of range; mid-range "MR LONGs" don't actually
                       mean-revert.
      SHORT:
        - Range gate: REQUIRE range_pos > 70 (must be genuinely overbought).
                       The MR trade log's 7 losing SHORTs were all at
                       48-64% — mid-range, not actually overbought. They
                       lost an average -8.5% per trade.

    Returns:
        (passed_signals, rejection_counts)
        - passed_signals: list of trade dicts that passed all filters
        - rejection_counts: dict tracking why filtered signals were dropped,
                            so the UI can show the user how many entries
                            each filter caught (transparency).

    Each passed signal:
        {"ticker", "entry_date", "direction", "entry_score", "regime",
         "range_pos_52w", "grade"}
    """
    signals = []
    rejections = {"long_regime_bear": 0, "long_grade_d": 0,
                  "short_regime_bull": 0, "short_regime_bear": 0,
                  "short_range_low": 0,
                  "mr_long_not_oversold": 0,
                  "mr_short_not_overbought": 0,
                  "mr_short_disabled": 0,
                  "trend_short_disabled": 0,
                  "same_ticker_cooldown": 0}
    if series is None or series.empty:
        return signals, rejections

    is_mr = (strategy == factors.MEAN_REVERSION)
    # Track most recent entry date (any direction) for cooldown enforcement.
    # Allows at most one signal per TICKER_SIGNAL_COOLDOWN_DAYS window so we
    # don't get correlated reruns (AAPL × 2 in 4 days, MRNA × 3 in 14 days)
    # masquerading as diversified bets.
    last_signal_date = None

    prev_score = None
    for date, row in series.iterrows():
        score = row["Score"]
        regime = row["Regime"]
        range_pos = row.get("RangePos52w")
        if prev_score is None:
            prev_score = score
            continue

        # ── LONG entry: crossed up through 65 ──
        if prev_score < LONG_ENTRY_THRESHOLD <= score:
            if regime == factors.BEAR:
                rejections["long_regime_bear"] += 1
            elif grade in LONG_GRADE_BLOCK:
                rejections["long_grade_d"] += 1
            elif is_mr and (range_pos is None
                            or range_pos > MR_LONG_MAX_RANGE_POSITION):
                # MR LONG must be genuinely oversold (< 30% of range). The
                # MR trade log showed BOTH winning LONGs were at 0-1% of
                # range — mean reversion only works at extremes for the
                # long side too, not at mid-range "looks like a dip" entries.
                rejections["mr_long_not_oversold"] += 1
            elif (last_signal_date is not None
                  and (date - last_signal_date).days < TICKER_SIGNAL_COOLDOWN_DAYS):
                # Too soon after the previous signal on this ticker — block
                # to prevent correlated bets masquerading as independent.
                rejections["same_ticker_cooldown"] += 1
            else:
                signals.append({
                    "ticker": ticker, "entry_date": date,
                    "direction": "LONG", "entry_score": float(score),
                    "regime": regime,
                    "range_pos_52w": (float(range_pos)
                                       if range_pos is not None else None),
                    "grade": grade or "N/A",
                })
                last_signal_date = date

        # ── SHORT entry: crossed down through 35 ──
        elif prev_score >= SHORT_ENTRY_THRESHOLD > score:
            # Strategy-specific kill switches — check FIRST so the rejection
            # bucket correctly attributes the block to the disable flag
            # rather than masking it behind a regime/range failure that
            # would have caught it anyway.
            if is_mr and MR_SHORTS_DISABLED:
                rejections["mr_short_disabled"] += 1
            elif (not is_mr) and TREND_SHORTS_DISABLED:
                rejections["trend_short_disabled"] += 1
            elif regime == factors.BULL:
                rejections["short_regime_bull"] += 1
            elif regime == factors.BEAR:
                # Inverted from v1 — trade log showed BEAR is the toxic
                # regime for SHORTs (33% win rate, -$10,632). SIDEWAYS-only
                # SHORTs avoid the oversold-bounce trap.
                rejections["short_regime_bear"] += 1
            elif (range_pos is not None
                  and range_pos < SHORT_MIN_RANGE_POSITION):
                # Already deep in 52-week range — capitulation trap (TREND rule)
                rejections["short_range_low"] += 1
            elif is_mr and (range_pos is None
                            or range_pos < MR_SHORT_MIN_RANGE_POSITION):
                # MR SHORT must be genuinely overbought (> 70% of range).
                # The MR trade log audit identified this as the engine's
                # biggest structural problem: 7 SHORT signals all fired at
                # mid-range (48-64% of 52-week range) and averaged -8.5%
                # P&L. Mid-range is not overbought — it's neutral.
                # (Defense in depth — this gate stays in place even when
                #  MR_SHORTS_DISABLED is False.)
                rejections["mr_short_not_overbought"] += 1
            elif (last_signal_date is not None
                  and (date - last_signal_date).days < TICKER_SIGNAL_COOLDOWN_DAYS):
                rejections["same_ticker_cooldown"] += 1
            else:
                signals.append({
                    "ticker": ticker, "entry_date": date,
                    "direction": "SHORT", "entry_score": float(score),
                    "regime": regime,
                    "range_pos_52w": (float(range_pos)
                                       if range_pos is not None else None),
                    "grade": grade or "N/A",
                })
                last_signal_date = date

        prev_score = score
    return signals, rejections


def _simulate_trade(signal: dict, series: pd.DataFrame,
                     target_dollars: float = DEFAULT_TARGET_DOLLARS,
                     cost_bps_per_side: float = DEFAULT_COST_BPS_PER_SIDE
                     ) -> dict:
    """Simulate one trade with equal-dollar position sizing + transaction costs.

    Sizing: shares = int(target_dollars / entry_price). Minimum 1 share —
    won't open positions on tickers priced above target_dollars (e.g.
    Berkshire-A at $700k+) but every other ticker gets a sane sizing.

    Costs: cost_bps_per_side charged on BOTH entry and exit notional values.
    Default 7.5 bps each side = 15 bps round-trip. Charged as dollar cost,
    deducted from gross P&L.

    Returns:
      - status="closed": full 20-day hold completed, real P&L
      - status="open":   signal fired but hold period extends past data;
                         P&L=None, NOT counted in win-rate stats
      - status="skipped": entry date missing from series (rare data issue)
    """
    entry_date = signal["entry_date"]
    if entry_date not in series.index:
        return {**signal, "status": "skipped",
                "reason": "entry date missing from series"}

    entry_loc = series.index.get_loc(entry_date)
    exit_loc = entry_loc + HOLD_DAYS
    entry_price = float(series["Close"].iloc[entry_loc])

    # Equal-dollar sizing — derive shares from target $ exposure. floor to
    # int (no fractional shares — most retail brokers still don't support
    # them universally). Minimum 1 share so absurdly expensive names (BRK-A)
    # don't generate zero-share signals.
    shares = max(1, int(target_dollars / entry_price))

    if exit_loc >= len(series):
        # Trade still open — entered too late in the window to complete
        # 20-day hold. NOT force-closed (per design): we mark it as open
        # and exclude from win-rate stats. UI surfaces these separately
        # so users can see how many were excluded from the win-rate sample.
        return {
            **signal, "status": "open",
            "entry_price": entry_price,
            "exit_date": None, "exit_price": None,
            "shares": shares,
            "notional_entry": shares * entry_price,
            "pnl": None, "pnl_pct": None,
        }

    exit_date = series.index[exit_loc]
    exit_price = float(series["Close"].iloc[exit_loc])

    # Direction-aware gross P&L
    if signal["direction"] == "LONG":
        gross_pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1) * 100
    else:  # SHORT
        gross_pnl = (entry_price - exit_price) * shares
        pnl_pct = (entry_price / exit_price - 1) * 100

    # Transaction costs (both sides). cost_bps_per_side is a percentage,
    # so divide by 10_000 to convert basis-points → decimal multiplier.
    # Cost applies to the NOTIONAL value at each execution.
    cost_mult = cost_bps_per_side / 10_000
    entry_cost = shares * entry_price * cost_mult
    exit_cost  = shares * exit_price  * cost_mult
    total_cost = entry_cost + exit_cost
    net_pnl = gross_pnl - total_cost

    # Recompute pnl_pct on net basis (so the % includes cost drag)
    notional_entry = shares * entry_price
    net_pnl_pct = (net_pnl / notional_entry) * 100 if notional_entry else 0

    return {
        **signal,
        "status": "closed",
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "shares": shares,
        "notional_entry": notional_entry,
        "gross_pnl": gross_pnl,
        "transaction_cost": total_cost,
        "pnl": net_pnl,           # NET P&L — what users see in win-rate stats
        "pnl_pct": net_pnl_pct,
        "winner": net_pnl > 0,
    }


def run(watchlist: list[str], strategy: str,
        macro_history: dict | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        target_dollars: float = DEFAULT_TARGET_DOLLARS,
        cost_bps_per_side: float = DEFAULT_COST_BPS_PER_SIDE) -> dict:
    """Run the portfolio-level backtest.

    Args:
        watchlist:     list of tickers (e.g. ["AAPL", "MSFT", ...])
        strategy:      "Trend-Following" or "Mean Reversion"
        macro_history: optional dict from macro_history.regime_timeseries()
                       used to gate trades by historical regime. None =
                       skip regime gating (SIDEWAYS for every day).
        lookback_days: how many trading days back to consider for signal
                       detection (default 63 ≈ 3 months).
        target_dollars: target $ exposure per trade for equal-dollar sizing
                        (default $10,000). Replaces the prior fixed-100-shares
                        sizing — see DEFAULT_TARGET_DOLLARS comment.
        cost_bps_per_side: transaction cost in basis points charged on each
                           side of the trade (default 7.5 bps = 0.075%).
                           Round-trip cost = 2 × this value.

    Returns:
        {
          "strategy": str,
          "watchlist": list,
          "lookback_days": int,
          "hold_days": int,
          "shares_per_trade": int,
          "window_start": datetime,
          "window_end": datetime,
          "trades": list of trade dicts (closed + open),
          "stats_overall": {
            "n_closed": int, "n_winners": int, "n_losers": int,
            "n_open": int,
            "win_rate": float | None,   # None if n_closed == 0
            "total_pnl": float,
            "avg_pnl": float | None,
            "avg_pnl_pct": float | None,
            "best_trade": dict | None,
            "worst_trade": dict | None,
          },
          "stats_long":   {...same shape, LONG trades only...},
          "stats_short":  {...same shape, SHORT trades only...},
          "stats_per_ticker": [{ticker, n_trades, win_rate, total_pnl}, ...],
          "warnings": list of str,
        }
    """
    timestamp = datetime.now().isoformat()
    warnings = []
    if not watchlist:
        return _empty_result(strategy, watchlist, lookback_days,
                              "Watchlist is empty.", timestamp)

    # ── 1. Build per-ticker score series ──
    series_by_ticker = {}
    skipped = []
    illiquid = []  # tickers with avg daily dollar volume below threshold
    corrupt: list[tuple[str, str]] = []  # (ticker, reason)
    for t in watchlist:
        # Liquidity gate (Tier A audit fix) — skip tickers that can't be
        # traded at scale. Cheap check; cached via the underlying history
        # fetch's TTL so this adds no network cost.
        liquid, adv = du.is_liquid(t)
        if not liquid:
            illiquid.append((t, adv))
            continue
        series = _per_ticker_score_series(t, strategy, macro_history)
        if series is None:
            skipped.append(t)
            continue
        # Price-corruption gate (added after TREND audit revealed MXL data
        # showing phantom 4x jump from $16 → $52 between 2026-03-30 and
        # 2026-04-28, producing a -$22k phantom SHORT loss and +$32k phantom
        # LONG win — both fake). Excluding tickers with implausible single-
        # day moves prevents these from polluting the trade log.
        is_corrupt, reason = _detect_price_corruption(series)
        if is_corrupt:
            corrupt.append((t, reason))
            continue
        series_by_ticker[t] = series
    if not series_by_ticker:
        return _empty_result(strategy, watchlist, lookback_days,
                              "No tickers returned usable history.",
                              timestamp)
    if skipped:
        warnings.append(
            f"{len(skipped)} ticker(s) skipped due to insufficient data: "
            + ", ".join(skipped))
    if illiquid:
        warnings.append(
            f"{len(illiquid)} ticker(s) skipped — average daily dollar "
            f"volume below ${du.MIN_DOLLAR_VOLUME_USD/1e6:.0f}M floor: "
            + ", ".join(
                f"{t} (${adv/1e6:.1f}M/day)" if adv else f"{t} (—)"
                for t, adv in illiquid))
    if corrupt:
        warnings.append(
            f"⚠ {len(corrupt)} ticker(s) excluded due to suspicious price "
            f"data (likely split-adjustment or yfinance fetch issues): "
            + "; ".join(f"{t} — {reason}" for t, reason in corrupt))

    # ── 2. Limit signal-detection window to the last N trading days ──
    # Find a common end date and restrict the per-ticker frames
    all_indices = [s.index for s in series_by_ticker.values()]
    window_end = min(idx.max() for idx in all_indices)
    cutoff_loc_estimates = []
    for s in series_by_ticker.values():
        # find the index N trading days before window_end
        end_loc = s.index.get_indexer([window_end], method="nearest")[0]
        cutoff_loc_estimates.append(max(0, end_loc - lookback_days + 1))
    # Use a uniform window: scan signals in each ticker's last N rows
    # (might differ slightly per ticker if data is missing — minor)

    # ── 3. Fetch fundamental grade per ticker (used for LONG filter) ──
    # IMPORTANT — this is *current* fundamental data applied to *historical*
    # trades. Technically look-ahead bias, but fundamentals change slowly
    # (90-day quarterly cadence) so a name's grade today is a near-perfect
    # proxy for what it was 60 days ago. Documented in the methodology
    # expander on the UI so users know.
    import run_scanner as sc
    grade_by_ticker = {}
    for t in series_by_ticker:
        try:
            grade_info = sc.calculate_fundamental_grade(t)
            grade_by_ticker[t] = grade_info.get("grade", "N/A")
        except Exception:
            grade_by_ticker[t] = "N/A"

    # ── 4. Detect signals ticker-by-ticker ──
    all_signals = []
    rejection_totals = {"long_regime_bear": 0, "long_grade_d": 0,
                        "short_regime_bull": 0, "short_regime_bear": 0,
                        "short_range_low": 0,
                        "mr_long_not_oversold": 0,
                        "mr_short_not_overbought": 0,
                  "mr_short_disabled": 0,
                  "trend_short_disabled": 0,
                  "same_ticker_cooldown": 0}
    for t, s in series_by_ticker.items():
        # restrict to last lookback_days trading rows
        windowed = s.tail(lookback_days + 1)  # +1 so the FIRST window day can detect a crossing
        sigs, rej = _detect_signals(windowed, t,
                                     grade=grade_by_ticker.get(t),
                                     strategy=strategy)
        all_signals.extend(sigs)
        for k, v in rej.items():
            rejection_totals[k] += v

    if not all_signals:
        warnings.append("No signal crossings passed entry filters in the "
                        "window. Try a longer lookback or different strategy.")
        # Even with zero signals, surface what the filters rejected
        if any(rejection_totals.values()):
            rej_msg = "Filters rejected: " + ", ".join(
                f"{v} {k.replace('_', ' ')}"
                for k, v in rejection_totals.items() if v > 0)
            warnings.append(rej_msg)
        window_start = window_end - pd.Timedelta(days=lookback_days * 1.5)
        return {
            "strategy": strategy, "watchlist": watchlist,
            "lookback_days": lookback_days, "hold_days": HOLD_DAYS,
            "target_dollars": target_dollars,
            "cost_bps_per_side": cost_bps_per_side,
            "window_start": window_start, "window_end": window_end,
            "trades": [], "stats_overall": _empty_stats(),
            "stats_long": _empty_stats(), "stats_short": _empty_stats(),
            "stats_per_ticker": [], "warnings": warnings,
            "rejections": rejection_totals,
            "illiquid_skipped": illiquid,
            "timestamp": timestamp,
        }

    # ── 5. Simulate each trade ──
    trades = []
    for sig in all_signals:
        series = series_by_ticker[sig["ticker"]]
        trade = _simulate_trade(sig, series,
                                 target_dollars=target_dollars,
                                 cost_bps_per_side=cost_bps_per_side)
        trades.append(trade)

    # sort by entry_date for chronological readability
    trades.sort(key=lambda t: t["entry_date"])

    # ── 5. Aggregate stats ──
    closed = [t for t in trades if t["status"] == "closed"]
    open_  = [t for t in trades if t["status"] == "open"]

    stats_overall = _aggregate(closed, open_)
    stats_long  = _aggregate(
        [t for t in closed if t["direction"] == "LONG"],
        [t for t in open_  if t["direction"] == "LONG"])
    stats_short = _aggregate(
        [t for t in closed if t["direction"] == "SHORT"],
        [t for t in open_  if t["direction"] == "SHORT"])

    # per-ticker breakdown
    per_ticker = []
    for t in sorted(series_by_ticker.keys()):
        t_closed = [tr for tr in closed if tr["ticker"] == t]
        t_open   = [tr for tr in open_  if tr["ticker"] == t]
        if not t_closed and not t_open:
            continue
        wins = sum(1 for tr in t_closed if tr["winner"])
        per_ticker.append({
            "ticker": t,
            "n_trades": len(t_closed),
            "n_open": len(t_open),
            "n_winners": wins,
            "win_rate": (wins / len(t_closed) * 100
                         if t_closed else None),
            "total_pnl": sum(tr["pnl"] for tr in t_closed),
        })

    window_start = min(t["entry_date"] for t in trades)

    return {
        "strategy": strategy, "watchlist": watchlist,
        "lookback_days": lookback_days, "hold_days": HOLD_DAYS,
        "target_dollars": target_dollars,
            "cost_bps_per_side": cost_bps_per_side,
        "window_start": window_start, "window_end": window_end,
        "trades": trades, "stats_overall": stats_overall,
        "stats_long": stats_long, "stats_short": stats_short,
        "stats_per_ticker": per_ticker, "warnings": warnings,
        "rejections": rejection_totals,
        "illiquid_skipped": illiquid,
        "timestamp": timestamp,
    }


def _aggregate(closed_trades: list[dict], open_trades: list[dict]) -> dict:
    """Compute summary stats for a list of closed + open trades."""
    n_closed = len(closed_trades)
    n_open = len(open_trades)
    if n_closed == 0:
        return {
            "n_closed": 0, "n_winners": 0, "n_losers": 0,
            "n_open": n_open, "win_rate": None,
            "total_pnl": 0.0, "avg_pnl": None, "avg_pnl_pct": None,
            "best_trade": None, "worst_trade": None,
        }
    winners = [t for t in closed_trades if t["winner"]]
    losers = [t for t in closed_trades if not t["winner"]]
    total_pnl = sum(t["pnl"] for t in closed_trades)
    avg_pnl = total_pnl / n_closed
    avg_pct = sum(t["pnl_pct"] for t in closed_trades) / n_closed
    best = max(closed_trades, key=lambda t: t["pnl"])
    worst = min(closed_trades, key=lambda t: t["pnl"])
    return {
        "n_closed": n_closed,
        "n_winners": len(winners),
        "n_losers": len(losers),
        "n_open": n_open,
        "win_rate": len(winners) / n_closed * 100,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "avg_pnl_pct": avg_pct,
        "best_trade": best,
        "worst_trade": worst,
    }


def _empty_stats() -> dict:
    return {
        "n_closed": 0, "n_winners": 0, "n_losers": 0, "n_open": 0,
        "win_rate": None, "total_pnl": 0.0, "avg_pnl": None,
        "avg_pnl_pct": None, "best_trade": None, "worst_trade": None,
    }


def _empty_result(strategy, watchlist, lookback_days, reason, timestamp):
    return {
        "strategy": strategy, "watchlist": watchlist,
        "lookback_days": lookback_days, "hold_days": HOLD_DAYS,
        "target_dollars": DEFAULT_TARGET_DOLLARS,
        "cost_bps_per_side": DEFAULT_COST_BPS_PER_SIDE,
        "window_start": None, "window_end": None,
        "trades": [], "stats_overall": _empty_stats(),
        "stats_long": _empty_stats(), "stats_short": _empty_stats(),
        "stats_per_ticker": [],
        "warnings": [reason],
        "rejections": {"long_regime_bear": 0, "long_grade_d": 0,
                       "short_regime_bull": 0, "short_regime_bear": 0,
                       "short_range_low": 0,
                       "mr_long_not_oversold": 0,
                       "mr_short_not_overbought": 0,
                       "mr_short_disabled": 0,
                       "trend_short_disabled": 0,
                       "same_ticker_cooldown": 0},
        "illiquid_skipped": [],
        "timestamp": timestamp,
    }
