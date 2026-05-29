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

# ── Strategy-specific Institutional Flow Weighted factor models ───────────────
# Different strategies want different things. TREND rewards leadership and
# momentum confirmation (RS + Momentum heaviest). MEAN_REVERSION rewards
# capitulation-position context (Range Proximity to a 52-week low + Options
# Flow + Volume exhaustion heaviest, with Momentum + RS de-emphasized since
# they're contra-indicators for a reversion trade).
#
# Both dicts MUST sum to exactly 1.0 (asserted below). The composite is a
# weighted sum of clamped factor scores -> mathematically bounded in [0, 100].
TREND_WEIGHTS = {
    "Relative Strength": 0.25,
    "Momentum":          0.25,
    "Volume Surge":      0.20,
    "Range Proximity":   0.15,
    "Options Flow":      0.10,
    "Short Interest":    0.05,
}
MR_WEIGHTS = {
    "Range Proximity":   0.30,
    "Options Flow":      0.25,
    "Volume Surge":      0.20,
    "Short Interest":    0.15,
    "Momentum":          0.05,
    "Relative Strength": 0.05,
}
assert abs(sum(TREND_WEIGHTS.values()) - 1.0) < 1e-9, \
    f"TREND_WEIGHTS must sum to 1.0; got {sum(TREND_WEIGHTS.values())}"
assert abs(sum(MR_WEIGHTS.values()) - 1.0) < 1e-9, \
    f"MR_WEIGHTS must sum to 1.0; got {sum(MR_WEIGHTS.values())}"
assert set(TREND_WEIGHTS) == set(MR_WEIGHTS), \
    "TREND and MR weight dicts must cover the same 6 factor names"


def _active_weights(strategy: str) -> dict:
    """Resolve the strategy name to the matching weights dict. Unknown
    strategies fall back to TREND_WEIGHTS (defensive: better to scan with
    *some* weighting than silently produce a zero composite)."""
    if strategy == MEAN_REVERSION:
        return MR_WEIGHTS
    return TREND_WEIGHTS


def factor_display_labels(strategy: str) -> dict:
    """Plain-English column labels with the *active strategy's* weight %
    appended, so the table header tells the user which strategy is driving
    the composite.

    Pure function — Page 2 can call this every render to refresh the
    headers when the strategy toggle changes.
    """
    w = _active_weights(strategy)
    base_label = {
        "Options Flow":      "Options Flow",
        "Volume Surge":      "Big Money Volume",
        "Momentum":           "Price Speed",
        "Relative Strength": "Market Leader",
        "Short Interest":    "Squeeze Fuel",
        "Range Proximity":   "Chart Position",
    }
    return {k: f"{base_label[k]} ({w[k]*100:.0f}%)" for k in w}


# ── Back-compat shim for legacy callers ──────────────────────────────────────
# Some code (and the backtest) still reads the module-level FACTOR_WEIGHTS.
# Keep the name pointing at TREND_WEIGHTS as a default so legacy paths don't
# crash. New code should call _active_weights(strategy) explicitly.
FACTOR_WEIGHTS = TREND_WEIGHTS

# Plain-English DEFAULT labels (TREND weights). Page 2 rebuilds these per
# render via factor_display_labels(strategy) so the table reflects the
# currently-active strategy's weighting.
FACTOR_DISPLAY_LABELS = factor_display_labels(TREND)

# ── Conviction Tiers (institutional bucketing) ──────────────────────────────
# Broader buckets than the underlying 0-100 score because distinguishing an 81
# from a 79 is false precision — factor noise is much larger than a 2-point
# gap. The 5 tiers below are the actual decision-level granularity. The exact
# integer score remains available in the table for users who want it.
STATUS_TIERS = [
    (80, "🟢 HIGH CONVICTION", "#22e08a"),
    (65, "🟢 TRADABLE",         "#7fd98a"),
    (50, "🟡 NEUTRAL",          "#f5c344"),
    (35, "🟠 CAUTION",          "#ff9442"),
    (0,  "🔴 AVOID / SHORT",    "#ff5d6c"),
]

