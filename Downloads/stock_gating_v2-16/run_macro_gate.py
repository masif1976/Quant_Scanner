"""
run_macro_gate.py — PAGE 1: Macro Gate.

Answers: "Should I be deploying capital right now?"

The Composite Macro Score (0-100) is a strict, equally-weighted average of
7 internal metrics:

  1. VIX Level            5. Put/Call ROC
  2. VIX Term Structure   6. Factor Crowding
  3. Watchlist Breadth    7. Mega-Cap Rotation (MAGS/SPY)
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
    "Watchlist Breadth",
    "Credit Spreads",
    "Put/Call Sentiment",
    "Factor Crowding",
    "Mega-Cap Rotation",
]
_EQUAL_WEIGHT = round(1 / 7, 6)
WEIGHTS = {name: _EQUAL_WEIGHT for name in INTERNAL_SIGNALS}


def classify(score: float) -> dict:
    """Map composite score to deployment regime."""
    if score >= 70:
        return {
            "regime": "FULL DEPLOY", "color": "#22e08a", "scanner_enabled": True,
            "message": "Macro conditions support full capital deployment.",
        }
    elif score >= 40:
        return {
            "regime": "REDUCED", "color": "#f5c344", "scanner_enabled": True,
            "message": "Mixed conditions — deploy selectively at reduced exposure.",
        }
    else:
        return {
            "regime": "DEFENSIVE", "color": "#ff5d6c", "scanner_enabled": False,
            "message": "Defensive regime — scanner disabled, preserve capital.",
        }


def run(watchlist: list | None = None) -> dict:
    """
    Execute the 7 internal macro signals concurrently, plus the decoupled
    Fear & Greed reading. `watchlist` drives the Watchlist Breadth signal.
    """
    # the 7 internal signal callables (Watchlist Breadth takes the watchlist)
    signal_funcs = {
        "VIX Level":          signals_a.vix_level,
        "VIX Term Structure": signals_a.vix_term_structure,
        "Watchlist Breadth":  lambda: signals_a.market_breadth(watchlist),
        "Credit Spreads":     signals_b.credit_spreads,
        "Put/Call Sentiment": signals_b.put_call_sentiment,
        "Factor Crowding":    signals_b.factor_crowding,
        "Mega-Cap Rotation":  signals_b.sector_rotation,
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

    # Composite = strict equal-weighted average of the 7 internal signals only.
    composite = sum(WEIGHTS[n] * signals[n].get("score", 50.0) for n in WEIGHTS)
    composite = round(float(composite), 1)

    regime = classify(composite)

    return {
        "timestamp": datetime.now().isoformat(),
        "composite_score": composite,
        "weights": WEIGHTS,
        "signals": signals,            # the 7 internal signals
        "fear_greed": fear_greed,      # decoupled — reference only
        **regime,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
