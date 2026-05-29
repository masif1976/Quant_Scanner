"""
page_options_wheel.py — Page 7: Weekly Options Wheel Strategy

Phase 1 (this turn): Put Finder
Phase 2 (next turn): Wheel Manager (covered call mitigation engine)

Strategy overview
-----------------
The Wheel is a two-phase options-selling strategy:
  - Phase 1: Sell cash-secured puts on stocks you'd be willing to own
  - Phase 2: If assigned, sell covered calls against the shares
The goal is consistent income from option premiums, with stock ownership
as an acceptable fallback at favorable prices.

What this page does (Phase 1)
-----------------------------
For each watchlist ticker, scans the next weekly options expiry for puts
that meet your spec:
  - Expiration: nearest weekly cycle (~7 DTE)
  - Strike: 7-10% out-of-the-money (OTM) relative to spot
  - Premium yield: annualized ROC = (P / K) × (365 / 7)
  - Highlight: ROC ≥ 15% (configurable in the UI)

A composite Confidence Score combines:
  - Delta (probability of finishing OTM, capped at 0.85 for safety)
  - Bid-ask spread tightness (wide spreads = poor liquidity)
  - Volume / open interest (sanity check that contract is actively traded)
  - Earnings proximity (auto-warn if earnings within next 14 days)

Honest caveats surfaced in the UI
---------------------------------
- yfinance options data is 15-min delayed
- Delta is approximated locally (free tier doesn't ship Greeks)
- High hit rate ≠ high risk-adjusted return; the system explains this
- Wide bid-ask spreads on small-caps mean execution will be worse than
  shown; the page flags spreads > 50% of mid
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import theme
import data_utils as du
import market_cap_groups


# Constants — tune-able defaults
DEFAULT_MIN_OTM_PCT = 7.0
DEFAULT_MAX_OTM_PCT = 10.0
DEFAULT_MIN_ANNUALIZED_ROC = 15.0

# Filter presets — quick-set bundles of the three Put Finder filters that
# match recognized wheel-trading "styles." Picking a preset seeds the three
# filter values; users can fine-tune via the Advanced expander below.
#
# Style mapping:
#   Income-focused        — blue-chips only, steady income > yield. Tight OTM,
#                           low ROC floor. Accepts more candidates at lower yields.
#   Standard              — mainstream wheel. The textbook 7-12% OTM at 15% min ROC.
#   Patient/Disciplined   — strictest preset. Demands BOTH safety AND yield;
#                           willing to sit in cash on weeks without qualifying setups.
#
# Yield-aggressive was removed from the layman-facing dashboard. That preset
# is the one most likely to hurt new wheel traders — it surfaces high-IV
# trades where the rich premium correctly reflects real assignment risk.
# Experienced traders can still get that behavior by raising Min ROC in
# the Advanced expander, but the preset's misleading name (implying "more
# yield" without the corresponding risk warning) was a UX trap.
#
# Migration: any user with old preset names ("Conservative" or
# "Capital-preservation") in their session_state will hit the defensive
# "preset not in preset_names" check below and auto-reset to Standard.
FILTER_PRESETS = {
    "Standard": {
        "min_otm": 7.0, "max_otm": 12.0, "min_roc": 15.0,
        "blurb":   "Textbook wheel trading — the default that 80% of guides describe.",
        "detail":  "The mainstream approach: 7-12% OTM strikes with at least 15% "
                   "annualized return. Balances yield against safety. Most layman "
                   "wheel traders use these settings.",
        "cap_fit": "Works well across all cap tiers — mega, large, mid, and small. "
                   "The most robust default if you're not sure which preset to pick.",
    },
    "Income-focused": {
        "min_otm": 5.0, "max_otm": 10.0, "min_roc": 10.0,
        "blurb":   "Steady income from boring stocks. Lots of small wins, "
                   "occasional small assignments.",
        "detail":  "Lower yield floor (10% annualized) lets you collect modest "
                   "premium on safe blue-chip puts. The 5-10% OTM band keeps "
                   "strikes close enough that low-IV stocks still pay meaningful "
                   "premium. Accepts more candidates than Patient/Disciplined.",
        "cap_fit": "Best for blue-chip mega-caps (AAPL, MSFT, GOOGL, JPM, V) "
                   "and large-caps (AMD, COIN). Be cautious on high-volatility "
                   "mega-caps like NVDA or TSLA — the preset will surface "
                   "candidates but the vol-adjusted buffer label may flag "
                   "them as TIGHT. Trust the buffer over the preset.",
    },
    "Patient/Disciplined": {
        "min_otm": 8.0, "max_otm": 12.0, "min_roc": 20.0,
        "blurb":   "Wait for the right setup. Only trade when BOTH safety AND "
                   "yield are good — otherwise sit in cash.",
        "detail":  "The strictest preset. Requires 8%+ OTM cushion AND 20%+ "
                   "annualized return. Filters out low-premium safe trades AND "
                   "high-premium risky trades. You'll get fewer candidates per "
                   "scan but each one passes a quality bar. Weeks with no "
                   "qualifying trades = you do nothing, which is the right "
                   "answer when conditions don't fit your rules.",
        "cap_fit": "Best for large-caps (AMD, COIN) and mid-caps "
                   "(SMCI, COHR, MRVL). May return zero candidates on calm "
                   "mega-caps like AAPL — that's correct behavior, the preset "
                   "wants only top-quality setups. Use this when you're okay "
                   "holding cash on weeks with no good trades.",
    },
}

# Per-stock filter adjustments by market-cap tier.
# Idea: tighten filters for mega-caps (lower IV → lower premium, smaller
# buffer needed), loosen for small-caps (higher IV → richer premium, but
# you want a bigger buffer for safety). Applied ON TOP of the user's
# chosen preset baseline.
#
# Honest caveat: market cap is a LOOSE proxy for volatility. TSLA is a
# mega-cap but acts like a mid-cap (5% daily ATR). NVDA, COIN, and similar
# names are volatile despite their size. This adjustment is "directionally
# right but individually imperfect" — the vol-adjusted buffer label on
# each candidate card does the real per-stock risk calibration. The
# cap-tier filter is just for surfacing candidates the user might want
# to see, not for hiding risk.
#
# All offsets are added to the preset baseline (min_otm + offset, etc).
# Mid/small-cap nudges Min ROC higher because if you're locking up
# capital on a volatile name, you should demand a bigger premium for it.
CAP_TIER_ADJUSTMENTS = {
    "mega":  {"min_otm": -1.5, "max_otm": -2.0, "min_roc":  -5.0},
    "large": {"min_otm":  0.0, "max_otm":  0.0, "min_roc":   0.0},
    "mid":   {"min_otm": +1.0, "max_otm": +2.0, "min_roc":  +5.0},
    "small": {"min_otm": +2.0, "max_otm": +3.0, "min_roc": +10.0},
    "micro": {"min_otm": +2.0, "max_otm": +3.0, "min_roc": +10.0},
    "unclassified": {"min_otm": 0.0, "max_otm": 0.0, "min_roc": 0.0},  # safe default
}


def _derive_filters_for_ticker(ticker: str,
                                 base_min_otm: float,
                                 base_max_otm: float,
                                 base_min_roc: float) -> tuple[float, float, float, str]:
    """Apply per-stock cap-tier adjustments to the user's baseline filters.

    Args:
        ticker:        ticker symbol
        base_min_otm:  user's baseline (from preset or manual edit)
        base_max_otm:  user's baseline
        base_min_roc:  user's baseline

    Returns:
        (min_otm, max_otm, min_roc, cap_tier)
        — cap_tier is the classification used, for display on the card

    Bounds: clamps to reasonable ranges (Min OTM ≥ 1.5, Max OTM ≤ 25,
    Min ROC ≥ 5) to prevent extreme combinations from making nothing
    qualify.
    """
    try:
        fund = du.get_fundamentals(ticker)
        market_cap = (fund or {}).get("market_cap") if fund else None
    except Exception:
        market_cap = None
    cap_tier = market_cap_groups.classify_cap(market_cap)

    adj = CAP_TIER_ADJUSTMENTS.get(cap_tier, CAP_TIER_ADJUSTMENTS["unclassified"])
    min_otm = max(1.5,  base_min_otm + adj["min_otm"])
    max_otm = min(25.0, base_max_otm + adj["max_otm"])
    min_roc = max(5.0,  base_min_roc + adj["min_roc"])

    # Sanity: ensure max_otm > min_otm after adjustments
    if max_otm <= min_otm:
        max_otm = min_otm + 2.0

    return min_otm, max_otm, min_roc, cap_tier


def render():
    st.markdown(
        "<div class='kicker'>Page 7</div>"
        "<h1 style='margin-top:0'>Options Wheel — Weekly Income Engine</h1>",
        unsafe_allow_html=True)

    # Honest strategy framing
    with st.expander("📖 Strategy Notes — read before using", expanded=False):
        st.markdown("""
**What this is.** A decision-support tool for the Wheel strategy:
sell cash-secured puts on stocks you'd want to own; if assigned, sell
covered calls against the shares. The math is correct; the data is
real (yfinance, 15-min delayed); the recommendations are *signals,
not endorsements*.

**What the "high success rate" framing hides.** Selling weekly puts at
7-10% OTM has a high hit rate — most weeks the put expires worthless
and you keep premium. But the rare losers are big: a -25% gap on a
single name wipes out 5-10 weeks of premium income. The strategy is
income-focused, not risk-free. **Use only on stocks you genuinely want
to own at the strike price.**

**What you'll see below:**
- **Put Finder** tab: scans your watchlist for puts matching strike +
  yield constraints. Each row is a candidate trade.
- **Wheel Manager** tab (coming soon): tracks positions where you got
  assigned, recommends covered call strikes to exit the position
  without a structural loss.

**Honest data caveats:**
- Free yfinance options data is 15-min delayed
- Greeks (delta) are approximated locally — for screening only, not
  execution decisions
- Bid-ask spreads on small-caps can be wide; the page warns when
  spread > 50% of mid
- Earnings proximity flag warns about expected IV crush