# Legacy tier names from the prior 6-bucket directional-bias scheme. The
# signal journal (Page 5) may carry rows logged under these labels — when
# computing performance-by-tier, callers normalize old labels to the new
# conviction names via `normalize_tier_label()` below.
_LEGACY_TIER_MAP = {
    "🟢 STRONG LONG":  "🟢 HIGH CONVICTION",
    "🟢 LEAN LONG":    "🟢 TRADABLE",
    "🟡 HOLD / CASH":  "🟡 NEUTRAL",
    "🟠 WATCH SHORT":  "🟠 CAUTION",
    "🔴 LEAN SHORT":   "🔴 AVOID / SHORT",
    "🔴 STRONG SHORT": "🔴 AVOID / SHORT",
}


def classify_status(score: float) -> tuple[str, str]:
    """Map a 0-100 composite to (conviction tier label, hex color).

    Note: regime-blocked rows carry the sentinel score `-1` and are NOT
    routed through this function — they get the explicit "❌ BLOCKED BY
    REGIME" label upstream in `_composite()`."""
    for threshold, label, color in STATUS_TIERS:
        if score >= threshold:
            return label, color
    return STATUS_TIERS[-1][1], STATUS_TIERS[-1][2]


def normalize_tier_label(label) -> str:
    """Map any historical tier label (from a prior 6-bucket scheme or the
    current 5-bucket scheme) to the canonical current conviction name.
    Used by the Performance Journal so old logged signals group correctly
    alongside new ones."""
    if not label:
        return label
    return _LEGACY_TIER_MAP.get(label, label)


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
        # Aligned with the Conviction Tier "🟠 CAUTION" — this is a wait-and-see
        # zone, not a deploy-shorts zone. The action is wait-and-see.
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


# ── Fundamental Grade engine — 6-Pillar Institutional Model ────────────────
# Independent from the technical Conviction Tier and the macro-aware Tranche
# Action. This grades the underlying *business quality* of the company —
# a value/quality screen layered on top of the technical setup. A stock can
# have a TRADABLE technical signal AND a D fundamental grade (cheap junk
# bouncing) or a HIGH CONVICTION signal AND an A grade (great business at a
# breakout) — the two dimensions answer different questions.
#
# Model design notes:
#   - Six pillars, each scored 0-100 from absolute thresholds (not relative
#     percentiles). Per spec: missing data defaults to 50 (neutral) so a
#     sparse fetch doesn't punish a name unfairly. yfinance often returns
#     null for one or two fields on smaller-cap names.
#   - Pillars 1-4 weighted 20% each, pillars 5-6 weighted 10% each.
#     Sum = 100%.
#   - Letter grade brackets unchanged from prior version: A>=85, B 70-84,
#     C 50-69, D<50. N/A only when literally no fields came back at all.

# Letter-grade thresholds — order matters (descending). D and E are both
# considered weak fundamentals and both block LONGs by default (see
# LONG_GRADE_BLOCK in run_portfolio_backtest.py). The D/E split is purely
# informational: E flags structurally broken companies (multiple pillars
# scoring below 30), while D covers "weak but viable" names. Both treated
# identically by the engine; the visual distinction helps spot deep value
# traps vs ordinary low-quality names.
_GRADE_THRESHOLDS = [
    (85, "A", "#22e08a"),   # excellent — wide-moat quality
    (70, "B", "#7fd98a"),   # solid quality
    (50, "C", "#f5c344"),   # average / mixed
    (30, "D", "#ff9442"),   # weak — multiple soft pillars, but viable business
    (0,  "E", "#ff5d6c"),   # structurally broken — actively avoid
]

# Six-pillar weights — must sum to 1.0
_PILLAR_WEIGHTS = {
    "valuation":     0.20,
    "growth":        0.20,
    "profitability": 0.20,
    "cash_flow":     0.20,
    "balance_sheet": 0.10,
    "efficiency":    0.10,
}
assert abs(sum(_PILLAR_WEIGHTS.values()) - 1.0) < 1e-9


def _missing_neutral():
    """Per spec: missing data defaults to 50 (neutral). Centralized so
    every pillar uses the same fallback semantics."""
    return 50.0


# ── Pillar 1: Valuation ──────────────────────────────────────────────────
# Combines EV/EBITDA (preferred — capital-structure neutral) and forward
# P/E. Both scored individually then averaged within the pillar.

