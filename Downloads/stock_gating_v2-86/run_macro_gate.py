"""
run_macro_gate.py — PAGE 1: MarketSense.

Answers: "Should I be deploying capital right now?"

The Composite Macro Score (0-100) is a strict, equally-weighted average of
7 internal metrics:

  1. VIX Level            5. VIX Momentum (20d ROC of VIX)
  2. VIX Term Structure   6. Factor Crowding
  3. Sector Breadth       7. Mega-Cap Rotation (MAGS/SPY)
  4. Credit Spreads

The CNN Fear & Greed Index is fetched separately and DECOUPLED — it is shown
next to the composite for reference only and is NOT part of the score.
"""

from __future__ import annotations
import concurrent.futures
from datetime import datetime

from macro_signals import signals_a, signals_b

# The 7 internal metrics — equally weighted (composite = simple average).
INTERNAL_SIGNALS = [
    "VIX Level",
    "VIX Term Structure",
    "Sector Breadth",
    "Credit Spreads",
    "VIX Momentum",
    "Factor Crowding",
    "Mega-Cap Rotation",
]

# ── Institutional Flow weighted macro composite ──────────────────────────────
# Rationale: credit-market stress remains the highest-signal regime indicator
# (25%). Sector Breadth is bumped to 20% — together with Mega-Cap Rotation (10%)
# equity-side signals now carry 30% of the composite, reducing the overlap
# from the volatility cluster (VIX Level + VIX Term + VIX Momentum = 40% of
# the composite, but these three move together — they don't add 4 independent
# signals, they add ~1.5 signals' worth of fear data repeated). Weights sum to 1.0.
WEIGHTS = {
    "Credit Spreads":     0.25,   # Debt Market Stress — highest-signal regime indicator
    "Sector Breadth":     0.20,   # Uptrend Health     — % of 11 sector ETFs above 50-SMA
    "VIX Term Structure": 0.15,   # Crash Warning      — contango / backwardation signal
    "VIX Level":          0.15,   # Current Fear Gauge — spot volatility level
    "VIX Momentum":       0.10,   # Fear Velocity      — 20-day VIX ROC
    "Mega-Cap Rotation":  0.10,   # Big Money Flow     — MAGS / SPY
    "Factor Crowding":    0.05,   # Algorithmic Stability — pairwise correlation
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, \
    f"Macro WEIGHTS must sum to 1.0; got {sum(WEIGHTS.values())}"
assert set(WEIGHTS.keys()) == set(INTERNAL_SIGNALS), \
    "Macro WEIGHTS keys must match INTERNAL_SIGNALS exactly"


def compute_composite(scores_by_name: dict) -> float:
    """
    SINGLE SOURCE OF TRUTH for the 7-metric Composite Macro Score.

    Institutional Flow weighted average of the 7 internal signals (CNN F&G
    excluded). Missing signals default to neutral 50.0 so the score still
    computes when one metric is unavailable. Result is clamped to [0, 100]
    and rounded to the nearest integer.

    See `WEIGHTS` (above) for the per-signal weighting and rationale.
    """
    raw = sum(WEIGHTS[n] * scores_by_name.get(n, 50.0) for n in WEIGHTS)
    # Each input is already clamped to [0, 100] by its signal function, and
    # the weights sum to 1.0, so raw is mathematically in [0, 100]. The
    # explicit clamp + int() is a belt-and-braces guard against any future
    # caller passing out-of-bounds scores (which would silently skew the
    # composite without it).
    bounded = max(0.0, min(100.0, raw))
    return int(round(bounded))


def classify(score: float) -> dict:
    """Map composite score to deployment regime."""
    if score >= 70:
        return {
            "regime": "BULL REGIME", "color": "#22e08a", "scanner_enabled": True,
            "message": "Macro conditions support full capital deployment.",
        }
    elif score >= 40:
        return {
            "regime": "SIDEWAYS REGIME", "color": "#f5c344", "scanner_enabled": True,
            "message": "Mixed conditions — deploy selectively at reduced exposure.",
        }
    else:
        return {
            "regime": "BEAR REGIME", "color": "#ff5d6c", "scanner_enabled": False,
            "message": "Defensive regime — scanner disabled, preserve capital.",
        }


def run(watchlist: list | None = None,
        horizon: str = "Swing Trade System") -> dict:
    """
    Execute the 7 internal macro signals concurrently, plus the decoupled
    Fear & Greed reading.

    `watchlist` is kept on the API for back-compat but Sector Breadth (the
    replacement for the old Watchlist Breadth) does NOT depend on the user's
    watchlist — it samples the 11 SPDR sector ETFs for a stable, market-wide
    measure that doesn't drift between users.

    `horizon` (SWING / LONG_TERM) only affects two macro signals:
      - Sector Breadth: SMA window stretches (50 -> 200)
      - VIX Momentum: ROC window stretches (20 -> 60)
    The other five signals are point-in-time or already-long-lookback and
    are not horizon-sensitive.
    """
    # the 7 internal signal callables (Sector Breadth ignores watchlist)
    signal_funcs = {
        "VIX Level":          signals_a.vix_level,
        "VIX Term Structure": signals_a.vix_term_structure,
        "Sector Breadth":     lambda: signals_a.sector_breadth(
                                          watchlist, horizon=horizon),
        "Credit Spreads":     signals_b.credit_spreads,
        "VIX Momentum":       lambda: signals_b.vix_momentum(horizon=horizon),
        "Factor Crowding":    signals_b.factor_crowding,
        "Mega-Cap Rotation":  signals_b.megacap_rotation,
    }

    signals: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {name: pool.submit(fn) for name, fn in signal_funcs.items()}
        # decoupled Fear & Greed runs in the same pool but is kept separate
        fg_future = pool.submit(signals_b.fear_and_greed)

        for name, fut in futures.items():
            try:
                signals[name] = fut.result(timeout=90)
            except Exception as e:
                signals[name] = {
                    "name": name, "score": 50.0, "status": "error",
                    "detail": f"Error: {e}", "error": str(e),
                }
        try:
            fear_greed = fg_future.result(timeout=30)
        except Exception as e:
            fear_greed = {"name": "Fear & Greed", "score": 50.0,
                          "status": "error", "detail": f"Error: {e}"}

    # Composite computed via the canonical single-source-of-truth function
    scores = {n: signals[n].get("score", 50.0) for n in WEIGHTS}
    composite = compute_composite(scores)

    regime = classify(composite)

    return {
        "timestamp": datetime.now().isoformat(),
        "horizon": horizon,
        "composite_score": composite,
        "weights": WEIGHTS,
        "signals": signals,            # the 7 internal signals
        "fear_greed": fear_greed,      # decoupled — reference only
        **regime,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
