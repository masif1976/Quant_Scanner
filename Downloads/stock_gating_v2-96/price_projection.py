"""
price_projection.py — Forward-looking price target generators.

Three independent methodologies for estimating where price might go. These
are DIFFERENT LENSES, not competing predictions — each measures something
different and they will legitimately disagree:

  1. Options Expected Move — statistical 1-sigma range implied by the
     options market. Has a real probabilistic basis (it's literally what
     option prices encode). Best for "where will price likely stay."

  2. Floor Trader Pivots — structural support/resistance from the prior
     period's range. Decades of use by floor traders; works as a
     self-fulfilling reference grid. Best for mean-reversion targets.

  3. Fibonacci Trend Extensions — momentum-continuation targets from a
     swing path. Widely watched but NOT statistically validated — the
     edge, if any, is self-fulfilling (lots of traders watch the same
     levels). Best treated as "zones others are watching," not magic.

All functions are pure (no side effects, no I/O) and individually testable.
This module deliberately does NOT touch the existing ATR-based trade_plan
logic — it's an additive set of alternative projections.
"""

from __future__ import annotations
import math


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via Abramowitz-Stegun 26.2.17 (no scipy dep)."""
    # Constants
    a1, a2, a3 = 0.319381530, -0.356563782, 1.781477937
    a4, a5 = -1.821255978, 1.330274429
    k = 1.0 / (1.0 + 0.2316419 * abs(x))
    w = (a1 * k + a2 * k**2 + a3 * k**3 + a4 * k**4 + a5 * k**5)
    w = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2.0) * w
    return w if x >= 0 else 1.0 - w


def _bs_price(spot: float, strike: float, t_years: float, sigma: float,
               kind: str) -> float:
    """Black-Scholes price with risk-free rate = 0 (good enough for short-
    dated options where the rate term is negligible). Used to back out
    implied volatility numerically when a data feed doesn't supply it."""
    if sigma <= 0 or t_years <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t_years) / \
         (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    else:  # put
        return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol_from_price(option_price: float, spot: float, strike: float,
                            dte: int, kind: str = "put") -> float | None:
    """Back out annualized implied volatility from an option's market price
    via bisection on the Black-Scholes formula (r=0).

    This is the genuine meaning of "implied" volatility — the sigma that
    makes the theoretical price equal the observed market price. Use this
    as a fallback when a data feed (e.g. yfinance free tier) returns 0 or
    missing IV but DOES return a usable bid/ask/last price.

    Args:
        option_price: observed option price (use mid of bid/ask, or last)
        spot:         underlying spot price
        strike:       option strike
        dte:          days to expiration
        kind:         "put" or "call"

    Returns:
        Annualized IV as a PERCENTAGE (e.g. 45.0 for 45%), or None if it
        can't be solved (price below intrinsic, non-convergent, etc).

    Bisection over sigma in [0.01, 5.0] (1% to 500% annualized). Returns
    None rather than a garbage edge value if the price is outside the
    arbitrage-free range.
    """
    if (option_price is None or spot is None or strike is None
            or option_price <= 0 or spot <= 0 or strike <= 0 or dte <= 0):
        return None
    t = dte / 365.0

    # Intrinsic value floor — price below intrinsic is un-solvable (bad data)
    if kind == "put":
        intrinsic = max(strike - spot, 0.0)
    else:
        intrinsic = max(spot - strike, 0.0)
    if option_price < intrinsic - 0.01:
        return None  # price below intrinsic → no valid IV

    lo, hi = 0.01, 5.0
    # Ensure the target is bracketed; if even max-vol price is below the
    # observed price, the price is implausibly high → bail.
    price_at_hi = _bs_price(spot, strike, t, hi, kind)
    if price_at_hi < option_price:
        return None

    # Bisection — 60 iterations is overkill-accurate for a 1-D monotonic solve
    for _ in range(60):
        mid = (lo + hi) / 2.0
        price_mid = _bs_price(spot, strike, t, mid, kind)
        if abs(price_mid - option_price) < 1e-4:
            break
        if price_mid < option_price:
            lo = mid
        else:
            hi = mid
    sigma = (lo + hi) / 2.0
    iv_pct = round(sigma * 100, 1)
    # Sanity bounds — reject implausible solutions at the bracket edges
    if iv_pct < 1.0 or iv_pct > 499.0:
        return None
    return iv_pct


# ─────────────────────────────────────────────────────────────────────────
# 1. Options-Based Expected Move (1-Standard-Deviation range)
# ─────────────────────────────────────────────────────────────────────────

def expected_move(spot_price: float, iv: float, dte: int = 7) -> dict | None:
    """1-sigma expected price range implied by options-market volatility.

    The options market prices in roughly a 68.3% probability that the
    stock finishes within ±1 standard deviation over the given horizon.
    This is the textbook expected-move formula:

        expected_move = spot × (IV/100) × sqrt(DTE/365)

    Args:
        spot_price: current price (dollars)
        iv:         annualized implied volatility as a PERCENTAGE
                    (e.g. 45.0 for 45%, not 0.45)
        dte:        days to expiration (default 7 = weekly cycle)

    Returns:
        dict with:
          move_dollars:   the ±dollar amount of the 1-sigma move
          move_pct:       that move as a percent of spot
          upper_boundary: spot + move
          lower_boundary: spot - move
          dte, iv, spot:  echoed inputs
        Returns None on invalid inputs (non-positive spot/iv/dte).

    Note: this is the EXPIRY-distribution 1-sigma. The probability of
    TOUCHING a boundary intraday before expiry is roughly 2× the
    probability of finishing beyond it — so ~68% inside is an
    at-expiry figure, not a never-touched figure.
    """
    if spot_price is None or iv is None or dte is None:
        return None
    try:
        spot = float(spot_price)
        iv_frac = float(iv) / 100.0
        days = float(dte)
    except (TypeError, ValueError):
        return None
    if spot <= 0 or iv_frac <= 0 or days <= 0:
        return None

    move = spot * iv_frac * math.sqrt(days / 365.0)
    return {
        "move_dollars":   round(move, 2),
        "move_pct":       round(move / spot * 100, 2),
        "upper_boundary": round(spot + move, 2),
        "lower_boundary": round(spot - move, 2),
        "spot":           round(spot, 2),
        "iv":             round(float(iv), 1),
        "dte":            int(days),
    }


def prob_finish_beyond(spot: float, target: float, iv: float,
                        dte: int) -> float | None:
    """Probability the stock FINISHES beyond `target` at expiry, under a
    lognormal random-walk with zero drift (the standard options-pricing
    assumption).

    For a target ABOVE spot, returns P(S_T >= target).
    For a target BELOW spot, returns P(S_T <= target).
    Either way: the probability of ending up on the far side of the level.

    Math: under zero-drift geometric Brownian motion,
        ln(S_T/S_0) ~ Normal(-0.5 σ² T, σ² T)
    so P(S_T >= K) = N(-d2) where
        d2 = [ln(S_0/K) - 0.5 σ² T] / (σ √T)

    Args:
        spot, target: prices (dollars)
        iv:   annualized implied volatility as a PERCENTAGE (45.0 = 45%)
        dte:  days to expiration

    Returns a probability in [0, 1], or None on invalid input.

    IMPORTANT: this is a MODEL probability under a zero-drift lognormal
    walk — NOT an empirical frequency and NOT a forecast. Real markets
    have fat tails, drift, and event risk the model ignores.
    """
    if spot is None or target is None or iv is None or dte is None:
        return None
    try:
        s = float(spot); k = float(target)
        sigma = float(iv) / 100.0; t = float(dte) / 365.0
    except (TypeError, ValueError):
        return None
    if s <= 0 or k <= 0 or sigma <= 0 or t <= 0:
        return None

    d2 = (math.log(s / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    if k >= s:
        # Probability of finishing AT or ABOVE an upside target
        return round(_norm_cdf(d2), 4)
    else:
        # Probability of finishing AT or BELOW a downside target
        # P(S_T <= K) = 1 - P(S_T >= K) = 1 - N(d2) = N(-d2)
        return round(1.0 - _norm_cdf(d2), 4)


def prob_touch_before_expiry(spot: float, target: float, iv: float,
                              dte: int) -> float | None:
    """Probability the stock TOUCHES `target` at any point before expiry,
    under zero-drift geometric Brownian motion.

    For a single absorbing barrier with zero drift, the reflection
    principle gives a clean result: the touch probability is exactly
    TWICE the finish-beyond probability (capped at 1.0).

        P(touch K before T) = 2 × P(S_T beyond K)

    This is why a target can have, say, a 14% chance of being the closing
    price but a ~28% chance of being touched intraday at some point.

    Args:
        spot, target, iv (percentage), dte — same as prob_finish_beyond.

    Returns a probability in [0, 1], or None on invalid input.

    Same modeling caveats as prob_finish_beyond: zero-drift lognormal,
    no fat tails, no events. A model estimate, not a forecast.
    """
    finish = prob_finish_beyond(spot, target, iv, dte)
    if finish is None:
        return None
    return round(min(2.0 * finish, 1.0), 4)


def probability_table(spot: float, iv: float, dte: int,
                       levels: list[float] | None = None) -> list[dict] | None:
    """Build a table of reach-probabilities for a set of price levels.

    For each level, computes both the touch-before-expiry and
    finish-beyond-at-expiry probabilities. If no levels are supplied,
    generates a sensible default ladder spanning roughly ±1.5 expected
    moves around spot.

    Returns a list of dicts (sorted high → low price), each:
        {level, pct_from_spot, prob_touch, prob_finish, direction}
    or None on invalid input.
    """
    if spot is None or iv is None or dte is None:
        return None
    try:
        s = float(spot); sigma_pct = float(iv); d = int(dte)
    except (TypeError, ValueError):
        return None
    if s <= 0 or sigma_pct <= 0 or d <= 0:
        return None

    if not levels:
        # Default ladder: spot ± {0.5, 1.0, 1.5} expected moves
        em = expected_move(s, sigma_pct, d)
        if em is None:
            return None
        move = em["move_dollars"]
        offsets = [1.5, 1.0, 0.5, -0.5, -1.0, -1.5]
        levels = [round(s + o * move, 2) for o in offsets]

    rows = []
    for lvl in sorted(set(levels), reverse=True):
        if lvl <= 0:
            continue
        touch = prob_touch_before_expiry(s, lvl, sigma_pct, d)
        finish = prob_finish_beyond(s, lvl, sigma_pct, d)
        if touch is None or finish is None:
            continue
        rows.append({
            "level": round(lvl, 2),
            "pct_from_spot": round((lvl - s) / s * 100, 1),
            "prob_touch": touch,
            "prob_finish": finish,
            "direction": "up" if lvl >= s else "down",
        })
    return rows if rows else None


# ─────────────────────────────────────────────────────────────────────────
# 2. Floor Trader Pivots (classic structural levels)
# ─────────────────────────────────────────────────────────────────────────

def floor_pivots(high: float, low: float, close: float) -> dict | None:
    """Classic floor-trader pivot levels from the prior period's HLC.

    Standard formulas:
        P  = (High + Low + Close) / 3
        R1 = (P × 2) - Low
        S1 = (P × 2) - High
        R2 = P + (High - Low)
        S2 = P - (High - Low)

    Args:
        high:  prior period high
        low:   prior period low
        close: prior period close

    Returns:
        dict with pivot (P), r1, r2, s1, s2 — or None on invalid input.

    The central pivot P is the day's "fair value" reference; price above
    P leans bullish, below P leans bearish. R/S levels are where mean-
    reversion traders expect reactions.
    """
    if high is None or low is None or close is None:
        return None
    try:
        h, l, c = float(high), float(low), float(close)
    except (TypeError, ValueError):
        return None
    if h <= 0 or l <= 0 or c <= 0 or h < l:
        return None

    p = (h + l + c) / 3.0
    rng = h - l
    return {
        "pivot": round(p, 2),
        "r1":    round((p * 2) - l, 2),
        "s1":    round((p * 2) - h, 2),
        "r2":    round(p + rng, 2),
        "s2":    round(p - rng, 2),
        "range": round(rng, 2),
    }


# ─────────────────────────────────────────────────────────────────────────
# 3. Fibonacci Trend Extensions (momentum-continuation targets)
# ─────────────────────────────────────────────────────────────────────────

def fibonacci_extensions(swing_high: float, swing_low: float,
                          pullback_low: float) -> dict | None:
    """Fibonacci extension targets for trend continuation.

    Given a measured swing (low → high) and a pullback, project where the
    next leg might extend to:

        leg = swing_high - swing_low
        Ext 161.8% = pullback_low + (leg × 1.618)
        Ext 261.8% = pullback_low + (leg × 2.618)

    Args:
        swing_high:   the high of the prior up-swing
        swing_low:    the low that started the prior up-swing
        pullback_low: the low of the retracement (or current entry)

    Returns:
        dict with ext_1618, ext_2618, leg_size, and the echoed inputs —
        or None on invalid input.

    HONEST CAVEAT: Fibonacci extensions are widely watched but have no
    proven statistical edge. Any predictive value is largely self-
    fulfilling (many traders place orders at the same levels). Treat
    these as "zones other traders are watching," not as physics.
    """
    if swing_high is None or swing_low is None or pullback_low is None:
        return None
    try:
        sh, sl, pb = float(swing_high), float(swing_low), float(pullback_low)
    except (TypeError, ValueError):
        return None
    if sh <= 0 or sl <= 0 or pb <= 0:
        return None
    if sh <= sl:
        # Swing high must exceed swing low for a valid up-swing
        return None

    leg = sh - sl
    return {
        "ext_1618":     round(pb + leg * 1.618, 2),
        "ext_2618":     round(pb + leg * 2.618, 2),
        "leg_size":     round(leg, 2),
        "swing_high":   round(sh, 2),
        "swing_low":    round(sl, 2),
        "pullback_low": round(pb, 2),
    }