def _score_enterprise_to_ebitda(ev_ebitda) -> float:
    """EV/EBITDA — institutional value metric. Lower = cheaper, but
    extreme low can signal distress."""
    if ev_ebitda is None:
        return _missing_neutral()
    try:
        v = float(ev_ebitda)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v <= 0:               # negative EBITDA — unprofitable at operating level
        return 20.0
    if v < 6:                # very cheap — value or distress?
        return 85.0
    if v < 10:               # cheap-fair (traditional value)
        return 90.0
    if v < 15:               # fair (large-cap normal)
        return 75.0
    if v < 22:               # premium (growth)
        return 55.0
    if v < 35:               # expensive
        return 35.0
    return 15.0              # extremely expensive


def _score_forward_pe(pe) -> float:
    """Forward P/E — earnings-based valuation looking 12 months out.
    Same general curve as EV/EBITDA but on a different scale."""
    if pe is None:
        return _missing_neutral()
    try:
        v = float(pe)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v <= 0:               # forward earnings expected to be negative
        return 25.0
    if v < 10:               # cheap
        return 90.0
    if v < 18:               # fair (S&P historical median ~17)
        return 80.0
    if v < 28:               # mild premium (growth)
        return 60.0
    if v < 45:               # premium
        return 35.0
    return 15.0              # extremely expensive


def _score_valuation_pillar(f: dict) -> float:
    """Pillar 1 — average of EV/EBITDA + forward P/E sub-scores."""
    a = _score_enterprise_to_ebitda(f.get("enterprise_to_ebitda"))
    b = _score_forward_pe(f.get("forward_pe"))
    return round((a + b) / 2.0, 1)


# ── Pillar 2: Growth ─────────────────────────────────────────────────────
# Revenue growth + earnings growth, both YoY decimals from yfinance.

def _score_revenue_growth(rg) -> float:
    """YoY revenue growth as decimal (0.15 = 15%)."""
    if rg is None:
        return _missing_neutral()
    try:
        v = float(rg)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < -0.10:            # contracting >10%
        return 10.0
    if v < 0:                # mild contraction
        return 30.0
    if v < 0.05:             # stagnant
        return 50.0
    if v < 0.15:             # healthy
        return 75.0
    if v < 0.30:             # strong
        return 90.0
    return 95.0              # exceptional (capped — extreme values often noisy)


def _score_earnings_growth(eg) -> float:
    """YoY earnings growth as decimal. More volatile than revenue —
    a single quarter can swing this dramatically — but the directional
    signal still matters."""
    if eg is None:
        return _missing_neutral()
    try:
        v = float(eg)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < -0.25:            # collapse
        return 10.0
    if v < 0:                # negative
        return 30.0
    if v < 0.10:             # weak
        return 55.0
    if v < 0.25:             # strong
        return 80.0
    if v < 0.50:             # very strong
        return 92.0
    return 95.0              # exceptional


def _score_growth_pillar(f: dict) -> float:
    """Pillar 2 — average of revenue + earnings growth sub-scores."""
    a = _score_revenue_growth(f.get("revenue_growth"))
    b = _score_earnings_growth(f.get("earnings_growth"))
    return round((a + b) / 2.0, 1)


# ── Pillar 3: Profitability / Moat ───────────────────────────────────────
# Gross margins (pricing power) + operating margins (operational leverage).
# These are what wide-moat businesses look like in numbers.

def _score_gross_margins(gm) -> float:
    """Gross margins as decimal. Pricing power signal — businesses with
    moats (software, branded consumer) sustain >50%; commodity businesses
    (energy, materials) live around 20-30%."""
    if gm is None:
        return _missing_neutral()
    try:
        v = float(gm)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < 0:                # negative gross margin = catastrophic
        return 5.0
    if v < 0.15:             # commodity-like, no pricing power
        return 30.0
    if v < 0.30:             # industrial / retail
        return 50.0
    if v < 0.50:             # solid (consumer brands)
        return 75.0
    if v < 0.70:             # strong (software, pharma)
        return 90.0
    return 95.0              # exceptional (pure software, top biotech)


def _score_operating_margins(om) -> float:
    """Operating margins — what's left after running the business."""
    if om is None:
        return _missing_neutral()
    try:
        v = float(om)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < 0:                # losing money operationally
        return 10.0
    if v < 0.05:             # razor-thin (retail, airlines)
        return 35.0
    if v < 0.15:             # decent (large industrials)
        return 60.0
    if v < 0.25:             # strong (large-cap tech, premium consumer)
        return 80.0
    if v < 0.40:             # exceptional (top software, asset-light)
        return 92.0
    return 95.0              # rarefied (Visa, Mastercard)


