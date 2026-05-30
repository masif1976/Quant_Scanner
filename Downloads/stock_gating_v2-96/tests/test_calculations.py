"""
test_calculations.py — Unit tests for the pure calculation layer.

Per audit finding L-2: the pure-math modules are the highest-value,
lowest-effort test targets. They take values and return values with no
Streamlit, no network, no I/O — so they run fast and deterministically.

Run with:
    cd stock_gating_v2
    python -m pytest tests/ -v

Or without pytest:
    python tests/test_calculations.py

Covers:
  - price_projection: expected move, probabilities, pivots, fibonacci, IV solver
  - trade_plan: ATR computation
  - market_cap_groups: tier classification boundaries
  - assigned_positions: wheel close P&L formula (pure arithmetic check)
  - run_macro_gate: composite weighting bounds
"""

from __future__ import annotations
import math
import os
import sys

# Make the project root importable whether run via pytest or directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ─────────────────────────────────────────────────────────────────────────
# price_projection — expected move
# ─────────────────────────────────────────────────────────────────────────

def test_expected_move_basic():
    import price_projection as pp
    em = pp.expected_move(200.0, 45.0, 7)
    expected = 200 * 0.45 * math.sqrt(7 / 365)
    assert abs(em["move_dollars"] - expected) < 0.01
    assert em["upper_boundary"] == round(200 + expected, 2)
    assert em["lower_boundary"] == round(200 - expected, 2)
    assert em["dte"] == 7


def test_expected_move_scales_with_horizon():
    import price_projection as pp
    em7 = pp.expected_move(200.0, 45.0, 7)
    em30 = pp.expected_move(200.0, 45.0, 30)
    # Longer horizon → wider move (sqrt-time scaling)
    assert em30["move_dollars"] > em7["move_dollars"]


def test_expected_move_invalid_inputs():
    import price_projection as pp
    assert pp.expected_move(0, 45, 7) is None
    assert pp.expected_move(200, 0, 7) is None
    assert pp.expected_move(200, 45, 0) is None
    assert pp.expected_move(None, 45, 7) is None


# ─────────────────────────────────────────────────────────────────────────
# price_projection — probabilities
# ─────────────────────────────────────────────────────────────────────────

def test_atm_finish_probability_near_half():
    import price_projection as pp
    # A target at spot should be ~50% to finish above (tiny drift drag)
    p = pp.prob_finish_beyond(500, 500, 45, 7)
    assert 0.47 <= p <= 0.51


def test_touch_is_double_finish():
    import price_projection as pp
    # Reflection principle: touch ≈ 2 × finish (capped at 1.0)
    for tgt in (520, 540, 560):
        pf = pp.prob_finish_beyond(500, tgt, 45, 7)
        pt = pp.prob_touch_before_expiry(500, tgt, 45, 7)
        assert abs(pt - min(2 * pf, 1.0)) < 1e-6


def test_probability_monotonic_with_distance():
    import price_projection as pp
    probs = [pp.prob_touch_before_expiry(500, t, 45, 7)
             for t in (510, 530, 550, 582)]
    for i in range(len(probs) - 1):
        assert probs[i] > probs[i + 1]


def test_probability_symmetric_up_down():
    import price_projection as pp
    pf_down = pp.prob_finish_beyond(500, 480, 45, 7)
    pf_up = pp.prob_finish_beyond(500, 520, 45, 7)
    assert abs(pf_down - pf_up) < 0.02


def test_longer_horizon_higher_touch():
    import price_projection as pp
    assert (pp.prob_touch_before_expiry(500, 540, 45, 28)
            > pp.prob_touch_before_expiry(500, 540, 45, 7))


def test_probability_table_structure():
    import price_projection as pp
    table = pp.probability_table(500, 45, 7)
    assert table is not None and len(table) >= 4
    # Sorted high → low
    assert table[0]["level"] > table[-1]["level"]
    # Spans both directions
    assert any(r["direction"] == "up" for r in table)
    assert any(r["direction"] == "down" for r in table)


def test_probability_invalid_inputs():
    import price_projection as pp
    assert pp.prob_finish_beyond(0, 500, 45, 7) is None
    assert pp.prob_touch_before_expiry(500, 540, 0, 7) is None
    assert pp.probability_table(500, 45, 0) is None


# ─────────────────────────────────────────────────────────────────────────
# price_projection — floor pivots
# ─────────────────────────────────────────────────────────────────────────

def test_floor_pivots_known_values():
    import price_projection as pp
    piv = pp.floor_pivots(105, 95, 100)
    assert piv["pivot"] == 100.0
    assert piv["r1"] == 105.0
    assert piv["s1"] == 95.0
    assert piv["r2"] == 110.0
    assert piv["s2"] == 90.0


def test_floor_pivots_invalid():
    import price_projection as pp
    assert pp.floor_pivots(95, 105, 100) is None  # high < low
    assert pp.floor_pivots(0, 95, 100) is None
    assert pp.floor_pivots(None, 95, 100) is None


# ─────────────────────────────────────────────────────────────────────────
# price_projection — fibonacci
# ─────────────────────────────────────────────────────────────────────────

def test_fibonacci_extensions_known():
    import price_projection as pp
    fib = pp.fibonacci_extensions(120, 100, 110)
    assert fib["leg_size"] == 20.0
    assert fib["ext_1618"] == round(110 + 20 * 1.618, 2)
    assert fib["ext_2618"] == round(110 + 20 * 2.618, 2)