Educational tool, not financial advice.
""")

    tab1, tab2 = st.tabs(["📍 Put Finder", "🔄 Wheel Manager"])

    with tab1:
        _render_put_finder()

    with tab2:
        _render_wheel_manager()


def _render_timing_banner():
    """Day-of-week + DTE timing advisory.

    Rules (DTE = days until next Friday, calendar):
      0-2 DTE  → RED: too short, wait for Friday's new listing
      3-4 DTE  → YELLOW: workable but premium reduced
      5-8 DTE  → GREEN: sweet spot — most wheel traders enter here
      >8 DTE   → GREEN with caveat: further out than standard weekly

    The banner also identifies "Thursday late morning" as the moment when
    new weeklies actually get listed by exchanges — useful trivia for
    someone wondering why Friday is "the" day.

    Honest caveat: we don't know what time zone the user is in. The
    day-of-week calculation uses the server's local clock (which on the
    user's own laptop running Streamlit IS their local clock). The exact
    "best entry window" (11 AM - 2 PM ET) is hinted at in text but not
    enforced — there's no realtime check.
    """
    today = datetime.today()
    # weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    dow = today.weekday()
    day_name = today.strftime("%A")

    # Days until next Friday (could be today if today IS Friday)
    if dow == 4:  # Friday
        days_to_friday = 0
        # Same-day expiry — but the wheel cycle is about NEXT Friday's new listing
        # On Friday afternoon, the new weekly (expiring NEXT Friday, 7d) is the focus
        # On Friday morning, the THIS week's expiry is in hours — wheel traders ignore it
        target_dte = 7  # the new weekly that just listed (or will list today)
    elif dow < 4:  # Mon-Thu
        days_to_friday = 4 - dow
        target_dte = days_to_friday
    else:  # Sat=5, Sun=6
        days_to_friday = 4 + (7 - dow)  # Sat→6, Sun→5
        target_dte = days_to_friday

    # Determine advisory color + message
    if dow == 4:  # Friday
        color = theme.GREEN
        icon = "✨"
        title = "Today is Friday — the sweet spot for weekly puts"
        body = (
            "New weekly options were listed Thursday morning (expiring "
            "<b>next Friday</b>, 7 DTE). Selling now captures the full "
            "premium plus weekend theta decay. <b>Optimal window: "
            "11 AM - 2 PM ET</b> for tightest bid-ask spreads."
        )
    elif dow == 3:  # Thursday
        color = theme.GREEN
        icon = "🆕"
        title = "Today is Thursday — new weeklies are listing today"
        body = (
            "Exchanges typically list the next weekly expiry (Friday + 8d) "
            "Thursday morning around 10 AM ET. The OLD weekly expiring "
            "tomorrow has almost no premium left. Scan today after ~10:30 AM ET "
            "to see the freshly-listed cycle, OR wait until Friday."
        )
    elif dow in (5, 6):  # Saturday, Sunday
        color = theme.GREEN
        icon = "📅"
        title = f"Today is {day_name} — markets closed, but you can plan ahead"
        # Note: target_dte was computed as days-from-today-to-Friday (Sat→6,
        # Sun→5), but the user is going to PLACE the trade Monday morning.
        # Monday→Friday is always 4 days, regardless of which weekend day
        # they're scanning on. Tell them the Monday-relative DTE so the
        # number matches what they'll actually see when they trade.
        monday_dte = 4  # Mon=0, Fri=4; Mon→Fri = 4 days
        body = (
            f"You can still scan and pick candidates. Place trades Monday "
            f"morning — a trade placed Monday will be <b>{monday_dte} DTE</b> "
            f"to Friday expiry (workable but not ideal; ~25% theta decay "
            f"already happened over the weekend). Best entry remains Friday "
            f"for the freshest 7-DTE cycle, OR Thursday afternoon when the "
            f"next weekly lists."
        )
    elif dow == 0:  # Monday
        color = theme.YELLOW
        icon = "⏰"
        title = f"Today is Monday — {target_dte} days until Friday expiry"
        body = (
            "Workable but not ideal. The weekly is already ~3 days into its "
            "cycle — premium has decayed ~25% since it was listed Thursday. "
            "If you can wait, Friday gives you a fresh 7-DTE cycle with "
            "full premium and weekend theta. If you can't wait, smaller "
            "premium is what's available."
        )
    elif dow == 1:  # Tuesday
        color = theme.YELLOW
        icon = "⚠"
        title = f"Today is Tuesday — only {target_dte} days until Friday expiry"
        body = (
            "Premium has decayed ~50% since the cycle was listed Thursday. "
            "What's left is small relative to the capital you'd commit. "
            "Strong recommendation: <b>wait for Thursday afternoon or "
            "Friday</b> to scan the next cycle (will be 7-8 DTE with full "
            "premium)."
        )
    else:  # Wednesday (dow == 2)
        color = theme.RED
        icon = "⛔"
        title = f"Today is Wednesday — only {target_dte} days until Friday expiry"
        body = (
            "The current weekly is nearly expired. Premium is minimal "
            "(theta has eaten ~70% of original value). Scanning now will "
            "mostly show low-yield candidates. <b>Wait for Thursday "
            "afternoon</b> when the next weekly lists, OR <b>Friday</b> "
            "for the optimal entry window."
        )

    # Render the banner
    st.markdown(
        f"<div style='background:{color}11;border-left:4px solid {color};"
        f"border-radius:6px;padding:12px 16px;margin:8px 0 14px 0;"
        f"font-family:JetBrains Mono;font-size:0.82rem;line-height:1.5'>"
        f"<div style='font-weight:700;color:{color};margin-bottom:4px'>"
        f"{icon} {title}</div>"
        f"<div style='color:{theme.MUTED}'>{body}</div>"
        f"</div>",
        unsafe_allow_html=True)


def _render_put_finder():
    """Tab 1 — scan watchlist for weekly cash-secured put candidates."""
    watchlist = st.session_state.get("watchlist", [])
    if not watchlist:
        st.info("No tickers in your watchlist. Add tickers in the sidebar first.")
        return

    # ── timing advisory banner ──
    # Tells the user whether NOW is a good time to scan based on day-of-week
    # and days-until-next-Friday. Wheel traders typically sell on Friday
    # afternoon for the just-listed weeklies (best premium + weekend theta
    # capture). Other days are progressively worse as theta has already
    # decayed without giving you any new premium to collect.
    _render_timing_banner()

    # ── preset selector ──
    # User picks a wheel-trading style. The preset seeds the three filter
    # values below. After picking, the user can still tweak any individual
    # filter manually — picking a preset is a quick-start, not a lock.
    # On first load, defaults to "Standard."
    preset_names = list(FILTER_PRESETS.keys())
    # Defensive: if session_state has a stale/invalid value (e.g. from
    # a code change that renamed/removed a preset like Yield-aggressive),
    # reset to "Standard" rather than crashing the page render.
    if ("wheel_preset" not in st.session_state
            or st.session_state["wheel_preset"] not in preset_names):
        st.session_state["wheel_preset"] = "Standard"

    col_preset_label, col_preset_select = st.columns([1, 3])
    with col_preset_label:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"color:{theme.MUTED};padding-top:32px'>"
            f"Filter style:"
            f"</div>",
            unsafe_allow_html=True)
    with col_preset_select:
        selected_preset = st.radio(
            "Filter style",
            options=preset_names,
            horizontal=True,
            label_visibility="collapsed",
            key="wheel_preset_radio",
            index=preset_names.index(st.session_state["wheel_preset"]),
            help="Quick-set bundles of the three filter values. Picking a "
                 "preset updates the underlying filters to a recognized "
                 "wheel-trading style. Fine-tune via the Advanced expander "
                 "below if needed.")

    # If the preset selection changed since last render, reseed the filter
    # values into session_state so the number_inputs pick up the new defaults.
    if selected_preset != st.session_state["wheel_preset"]:
        st.session_state["wheel_preset"] = selected_preset
        p = FILTER_PRESETS[selected_preset]
        st.session_state["wheel_min_otm"] = p["min_otm"]
        st.session_state["wheel_max_otm"] = p["max_otm"]
        st.session_state["wheel_min_roc"] = p["min_roc"]
        st.rerun()

    # ── Preset info card ──
    # Three-part explanation: the short blurb (one-liner), the detail
    # paragraph explaining HOW the filters work, and the cap-fit note
    # explaining WHICH stocks the preset works well/poorly on.
    p_info = FILTER_PRESETS[selected_preset]
    st.markdown(
        f"<div style='background:{theme.PANEL_HI};border-radius:8px;"
        f"padding:14px 16px;margin:8px 0 16px 0;"
        f"border-left:3px solid {theme.GREEN}'>"
        # Header: preset name + blurb
        f"<div style='font-family:Sora;font-size:0.95rem;font-weight:700;"
        f"color:{theme.TEXT};margin-bottom:4px'>{selected_preset}</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.8rem;"
        f"color:{theme.TEXT};line-height:1.5;margin-bottom:10px'>"
        f"{p_info['blurb']}</div>"
        # How it works
        f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
        f"color:{theme.MUTED};line-height:1.55;margin-bottom:8px'>"
        f"<b style='color:{theme.TEXT}'>How it works:</b> {p_info['detail']}"
        f"</div>"
        # Cap-tier fit (honest about limitations)
        f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
        f"color:{theme.MUTED};line-height:1.55'>"
        f"<b style='color:{theme.TEXT}'>Works well for:</b> {p_info['cap_fit']}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True)

    # Initialize session_state-backed filter values from the preset on first run
    p_defaults = FILTER_PRESETS[selected_preset]
    for key, val in [("wheel_min_otm", p_defaults["min_otm"]),
                       ("wheel_max_otm", p_defaults["max_otm"]),
                       ("wheel_min_roc", p_defaults["min_roc"])]:
        if key not in st.session_state:
            st.session_state[key] = val

    # ── Advanced fine-tuning (collapsed by default) ──
    # Hidden from the default view to keep the UI layman-friendly. Power
    # users can still nudge individual filters here. The preset above
    # remains the primary control; this expander is for fine-tuning.
    with st.expander("⚙️ Advanced: fine-tune filter values", expanded=False):
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
            f"color:{theme.MUTED};margin-bottom:10px;line-height:1.5'>"
            f"These values were set by the <b>{selected_preset}</b> preset above. "
            f"Adjust if you want to fine-tune within that style. Per-stock "
            f"market-cap adjustments are still applied on top of whatever "
            f"you set here."
            f"</div>",
            unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            min_otm = st.number_input(
                "Min OTM %", min_value=1.0, max_value=30.0,
                key="wheel_min_otm", step=0.5,
                help="Baseline minimum out-of-the-money percentage. Per-stock "
                     "filters add a market-cap-tier nudge (mega-caps tighter, "
                     "small-caps wider).")
        with col2:
            max_otm = st.number_input(
                "Max OTM %", min_value=2.0, max_value=50.0,
                key="wheel_max_otm", step=0.5,
                help="Baseline maximum OTM percentage. The ROC filter does "
                     "the real 'too far OTM' filtering — this is a sanity bound.")
        with col3:
            min_roc = st.number_input(
                "Min annualized ROC %", min_value=0.0, max_value=100.0,
                key="wheel_min_roc", step=1.0,
                help="Baseline minimum annualized return on capital. "
                     "Per-stock filters tighten this for mega-caps and loosen "
                     "for small-caps.")

    # Read the (possibly user-edited) baseline values for use downstream
    min_otm = st.session_state["wheel_min_otm"]
    max_otm = st.session_state["wheel_max_otm"]
    min_roc = st.session_state["wheel_min_roc"]

    if min_otm >= max_otm:
        st.error("Min OTM % must be less than Max OTM %.")
        return

    # ── ticker picker ──
    # Default selection logic:
    #   - First time on this page in this session: select all watchlist tickers
    #   - Subsequent renders: preserve user's prior selection, intersected
    #     with current watchlist (handles the case where the user removed
    #     tickers from the watchlist after the last scan)
    # The intersection step prevents stale selections from carrying forward
    # after the watchlist is edited in the sidebar.
    prior_selection = st.session_state.get("wheel_selected_tickers")
    if prior_selection is None:
        default_selection = list(watchlist)
    else:
        default_selection = [t for t in prior_selection if t in watchlist]
        if not default_selection:
            # All previously-selected tickers were removed from watchlist —
            # fall back to "all current watchlist" rather than empty
            default_selection = list(watchlist)

    # Helper buttons row (Select all / Clear) above the multiselect.
    # The multiselect itself doesn't have these shortcuts built in.
    col_helper_label, col_select_all, col_clear = st.columns([5, 1, 1])
    with col_helper_label:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"color:{theme.MUTED};padding-top:10px'>"
            f"Pick which watchlist tickers to scan "
            f"(fewer = faster, ~2 seconds per ticker):"
            f"</div>",
            unsafe_allow_html=True)
    with col_select_all:
        if st.button("Select all", key="wheel_select_all",
                      width="stretch",
                      help="Select all tickers in your current watchlist"):
            st.session_state["wheel_selected_tickers"] = list(watchlist)
            st.rerun()
    with col_clear:
        if st.button("Clear", key="wheel_clear_selection",
                      width="stretch",
                      help="Deselect all tickers"):
            st.session_state["wheel_selected_tickers"] = []
            st.rerun()

    selected_tickers = st.multiselect(
        label="Tickers to scan",
        options=list(watchlist),
        default=default_selection,
        key="wheel_ticker_picker",
        label_visibility="collapsed",
        help="Pick the watchlist tickers to scan for weekly put candidates. "
             "Each ticker takes ~2 seconds to fetch (yfinance is rate-limited "
             "on free tier), so picking 5-8 tickers gives faster results "
             "than scanning all 23.")
    # Persist for next render
    st.session_state["wheel_selected_tickers"] = selected_tickers

    # ── scan ──
    n_selected = len(selected_tickers)
    scan_disabled = (n_selected == 0)
    scan_label = (
        "▶ Scan selected tickers for weekly put candidates"
        if n_selected > 0 else
        "▶ Pick at least one ticker above"
    )
    if st.button(scan_label, type="primary", width="stretch",
                  disabled=scan_disabled):
        with st.spinner(f"Scanning {n_selected} ticker"
                         f"{'s' if n_selected != 1 else ''} for next-Friday "
                         f"puts at {min_otm:.1f}-{max_otm:.1f}% OTM…"):
            results = _scan_watchlist_puts(selected_tickers, min_otm, max_otm, min_roc)
        # Store both the results AND the exact set of tickers that were
        # scanned. We compare on later renders to detect if the user has
        # changed their selection since the scan ran — if so, we filter
        # the displayed cards to only the currently-selected tickers AND
        # warn that the data is from a prior scan (so they know to re-run
        # if they want fresh prices).
        st.session_state["put_finder_results"] = results
        st.session_state["put_finder_scanned_tickers"] = list(selected_tickers)

    results = st.session_state.get("put_finder_results")
    if results is None:
        st.info("Pick tickers above and click **Scan selected tickers** to "
                 "find weekly put candidates.")
        return

    candidates = results["candidates"]
    diagnostics = results["diagnostics"]

    # ── Apply current ticker-selection filter to cached results ──
    # If the user has removed tickers from the picker since the last scan,
    # those tickers' candidates should NOT show in the cards or scatter chart.
    # Filtering happens HERE (not in the scan function) so we preserve the
    # original cached scan and can recover if the user re-adds a ticker.
    #
    # We also detect a "stale scan" condition: the current selection differs
    # from what was scanned. In that case, surface a warning so the user
    # knows the underlying data is from a previous scan run.
    selected_set = set(selected_tickers)
    scanned_set = set(st.session_state.get("put_finder_scanned_tickers", []))
    candidates = [c for c in candidates if c["ticker"] in selected_set]
    diagnostics = [(tk, msg) for tk, msg in diagnostics if tk in selected_set]

    # Detect stale conditions:
    #   - Removed tickers: scanned but no longer in selection (filtered out above)
    #   - Added tickers: selected now but never scanned (no data shown for them)
    removed_since_scan = scanned_set - selected_set
    added_since_scan = selected_set - scanned_set
    if removed_since_scan or added_since_scan:
        # Build an honest message about what's stale
        parts = []
        if removed_since_scan:
            removed_list = ", ".join(sorted(removed_since_scan)[:5])
            if len(removed_since_scan) > 5:
                removed_list += f" +{len(removed_since_scan)-5} more"
            parts.append(f"<b>{len(removed_since_scan)} ticker(s) removed</b> "
                          f"from selection ({removed_list}) — those cards have "
                          f"been hidden")
        if added_since_scan:
            added_list = ", ".join(sorted(added_since_scan)[:5])
            if len(added_since_scan) > 5:
                added_list += f" +{len(added_since_scan)-5} more"
            parts.append(f"<b>{len(added_since_scan)} ticker(s) added</b> "
                          f"to selection ({added_list}) — no data shown for "
                          f"them until you re-scan")
        st.markdown(
            f"<div style='background:{theme.YELLOW}11;"
            f"border-left:4px solid {theme.YELLOW};border-radius:6px;"
            f"padding:10px 14px;margin:8px 0;font-family:JetBrains Mono;"
            f"font-size:0.78rem;line-height:1.5'>"
            f"<b style='color:{theme.YELLOW}'>⚠ Selection changed since "
            f"last scan:</b> {' &middot; '.join(parts)}. "
            f"Click <b>Scan selected tickers</b> above to refresh with "
            f"current prices and the new selection."
            f"</div>",
            unsafe_allow_html=True)

    # ── diagnostics ──
    if diagnostics:
        with st.expander(f"⚠ {len(diagnostics)} ticker(s) had data issues",
                          expanded=False):
            for tk, msg in diagnostics:
                st.markdown(f"- **{tk}**: {msg}")

    if not candidates:
        # Distinguish two cases:
        # (a) Scan returned candidates but all were filtered out by the current
        #     ticker selection (user removed all scanned tickers)
        # (b) Scan returned no candidates from the start (filters too tight)
        if results["candidates"]:
            st.info(
                f"All scanned tickers have been removed from your selection. "
                f"Either re-add tickers via the picker above, or re-scan with "
                f"the current selection.")
        else:
            st.warning(
                f"No put candidates found matching your filters "
                f"({min_otm:.1f}-{max_otm:.1f}% OTM, ≥{min_roc:.0f}% annualized "
                f"ROC). Try widening the OTM range or lowering the yield "
                f"threshold.")
        return

    # ── summary metrics ──
    # IMPORTANT: distinguish between strike-level and ticker-level counts.
    # `len(candidates)` is the number of qualifying STRIKES across all
    # scanned tickers — but the chart and cards show ONE per ticker (the
    # headline). A user seeing "Candidates: 19" but only 4 cards naturally
    # wonders where the other 15 are: they're alternate strikes on the
    # same tickers, available inside each card's "other strikes" expander.
    #
    # We surface both numbers honestly + show the headline-only averages
    # (which match what's visible on the chart) alongside the all-strike
    # averages.
    n_strikes = len(candidates)
    # Group to count unique tickers and identify headline candidates
    _by_ticker: dict[str, list[dict]] = {}
    for c in candidates:
        _by_ticker.setdefault(c["ticker"], []).append(c)
    n_tickers = len(_by_ticker)
    # Headline = best strike per ticker (same ranking as render: confidence > ROC)
    _headlines = [
        sorted(group, key=lambda x: (x["confidence"], x["annualized_roc"]),
                reverse=True)[0]
        for group in _by_ticker.values()
    ]
    headline_avg_roc = (sum(c["annualized_roc"] for c in _headlines)
                          / len(_headlines)) if _headlines else 0
    headline_avg_conf = (sum(c["confidence"] for c in _headlines)
                           / len(_headlines)) if _headlines else 0

    st.markdown(
        f"<div style='display:flex;gap:18px;margin:14px 0;flex-wrap:wrap;"
        f"font-family:JetBrains Mono;font-size:0.85rem'>"
        # Tickers — matches dots on the chart and cards shown
        f"<span><span style='color:{theme.MUTED}'>Tickers:</span> "
        f"<b style='color:{theme.TEXT}'>{n_tickers}</b></span>"
        # Strikes — total qualifying strikes (including alternates inside cards)
        f"<span><span style='color:{theme.MUTED}'>Strikes total:</span> "
        f"<b style='color:{theme.TEXT}'>{n_strikes}</b> "
        f"<span style='color:{theme.MUTED};font-size:0.72rem'>"
        f"({n_strikes - n_tickers} extra inside &quot;other strikes&quot;)</span></span>"
        # Headline averages — what's visible on the chart
        f"<span><span style='color:{theme.MUTED}'>Headline avg ROC:</span> "
        f"<b style='color:{theme.TEXT}'>{headline_avg_roc:.1f}%</b></span>"
        f"<span><span style='color:{theme.MUTED}'>Headline avg confidence:</span> "
        f"<b style='color:{theme.TEXT}'>{headline_avg_conf:.0f}/100</b></span>"
        f"</div>",
        unsafe_allow_html=True)

    # Also show which scanned tickers produced ZERO candidates — currently
    # they're silent. Helps the user understand why 8 tickers scanned but
    # only N cards appear.
    scanned = st.session_state.get("put_finder_scanned_tickers", []) or []
    scanned_set = set(scanned)
    tickers_with_candidates = set(_by_ticker.keys())
    silent_zero_tickers = sorted(scanned_set - tickers_with_candidates)
    # Exclude tickers that already have a diagnostic message (they're
    # already surfaced in the expander above)
    diag_tickers = {d[0] for d in diagnostics}
    silent_zero_tickers = [t for t in silent_zero_tickers if t not in diag_tickers]
    if silent_zero_tickers:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
            f"color:{theme.MUTED};margin:-4px 0 8px 0;line-height:1.5'>"
            f"<b style='color:{theme.TEXT}'>"
            f"Scanned but no qualifying strikes:</b> "
            f"{', '.join(silent_zero_tickers)}. "
            f"<i>Likely no strikes met the cap-tier-adjusted ROC threshold, "
            f"or all OTM-range strikes had zero bid. For mega-caps with "
            f"low IV, try Income-focused preset (lower ROC floor), or widen "
            f"Max OTM in Advanced.</i>"
            f"</div>",
            unsafe_allow_html=True)

    # ── candidate cards ──
    # Group candidates by ticker. Each ticker gets ONE headline card (its
    # best strike by confidence > ROC) with remaining strikes available
    # inside an expandable "compare" section.
    #
    # Ranking within a ticker:
    #   1. Highest confidence (the engine's combined-risk read)
    #   2. Higher annualized ROC as tiebreaker
    #
    # A WEAK strike with 112% ROC is worse than a MODERATE strike at 95%
    # because WEAK means real-money problems (wide spread, low liquidity)
    # that erode the displayed yield.
    grouped: dict[str, list[dict]] = {}
    for c in candidates:
        grouped.setdefault(c["ticker"], []).append(c)
    for ticker, group in grouped.items():
        group.sort(
            key=lambda c: (c["confidence"], c["annualized_roc"]),
            reverse=True,
        )

    # Order tickers by their best (headline) candidate's score — strongest
    # opportunity first.
    tickers_ordered = sorted(
        grouped.keys(),
        key=lambda t: (
            grouped[t][0]["confidence"],
            grouped[t][0]["annualized_roc"]
        ),
        reverse=True,
    )

    st.markdown(
        f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
        f"color:{theme.MUTED};margin:8px 0 4px 0'>"
        f"One card per ticker showing the engine's pick (best confidence × "
        f"yield). If a ticker has multiple qualifying strikes, expand "
        f"<b>'other strikes available'</b> inside the card to compare. "
        f"Earnings warnings appear at the top of affected cards."
        f"</div>",
        unsafe_allow_html=True)

    # ── Risk/Reward scatter chart ──
    # Plots the headline candidate per ticker on a 2-axis space:
    #   X = confidence score (the system's quality read)
    #   Y = annualized ROC (the yield on offer)
    # Top-right = both high = obvious pick. Top-left = high yield, low
    # confidence = chasing risky premium. Bottom-right = safe but barely
    # worth the capital. Bottom-left = avoid.
    # Click a dot to scroll to that ticker's card below.
    #
    # Defensive wrapper: if plotly or the chart logic ever raises, we want
    # the rest of the page (cards, diagnostics) to still render. The chart
    # is decoration on top of the data, not the primary view. We DO show
    # a small warning so the user knows something went wrong rather than
    # silently swallowing the failure.
    if len(tickers_ordered) >= 2:
        try:
            _render_risk_reward_scatter(
                [grouped[t][0] for t in tickers_ordered]
            )
        except Exception as e:
            st.markdown(
                f"<div style='background:{theme.YELLOW}11;"
                f"border-left:3px solid {theme.YELLOW};border-radius:4px;"
                f"padding:8px 12px;margin:8px 0;font-family:JetBrains Mono;"
                f"font-size:0.75rem;color:{theme.MUTED}'>"
                f"⚠ Risk/Reward scatter chart failed to render: "
                f"<code>{type(e).__name__}: {str(e)[:120]}</code>. "
                f"The candidate cards below are unaffected. "
                f"(If this persists, try restarting Streamlit.)"
                f"</div>",
                unsafe_allow_html=True)

    for ticker in tickers_ordered:
        group = grouped[ticker]
        headline = group[0]
        alternates = group[1:]  # may be empty
        # HTML anchor for click-to-scroll from the scatter chart.
        # The card's own container doesn't expose an id, so we render an
        # invisible anchor just before it. Slug uses the ticker (uppercased
        # by data layer) so anchors are stable across reruns.
        st.markdown(
            f"<div id='wheel-card-{ticker}' style='position:relative;"
            f"top:-60px;height:0;overflow:hidden'></div>",
            unsafe_allow_html=True)
        _render_put_candidate_card(headline, alternates=alternates)

    # ── usage notes ──
    st.caption(
        "Educational tool, not financial advice. yfinance data is 15-min "
        "delayed; bid prices shown are what you'd RECEIVE selling at market — "
        "real fills may be lower in fast markets or on wide-spread contracts."
    )


def _render_put_candidate_card(c: dict, alternates: list[dict] | None = None):
    """Render ONE put candidate as a visual card.

    Args:
        c: the headline candidate dict for this ticker (best by confidence
           × ROC ranking).
        alternates: list of other qualifying strikes on the same ticker that
           weren't picked as headline. Rendered inside a collapsible
           "other strikes" section so users can compare. May be None or [].

    Layout (top to bottom):
      [Ticker + Strike] header + [Confidence pill] right-aligned
      "Why <bucket>" reason line
      [Concentration warning if user holds the ticker already]
      [Earnings warning banner if within 14 days]
      Three metric columns: Cash Upfront (green), Capital Required, Target Price
      Visual safety buffer (custom HTML bar with "% drop required" label)
      Three-column context row: 30-day trend · grade · next earnings
      Plain-English summary line
      [Alternates expander if there are other strikes for this ticker]
      Expander: "Advanced Metrics" — Delta, Volume, OI, Spread, IV
    """
    ticker = c["ticker"]
    strike = c["strike"]
    bid    = c["bid"]
    spot   = c["spot"]
    otm    = c["otm_pct"]
    conf   = c["confidence"]
    roc    = c["annualized_roc"]
    p_otm  = c["p_otm"]
    warnings = c["warnings"]

    # Contract size = 100 shares per contract — standard equity option
    cash_upfront = bid * 100
    capital_required = strike * 100

    # Confidence color — same buckets as elsewhere in the codebase
    if conf >= 75:
        conf_color = theme.GREEN
        conf_label = "STRONG"
    elif conf >= 55:
        conf_color = theme.YELLOW
        conf_label = "MODERATE"
    else:
        conf_color = theme.RED
        conf_label = "WEAK"

    # Detect earnings warning (already computed in scan, prefix is "⚠ Earnings")
    earnings_warning = next(
        (w for w in warnings if "Earnings" in w),
        None
    )

    with st.container(border=True):
        # ── Card header: Ticker · Strike on left, Confidence pill on right ──
        col_hdr_left, col_hdr_right = st.columns([3, 1])
        with col_hdr_left:
            st.markdown(
                f"<div style='font-family:Sora;font-size:1.15rem;"
                f"font-weight:700;color:{theme.TEXT};margin:0'>"
                f"{ticker} &nbsp;&middot;&nbsp; "
                f"<span style='color:{theme.MUTED};font-weight:600'>"
                f"Sell <b style='color:{theme.TEXT}'>${strike:.2f}</b> put "
                f"&middot; {c['expiry']} ({c['dte']}d)</span>"
                f"</div>",
                unsafe_allow_html=True)
            # Cap-tier + applied-filter chip — small, under header line
            cap_tier = c.get("cap_tier", "unclassified")
            cap_tier_label = {
                "mega":   "Mega-cap",
                "large":  "Large-cap",
                "mid":    "Mid-cap",
                "small":  "Small-cap",
                "micro":  "Micro-cap",
                "unclassified": "Unclassified",
            }.get(cap_tier, cap_tier.title())
            applied_min_otm = c.get("applied_min_otm")
            applied_max_otm = c.get("applied_max_otm")
            applied_min_roc = c.get("applied_min_roc")
            if applied_min_otm is not None:
                filter_summary = (
                    f"{applied_min_otm:.1f}-{applied_max_otm:.1f}% OTM, "
                    f"≥{applied_min_roc:.0f}% ROC"
                )
            else:
                filter_summary = ""
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.82rem;"
                f"color:{theme.MUTED};margin-top:4px'>"
                f"<span style='background:{theme.PANEL_HI};"
                f"padding:3px 8px;border-radius:4px;"
                f"border:1px solid {theme.BORDER};font-weight:600'>"
                f"{cap_tier_label} · {filter_summary}"
                f"</span>"
                f"</div>",
                unsafe_allow_html=True)
        with col_hdr_right:
            st.markdown(
                f"<div style='text-align:right;padding-top:4px'>"
                f"<span style='background:{conf_color}22;color:{conf_color};"
                f"font-family:JetBrains Mono;font-size:0.7rem;font-weight:700;"
                f"padding:3px 10px;border-radius:10px;letter-spacing:0.06em;"
                f"border:1px solid {conf_color}66'>"
                f"{conf_label} · {conf}/100</span>"
                f"</div>",
                unsafe_allow_html=True)

        # ── "Why X" explanation line under the pill ──
        # Per-card rationale identifying the dominant drag (or strength).
        # Helps users understand WHY a candidate is rated STRONG/MODERATE/WEAK
        # instead of just seeing the label. Reason text is generated in
        # _compute_confidence by checking the worst factor first.
        conf_reason = c.get("confidence_reason", "")
        if conf_reason:
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
                f"color:{theme.MUTED};margin:-2px 0 8px 0;line-height:1.5'>"
                f"<span style='color:{conf_color};font-weight:700'>"
                f"Why {conf_label.lower()}:</span> {conf_reason}"
                f"</div>",
                unsafe_allow_html=True)

        # ── Concentration warning (TOP priority — appears before earnings) ──
        # If user already holds an assigned position on this ticker, adding
        # another short put = doubling concentration risk. They'd be holding
        # 200+ shares of the same name if the new put gets assigned too.
        if c.get("already_assigned"):
            st.warning(
                f"⚠️ **You already hold an assigned position in {ticker}.** "
                f"Selling this put adds concentration risk — if it gets "
                f"assigned, you'd own another 100 shares of the same name. "
                f"Check your Wheel Manager (Tab 2) before adding this trade."
            )

        # ── Earnings warning banner (PROMINENT, top of card) ──
        if earnings_warning:
            st.error(
                f"⚠️ **EARNINGS THIS WEEK** — {earnings_warning.replace('⚠ ', '')}. "
                f"Stock has higher-than-normal risk of a big move that puts "
                f"this strike in the money. IV is elevated (so premium looks "
                f"juicy) but the price gap on the report is the actual risk. "
                f"**Consider skipping this trade.**"
            )

        # ── Three core metrics ──
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                label="💰 Cash upfront",
                value=f"${cash_upfront:,.0f}",
                delta=f"+{roc:.0f}% annualized",
                delta_color="normal",  # green when positive (default)
                help="Total premium credited to your account immediately at "
                     "sale (per contract = 100 shares × bid). Yours to keep "
                     "regardless of outcome. If the put gets assigned, the "
                     "cash isn't taken back — but you'll be buying 100 "
                     "shares at the strike price, which may be worth less "
                     "than what you paid. Delta shows annualized return on "
                     "capital required.",
            )
        with col2:
            st.metric(
                label="🔒 Capital required",
                value=f"${capital_required:,.0f}",
                delta=None,
                help="Cash you must set aside until expiry (strike × 100). "
                     "This is what you'd pay for the shares IF assigned. "
                     "Brokers require this collateral for cash-secured puts.",
            )
        with col3:
            st.metric(
                label="🎯 Strike (assignment price)",
                value=f"${strike:.2f}",
                delta=f"−{otm:.1f}% from spot ${spot:.2f}",
                delta_color="off",  # neutral — directional info, not gain/loss
                help="If the stock closes below this price at expiry, the put "
                     "gets assigned and you BUY 100 shares at this price. "
                     "Make sure this is a price you'd be willing to pay.",
            )

        # ── High-yield qualifier ──
        # Tells the user honestly that big annualized numbers reflect the
        # market pricing in real risk. The annualized ROC is still useful
        # for comparison BETWEEN candidates; this just stops it from
        # reading as "free money" in absolute terms.
        #
        # Note: we don't make specific causal claims about IV here because
        # ROC and IV are usually correlated but not always — a wide-spread
        # mid-cap can show inflated apparent ROC from a depressed bid
        # without exceptional IV. "Elevated risk" is the honest framing
        # that covers all causes (high IV, wide spreads, low liquidity).
        if roc > 50:
            qualifier_color = theme.YELLOW if roc < 100 else theme.RED
            qualifier_msg = (
                "High annualized yields reflect elevated risk priced into "
                "the premium — the market is pricing in either high "
                "volatility, wide spreads, or both. You can't sustainably "
                "compound this return; occasional losing weeks (assignment "
                "at a strike that's now deep ITM) eat through many winners. "
                "Focus on the absolute "
                f"<b style='color:{theme.GREEN}'>${cash_upfront:,.0f}</b> "
                "cash upfront and whether you'd be happy owning shares at "
                f"<b>${strike:.2f}</b>."
            )
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.7rem;"
                f"color:{theme.MUTED};margin-top:6px;padding:8px 10px;"
                f"background:{qualifier_color}0d;"
                f"border-left:3px solid {qualifier_color};border-radius:4px;"
                f"line-height:1.5'>"
                f"<b style='color:{qualifier_color}'>"
                f"⚠ Why this yield is so high:</b> {qualifier_msg}"
                f"</div>",
                unsafe_allow_html=True)

        # ── Visual safety buffer ──
        # Shows graphically how far the stock can fall before assignment.
        # Larger buffer = safer.
        _render_safety_buffer(spot, strike, otm,
                                iv=c.get("iv"), dte=c.get("dte"))

        # ── Tier 1 context row: trend + grade + next earnings ──
        # Three columns of decision-supportive context. Each cell is
        # independent — missing data shows a "—" placeholder rather than
        # hiding the column (preserves visual rhythm across cards).
        _render_context_row(c)

        # ── Trade narrative ──
        p_otm_str = f"{p_otm*100:.0f}%" if p_otm is not None else "—"
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"color:{theme.MUTED};line-height:1.5;margin-top:6px'>"
            f"<b style='color:{theme.TEXT}'>The trade:</b> If "
            f"<b style='color:{theme.TEXT}'>{ticker}</b> stays above "
            f"<b style='color:{theme.TEXT}'>${strike:.2f}</b> through "
            f"{c['expiry']} (~{p_otm_str} chance based on delta), the put "
            f"expires worthless and the "
            f"<b style='color:{theme.GREEN}'>${cash_upfront:.0f}</b> "
            f"premium (already in your account from day 1) is pure profit. "
            f"If it drops below ${strike:.2f}, you get assigned 100 shares "
            f"at that price — you keep the premium but now own shares worth "
            f"less than you paid. "
            f"<span style='color:{theme.TEXT}'>You can also buy this "
            f"contract back early</span> — typically at 50% of max profit "
            f"(when only $"
            f"{(c['bid'] / 2):.2f}/share remains) to lock in gains and free "
            f"up the capital sooner."
            f"</div>",
            unsafe_allow_html=True)

        # ── Non-earnings warnings (smaller, below main content) ──
        other_warnings = [w for w in warnings if "Earnings" not in w]
        if other_warnings:
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.72rem;"
                f"color:{theme.YELLOW};margin-top:6px;"
                f"padding:4px 8px;background:{theme.YELLOW}11;border-radius:4px'>"
                f"{' · '.join(other_warnings)}"
                f"</div>",
                unsafe_allow_html=True)

        # ── Alternate strikes for the same ticker (compare view) ──
        # Only shows when there's actually something to compare. Compact
        # rows — not full cards — so users can compare strikes without
        # re-reading all the surrounding context.
        if alternates:
            _render_alternate_strikes(ticker, alternates, headline=c)

        # ── Advanced metrics (hidden by default) ──
        with st.expander("🔬 Advanced metrics (Greeks, volume, spread)", expanded=False):
            adv_col1, adv_col2, adv_col3, adv_col4 = st.columns(4)
            with adv_col1:
                delta_str = f"{c['delta']:.2f}" if c['delta'] is not None else "—"
                st.metric("Delta (approx)", delta_str,
                           help="Approximate Black-Scholes delta. Magnitude "
                                "≈ probability of finishing ITM. -0.15 means "
                                "~15% chance of assignment.")
            with adv_col2:
                st.metric("IV", f"{c['iv']*100:.0f}%" if c['iv'] else "—",
                           help="Implied volatility. Higher IV = juicier "
                                "premium AND larger expected price swings. "
                                "Sell premium when IV is high relative to "
                                "history.")
            with adv_col3:
                vol_str = f"{int(c['volume']):,}" if c['volume'] else "0"
                st.metric("Volume today", vol_str,
                           help="Contracts traded today. >100 = actively "
                                "traded. 0 = nobody's touched this contract.")
            with adv_col4:
                oi_str = f"{int(c['oi']):,}" if c['oi'] else "0"
                st.metric("Open interest", oi_str,
                           help="Total contracts outstanding. >500 = good "
                                "liquidity; spreads will be tighter.")
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.72rem;"
                f"color:{theme.MUTED};margin-top:8px;line-height:1.5'>"
                f"<b>Bid:</b> ${c['bid']:.2f} &middot; "
                f"<b>Ask:</b> ${c['ask']:.2f} &middot; "
                f"<b>Spread:</b> {c['spread_quality']}. "
                f"Wide spreads mean you'll get filled WORSE than the bid "
                f"price shown — for spread quality 'Wide' or worse, "
                f"expect ~5-15¢ slippage per contract on a market order."
                f"</div>",
                unsafe_allow_html=True)


def _render_safety_buffer(spot: float, strike: float, otm_pct: float,
                            iv: float | None = None, dte: int | None = None):
    """Visual safety buffer — shows graphically how far the stock can fall
    before this put assignment triggers, with a VOLATILITY-ADJUSTED rating.

    Why volatility-adjusted instead of static thresholds:
      A 9.5% buffer is "huge" on AAPL (1% daily ATR → ~5σ over 10 days)
      but "tight" on COHR (3-4% daily ATR with 50%+ IV → ~1σ over 10 days).
      Using a static "X% = COMFORTABLE" rule made high-volatility names
      look safer than they actually are — exactly the type of UX trap that
      gets retail traders blown up.

    The new rating compares the buffer to the option market's IMPLIED
    expected move over the option's lifetime:
        expected_1σ_move = spot × IV × √(DTE / 365)
        buffer_in_sigmas = (spot - strike) / expected_1σ_move

      ≥ 1.5 sigma: COMFORTABLE  (only ~7% probability of touching strike)
      ≥ 0.8 sigma: ACCEPTABLE   (~20-40% probability)
      <  0.8 sigma: TIGHT       (assignment is a real possibility)

    Falls back to the old static thresholds when IV/DTE unavailable.

    Layout: [strike marker] -------- [spot marker]
            $strike                    $spot
    """
    # Compute volatility-adjusted rating when we have IV data
    buffer_in_sigmas = None
    expected_move_pct = None
    if iv and iv > 0 and dte and dte > 0:
        # Expected 1-sigma move over the option's lifetime (in dollars)
        expected_move = spot * iv * (dte / 365.0) ** 0.5
        if expected_move > 0:
            buffer_dollars = spot - strike
            buffer_in_sigmas = buffer_dollars / expected_move
            expected_move_pct = (expected_move / spot) * 100

    if buffer_in_sigmas is not None:
        if buffer_in_sigmas >= 1.5:
            buffer_color = theme.GREEN
            buffer_label = "COMFORTABLE"
        elif buffer_in_sigmas >= 0.8:
            buffer_color = theme.YELLOW
            buffer_label = "ACCEPTABLE"
        else:
            buffer_color = theme.RED
            buffer_label = "TIGHT"
    else:
        # Fallback to static thresholds when IV unavailable
        if otm_pct < 5:
            buffer_color = theme.RED
            buffer_label = "TIGHT"
        elif otm_pct < 8:
            buffer_color = theme.YELLOW
            buffer_label = "ACCEPTABLE"
        else:
            buffer_color = theme.GREEN
            buffer_label = "COMFORTABLE"

    # Build the rationale subtext that goes under the main header.
    # Shows the user WHY this is rated the way it is.
    if buffer_in_sigmas is not None and expected_move_pct is not None:
        # Format sigma to 1 decimal, but if very small or large round
        sigma_str = f"{buffer_in_sigmas:.1f}σ"
        rationale = (
            f"This is a <b style='color:{buffer_color}'>{sigma_str}</b> "
            f"move ({otm_pct:.1f}% drop vs ~{expected_move_pct:.1f}% "
            f"expected by options market over {dte}d)."
        )
    else:
        rationale = ""

    # Bar visualization
    st.markdown(
        f"<div style='margin-top:14px;padding:10px 12px;"
        f"background:{theme.PANEL_HI};border-radius:6px'>"
        # Header line
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;font-family:JetBrains Mono;font-size:0.72rem;"
        f"margin-bottom:8px'>"
        f"<span style='color:{theme.TEXT}'>"
        f"<b>Safety buffer:</b> requires a "
        f"<b style='color:{buffer_color}'>{otm_pct:.1f}% drop</b> in "
        f"the stock to be assigned.</span>"
        f"<span style='background:{buffer_color}22;color:{buffer_color};"
        f"font-size:0.62rem;padding:2px 8px;border-radius:8px;"
        f"font-weight:700;letter-spacing:0.06em;"
        f"border:1px solid {buffer_color}66'>{buffer_label}</span>"
        f"</div>"
        # The bar itself: full-width grey track with strike marker on left,
        # spot marker on right. Colored segment between = safety buffer.
        f"<div style='position:relative;height:8px;"
        f"background:{theme.BORDER};border-radius:4px;margin:6px 0 4px 0'>"
        # The safety-buffer colored fill (from left = strike to right = spot)
        f"<div style='position:absolute;left:0;top:0;height:100%;"
        f"width:100%;background:linear-gradient(90deg,"
        f"{buffer_color}66 0%, {buffer_color} 100%);border-radius:4px'></div>"
        # Strike marker (left edge)
        f"<div style='position:absolute;left:-2px;top:-4px;"
        f"width:4px;height:16px;background:{theme.TEXT};border-radius:2px'></div>"
        # Spot marker (right edge)
        f"<div style='position:absolute;right:-2px;top:-4px;"
        f"width:4px;height:16px;background:{theme.GREEN};border-radius:2px'></div>"
        f"</div>"
        # Footer with price labels
        f"<div style='display:flex;justify-content:space-between;"
        f"font-family:JetBrains Mono;font-size:0.68rem;"
        f"color:{theme.MUTED};margin-top:4px'>"
        f"<span><b style='color:{theme.TEXT}'>${strike:.2f}</b> "
        f"<span style='font-size:0.6rem'>STRIKE (assignment line)</span></span>"
        f"<span style='font-size:0.6rem'>CURRENT SPOT</span "
        f"><b style='color:{theme.GREEN}'>${spot:.2f}</b></span>"
        f"</div>"
        # Volatility-adjusted rationale subtext (only when IV data was available)
        + (f"<div style='font-family:JetBrains Mono;font-size:0.68rem;"
           f"color:{theme.MUTED};margin-top:6px;padding-top:6px;"
           f"border-top:1px solid {theme.BORDER};line-height:1.5'>"
           f"{rationale}</div>" if rationale else "")
        + f"</div>",
        unsafe_allow_html=True)


def _compute_setup_grade(confidence: float, annualized_roc: float) -> str:
    """Map (confidence, annualized_roc) → a Setup Grade letter A-E.

    Setup Grade is DISPLAY-ONLY for the scatter chart — it doesn't affect
    filtering, ranking, or any other math. The grade is just a quick visual
    classifier so users can see at a glance where each ticker falls in the
    risk/reward space.

    Rules (per user spec):
      Grade A: Confidence ≥ 75 AND Yield ≥ 40  → quality + yield (best)
      Grade B: Confidence ≥ 75 AND Yield < 40  → quality, but thin yield
      Grade C: 60 ≤ Confidence < 75 AND Yield ≥ 40  → mid-quality + yield
      Grade D: Confidence < 60 AND Yield ≥ 40  → yield-chasing (rich but risky)
      Grade E: Confidence < 75 AND Yield < 40 (excluding Grade C)  → bottom tier

    Note: this Setup Grade is separate from the Fundamental Grade shown on
    each candidate card (which scores stock quality, not option setup
    quality). They share letter labels but measure different things — the
    chart calls this "Setup Grade" to keep the distinction clear.

    Args:
        confidence:     0-100 composite confidence score
        annualized_roc: annualized yield percentage (e.g. 50.0 for 50%)

    Returns:
        Single-letter grade: 'A', 'B', 'C', 'D', or 'E'
    """
    if confidence >= 75 and annualized_roc >= 40:
        return "A"
    if confidence >= 75 and annualized_roc < 40:
        return "B"
    if 60 <= confidence < 75 and annualized_roc >= 40:
        return "C"
    if confidence < 60 and annualized_roc >= 40:
        return "D"
    # Everything else (low yield, not already A/B/C) → E
    return "E"


def _render_risk_reward_scatter(headlines: list[dict]):
    """Risk/Reward scatter — confidence (X) vs. annualized ROC (Y).

    One dot per ticker (the headline candidate). Color matches the
    conviction bucket (green/yellow/red). Quadrant guides at confidence=75
    and ROC=50 visually separate:
      - Top-right: high confidence + high yield = the holy grail
      - Top-left:  low confidence + high yield = chasing risky premium
      - Bottom-right: high confidence + thin yield = safe but barely worth it
      - Bottom-left:  avoid

    Clicking a dot scrolls to that ticker's card below. Uses Plotly's
    on_select="rerun" mechanism — when a user clicks a dot, Streamlit
    re-runs with the selection in session_state and we inject a JS scroll
    to the matching anchor (#wheel-card-TICKER).

    Args:
        headlines: list of headline candidate dicts (best per ticker)
    """
    import plotly.graph_objects as go

    # Prepare data
    tickers = [c["ticker"] for c in headlines]
    xs = [c["confidence"] for c in headlines]
    ys = [c["annualized_roc"] for c in headlines]

    # Setup Grade per ticker — display-only classifier mapping (confidence, ROC)
    # to A-E letter. Does NOT affect filtering, ranking, or any other math
    # on the page. Pure visual aid for the scatter chart.
    setup_grades = [_compute_setup_grade(c["confidence"], c["annualized_roc"])
                      for c in headlines]

    # Dot labels = "TICKER (G)" so the grade is visible without hovering.
    # Format chosen for compactness — parens disambiguate the grade from
    # the ticker symbol (e.g. avoids reading "AAPLA" as one token).
    dot_labels = [f"{t} ({g})" for t, g in zip(tickers, setup_grades)]

    # Bucket colors — same buckets as conviction pills (UNCHANGED)
    # Note: dot color stays tied to CONFIDENCE buckets, not Setup Grade.
    # Setup Grade is shown as a letter on the label; color stays consistent
    # with the conviction pills on the cards below.
    colors = []
    for conf in xs:
        if conf >= 75:
            colors.append(theme.GREEN)
        elif conf >= 55:
            colors.append(theme.YELLOW)
        else:
            colors.append(theme.RED)

    # Hover text with all the salient info (now includes Setup Grade)
    hover_texts = []
    for c, g in zip(headlines, setup_grades):
        otm = c["otm_pct"]
        bid = c["bid"]
        reason = c.get("confidence_reason", "")
        hover_texts.append(
            f"<b>{c['ticker']}</b> &nbsp;·&nbsp; <b>Setup Grade: {g}</b><br>"
            f"Sell ${c['strike']:.2f} put · {c['dte']}d<br>"
            f"Cash upfront: ${bid*100:,.0f}<br>"
            f"Confidence: {c['confidence']}/100<br>"
            f"Annualized ROC: {c['annualized_roc']:.0f}%<br>"
            f"OTM: {otm:.1f}%<br>"
            f"<br><i>{reason}</i><br>"
            f"<br>(Click to jump to card)"
        )

    # Build figure
    fig = go.Figure()

    # Quadrant guides — dashed lines at confidence=75 and ROC=50
    # Determine sensible Y-axis range (auto-scale with headroom)
    y_max = max(ys) * 1.15 if ys else 50
    x_min, x_max = 0, 100
    fig.add_shape(type="line", x0=75, x1=75, y0=0, y1=y_max,
                    line=dict(color=theme.MUTED, width=1, dash="dot"),
                    opacity=0.4)
    fig.add_shape(type="line", x0=x_min, x1=x_max, y0=50, y1=50,
                    line=dict(color=theme.MUTED, width=1, dash="dot"),
                    opacity=0.4)

    # Quadrant labels (anchored in each corner of the chart)
    annotations = [
        dict(x=88, y=y_max * 0.96, text="🎯 Best", showarrow=False,
              font=dict(color=theme.GREEN, size=10, family="JetBrains Mono"),
              opacity=0.7),
        dict(x=30, y=y_max * 0.96, text="⚠ Chasing yield", showarrow=False,
              font=dict(color=theme.RED, size=10, family="JetBrains Mono"),
              opacity=0.7),
        dict(x=88, y=15, text="🛡 Safe but thin", showarrow=False,
              font=dict(color=theme.MUTED, size=10, family="JetBrains Mono"),
              opacity=0.7),
        dict(x=30, y=15, text="❌ Avoid", showarrow=False,
              font=dict(color=theme.MUTED, size=10, family="JetBrains Mono"),
              opacity=0.7),
    ]

    # Scatter points — labels show "TICKER (G)" where G is the Setup Grade.
    # customdata stays as the bare ticker string (used by the click handler
    # to scroll to the corresponding card anchor; the grade letter would
    # break the anchor lookup if included).
    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="markers+text",
        marker=dict(size=14, color=colors,
                     line=dict(width=1.5, color=theme.PANEL)),
        text=dot_labels,
        textposition="top center",
        textfont=dict(color=theme.TEXT, size=11, family="JetBrains Mono"),
        hovertext=hover_texts,
        hoverinfo="text",
        customdata=tickers,  # bare ticker, used for click handler
        showlegend=False,
    ))

    fig.update_layout(
        title=dict(
            text="<b>Risk vs. Reward — Setup Grade per ticker</b>",
            font=dict(color=theme.TEXT, size=14, family="Sora"),
            x=0.02, xanchor="left",
        ),
        xaxis=dict(
            title=dict(text="Confidence (system quality read) →",
                        font=dict(color=theme.MUTED, size=11,
                                   family="JetBrains Mono")),
            range=[x_min, x_max],
            gridcolor=theme.BORDER, zerolinecolor=theme.BORDER,
            tickfont=dict(color=theme.MUTED, size=10),
        ),
        yaxis=dict(
            title=dict(text="Annualized ROC (yield) % →",
                        font=dict(color=theme.MUTED, size=11,
                                   family="JetBrains Mono")),
            range=[0, y_max],
            gridcolor=theme.BORDER, zerolinecolor=theme.BORDER,
            tickfont=dict(color=theme.MUTED, size=10),
        ),
        annotations=annotations,
        plot_bgcolor=theme.PANEL,
        paper_bgcolor=theme.PANEL,
        height=380,
        margin=dict(l=60, r=20, t=60, b=50),
    )

    # Render with click-to-select. Streamlit returns the click event
    # which we use to fire a browser-side scroll to the card anchor.
    chart_event = st.plotly_chart(
        fig, width="stretch",
        on_select="rerun",
        selection_mode="points",
        key="wheel_scatter",
    )

    # If a point was clicked this render, scroll the page to that card.
    # The click event payload contains the selected point's customdata
    # (the ticker symbol) — we inject a <script> tag that scrolls the
    # corresponding anchor into view.
    try:
        sel = (chart_event.get("selection", {}).get("points") or [])
    except Exception:
        sel = []
    if sel:
        clicked_ticker = sel[0].get("customdata")
        if clicked_ticker:
            # Inject scroll script. JavaScript is evaluated after the
            # markdown renders, so the anchor div will already exist.
            st.markdown(
                f"<script>"
                f"window.setTimeout(function() {{"
                f"  var el = document.getElementById('wheel-card-{clicked_ticker}');"
                f"  if (el) el.scrollIntoView({{behavior:'smooth', block:'start'}});"
                f"}}, 100);"
                f"</script>",
                unsafe_allow_html=True)

    # Quick legend / interpretation guide — now includes Setup Grade rules
    st.markdown(
        f"<div style='font-family:JetBrains Mono;font-size:0.72rem;"
        f"color:{theme.MUTED};margin:-4px 0 14px 0;line-height:1.6'>"
        f"<b>How to read this:</b> dots in the top-right are the best "
        f"trades (high confidence AND meaningful yield). Top-left is the "
        f"<b style='color:{theme.RED}'>yield-chasing trap</b> — premium "
        f"is rich but for a reason. Bottom-right is safe but the premium "
        f"barely justifies the capital. Click any dot to jump to that "
        f"ticker's full card."
        f"<br><br>"
        f"<b>Setup Grade</b> (letter next to each ticker, display-only — "
        f"doesn't affect filtering): "
        f"<b style='color:{theme.GREEN}'>A</b> = quality + yield "
        f"(conf ≥75, ROC ≥40%) &middot; "
        f"<b style='color:{theme.GREEN}'>B</b> = quality, thin yield "
        f"(conf ≥75, ROC &lt;40%) &middot; "
        f"<b style='color:{theme.YELLOW}'>C</b> = mid-quality + yield "
        f"(conf 60-74, ROC ≥40%) &middot; "
        f"<b style='color:{theme.RED}'>D</b> = yield-chasing "
        f"(conf &lt;60, ROC ≥40%) &middot; "
        f"<b style='color:{theme.MUTED}'>E</b> = bottom tier (everything else). "
        f"<i>Note: this is separate from the Fundamental Grade on each "
        f"card — same letters, different meaning.</i>"
        f"</div>",
        unsafe_allow_html=True)


def _render_alternate_strikes(ticker: str, alternates: list[dict], headline: dict):
    """Compact comparison rows for OTHER strikes on the same ticker.

    Renders inside a collapsible expander on the headline card. Each
    alternate gets a single compact row with: strike · OTM% · bid premium
    · ROC · confidence pill + "Why X" reason. Just enough context to
    decide if any alternate beats the engine's headline pick.

    Args:
        ticker: ticker symbol (for the expander title)
        alternates: list of candidate dicts (already sorted, headline excluded)
        headline: the headline candidate dict — used for visual comparison
                   ("Headline picked $345 strike, here are 2 alternates")
    """
    n = len(alternates)
    title = (
        f"▶ {n} other {ticker} strike{'s' if n != 1 else ''} available "
        f"(compare vs headline ${headline['strike']:.2f})"
    )

    with st.expander(title, expanded=False):
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.74rem;"
            f"color:{theme.MUTED};margin-bottom:8px;line-height:1.4'>"
            f"You'd execute only ONE of these per expiry on this ticker. "
            f"Sorted by confidence × yield; the engine's headline pick "
            f"(${headline['strike']:.2f}, "
            f"{_conf_label_for(headline['confidence'])} · "
            f"{headline['confidence']}/100) is above. Compare to see if "
            f"another strike would suit your risk preference."
            f"</div>",
            unsafe_allow_html=True)

        for alt in alternates:
            # Determine pill color/label
            label, color = _conf_label_and_color(alt["confidence"])
            # Compact row: strike · OTM% · bid · ROC · pill + reason
            cash_alt = alt["bid"] * 100
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:14px;"
                f"padding:10px 12px;margin:6px 0;"
                f"background:{theme.PANEL_HI};border-left:3px solid {color};"
                f"border-radius:6px'>"
                # Strike column
                f"<div style='min-width:90px'>"
                f"<div style='font-family:Sora;font-size:1.0rem;"
                f"font-weight:700;color:{theme.TEXT}'>"
                f"${alt['strike']:.2f}</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
                f"color:{theme.MUTED}'>"
                f"−{alt['otm_pct']:.1f}% from spot</div>"
                f"</div>"
                # Premium column
                f"<div style='min-width:90px'>"
                f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
                f"color:{theme.MUTED};font-weight:700;letter-spacing:0.06em'>"
                f"CASH UPFRONT</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.95rem;"
                f"color:{theme.GREEN};font-weight:700'>"
                f"${cash_alt:,.0f}</div>"
                f"</div>"
                # ROC column
                f"<div style='min-width:90px'>"
                f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
                f"color:{theme.MUTED};font-weight:700;letter-spacing:0.06em'>"
                f"ANN. ROC</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.85rem;"
                f"color:{theme.TEXT};font-weight:700'>"
                f"{alt['annualized_roc']:.0f}%</div>"
                f"</div>"
                # Confidence pill + reason
                f"<div style='flex:1;display:flex;flex-direction:column;"
                f"gap:4px;align-items:flex-start'>"
                f"<span style='background:{color}22;color:{color};"
                f"font-family:JetBrains Mono;font-size:0.66rem;font-weight:700;"
                f"padding:2px 8px;border-radius:8px;letter-spacing:0.05em;"
                f"border:1px solid {color}66'>"
                f"{label} · {alt['confidence']}/100</span>"
                f"<span style='font-family:JetBrains Mono;font-size:0.7rem;"
                f"color:{theme.MUTED};line-height:1.4'>"
                f"{alt.get('confidence_reason', '')}</span>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True)


def _conf_label_for(score: int) -> str:
    """Just the label (STRONG/MODERATE/WEAK) for a given confidence score."""
    if score >= 75: return "STRONG"
    if score >= 55: return "MODERATE"
    return "WEAK"


def _conf_label_and_color(score: int) -> tuple[str, str]:
    """Label + theme color for a confidence score. Same buckets as the
    headline card pill so visual association is consistent."""
    if score >= 75: return "STRONG", theme.GREEN
    if score >= 55: return "MODERATE", theme.YELLOW
    return "WEAK", theme.RED


def _render_context_row(c: dict):
    """Three-column context row: recent trend · fundamental grade · next
    earnings date. Inserted between the safety buffer and the plain-English
    summary on each put-candidate card.

    Each column degrades gracefully — missing data shows a "—" placeholder
    rather than hiding the column. This preserves a consistent visual
    rhythm across cards (every card has the same layout regardless of
    data availability).
    """
    col_trend, col_grade, col_earnings = st.columns(3)

    # ── Column 1: 30-day price trend ──
    with col_trend:
        trend_pct = c.get("trend_30d_pct")
        pts = c.get("trend_sparkline_points")
        if trend_pct is None or not pts:
            trend_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>📊 30-DAY TREND</div>"
                f"<div style='color:{theme.MUTED};font-size:0.78rem'>"
                f"— (no history)</div>"
            )
        else:
            # Color: green for positive, red for negative, near-flat = muted
            if abs(trend_pct) < 1:
                trend_color = theme.MUTED
                arrow = "→"
            elif trend_pct >= 0:
                trend_color = theme.GREEN
                arrow = "▲"
            else:
                trend_color = theme.RED
                arrow = "▼"
            # Build SVG sparkline path
            path = "M " + " L ".join(f"{x},{y}" for x, y in pts)
            trend_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>📊 30-DAY TREND</div>"
                f"<div style='display:flex;align-items:center;gap:8px'>"
                # Sparkline SVG
                f"<svg viewBox='0 0 100 100' "
                f"style='width:64px;height:24px;flex-shrink:0' "
                f"preserveAspectRatio='none'>"
                f"<path d='{path}' fill='none' stroke='{trend_color}' "
                f"stroke-width='2.5' stroke-linecap='round' "
                f"stroke-linejoin='round'/>"
                f"</svg>"
                # % callout
                f"<span style='font-family:JetBrains Mono;font-size:0.85rem;"
                f"font-weight:700;color:{trend_color}'>"
                f"{arrow} {trend_pct:+.1f}%</span>"
                f"</div>"
            )
        st.markdown(
            f"<div style='background:{theme.PANEL_HI};padding:8px 10px;"
            f"border-radius:6px;height:100%'>{trend_html}</div>",
            unsafe_allow_html=True)

    # ── Column 2: Fundamental Grade ──
    with col_grade:
        grade = c.get("fundamental_grade")
        gscore = c.get("fundamental_grade_score")
        gcolor = c.get("fundamental_grade_color") or theme.MUTED
        if grade is None or grade == "N/A":
            grade_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>🅰 FUNDAMENTAL GRADE</div>"
                f"<div style='color:{theme.MUTED};font-size:0.78rem'>"
                f"— (no fundamentals)</div>"
            )
        else:
            # Plain-English label by grade — one-line, layman-readable
            grade_label = {
                "A": "Excellent — wide-moat quality",
                "B": "Solid quality",
                "C": "Average / mixed",
                "D": "Weak but viable",
                "E": "Structurally broken — caution",
            }.get(grade, "")
            gscore_str = f"({gscore:.0f}/100)" if gscore is not None else ""
            grade_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>🅰 FUNDAMENTAL GRADE</div>"
                f"<div style='display:flex;align-items:center;gap:8px'>"
                # Big grade letter
                f"<span style='font-family:Sora;font-size:1.6rem;"
                f"font-weight:800;color:{gcolor};line-height:1'>"
                f"{grade}</span>"
                # Label + score
                f"<div style='line-height:1.3'>"
                f"<div style='font-family:JetBrains Mono;font-size:0.7rem;"
                f"color:{theme.TEXT};font-weight:600'>{grade_label}</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
                f"color:{theme.MUTED}'>{gscore_str}</div>"
                f"</div>"
                f"</div>"
            )
        st.markdown(
            f"<div style='background:{theme.PANEL_HI};padding:8px 10px;"
            f"border-radius:6px;height:100%'>{grade_html}</div>",
            unsafe_allow_html=True)

    # ── Column 3: Next earnings date ──
    with col_earnings:
        edate = c.get("next_earnings_date")
        edays = c.get("next_earnings_days")
        if edate is None:
            earnings_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>📅 NEXT EARNINGS</div>"
                f"<div style='color:{theme.MUTED};font-size:0.78rem'>"
                f"— (date unknown)</div>"
            )
        else:
            # Color by proximity: red if within DTE (will hit during trade),
            # yellow if within 30 days (next roll), grey otherwise
            dte = c.get("dte") or 7
            if edays <= dte:
                ec_color = theme.RED
                ec_note = "BEFORE EXPIRY — high risk"
            elif edays <= 14:
                ec_color = theme.RED
                ec_note = "soon — IV may be inflated"
            elif edays <= 30:
                ec_color = theme.YELLOW
                ec_note = "plan rolls accordingly"
            else:
                ec_color = theme.GREEN
                ec_note = "comfortably distant"
            day_word = "day" if edays == 1 else "days"
            earnings_html = (
                f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
                f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
                f"margin-bottom:4px'>📅 NEXT EARNINGS</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.85rem;"
                f"font-weight:700;color:{ec_color}'>"
                f"{edate}</div>"
                f"<div style='font-family:JetBrains Mono;font-size:0.66rem;"
                f"color:{theme.MUTED};margin-top:2px'>"
                f"{edays} {day_word} away · {ec_note}</div>"
            )
        st.markdown(
            f"<div style='background:{theme.PANEL_HI};padding:8px 10px;"
            f"border-radius:6px;height:100%'>{earnings_html}</div>",
            unsafe_allow_html=True)


def _scan_watchlist_puts(watchlist, min_otm, max_otm, min_roc):
    """Scan each ticker for weekly put candidates meeting filters.

    Returns dict with 'candidates' (list of dicts) and 'diagnostics' (list of
    (ticker, message) tuples for tickers that produced no data).

    Per-candidate enrichment (Tier 1 additions):
      - already_assigned: bool — whether user already holds an assigned
        position on this ticker (concentration risk warning)
      - trend_30d_pct: 30-day % change (signed)
      - trend_sparkline_points: SVG-ready normalized points for the inline chart
      - fundamental_grade: A/B/C/D/E or None
      - fundamental_grade_score: 0-100 or None
      - fundamental_grade_color: hex color string
      - next_earnings_date: ISO date string or None
      - next_earnings_days: int or None (days until next earnings)
    """
    import assigned_positions as ap
    import run_scanner as scanner

    candidates = []
    diagnostics = []

    # Pre-compute the set of tickers we already have assigned positions on.
    # One SQLite query for the whole scan instead of per-ticker.
    try:
        assigned_tickers = {p["ticker"].upper() for p in ap.list_open()}
    except Exception:
        assigned_tickers = set()

    for ticker in watchlist:
        chain = du.get_weekly_option_chain(ticker, target_dte_days=7)
        if chain["status"] != "ok":
            diagnostics.append((ticker, chain.get("error") or chain["status"]))
            continue

        puts = chain.get("puts")
        if puts is None or puts.empty:
            diagnostics.append((ticker, "no puts in chain"))
            continue

        spot = chain.get("current_price")
        if spot is None or spot <= 0:
            diagnostics.append((ticker, "no spot price"))
            continue

        # ── Per-ticker enrichment (computed once per ticker, used for all
        # candidate strikes on that ticker) ──
        already_assigned = ticker.upper() in assigned_tickers

        # 30-day trend — from OHLCV cache (already populated by Page 1).
        # Failures here are non-fatal: trend is informational, not gating.
        trend_pct = None
        sparkline_points = None
        try:
            hist = du.get_history(ticker, days=45)  # ~30 trading days
            if hist is not None and not hist.empty and "Close" in hist.columns:
                closes = hist["Close"].dropna().tail(30)
                if len(closes) >= 5:
                    trend_pct = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100
                    # Build a normalized sparkline path: x sweeps 0→100,
                    # y is inverted (SVG y-axis grows down)
                    lo, hi = float(closes.min()), float(closes.max())
                    span = hi - lo if hi > lo else 1.0
                    n = len(closes)
                    pts = []
                    for i, v in enumerate(closes):
                        x = (i / (n - 1)) * 100 if n > 1 else 50
                        y = 100 - ((float(v) - lo) / span) * 100
                        pts.append((round(x, 2), round(y, 2)))
                    sparkline_points = pts
        except Exception:
            pass

        # Fundamental Grade — pull cached fundamentals + compute Grade.
        # Same pillar engine as Page 2.
        f_grade, f_score, f_color = None, None, None
        try:
            fund = du.get_fundamentals(ticker)
            if fund:
                grade_info = scanner.calculate_fundamental_grade(ticker, fund)
                f_grade = grade_info.get("grade")
                f_score = grade_info.get("score")
                f_color = grade_info.get("color")
        except Exception:
            pass

        # Next earnings date — Finnhub-backed. Show even when >14 days away
        # (the existing red banner only fires for the urgent 0-14d case;
        # this is a neutral "for your planning" data point).
        next_earnings_date = None
        next_earnings_days = None
        try:
            edate = du.get_earnings_date(ticker)
            if edate is not None:
                if isinstance(edate, str):
                    edate_obj = datetime.strptime(edate, "%Y-%m-%d").date()
                elif hasattr(edate, "date"):
                    edate_obj = edate.date()
                else:
                    edate_obj = edate
                days = (edate_obj - datetime.today().date()).days
                if days >= 0:  # only show future earnings
                    next_earnings_date = edate_obj.isoformat()
                    next_earnings_days = days
        except Exception:
            pass

        # ── Derive per-stock filter values (cap-tier adjusted) ──
        # The function parameters are the user's BASELINE filters; per-stock
        # adjustments nudge them up or down based on the ticker's market-cap
        # tier. See CAP_TIER_ADJUSTMENTS for the rules. The vol-adjusted
        # buffer label on each card does the real risk calibration; this
        # adjustment is just about surfacing the right candidates per stock.
        t_min_otm, t_max_otm, t_min_roc, cap_tier = _derive_filters_for_ticker(
            ticker, min_otm, max_otm, min_roc)

        # Filter to OTM range [min_otm%, max_otm%] BELOW spot (puts are OTM
        # when strike < spot). Uses the ticker-specific filter values.
        lower_strike = spot * (1 - t_max_otm / 100)
        upper_strike = spot * (1 - t_min_otm / 100)
        candidates_for_ticker = puts[
            (puts["strike"] >= lower_strike)
            & (puts["strike"] <= upper_strike)
            & (puts["bid"] > 0)  # must have a buyer
        ].copy()

        if candidates_for_ticker.empty:
            diagnostics.append((ticker, f"no strikes in {t_min_otm:.1f}-"
                                          f"{t_max_otm:.1f}% OTM range "
                                          f"({cap_tier}-cap adjusted)"))
            continue

        # Check earnings proximity for warnings (existing 14-day check)
        earnings_warning = _check_earnings_warning(ticker)

        for _, row in candidates_for_ticker.iterrows():
            strike = float(row["strike"])
            bid    = float(row["bid"])
            ask    = float(row.get("ask", 0) or 0)
            iv     = float(row.get("impliedVolatility", 0) or 0)
            vol    = float(row.get("volume", 0) or 0)
            oi     = float(row.get("openInterest", 0) or 0)
            delta  = row.get("delta_approx")
            if delta is not None and not pd.isna(delta):
                delta = float(delta)
            else:
                delta = None

            otm_pct = (spot - strike) / spot * 100
            # Annualized ROC per your spec: (P / K) × (365 / DTE)
            annualized_roc = (bid / strike) * (365 / chain["dte"]) * 100
            # Probability OTM = 1 - |delta| (standard option screening proxy)
            p_otm = (1.0 - abs(delta)) if delta is not None else None

            # Skip rows that don't meet the per-ticker ROC threshold
            # (cap-tier adjusted from baseline)
            if annualized_roc < t_min_roc:
                continue

            # Spread quality
            mid = (bid + ask) / 2 if ask > 0 else bid
            if ask > 0 and mid > 0:
                spread_pct = (ask - bid) / mid * 100
                if spread_pct < 10:
                    spread_quality = "Tight"
                    spread_score = 100
                elif spread_pct < 25:
                    spread_quality = "OK"
                    spread_score = 70
                elif spread_pct < 50:
                    spread_quality = "Wide"
                    spread_score = 40
                else:
                    spread_quality = "Very Wide"
                    spread_score = 10
            else:
                spread_quality = "Unknown"
                spread_score = 30

            # Build warning list
            warnings = []
            if earnings_warning:
                warnings.append(earnings_warning)
            if spread_quality in ("Wide", "Very Wide"):
                warnings.append(f"⚠ {spread_quality} spread")
            if vol == 0 and oi < 10:
                warnings.append("⚠ Untraded contract")
            if iv > 1.5:  # IV > 150% is extreme
                warnings.append(f"⚠ Extreme IV ({iv*100:.0f}%)")

            # Confidence score: composite of P(OTM), spread, volume,
            # earnings proximity. Now returns (score, reason) where reason
            # is a plain-English explanation of the dominant drag (or
            # strength) for the bucket label shown on the card.
            confidence, conf_reason = _compute_confidence(
                p_otm=p_otm, spread_score=spread_score,
                volume=vol, oi=oi,
                has_earnings_warning=bool(earnings_warning),
                spread_quality=spread_quality)

            candidates.append({
                "ticker":          ticker,
                "spot":            spot,
                "strike":          strike,
                "otm_pct":         otm_pct,
                "bid":             bid,
                "ask":             ask,
                "iv":              iv,
                "volume":          vol,
                "oi":              oi,
                "delta":           delta,
                "p_otm":           p_otm,
                "annualized_roc":  annualized_roc,
                "spread_quality":  spread_quality,
                "confidence":      confidence,
                "confidence_reason": conf_reason,
                "warnings":        warnings,
                "expiry":          chain["expiry"],
                "dte":             chain["dte"],
                # ── Tier 1 enrichment (per-ticker, repeated across strikes) ──
                "already_assigned":         already_assigned,
                "trend_30d_pct":            trend_pct,
                "trend_sparkline_points":   sparkline_points,
                "fundamental_grade":        f_grade,
                "fundamental_grade_score":  f_score,
                "fundamental_grade_color":  f_color,
                "next_earnings_date":       next_earnings_date,
                "next_earnings_days":       next_earnings_days,
                # ── Per-stock filter info (cap-tier driven) ──
                "cap_tier":                 cap_tier,
                "applied_min_otm":          t_min_otm,
                "applied_max_otm":          t_max_otm,
                "applied_min_roc":          t_min_roc,
            })

    return {"candidates": candidates, "diagnostics": diagnostics}


def _compute_confidence(p_otm, spread_score, volume, oi, has_earnings_warning,
                          spread_quality: str = ""):
    """Composite confidence score (0-100) for a put candidate, plus a
    one-line plain-English reason explaining the bucket label.

    Returns: (score: int, reason: str)

    Score components:
      - 50% weight: probability of finishing OTM (delta-based)
      - 30% weight: bid-ask spread tightness (liquidity)
      - 20% weight: volume + open interest (active contract)
      - −15 penalty if earnings within 14 days

    Reason logic (rule-based, prioritized — worst factor wins):
      1. Earnings within 14 days — single biggest red flag for wheel
         strategies. IV crush + binary event risk dominate all other factors.
      2. Wide bid-ask spread — execution risk. Real fills will be worse
         than displayed.
      3. Low P(OTM) — assignment risk is elevated. The most common cause
         of MODERATE/WEAK ratings on otherwise-clean wheel candidates.
      4. Low liquidity (vol/OI) — hard to enter/exit at fair price.
      5. None of the above firing → score is STRONG, give a positive read.
    """
    # ── score components ──
    if p_otm is None:
        p_otm_score = 50
    else:
        p_otm_score = max(0, min(100, (p_otm - 0.5) / 0.45 * 100))

    if volume >= 100 or oi >= 500:
        liquidity_score = 100
    elif volume >= 20 or oi >= 100:
        liquidity_score = 70
    elif oi >= 20:
        liquidity_score = 40
    else:
        liquidity_score = 10

    composite = 0.5 * p_otm_score + 0.3 * spread_score + 0.2 * liquidity_score
    if has_earnings_warning:
        composite -= 15
    score = int(max(0, min(100, composite)))

    # ── reason: identify the dominant drag (or the strength) ──
    # Order matters: check the worst-first risks before falling through to
    # "everything looks good." This gives users actionable, specific info
    # instead of a generic "moderate setup" tagline.
    reason = ""

    if has_earnings_warning:
        # Most important risk for a wheel trade — overrides everything else.
        reason = ("earnings event in the next 14 days creates binary risk "
                   "and IV crush — the juicy premium reflects that risk")
    elif spread_quality in ("Wide", "Very Wide"):
        # Execution risk — premium shown may not be what you actually get.
        reason = (f"{spread_quality.lower()} bid-ask spread means real "
                   f"fills will be noticeably worse than the displayed bid")
    elif p_otm is not None and p_otm < 0.80:
        # Most common downgrade — elevated assignment risk.
        pct = int(p_otm * 100)
        reason = (f"elevated assignment risk — delta implies only "
                   f"~{pct}% chance of expiring OTM (you'd want 85%+ for "
                   f"a comfortable wheel setup)")
    elif liquidity_score < 70:
        # Liquidity issues — harder to manage the position.
        reason = ("low volume and open interest — fewer counterparties means "
                   "wider effective spreads if you need to close early")
    elif score >= 75:
        # No specific risk firing; just give a positive read.
        if p_otm is not None and p_otm >= 0.85:
            reason = (f"high probability of OTM expiry ({int(p_otm*100)}%), "
                       f"tight spread, active contract — no obvious risks")
        else:
            reason = "tight spread, good liquidity, no event risk"
    else:
        # Score in middle band but no single dominant drag — typically
        # multiple small factors averaging down.
        reason = ("multiple small factors averaging down — none alarming "
                   "individually but no standout strength either")

    return score, reason


def _check_earnings_warning(ticker: str) -> str:
    """Returns a string warning if earnings within next 14 days, else ''.
    Uses the existing Finnhub-backed earnings calendar."""
    try:
        edate = du.get_earnings_date(ticker)
        if edate is None:
            return ""
        if isinstance(edate, str):
            edate = datetime.strptime(edate, "%Y-%m-%d").date()
        elif hasattr(edate, "date"):
            edate = edate.date()
        days = (edate - datetime.today().date()).days
        if 0 <= days <= 14:
            return f"⚠ Earnings in {days}d"
    except Exception:
        pass
    return ""

# ═══════════════════════════════════════════════════════════════════════════
# Tab 2 — Wheel Manager (rebuilt for layman-friendly two-level flow)
#
# Design:
#   - Level 1: Compact list of all assigned positions. One row each. Click → drill in.
#   - Level 2: Single-stock detail view with plain-English narrative + Best Play
#              + Other Options + collapsible math + close controls.
#
# Why this is better than the previous "everything visible" layout:
#   - Most users want to know "what should I do this week with my RIVN?"
#   - They don't want to see 6 strikes for Path A, 6 strikes for Path B,
#     a cost basis ledger, and close controls for 3 positions simultaneously.
#   - The new flow: pick a position → see the recommended call to sell,
#     with one-line rationale. Other options shown but de-emphasized.
#     Math available on demand.
# ═══════════════════════════════════════════════════════════════════════════

def _render_wheel_manager():
    """Tab 2 — Wheel Manager. Two-level flow:
       Level 1 (default): Overview list of all assigned positions
       Level 2 (after click): Detailed recommendations for selected position
    """
    import assigned_positions as ap

    # Section header + brief plain-English intro
    st.markdown("### 🔄 Wheel Manager")
    st.markdown(
        f"<div style='color:{theme.MUTED};font-size:0.85rem;"
        f"margin-bottom:14px;line-height:1.5'>"
        f"After a put gets assigned, this engine helps you decide what call "
        f"to sell THIS WEEK to either recover the capital safely "
        f"or grab extra premium. Pick a position to see recommendations."
        f"</div>",
        unsafe_allow_html=True)

    # ── Mark-as-assigned form (always available at top) ──
    _render_mark_assigned_form(ap)

    open_positions = ap.list_open()
    stats = ap.coverage_stats()

    # ── Summary bar ──
    st.markdown(
        f"<div style='display:flex;gap:14px;margin:14px 0 8px 0;"
        f"font-family:JetBrains Mono;font-size:0.85rem'>"
        f"<span><span style='color:{theme.MUTED}'>Open positions:</span> "
        f"<b style='color:{theme.TEXT}'>{stats['open']}</b></span>"
        f"<span><span style='color:{theme.MUTED}'>Closed (lifetime):</span> "
        f"<b style='color:{theme.TEXT}'>{stats['closed']}</b></span>"
        f"<span><span style='color:{theme.MUTED}'>Realized P&amp;L:</span> "
        f"<b style='color:"
        f"{theme.GREEN if stats['total_realized_pnl'] >= 0 else theme.RED}'>"
        f"{_fmt_signed(stats['total_realized_pnl'])}</b></span>"
        f"</div>",
        unsafe_allow_html=True)

    if not open_positions:
        st.info("No open assigned positions yet. Use the **➕ Mark a position "
                "as assigned** form above to add one after a put assignment.")
        _render_closed_history(ap)
        return

    # ── LEVEL 1: Overview list (default) OR LEVEL 2: drill-down ──
    selected_id = st.session_state.get("wheel_selected_id")
    if selected_id is not None:
        # User clicked into a position — show detail view
        pos = next((p for p in open_positions if p["id"] == selected_id), None)
        if pos is None:
            # Position was closed elsewhere; reset selection
            st.session_state["wheel_selected_id"] = None
            st.rerun()
            return
        _render_position_detail(pos, ap)
    else:
        # Default: overview list
        _render_positions_overview(open_positions)

    _render_closed_history(ap)


def _render_mark_assigned_form(ap_module):
    """The "add an assignment" form — collapsed by default."""
    with st.expander("➕ Mark a position as assigned", expanded=False):
        st.markdown(
            f"<div style='color:{theme.MUTED};font-size:0.78rem;"
            f"margin-bottom:8px'>"
            f"Fill these in AFTER a put gets assigned to you. The original "
            f"premium is what you collected when you first SOLD the put."
            f"</div>",
            unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            new_ticker = st.text_input("Ticker", key="assign_ticker",
                                        placeholder="e.g. RIVN")
            new_strike = st.number_input("Assigned strike ($)",
                                          key="assign_strike",
                                          min_value=0.01, value=20.00,
                                          step=0.50, format="%.2f",
                                          help="The put strike you got "
                                               "assigned at (K_assigned).")
            new_shares = st.number_input("Shares",
                                          key="assign_shares",
                                          min_value=1, value=100, step=100,
                                          help="100 per contract. Multiple "
                                               "of 100 if you sold more.")
        with col2:
            new_premium = st.number_input(
                "Original put premium per share ($)",
                key="assign_premium", min_value=0.0, value=0.50,
                step=0.05, format="%.2f",
                help="Per-share premium you got when you SOLD the put. Bid "
                     "price you got filled at (e.g. $0.50, not $50).")
            new_date = st.date_input("Assigned date",
                                      key="assign_date",
                                      value=datetime.today().date())
            new_notes = st.text_input("Notes (optional)",
                                       key="assign_notes",
                                       placeholder="e.g. earnings miss")

        if new_strike > 0 and new_premium >= 0:
            cb_preview = new_strike - new_premium
            st.markdown(
                f"<div style='background:{theme.PANEL_HI};"
                f"padding:8px 12px;border-radius:6px;"
                f"font-family:JetBrains Mono;font-size:0.82rem;"
                f"margin-top:8px'>"
                f"<span style='color:{theme.MUTED}'>"
                f"Your real cost basis (after put premium):</span> "
                f"<b style='color:{theme.TEXT}'>${cb_preview:.2f}/share</b>"
                f"</div>",
                unsafe_allow_html=True)

        if st.button("Save assignment", type="primary", key="save_assignment"):
            if not new_ticker:
                st.error("Ticker is required.")
            elif new_strike <= 0:
                st.error("Strike must be positive.")
            else:
                pid = ap_module.mark_assigned(
                    ticker=new_ticker,
                    assigned_strike=new_strike,
                    original_put_premium=new_premium,
                    assigned_date=new_date.isoformat(),
                    shares=int(new_shares), notes=new_notes)
                if pid:
                    st.toast(f"Saved {new_ticker.upper()}", icon="✓")
                    st.rerun()
                else:
                    st.error("Failed to save.")


def _render_positions_overview(open_positions):
    """Level 1 — compact list of all assigned positions. One row each."""
    st.markdown(
        f"<div style='font-family:Sora;font-size:1.05rem;"
        f"font-weight:700;color:{theme.TEXT};margin:14px 0 8px 0'>"
        f"📋 Your Assigned Positions ({len(open_positions)})"
        f"</div>",
        unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:{theme.MUTED};font-size:0.78rem;margin-bottom:12px'>"
        f"Click a position below to see this week's call recommendations."
        f"</div>",
        unsafe_allow_html=True)

    # For each position, fetch current spot to compute the at-a-glance status
    for pos in open_positions:
        _render_overview_row(pos)


def _render_overview_row(pos: dict):
    """One row in the overview list. Compact, clickable."""
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]
    cb = K - P_put
    ticker = pos["ticker"]

    # Quick spot fetch (cached) — don't fetch the full chain for the list
    try:
        # Use a lightweight quote — the option chain is heavier than needed here
        quote = du.get_live_quotes((ticker,)).get(ticker, {})
        spot = quote.get("price") if quote.get("status") == "ok" else None
    except Exception:
        spot = None

    if spot is not None:
        pct = (spot - K) / K * 100
        net_pnl = (spot - K) * shares + P_put * shares
        pct_color = theme.GREEN if pct >= 0 else theme.RED
        arrow = "▲" if pct >= 0 else "▼"
        pct_str = f"{arrow} {pct:+.1f}%"
        net_str = _fmt_signed(net_pnl)
        net_color = theme.GREEN if net_pnl >= 0 else theme.RED
        spot_str = f"${spot:.2f}"
    else:
        pct_color = theme.MUTED
        pct_str = "—"
        net_str = "—"
        net_color = theme.MUTED
        spot_str = "—"

    # Layout: ticker / assigned info / current spot / status / pnl / button
    col1, col2, col3, col4, col5 = st.columns([1.5, 2, 1.5, 1.5, 1])
    with col1:
        st.markdown(
            f"<div style='font-family:Sora;font-size:1.1rem;"
            f"font-weight:700;color:{theme.TEXT};padding-top:6px'>"
            f"{ticker}</div>",
            unsafe_allow_html=True)
    with col2:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"color:{theme.MUTED};padding-top:8px;line-height:1.4'>"
            f"Assigned at <b style='color:{theme.TEXT}'>${K:.2f}</b><br>"
            f"<span style='font-size:0.7rem'>"
            f"{shares} sh · cost basis ${cb:.2f}</span>"
            f"</div>",
            unsafe_allow_html=True)
    with col3:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"padding-top:8px;line-height:1.4'>"
            f"<span style='color:{theme.MUTED}'>Now:</span> "
            f"<b style='color:{theme.TEXT}'>{spot_str}</b><br>"
            f"<b style='color:{pct_color};font-size:0.9rem'>{pct_str}</b>"
            f"</div>",
            unsafe_allow_html=True)
    with col4:
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
            f"padding-top:8px;line-height:1.4'>"
            f"<span style='color:{theme.MUTED}'>Unrealized:</span><br>"
            f"<b style='color:{net_color};font-size:0.95rem'>{net_str}</b>"
            f"</div>",
            unsafe_allow_html=True)
    with col5:
        # Use a unique key per position id so multiple rows don't collide
        if st.button("View ▸", key=f"view_pos_{pos['id']}",
                      width="stretch"):
            st.session_state["wheel_selected_id"] = pos["id"]
            st.rerun()

    # Light separator between rows
    st.markdown(
        f"<div style='border-bottom:1px solid {theme.BORDER};"
        f"margin:6px 0 6px 0'></div>",
        unsafe_allow_html=True)


def _render_position_detail(pos: dict, ap_module):
    """Level 2 — detail view for one selected position.
    Plain-English narrative + Best Play card + Other Options + math (collapsible).
    """
    # Back button
    col_back, col_spacer = st.columns([1, 5])
    with col_back:
        if st.button("← Back to list", key="back_to_list"):
            st.session_state["wheel_selected_id"] = None
            st.rerun()

    ticker = pos["ticker"]
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]
    cb = K - P_put

    # Live chain + spot
    chain = du.get_weekly_option_chain(ticker, target_dte_days=7)
    spot = chain.get("current_price") if chain.get("status") == "ok" else None

    # ── Header: plain-English status sentence ──
    _render_detail_header(pos, spot)

    # ── Recommendations ──
    if chain.get("status") != "ok" or chain.get("calls") is None:
        st.warning(
            f"⚠ Can't fetch call chain for {ticker} right now "
            f"(yfinance may be rate-limited). Try again in a few minutes."
        )
        _render_close_controls_simple(pos, spot, ap_module)
        return

    # Build candidate lists
    safe_candidates = _build_safe_candidates(pos, chain)        # Path A
    aggressive_candidates = _build_aggressive_candidates(pos, chain)  # Path B

    _render_best_play_card(safe_candidates, aggressive_candidates, pos, spot)

    # Other options (de-emphasized)
    _render_other_options(safe_candidates, aggressive_candidates, pos, spot)

    # Collapsible math section
    _render_math_details(pos, spot, chain)

    # Close-out controls
    _render_close_controls_simple(pos, spot, ap_module)


def _render_detail_header(pos: dict, spot: float | None):
    """Plain-English summary at top of detail view."""
    ticker = pos["ticker"]
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]
    cb = K - P_put
    assigned_date = pos["assigned_date"]
    notes = pos.get("notes") or ""

    if spot is not None:
        diff = (spot - K) * shares + P_put * shares
        pct = (spot - K) / K * 100
        if diff >= 0:
            status_sentence = (
                f"Currently at <b>${spot:.2f}</b> "
                f"<span style='color:{theme.GREEN}'>"
                f"▲ {pct:+.1f}% above your strike</span> — "
                f"you're up <b style='color:{theme.GREEN}'>"
                f"{_fmt_signed(diff)}</b> on this position (including the "
                f"put premium you already collected)."
            )
        else:
            status_sentence = (
                f"Currently at <b>${spot:.2f}</b> "
                f"<span style='color:{theme.RED}'>"
                f"▼ {pct:+.1f}% below your strike</span> — "
                f"you're down <b style='color:{theme.RED}'>"
                f"{_fmt_signed(diff)}</b> on this position right now "
                f"(but the put premium you collected helps absorb some of it)."
            )
    else:
        status_sentence = "Current price unavailable — yfinance may be down."

    st.markdown(
        f"<div style='background:{theme.PANEL};border:1px solid {theme.BORDER};"
        f"border-radius:8px;padding:16px 20px;margin:14px 0'>"
        f"<div style='font-family:Sora;font-size:1.4rem;font-weight:700;"
        f"color:{theme.TEXT};margin-bottom:6px'>{ticker}</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.85rem;"
        f"color:{theme.MUTED};margin-bottom:12px'>"
        f"Assigned <b>{assigned_date}</b> at <b>${K:.2f}</b>/share &middot; "
        f"{shares} shares &middot; "
        f"You collected <b style='color:{theme.GREEN}'>${P_put:.2f}/sh</b> "
        f"in put premium &middot; "
        f"Real cost basis: <b>${cb:.2f}</b>/share"
        f"{('<br>📝 ' + notes) if notes else ''}"
        f"</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.92rem;"
        f"color:{theme.TEXT};line-height:1.5'>"
        f"{status_sentence}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True)


def _build_safe_candidates(pos: dict, chain: dict) -> list[dict]:
    """Path A — calls with strike >= K_assigned. Sorted by best ROC."""
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]
    calls = chain["calls"]
    if calls is None or calls.empty:
        return []

    df = calls[(calls["strike"] >= K) & (calls["bid"] > 0)].copy()
    if df.empty:
        return []
    df = df.sort_values("strike").head(8)  # nearest 8 ITM/OTM calls above K

    out = []
    dte = chain.get("dte") or 7
    for _, row in df.iterrows():
        Kcall = float(row["strike"])
        Pcall = float(row["bid"])
        delta = row.get("delta_approx")
        if delta is not None and not pd.isna(delta):
            delta = float(delta)
        else:
            delta = None
        share_pnl = (Kcall - K) * shares
        put_d = P_put * shares
        call_d = Pcall * shares
        net_if_assigned = share_pnl + put_d + call_d
        net_if_expires = put_d + call_d  # if call expires worthless, keep shares + premiums
        # Probability of expiring OTM ≈ 1 - call_delta (so we KEEP the premium
        # AND the shares — best of both worlds)
        p_otm = (1.0 - delta) if delta is not None else None
        out.append({
            "strike": Kcall,
            "premium": Pcall,
            "delta": delta,
            "p_otm": p_otm,
            "net_if_assigned": net_if_assigned,
            "net_if_expires_otm": net_if_expires,
            "annualized_roc": (Pcall / Kcall) * (365 / dte) * 100,
            "is_safe": True,
        })
    return out


def _build_aggressive_candidates(pos: dict, chain: dict) -> list[dict]:
    """Path B — calls with strike < K_assigned. Sorted by net outcome desc."""
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]
    calls = chain["calls"]
    if calls is None or calls.empty:
        return []

    df = calls[(calls["strike"] < K) & (calls["bid"] > 0)].copy()
    if df.empty:
        return []
    # Sort by strike descending — strikes nearest to K first
    df = df.sort_values("strike", ascending=False).head(8)

    out = []
    dte = chain.get("dte") or 7
    for _, row in df.iterrows():
        Kcall = float(row["strike"])
        Pcall = float(row["bid"])
        delta = row.get("delta_approx")
        if delta is not None and not pd.isna(delta):
            delta = float(delta)
        else:
            delta = None
        share_loss = (Kcall - K) * shares  # negative
        put_d = P_put * shares
        call_d = Pcall * shares
        net_if_assigned = share_loss + put_d + call_d  # the all-important number
        net_if_expires = put_d + call_d
        p_otm = (1.0 - delta) if delta is not None else None
        out.append({
            "strike": Kcall,
            "premium": Pcall,
            "delta": delta,
            "p_otm": p_otm,
            "net_if_assigned": net_if_assigned,
            "net_if_expires_otm": net_if_expires,
            "annualized_roc": (Pcall / Kcall) * (365 / dte) * 100,
            "is_safe": False,
        })
    return out


def _pick_best_recommendation(safe: list, aggressive: list) -> tuple[dict, str]:
    """Decision logic for "which call should I sell THIS WEEK?"

    Priority order:
      1. Best safe candidate with annualized ROC >= 15% — capital preservation
         + meaningful yield. Use the strike NEAREST to K_assigned that
         meets the yield threshold (closest to spot = highest premium that
         still doesn't lock in a loss).
      2. If no safe meets yield threshold: best safe by ROC (even if low)
      3. If no safe candidates at all: best aggressive with net_if_assigned > 0
      4. If everything is a loss: best aggressive with smallest loss
      5. If nothing exists: None

    Returns (chosen_dict, reason_string).
    """
    if not safe and not aggressive:
        return None, "No call options available for this expiry."

    # Step 1: Find best safe candidate
    if safe:
        # Strike nearest to K_assigned = smallest strike (already sorted ascending)
        # Look for any with ROC >= 15%
        viable_safe = [c for c in safe if c["annualized_roc"] >= 15]
        if viable_safe:
            # Among viable, pick highest premium dollar (= lowest strike that
            # still doesn't lock in a loss = nearest to spot)
            best = max(viable_safe, key=lambda c: c["premium"])
            return best, (
                f"Safe choice with {best['annualized_roc']:.0f}% annualized "
                f"yield. If stock recovers and gets called, you net "
                f"{_fmt_signed(best['net_if_assigned'])}. If it stays low, "
                f"you pocket the premium and try again next week."
            )

        # Step 2: No safe meets threshold; use the best ROC available
        best = max(safe, key=lambda c: c["annualized_roc"])
        return best, (
            f"Safest choice available, though premium is thin "
            f"({best['annualized_roc']:.0f}% annualized). Stock has likely "
            f"fallen far below your strike — consider waiting for a relief "
            f"rally OR accept the small premium."
        )

    # Step 3: No safe candidates; check aggressive
    profitable_aggressive = [c for c in aggressive if c["net_if_assigned"] > 0]
    if profitable_aggressive:
        # Among profitable, pick the one with smallest assignment risk (lowest delta)
        best = min(profitable_aggressive,
                    key=lambda c: c["delta"] if c["delta"] is not None else 1.0)
        return best, (
            f"No safe calls available. This aggressive choice still nets "
            f"{_fmt_signed(best['net_if_assigned'])} IF the call gets "
            f"assigned — the put premium plus call premium overcome the "
            f"share loss. But you're capping your upside at this strike."
        )

    # Step 4: Everything is a loss; surface the smallest one with a warning
    if aggressive:
        best = max(aggressive, key=lambda c: c["net_if_assigned"])
        return best, (
            f"⚠ Every available call results in a net loss if assigned. "
            f"The best you can do is {_fmt_signed(best['net_if_assigned'])} "
            f"(less bad than other strikes). Consider HOLDING and waiting "
            f"for the stock to recover, OR selling shares manually and "
            f"taking the loss."
        )

    return None, "No actionable trades. Hold and wait."


def _render_best_play_card(safe: list, aggressive: list,
                             pos: dict, spot: float | None):
    """Render the 'recommended' card — the one trade the engine suggests."""
    best, reason = _pick_best_recommendation(safe, aggressive)
    if best is None:
        st.warning(
            "⚠ No call options to recommend right now. Either the chain is "
            "empty or all strikes have zero bids. Hold for now."
        )
        return

    is_safe = best.get("is_safe", False)
    is_loss = best["net_if_assigned"] < 0

    # Color coding: green if safe & profitable, yellow if aggressive but profitable,
    # red if best available is still a loss
    if is_safe and not is_loss:
        accent = theme.GREEN
        icon = "🛡"
        label = "Best Play: SAFE"
    elif not is_safe and not is_loss:
        accent = theme.YELLOW
        icon = "⚡"
        label = "Best Play: AGGRESSIVE"
    else:
        accent = theme.RED
        icon = "⚠"
        label = "Best Available (not great)"

    net_assigned_str = _fmt_signed(best["net_if_assigned"])
    net_otm_str = _fmt_signed(best["net_if_expires_otm"])
    p_otm_str = f"{best['p_otm']*100:.0f}%" if best['p_otm'] is not None else "—"

    st.markdown(
        f"<div style='background:{theme.PANEL};border:2px solid {accent};"
        f"border-radius:10px;padding:16px 20px;margin:14px 0;"
        f"box-shadow:0 0 16px {accent}33'>"
        # Header
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;margin-bottom:10px'>"
        f"<span style='font-family:Sora;font-size:1.0rem;font-weight:700;"
        f"color:{accent}'>{icon} {label}</span>"
        f"<span style='font-family:JetBrains Mono;font-size:0.72rem;"
        f"color:{theme.MUTED}'>this week</span>"
        f"</div>"
        # Action
        f"<div style='font-family:Sora;font-size:1.15rem;font-weight:700;"
        f"color:{theme.TEXT};margin-bottom:8px;line-height:1.3'>"
        f"Sell the <b>${best['strike']:.2f}</b> covered call for "
        f"<b style='color:{theme.GREEN}'>${best['premium']:.2f}</b>/share"
        f"</div>"
        # Two scenarios in plain English
        f"<div style='display:flex;gap:14px;margin:14px 0 10px 0'>"
        f"<div style='flex:1;background:{theme.PANEL_HI};padding:10px 12px;"
        f"border-radius:6px'>"
        f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
        f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
        f"margin-bottom:4px'>IF STOCK RISES &amp; CALLED</div>"
        f"<div style='font-family:JetBrains Mono;font-size:1.0rem;"
        f"color:{theme.GREEN if best['net_if_assigned'] >= 0 else theme.RED};"
        f"font-weight:700'>"
        f"You net {net_assigned_str}</div>"
        f"</div>"
        f"<div style='flex:1;background:{theme.PANEL_HI};padding:10px 12px;"
        f"border-radius:6px'>"
        f"<div style='font-family:JetBrains Mono;font-size:0.62rem;"
        f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
        f"margin-bottom:4px'>IF STOCK STAYS BELOW (P≈{p_otm_str})</div>"
        f"<div style='font-family:JetBrains Mono;font-size:1.0rem;"
        f"color:{theme.GREEN};font-weight:700'>"
        f"Keep {net_otm_str} premium, repeat next week</div>"
        f"</div>"
        f"</div>"
        # Why this one
        f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
        f"color:{theme.MUTED};line-height:1.5;padding-top:8px;"
        f"border-top:1px solid {theme.BORDER}'>"
        f"<b style='color:{theme.TEXT}'>Why this strike:</b> {reason}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True)


def _render_other_options(safe: list, aggressive: list,
                           pos: dict, spot: float | None):
    """De-emphasized list of alternative strikes — collapsed by default."""
    all_candidates = safe + aggressive
    if not all_candidates:
        return
    # Sort: profitable safe first, then profitable aggressive, then losing ones
    def _sort_key(c):
        is_safe = c.get("is_safe", False)
        is_profit = c["net_if_assigned"] >= 0
        # Lower sort key = appears first
        if is_safe and is_profit: return (0, -c["annualized_roc"])
        if not is_safe and is_profit: return (1, -c["annualized_roc"])
        if is_safe and not is_profit: return (2, -c["annualized_roc"])
        return (3, c["net_if_assigned"])  # worst losses last
    all_candidates.sort(key=_sort_key)

    with st.expander(f"⚖ Other options ({len(all_candidates)} strikes available)",
                      expanded=False):
        for c in all_candidates:
            _render_alternative_row(c, pos, spot)


def _render_alternative_row(c: dict, pos: dict, spot: float | None):
    """One row in the 'other options' list."""
    is_safe = c.get("is_safe", False)
    net = c["net_if_assigned"]
    K_call = c["strike"]

    if is_safe and net >= 0:
        accent = theme.GREEN
        tag = "🛡 SAFE"
    elif not is_safe and net >= 0:
        accent = theme.YELLOW
        tag = "⚡ AGGRESSIVE (profitable)"
    elif is_safe and net < 0:
        accent = theme.YELLOW
        tag = "🛡 SAFE (low premium)"
    else:
        accent = theme.RED
        tag = "❌ AVOID (locks in loss)"

    p_otm_str = (f"{c['p_otm']*100:.0f}%" if c['p_otm'] is not None else "—")
    itm_marker = ""
    if spot is not None and K_call < spot:
        itm_marker = " (ITM — very likely assigned)"

    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;padding:10px 14px;margin:6px 0;"
        f"background:{theme.PANEL_HI};border-left:3px solid {accent};"
        f"border-radius:6px;font-family:JetBrains Mono;font-size:0.82rem'>"
        f"<div style='flex:1'>"
        f"<div style='color:{theme.TEXT};font-weight:700'>"
        f"Sell <b>${K_call:.2f}</b> call for "
        f"<span style='color:{theme.GREEN}'>${c['premium']:.2f}</span>"
        f"{itm_marker}</div>"
        f"<div style='color:{accent};font-size:0.72rem;margin-top:2px'>"
        f"{tag}</div>"
        f"</div>"
        f"<div style='text-align:right;font-size:0.78rem'>"
        f"<div style='color:{theme.MUTED}'>If called away:</div>"
        f"<div style='color:{theme.GREEN if net >= 0 else theme.RED};"
        f"font-weight:700;font-size:0.92rem'>{_fmt_signed(net)}</div>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True)


def _render_math_details(pos: dict, spot: float | None, chain: dict):
    """Collapsible math section — for users who want to see how numbers are computed."""
    K = pos["assigned_strike"]
    P_put = pos["original_put_premium"]
    shares = pos["shares"]

    with st.expander("📒 Show the math", expanded=False):
        st.markdown(f"""
**Your real cost basis** = Assigned strike − Put premium per share

`${K:.2f} − ${P_put:.2f} = ${K - P_put:.2f}/share`

That's what you effectively paid for these shares because you got compensated
upfront for taking on the assignment risk.

**For each covered call, the "if assigned" outcome is:**

`(Call strike − Assigned strike) × {shares}  +  Put premium × {shares}  +  Call premium × {shares}`

- The **(Call strike − Assigned strike) × shares** part is the share P&L if
  the call gets exercised. POSITIVE if the call strike is above where you
  were assigned (Path A — safe), NEGATIVE if the call strike is below
  (Path B — locks in a loss).
- The **Put premium × shares** is what you already collected when you sold
  the original put. That money is yours regardless of what happens next.
- The **Call premium × shares** is what you collect when you sell the new
  covered call. Also yours regardless.

**The "if expires OTM" outcome is simpler:** you keep both premiums AND keep
the shares. Then you can sell another call next week.

Probability of expiring OTM is approximated from delta — a call with delta
0.20 has ~80% chance of finishing OTM (= you keep the premium + shares).
""")


def _render_close_controls_simple(pos: dict, spot: float | None, ap_module):
    """Close form — simpler than before, plain English."""
    pos_id = pos["id"]
    K = pos["assigned_strike"]

    with st.expander("🔚 Close this position (after you've exited)",
                      expanded=False):
        st.markdown(
            f"<div style='color:{theme.MUTED};font-size:0.78rem;"
            f"margin-bottom:10px'>"
            f"Use after you've actually exited the position — either your "
            f"call got assigned, you sold the shares manually, or you "
            f"closed out some other way."
            f"</div>",
            unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            closed_via = st.selectbox(
                "How did you exit?",
                ["call_assigned", "manual_sell", "other"],
                key=f"close_via_{pos_id}",
                format_func=lambda x: {
                    "call_assigned": "Call got assigned (shares called away)",
                    "manual_sell":   "Sold shares myself",
                    "other":         "Other"
                }[x])
            closed_price = st.number_input(
                "Exit price ($/share)",
                key=f"close_price_{pos_id}",
                min_value=0.01,
                value=(spot if spot is not None else float(K)),
                step=0.50, format="%.2f")
        with col2:
            call_premium = st.number_input(
                "Total call premiums collected during holding period ($/sh)",
                key=f"close_callprem_{pos_id}",
                min_value=0.0, value=0.0, step=0.05, format="%.2f",
                help="Sum of all covered call premiums you sold while "
                     "holding these shares. 0 if you didn't sell any.")
            closed_date = st.date_input(
                "Closed date",
                key=f"close_date_{pos_id}",
                value=datetime.today().date())

        # Preview
        share_pnl = (closed_price - K) * pos["shares"]
        put_d = pos["original_put_premium"] * pos["shares"]
        call_d = call_premium * pos["shares"]
        total = share_pnl + put_d + call_d
        st.markdown(
            f"<div style='background:{theme.PANEL_HI};padding:8px 12px;"
            f"border-radius:6px;font-family:JetBrains Mono;font-size:0.85rem;"
            f"margin:8px 0'>"
            f"Total realized P&amp;L: "
            f"<b style='color:{theme.GREEN if total >= 0 else theme.RED}'>"
            f"{_fmt_signed(total)}</b>"
            f"</div>",
            unsafe_allow_html=True)

        if st.button("Confirm close", key=f"close_btn_{pos_id}"):
            ok = ap_module.close_position(
                position_id=pos_id, closed_via=closed_via,
                closed_price=closed_price,
                call_premium_collected=call_premium,
                closed_date=closed_date.isoformat())
            if ok:
                st.toast(f"Closed {pos['ticker']} ({_fmt_signed(total)})",
                          icon="✓")
                st.session_state["wheel_selected_id"] = None  # back to overview
                st.rerun()
            else:
                st.error("Failed to close.")

        st.markdown(
            f"<div style='color:{theme.MUTED};font-size:0.7rem;"
            f"margin-top:8px'>"
            f"Made a typo? Use Delete below to remove the entry without "
            f"keeping an audit record."
            f"</div>",
            unsafe_allow_html=True)
        if st.button("🗑 Delete entry (no record)",
                      key=f"delete_btn_{pos_id}"):
            ap_module.delete_position(pos_id)
            st.session_state["wheel_selected_id"] = None
            st.toast(f"Deleted entry {pos_id}", icon="🗑")
            st.rerun()


def _render_closed_history(ap_module):
    """Bottom panel — recent closed positions for audit."""
    closed = ap_module.list_closed(limit=20)
    if not closed:
        return
    with st.expander(f"📜 Closed positions history ({len(closed)})",
                      expanded=False):
        rows = []
        for c in closed:
            rows.append({
                "Ticker":       c["ticker"],
                "Assigned":     c["assigned_date"],
                "Closed":       c["closed_date"],
                "Days held":    _days_between(c["assigned_date"],
                                                c["closed_date"]),
                "K_assigned":   f"${c['assigned_strike']:.2f}",
                "Put premium":  f"${c['original_put_premium']:.2f}",
                "Exit price":   (f"${c['closed_price']:.2f}"
                                  if c.get('closed_price') else "—"),
                "Exit via":     c.get("closed_via") or "—",
                "Realized P&L": (_fmt_signed(c['realized_pnl'])
                                  if c.get('realized_pnl') is not None else "—"),
                "Notes":        c.get("notes") or "",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, width="stretch")


def _days_between(start_str: str, end_str: str) -> str:
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d").date()
        e = datetime.strptime(end_str, "%Y-%m-%d").date()
        return f"{(e - s).days}d"
    except (ValueError, TypeError):
        return "—"


def _fmt_signed(amt: float) -> str:
    """Format a dollar amount with sign before the dollar (accounting style):
    +$1,234 / -$1,234. Returns '—' for None."""
    if amt is None:
        return "—"
    if amt >= 0:
        return f"+${amt:,.0f}"
    return f"-${abs(amt):,.0f}"