def _score_profitability_pillar(f: dict) -> float:
    """Pillar 3 — average of gross + operating margin sub-scores."""
    a = _score_gross_margins(f.get("gross_margins"))
    b = _score_operating_margins(f.get("operating_margins"))
    return round((a + b) / 2.0, 1)


# ── Pillar 4: Cash Flow ──────────────────────────────────────────────────
# Free cash flow yield — FCF / market cap. The "free yield" institutional
# investors actually care about. A 5% FCF yield is solid; 10%+ is rare and
# strong; negative FCF on a growth name is fine, on a value name is alarming.

def _score_cash_flow_pillar(f: dict) -> float:
    """Pillar 4 — FCF yield (FCF / market cap) thresholded.

    Special case: when either FCF or market cap is missing, this pillar
    can't be computed — fall back to neutral 50 per spec. Negative FCF is
    NOT missing — it's a real (and bearish) signal. Score it 20."""
    fcf = f.get("free_cashflow")
    mcap = f.get("market_cap")
    if fcf is None or mcap is None or mcap <= 0:
        return _missing_neutral()
    try:
        yield_pct = (float(fcf) / float(mcap)) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return _missing_neutral()
    if yield_pct < -2:       # significant cash burn
        return 10.0
    if yield_pct < 0:        # mild cash burn (often fine for growth)
        return 30.0
    if yield_pct < 2:        # low but positive
        return 55.0
    if yield_pct < 5:        # healthy
        return 75.0
    if yield_pct < 10:       # strong
        return 90.0
    return 95.0              # exceptional (rare on large-caps)


# ── Pillar 5: Balance Sheet / Solvency ───────────────────────────────────
# Current ratio = current assets / current liabilities. Measures short-term
# liquidity. <1 means current liabilities exceed current assets (potential
# squeeze); 1-2 is healthy; >2 may indicate inefficient cash deployment.
# Note: banks/REITs legitimately don't have a current ratio — yfinance
# returns null for them, which becomes the neutral 50 default.

def _score_balance_sheet_pillar(f: dict) -> float:
    cr = f.get("current_ratio")
    if cr is None:
        return _missing_neutral()
    try:
        v = float(cr)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < 0.7:              # severe liquidity stress
        return 15.0
    if v < 1.0:              # under-collateralized short-term
        return 35.0
    if v < 1.5:              # adequate
        return 65.0
    if v < 2.5:              # strong (textbook ideal range)
        return 90.0
    if v < 4.0:              # very strong (or inefficient deployment?)
        return 80.0
    return 65.0              # excessive cash hoard or distorted reading


# ── Pillar 6: Efficiency ─────────────────────────────────────────────────
# Return on Equity — earnings / shareholder equity. Same caveat as before:
# heavily-leveraged or buyback-heavy companies show inflated ROE, so the
# upper end is capped to avoid rewarding leverage.

def _score_efficiency_pillar(f: dict) -> float:
    roe = f.get("roe")
    if roe is None:
        return _missing_neutral()
    try:
        v = float(roe)
    except (TypeError, ValueError):
        return _missing_neutral()
    if v < 0:                # losing money
        return 10.0
    if v < 0.05:             # weak
        return 30.0
    if v < 0.10:             # marginal
        return 50.0
    if v < 0.20:             # good
        return 70.0
    if v < 0.35:             # strong
        return 90.0
    return 95.0              # exceptional (cap — discourage leverage rewards)