def test_fibonacci_invalid():
    import price_projection as pp
    assert pp.fibonacci_extensions(100, 120, 110) is None  # high <= low
    assert pp.fibonacci_extensions(120, 100, 0) is None


# ─────────────────────────────────────────────────────────────────────────
# price_projection — implied vol solver (round-trip)
# ─────────────────────────────────────────────────────────────────────────

def test_iv_solver_roundtrip():
    import price_projection as pp
    # Generate a BS price at a known IV, then recover that IV from the price
    for spot, strike, dte, kind, true_iv in [
        (495, 466, 8, "put", 45.0),
        (495, 525, 8, "call", 42.0),
        (100, 95, 30, "put", 30.0),
        (200, 220, 14, "call", 60.0),
    ]:
        t = dte / 365.0
        price = pp._bs_price(spot, strike, t, true_iv / 100, kind)
        recovered = pp.implied_vol_from_price(price, spot, strike, dte, kind)
        assert recovered is not None
        assert abs(recovered - true_iv) < 0.2


def test_iv_solver_rejects_below_intrinsic():
    import price_projection as pp
    # Put with intrinsic = 30 (strike 525, spot 495); price 0.50 < intrinsic
    assert pp.implied_vol_from_price(0.50, 495, 525, 8, "put") is None
    assert pp.implied_vol_from_price(0, 495, 466, 8, "put") is None


# ─────────────────────────────────────────────────────────────────────────
# trade_plan — ATR
# ─────────────────────────────────────────────────────────────────────────

def test_compute_atr_constant_range():
    import pandas as pd
    import trade_plan as tp
    # Constant $10 high-low spread, flat closes → ATR should be ~$10
    n = 30
    df = pd.DataFrame({
        "High": [105.0] * n,
        "Low": [95.0] * n,
        "Close": [100.0] * n,
    }, index=pd.date_range("2026-01-01", periods=n, freq="B"))
    atr = tp.compute_atr(df, window=14)
    assert atr is not None
    assert abs(atr - 10.0) < 0.5


def test_compute_atr_insufficient_data():
    import pandas as pd
    import trade_plan as tp
    df = pd.DataFrame({
        "High": [105.0, 106.0],
        "Low": [95.0, 96.0],
        "Close": [100.0, 101.0],
    }, index=pd.date_range("2026-01-01", periods=2, freq="B"))
    assert tp.compute_atr(df, window=14) is None


# ─────────────────────────────────────────────────────────────────────────
# market_cap_groups — tier boundaries
# ─────────────────────────────────────────────────────────────────────────

def test_cap_tier_thresholds():
    import market_cap_groups as mcg
    # Boundaries: mega ≥ 400B, large ≥ 50B, mid ≥ 10B, small ≥ 2B, micro < 2B
    assert mcg.classify_cap(3_500_000_000_000) == "mega"   # AAPL-tier
    assert mcg.classify_cap(400_000_000_000) == "mega"      # exact boundary
    assert mcg.classify_cap(399_999_999_999) == "large"     # just under
    assert mcg.classify_cap(50_000_000_000) == "large"
    assert mcg.classify_cap(49_999_999_999) == "mid"
    assert mcg.classify_cap(10_000_000_000) == "mid"
    assert mcg.classify_cap(9_999_999_999) == "small"
    assert mcg.classify_cap(2_000_000_000) == "small"
    assert mcg.classify_cap(1_999_999_999) == "micro"


def test_cap_tier_missing_data():
    import market_cap_groups as mcg
    # None / zero / negative → unclassified (not a crash)
    assert mcg.classify_cap(None) == "unclassified"
    assert mcg.classify_cap(0) == "unclassified"


# ─────────────────────────────────────────────────────────────────────────
# Wheel close P&L — pure arithmetic check of the documented formula
# ─────────────────────────────────────────────────────────────────────────

def test_wheel_close_pnl_formula():
    # The documented formula (per share, × shares):
    #   put_premium + (closed_price - assigned_strike) + call_premium
    # Verify the arithmetic directly (no DB needed).
    assigned_strike = 100.0
    put_premium = 2.50       # collected when the put was sold
    shares = 100
    # Scenario A: shares called away at $105, collected $1.50 call premium
    closed_price = 105.0
    call_premium = 1.50
    share_pnl = (closed_price - assigned_strike) * shares     # +500
    put_p = put_premium * shares                              # +250
    call_p = call_premium * shares                            # +150
    total = put_p + share_pnl + call_p
    assert total == 900.0
    # Scenario B: shares drop, sold at $92, no call sold
    closed_price2 = 92.0
    share_pnl2 = (closed_price2 - assigned_strike) * shares   # -800
    total2 = put_premium * shares + share_pnl2 + 0
    assert total2 == -550.0  # 250 - 800


# ─────────────────────────────────────────────────────────────────────────
# Macro composite — weights sum to 1.0 (guards the assertion in module)
# ─────────────────────────────────────────────────────────────────────────

def test_macro_weights_sum_to_one():
    import run_macro_gate as rmg
    assert abs(sum(rmg.WEIGHTS.values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
# Direct runner (no pytest required)
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed of {len(tests)} tests")
    sys.exit(1 if failed else 0)