def calculate_fundamental_grade(ticker: str,
                                 fundamentals: dict | None = None) -> dict:
    """Compute the 6-Pillar Fundamental Grade for a ticker.

    The six pillars (with weights):
      1. Valuation         (20%) — EV/EBITDA + forward P/E
      2. Growth            (20%) — revenue + earnings growth (YoY)
      3. Profitability     (20%) — gross + operating margins
      4. Cash Flow         (20%) — FCF / market cap (FCF yield)
      5. Balance Sheet     (10%) — current ratio
      6. Efficiency        (10%) — ROE

    Each pillar scored 0-100 from absolute thresholds. Missing data
    defaults to 50 (neutral) per spec. Weighted sum mapped to letter
    grade: A>=85, B 70-84, C 50-69, D 30-49, E <30.

    The D/E split distinguishes "weak but viable" (D) from "structurally
    broken" (E). Both are blocked from LONG trades by the backtest engine
    (LONG_GRADE_BLOCK in run_portfolio_backtest.py); the visual distinction
    is informational only.

    Args:
        ticker:       symbol (display only — pillar math uses fundamentals dict)
        fundamentals: pre-fetched dict from data_utils.get_fundamentals().
                      If omitted, fetches its own copy (1-hr cached).

    Returns:
        {
          "grade":     "A" | "B" | "C" | "D" | "E" | "N/A",
          "score":     0-100 float, or None if N/A
          "color":     hex string
          "label":     emoji + grade, e.g. "🟢 Grade: A"
          "detail":    pillar-level breakdown for tooltip
          "pillars":   {pillar_name: 0-100 score} — for transparency
        }
    """
    if fundamentals is None:
        try:
            import data_utils as du
            fundamentals = du.get_fundamentals(ticker)
        except Exception:
            fundamentals = None

    # N/A only when literally NO fields came back. Otherwise pillars with
    # missing data default to neutral 50 per spec.
    pillar_input_keys = (
        "enterprise_to_ebitda", "forward_pe",
        "revenue_growth", "earnings_growth",
        "gross_margins", "operating_margins",
        "free_cashflow", "market_cap",
        "current_ratio", "roe")
    usable = fundamentals and any(
        fundamentals.get(k) is not None for k in pillar_input_keys)
    if not usable:
        return {
            "grade": "N/A",
            "score": None,
            "color": "#7d8aa5",
            "label": "⚪ Grade: N/A",
            "detail": "fundamentals unavailable from yfinance",
            "pillars": {},
        }

    # Compute each pillar score
    pillars = {
        "valuation":     _score_valuation_pillar(fundamentals),
        "growth":        _score_growth_pillar(fundamentals),
        "profitability": _score_profitability_pillar(fundamentals),
        "cash_flow":     _score_cash_flow_pillar(fundamentals),
        "balance_sheet": _score_balance_sheet_pillar(fundamentals),
        "efficiency":    _score_efficiency_pillar(fundamentals),
    }

    # Track which pillars actually had usable input data vs. fell back to
    # the 50-neutral default. This is what powers the "no data — defaulted"
    # transparency on the UI side. A pillar counts as "has data" if AT LEAST
    # ONE of its inputs was non-null — partial data is still informative
    # (a Valuation pillar with EV/EBITDA but no Forward P/E is meaningful).
    pillar_inputs = {
        "valuation":     ("enterprise_to_ebitda", "forward_pe"),
        "growth":        ("revenue_growth", "earnings_growth"),
        "profitability": ("gross_margins", "operating_margins"),
        "cash_flow":     ("free_cashflow", "market_cap"),  # both required
        "balance_sheet": ("current_ratio",),
        "efficiency":    ("roe",),
    }
    pillar_data_present = {}
    for pname, fields in pillar_inputs.items():
        if pname == "cash_flow":
            # FCF yield requires BOTH FCF and market cap; either missing → no data
            fcf = fundamentals.get("free_cashflow")
            mcap = fundamentals.get("market_cap")
            pillar_data_present[pname] = (fcf is not None
                                          and mcap is not None
                                          and mcap > 0)
        else:
            pillar_data_present[pname] = any(
                fundamentals.get(f) is not None for f in fields)

    # Weighted master score
    raw = sum(_PILLAR_WEIGHTS[k] * pillars[k] for k in _PILLAR_WEIGHTS)
    score = max(0.0, min(100.0, raw))

    # Map to letter grade
    for thresh, letter, color in _GRADE_THRESHOLDS:
        if score >= thresh:
            grade, grade_color = letter, color
            break
    else:
        # Defensive: lowest tier in _GRADE_THRESHOLDS has thresh=0 so this
        # branch is unreachable for any non-negative score, but keep a sane
        # fallback in case scoring logic ever produces a negative composite.
        grade, grade_color = _GRADE_THRESHOLDS[-1][1], _GRADE_THRESHOLDS[-1][2]

    emoji = {"A": "🟢", "B": "🟢", "C": "🟡",
              "D": "🟠", "E": "🔴"}.get(grade, "⚪")

    # Pillar-level breakdown for the tooltip / audit trail
    detail = (
        f"Val {pillars['valuation']:.0f} · "
        f"Gro {pillars['growth']:.0f} · "
        f"Pro {pillars['profitability']:.0f} · "
        f"Cash {pillars['cash_flow']:.0f} · "
        f"Bal {pillars['balance_sheet']:.0f} · "
        f"Eff {pillars['efficiency']:.0f}"
    )

    return {
        "grade":   grade,
        "score":   round(score, 1),
        "color":   grade_color,
        "label":   f"{emoji} Grade: {grade}",
        "detail":  detail,
        "pillars": pillars,
        "pillar_data_present": pillar_data_present,
    }


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
              macro_score: float | None = None,
              horizon: str = factors.SWING) -> dict:
    """Compute all per-ticker factor results for the given strategy/regime.

    `horizon` (SWING or LONG_TERM) controls the lookback windows in the
    horizon-aware factors (Momentum, Volume Surge, Relative Strength).
    Range Proximity, Short Interest and Options Flow are horizon-independent
    by design — see LOOKBACKS in scanner_factors/factors.py."""
    f_mom = factors.momentum(df, strategy, macro_score=macro_score,
                              horizon=horizon)
    f_vol = factors.volume_surge(df, strategy, macro_score=macro_score,
                                  horizon=horizon)
    f_rs  = factors.relative_strength(df, spy_close, strategy,
                                       macro_score=macro_score,
                                       horizon=horizon)
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
                        "roe": None, "profit_margin": None,
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

    # Liquidity check (Tier A audit fix). Doesn't block the row — user added
    # this ticker deliberately and may have reasons — but surfaces a warning
    # so they know the signal may not be tradable at retail scale. Uses the
    # same $20M/day floor as the strategy backtest's hard filter.
    is_liquid, adv_usd = du.is_liquid(ticker)

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
        "is_liquid": is_liquid,          # True if avg dollar volume >= floor
        "avg_dollar_volume": adv_usd,    # average daily $ volume in USD
        "has_data": not df.empty,
    }


def _composite(raw_results: dict, watchlist: list, rankable_raw: dict,
               strategy: str = TREND) -> list:
    """Assemble final scored rows from raw factor results.

    `strategy` selects which weighting model is applied (TREND vs MR), since
    different strategies want different factor emphasis (see TREND_WEIGHTS
    and MR_WEIGHTS at the top of the module). The weighted sum produces a
    score in [0, 100] which is then clamped + rounded to integer.

    Regime-blocked factors are mathematically neutralized to 50.0 (instead of
    0.0) so they don't accidentally drive the composite into the SHORT tier.
    The row's composite is then sentineled with -1 so it sorts BELOW all
    valid shorts (which legitimately score 0-19).
    """
    weights = _active_weights(strategy)

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

        for fname in weights:
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

        # Composite = STRATEGY-SPECIFIC Institutional Flow weighted sum.
        # Each factor_score is already clamped to [0, 100] by its factor
        # function, and weights sum to exactly 1.0, so the raw sum is in
        # [0, 100]. Belt-and-braces clamp + int round per spec.
        raw_composite = sum(
            weights[f] * factor_scores[f] for f in weights)
        composite = int(round(max(0.0, min(100.0, raw_composite))))

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

        # Fundamental Grade — letter (A/B/C/D/N/A) derived from the same
        # `fund` dict we already fetched, no extra network calls.
        grade_result = calculate_fundamental_grade(t, fund)

        # Analyst recommendations — Finnhub-only signal (yfinance's scraped
        # recommendations are often empty). Light call, 24h cached, so the
        # cost across a 23-ticker watchlist is at most 23 cache misses.
        analyst = None
        try:
            import finnhub_client as fh
            analyst = fh.get_analyst_recommendations(t)
        except Exception:
            analyst = None

        rows.append({
            "ticker": t, "total_score": composite,
            "factor_scores": {k: round(v, 1) for k, v in factor_scores.items()},
            "factor_detail": {k: fr.get(k, {}).get("detail", "—")
                              for k in weights},
            "price": res.get("price"),
            "next_earnings": earn.get("next_earnings"),
            "earnings_days_away": earn.get("trading_days_away"),
            "earnings_flag": earn.get("flag", False),
            "status_label": status_label, "status_color": status_color,
            # decoupled display-only fields
            "trailing_pe": fund.get("trailing_pe"),
            "forward_pe": fund.get("forward_pe"),
            "roe": fund.get("roe"),
            "profit_margin": fund.get("profit_margin"),
            "fundamental_grade":       grade_result["grade"],
            "fundamental_grade_score": grade_result["score"],
            "fundamental_grade_color": grade_result["color"],
            "fundamental_grade_label": grade_result["label"],
            "fundamental_grade_detail": grade_result["detail"],
            # which data source served this ticker's fundamentals
            # ("finnhub" / "yfinance" / "lkg") — used for UI source badge
            "fundamental_source": fund.get("source", "unknown"),
            # Analyst consensus (Finnhub) — None if Finnhub returned nothing
            "analyst_consensus":    (analyst or {}).get("consensus"),
            "analyst_color":        (analyst or {}).get("consensus_color"),
            "analyst_score":        (analyst or {}).get("weighted_score"),
            "analyst_breakdown":    analyst,    # full dict for the panel
            # Keep the structured pillar dict so the UI can render a proper
            # row-per-pillar breakdown instead of cramming everything into
            # a single text line.
            "fundamental_grade_pillars": grade_result.get("pillars", {}),
            # Track which pillars had usable data vs. defaulted to neutral 50,
            # so the UI can mark "* defaulted" inline and summarize at the
            # bottom of the card.
            "fundamental_grade_pillar_data_present": grade_result.get(
                "pillar_data_present", {}),
            "range_high": res.get("range_high"),
            "range_low": res.get("range_low"),
            "range_position": res.get("range_position"),
            "rvol": res.get("rvol"),
            "rvol_label": res.get("rvol_label", "—"),
            "is_liquid": res.get("is_liquid", True),
            "avg_dollar_volume": res.get("avg_dollar_volume"),
        })
    return rows


def run(watchlist: list[str], strategy: str = TREND,
        macro_score: float | None = None,
        horizon: str = factors.SWING) -> dict:
    """Scan the custom watchlist with the active strategy engine.

    `macro_score` controls regime-aware factor behavior. When None the factors
    fall back to SIDEWAYS REGIME as a neutral default.
    `horizon` switches the lookback window family (SWING vs LONG_TERM).
    """
    watchlist = [t.upper().strip() for t in watchlist if t.strip()]
    if not watchlist:
        return {"timestamp": datetime.now().isoformat(), "rows": [],
                "strategy": strategy, "horizon": horizon,
                "error": "Empty watchlist"}

    # LONG_TERM uses 200-day windows -> need extra history to compute them.
    # SWING gets the original 120/400-day fetches; LONG_TERM stretches both.
    spy_days  = 300 if horizon == factors.LONG_TERM else 120
    hist_days = 500 if horizon == factors.LONG_TERM else 400

    spy_close = du.get_close_series("SPY", days=spy_days)
    data = du.get_bulk_history(watchlist, days=hist_days)

    raw_results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(watchlist))) as pool:
        futures = {
            pool.submit(_scan_one, t, data.get(t, pd.DataFrame()),
                        spy_close, strategy, True, macro_score, horizon): t
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

    rows = _composite(raw_results, watchlist, rankable_raw, strategy)
    # Sort by composite — blocked stocks have score -1 and naturally fall to
    # the bottom of the table as required by Phase 3.
    rows.sort(key=lambda r: r["total_score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "timestamp": datetime.now().isoformat(),
        "watchlist": watchlist, "strategy": strategy, "horizon": horizon,
        "macro_score": macro_score,
        # Report the ACTIVE strategy's weights — Page 2 displays these in
        # the table header so the user sees which weighting is in effect.
        "factor_weights": _active_weights(strategy), "rows": rows,
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

    rows = _composite(raw_results, universe, rankable_raw, strategy)
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
        "roe": None, "profit_margin": None,
        "fundamental_grade": "N/A",
        "fundamental_grade_score": None,
        "fundamental_grade_color": "#7d8aa5",
        "fundamental_grade_label": "⚪ Grade: N/A",
        "fundamental_grade_detail": "no data",
        "fundamental_grade_pillars": {},
        "fundamental_grade_pillar_data_present": {},
        "fundamental_source": "unknown",
        "analyst_consensus": None,
        "analyst_color": None,
        "analyst_score": None,
        "analyst_breakdown": None,
        "range_high": None, "range_low": None, "range_position": None,
        "rvol": None, "rvol_label": "—",
        "is_liquid": True, "avg_dollar_volume": None,
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
