"""
page_scanner.py — Page 2: Custom Watchlist Scanner.

  - Strategy & Factor Definitions expander
  - Macro Regime banner
  - Ranked composite-score bar chart
  - Data table: Ticker, Conviction Tier, Score (raw), Tactical Allocation Action,
    52W Range Position (progress bar), Price, Trailing P/E, Fwd P/E,
    Next Earnings — with single-row selection
  - On-demand 1-year technical chart (VWAP, pivots, ROC) for the selected row

The 0-100 score uses ONLY the 6 technical/institutional factors.
P/E and the 52-week range position are decoupled — shown for context, never
scored.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

import theme


def _score_bar(rows):
    # blocked rows have composite = -1 (sentinel) — exclude from the bar chart
    # since a tiny negative bar is misleading; they're already greyed out in
    # the table below with their BLOCKED status.
    visible = [r for r in rows if r.get("total_score", 0) >= 0]
    if not visible:
        visible = rows  # fallback so the chart never goes empty
    tickers = [r["ticker"] for r in visible]
    scores = [r["total_score"] for r in visible]
    colors = [r.get("status_color", theme.MUTED) for r in visible]
    fig = go.Figure(go.Bar(
        x=tickers, y=scores, marker_color=colors, marker_line_width=0,
        text=[f"{s:.0f}" for s in scores], textposition="outside",
        textfont=dict(family="JetBrains Mono", size=13, color=theme.TEXT)))
    fig.update_layout(
        xaxis=dict(showgrid=False, color=theme.MUTED,
                   tickfont=dict(family="JetBrains Mono", size=12)),
        yaxis=dict(range=[0, 115], showgrid=True, gridcolor=theme.BORDER,
                   color=theme.MUTED),
        bargap=0.45, showlegend=False)
    return theme.plotly_layout_dark(fig, height=300)


def _render_definitions():
    """Page 2's Strategy & Factor Definitions.

    Describes each of the 6 scored factors honestly — including the fact that
    under MEAN_REVERSION the underlying math shifts (e.g. Momentum uses
    RSI + Bollinger Bands instead of EMA10/50) — and shows the actual weight
    percentage each factor carries under each strategy. Weights are pulled
    live from run_scanner.TREND_WEIGHTS / MR_WEIGHTS so the UI never drifts
    from the engine.
    """
    import run_scanner as sc

    def _w(strategy: str, factor: str) -> str:
        weights = sc.TREND_WEIGHTS if strategy == "TREND" else sc.MR_WEIGHTS
        return f"{int(weights[factor] * 100)}%"

    with st.expander("📖 Strategy & Factor Definitions"):
        st.markdown(f"""
**This page scores individual stocks.** It is a different model from the
**Macro Composite** on Page 1 — Page 1's score gauges the overall market
*regime*; Page 2's score ranks *which tickers in your watchlist* to trade
within that regime. Both models are "Institutional Flow Weighted," but they
weight different things and produce different numbers.

---

**The two strategies**

- **Trend-Following (TREND):** *"Riding the wave."* Buying stocks already
  breaking out with strong institutional momentum, expecting the trend to
  continue. Rewards leadership, fast prices, volume confirmation, and
  proximity to 52-week highs.
- **Mean Reversion (MR):** *"The rubber band effect."* Buying stocks that have
  crashed violently and are extremely oversold, expecting a snap-back rally.
  Rewards proximity to 52-week lows, dealer-priced fear (high IV), volume
  exhaustion, and elevated short interest.

The active strategy selector in the sidebar drives both *which math each
factor applies* and *the weight each factor gets* in the composite.

---

**The 6 scored factors**

Each factor returns a 0–100 score. The composite is the weighted sum, then
clamped to [0, 100] and rounded to an integer. Weights vary per strategy
(shown after each factor name).

- **Market Leader** · TREND **{_w("TREND", "Relative Strength")}** · MR **{_w("MR", "Relative Strength")}**
  20-day stock return minus 20-day SPY return, in percentage points.
  *Under TREND:* outperformance scores high — winners keep winning. *Under MR:*
  the math inverts — deep *under*performance scores high, since extreme laggards
  are the snap-back candidates.

- **Price Speed** · TREND **{_w("TREND", "Momentum")}** · MR **{_w("MR", "Momentum")}**
  *Under TREND:* compares the 10-day EMA to the 50-day EMA. A positive gap
  (10 > 50) signals an active uptrend; the wider the gap, the higher the score.
  *Under MR:* the engine switches to RSI + 20-day SMA + Bollinger Bands. A
  shallow dip (RSI ≤ 40, price at 20-SMA) in a BULL regime scores high as a
  buyable pullback; a bounce-to-resistance in a BEAR regime scores low as a
  shortable setup.

- **Big Money Volume** · TREND **{_w("TREND", "Volume Surge")}** · MR **{_w("MR", "Volume Surge")}**
  5-day average volume divided by 20-day average volume. The same number is
  read differently per strategy: under TREND a surge is *confirmation* that
  institutions are stepping in; under MR a surge on a sold-off name signals
  *seller exhaustion / capitulation*.

- **Chart Position** · TREND **{_w("TREND", "Range Proximity")}** · MR **{_w("MR", "Range Proximity")}**
  *Under TREND:* how close to the 52-week HIGH (closer = stronger setup).
  *Under MR:* how close to the 52-week LOW (closer = better mean-reversion
  candidate). This is the highest-weighted factor under MR by design —
  capitulation positioning is the defining feature of a MR setup.

- **Squeeze Fuel** · TREND **{_w("TREND", "Short Interest")}** · MR **{_w("MR", "Short Interest")}**
  Month-over-month change in short interest.
  *Under TREND:* declining shorts → high score (bears giving up confirms the
  uptrend). *Under MR:* elevated/rising shorts → high score (squeeze fuel —
  bears piling in just before the reversal pop). Note: yfinance updates SI
  twice a month, so this factor has a built-in 1–14 day lag.

- **Options Flow** · TREND **{_w("TREND", "Options Flow")}** · MR **{_w("MR", "Options Flow")}**
  IV percentile (252-day) + the stock's own put/call open-interest ratio.
  *Under TREND:* low IV + call-heavy positioning = calm, favorable bull tape →
  high score. *Under MR:* high IV + put-heavy positioning = peak fear →
  high score (contrarian fade setup).

---

**Context-only (NOT scored)**

These columns appear in the scanner table but are NOT inputs to the score.
They're shown so you can size positions and stop-loss consciously around
real-world risk:

- **52W Low / 52W High** — the stock's annual price range
- **Volume Pace** — Big Money Volume rendered as plain English: "Heavy
  (Institutional)" / "Normal" / "Quiet (Retail)". Same RVOL number Big Money
  Volume uses, just categorical
- **Trailing / Forward P/E** — valuation context from company fundamentals
- **⚠️ Earnings Warning Icon** — ⚠️ next to the ticker means earnings within
  ~5 trading days. Visual heads-up only; the score and tranche action are
  unaffected. Size positions consciously around the event

---

**Macro Regimes & Capital Deployment**

The Page 1 Composite Macro Score (0–100) sets the regime. The regime then
gates the engine: BEAR blocks longs entirely, BULL blocks shorts, SIDEWAYS
caps TREND scores at 70 unless saved by the A+ Relative Strength override.

- **🟢 BULL REGIME (Macro Score 70–100):** Low-risk bull environment.
  - *Longs:* full-sized positions allowed (Tranche 3 = MAX LONG)
  - *Shorts:* completely blocked at the factor level. Score-driven short
    setups get the `❌ SHORT BLOCKED: BULL REGIME` label

- **🟡 SIDEWAYS REGIME (Macro Score 40–69):** Mixed, chop-prone environment.
  - *Longs:* TREND momentum factors capped at 70. Only A+ Relative Strength
    stocks can break above 80 → MID LONG tranche
  - *Shorts:* standard sizing for genuine breakdowns (LEAN SHORT)

- **🔴 BEAR REGIME (Macro Score 0–39):** High-risk environment.
  - *Longs:* completely blocked. Score-driven long setups get the
    `❌ LONG BLOCKED: BEAR REGIME` label and a `❌ RISK OFF: COLD CASH` action
  - *Shorts:* full-sized positions allowed (Tranche 3 = MAX SHORT)
""")


def _render_formulas():
    """📚 Factor Definitions & Mathematical Formulas.

    Renders the canonical (textbook) formula for each of the 6 scored factors
    alongside the actual formula the dashboard's engine uses. Some are the
    same; others differ for documented engineering reasons (e.g. EMA instead
    of SMA for momentum smoothness; difference-of-returns instead of ratio
    for RS to avoid division-by-zero near SPY-flat days).

    This is the single source of truth for "what is the engine doing with
    my data." It sits alongside the higher-level Strategy & Factor
    Definitions expander above.
    """
    with st.expander("📚 Factor Definitions & Mathematical Formulas"):
        st.markdown(
            "<div class='tiny' style='margin-bottom:14px'>"
            "Each factor below shows the <b>classical textbook formula</b> "
            "for the metric and the <b>engine's actual formula</b> when they "
            "differ. Differences are intentional and documented — the "
            "engine prefers robust implementations (e.g. EMA over SMA for "
            "smoother momentum, return-difference over ratio for RS to avoid "
            "numerical instability when SPY return is near zero)."
            "</div>", unsafe_allow_html=True)

        # ── 1. Momentum ──
        st.markdown("#### 1. Momentum")
        st.markdown(
            "Checks short-term price action against the longer-term trend "
            "to see if price is accelerating.")
        st.markdown("**Classical formula:**")
        st.latex(r"\text{Momentum} = \frac{SMA_{10} - SMA_{50}}{SMA_{50}} \times 100")
        st.markdown("**Engine's actual formula (TREND mode):**")
        st.latex(r"\text{Momentum}_{TREND} = \frac{EMA_{10} - EMA_{50}}{P_{\text{current}}} \times 100")
        st.markdown(
            "<div class='tiny'>EMA over SMA for faster reaction to fresh "
            "trend changes; normalized by current price (not the slower "
            "average) for tighter scaling. Under <b>MEAN_REVERSION mode</b> "
            "the formula switches entirely: a buyable-dip score from RSI + "
            "20-day Bollinger Band z-score (different math, different "
            "intent — see the Strategy & Factor Definitions expander above)."
            "</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── 2. Volume Surge ──
        st.markdown("#### 2. Volume Surge")
        st.markdown(
            "Compares the recent 5-day average volume to the 20-day average. "
            "A surge above 1.0 indicates institutional accumulation; below "
            "1.0 indicates distribution or apathy.")
        st.markdown("**Formula (engine matches textbook exactly):**")
        st.latex(r"\text{Surge Ratio} = \frac{\overline{V}_{5\text{-day}}}{\overline{V}_{20\text{-day}}}")
        st.markdown(
            "<div class='tiny'>Mapped to a 0–100 score linearly: 0.7× → 0, "
            "2.0× → 100. Heavy ratios (≥1.5×) indicate the kind of volume "
            "spikes that confirm trends or signal seller exhaustion (the "
            "interpretation flips per active strategy)."
            "</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── 3. Relative Strength ──
        st.markdown("#### 3. Relative Strength (vs. SPY)")
        st.markdown(
            "Measures how the stock is performing compared to the broader "
            "S&P 500 over a 20-day window.")
        st.markdown("**Classical formula (ratio):**")
        st.latex(r"RS_{\text{classic}} = \frac{\%\Delta P_{\text{stock}}}{\%\Delta P_{\text{SPY}}}")
        st.markdown("**Engine's actual formula (difference, in percentage points):**")
        st.latex(r"RS_{\text{engine}} = \%\Delta P_{\text{stock}}^{(20d)} - \%\Delta P_{\text{SPY}}^{(20d)}")
        st.markdown(
            "<div class='tiny'>The classical ratio is undefined when SPY's "
            "return crosses zero (division-by-zero / sign-flip instability). "
            "The engine uses the difference (in pp), which is well-defined "
            "across all market conditions and produces the same ranking on "
            "non-degenerate days. Under <b>MEAN_REVERSION</b> the score "
            "inverts: deep underperformance scores HIGH (snap-back setup)."
            "</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── 4. Range Proximity ──
        st.markdown("#### 4. Range Proximity")
        st.markdown(
            "Where the stock sits within its 52-week price range. "
            "Under TREND, proximity to the 52-week HIGH scores high (breakout "
            "setup). Under MR, proximity to the 52-week LOW scores high "
            "(capitulation setup).")
        st.markdown("**Classical formula (position-in-range, 0–100):**")
        st.latex(r"\text{Position} = \left( \frac{P_{\text{current}} - P_{\text{low52}}}{P_{\text{high52}} - P_{\text{low52}}} \right) \times 100")
        st.markdown("**Engine's actual formulas:**")
        st.latex(r"\text{Engine}_{TREND} = \frac{P_{\text{current}}}{P_{\text{high52}}} \quad\quad\quad \text{Engine}_{MR} = \frac{P_{\text{current}}}{P_{\text{low52}}}")
        st.markdown(
            "<div class='tiny'>The engine uses two separate single-anchor "
            "ratios (one per strategy) rather than the classical position-in-range. "
            "This gives a cleaner score under each strategy: a stock at "
            "92% of its 52-week high is unambiguously a TREND setup; "
            "the same stock at 103% of its 52-week low is the same datum "
            "but irrelevant under MR. Each ratio is then mapped linearly to "
            "0–100. The 52W Low / 52W High columns in the scanner table show "
            "the underlying anchors."
            "</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── 5. Short Interest ──
        st.markdown("#### 5. Short Interest")
        st.markdown(
            "The amount of bets against the stock. Declining shorts confirm "
            "an uptrend (TREND); elevated/rising shorts provide squeeze fuel "
            "for a violent reversal (MR).")
        st.markdown("**Classical formula (short-interest ratio, level):**")
        st.latex(r"SI\% = \left( \frac{\text{Shares Short}}{\text{Shares Floating}} \right) \times 100")
        st.markdown("**Engine's actual formula (month-over-month change in level):**")
        st.latex(r"\Delta SI = \frac{SI_{\text{current month}} - SI_{\text{prior month}}}{SI_{\text{prior month}}} \times 100")
        st.markdown(
            "<div class='tiny'>The engine measures the <b>change</b> in SI "
            "rather than the level, because the level varies enormously by "
            "ticker (some names always carry 20%+ SI; some never break 1%) "
            "while the change is a clean cross-sectional signal. "
            "<b>Data caveat:</b> FINRA publishes SI ~twice a month, so this "
            "factor has a built-in 1–14 day lag — yfinance can't be more "
            "current than its source."
            "</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── 6. Options Flow ──
        st.markdown("#### 6. Options Flow & Volatility")
        st.markdown(
            "Evaluates dealer positioning and risk pricing via implied "
            "volatility and put/call positioning.")
        st.markdown("**Classical formulas (PCR + IV Rank):**")
        st.latex(r"PCR = \frac{\text{Put Volume}}{\text{Call Volume}} \quad\quad IVR = \frac{IV_{\text{current}} - IV_{\text{min}}}{IV_{\text{max}} - IV_{\text{min}}}")
        st.markdown("**Engine's actual formulas:**")
        st.latex(r"PCR_{\text{engine}} = \frac{\text{Put OI (all strikes)}}{\text{Call OI (all strikes)}}")
        st.latex(r"IV\text{-pct} = P_{252}\big( IV_{\text{current}}, \{IV_{t-252} \ldots IV_t\} \big)")
        st.markdown(
            "<div class='tiny'>The engine uses <b>open interest</b> (cumulative "
            "positioning) instead of <b>volume</b> (single-day flow) — OI is "
            "the better signal for dealer/whale positioning, volume is noisier. "
            "IV percentile (rather than IV rank) is computed over a rolling "
            "252-day window: the % of trailing days where IV was LOWER than "
            "today. PCR is computed from the stock's own option chain (not "
            "the CBOE published put/call ratio, which is a different metric "
            "that aggregates over all SPY options). The two scores are "
            "combined into the Options Flow factor, with weighting that "
            "differs per strategy."
            "</div>", unsafe_allow_html=True)


def render():
    st.markdown("<div class='kicker'>PAGE 2 · CUSTOM WATCHLIST SCANNER</div>",
                unsafe_allow_html=True)
    st.markdown("# Directional Trade Scanner")

    # active-horizon badge (same component as Page 1)
    active_horizon = st.session_state.get("horizon", "Swing Trade System")
    st.markdown(theme.horizon_pill_html(active_horizon),
                unsafe_allow_html=True)

    strategy = st.session_state.get("strategy", "Trend-Following")
    # Friendlier display label for MEAN_REVERSION — adds the plain-English
    # tagline ("Dip Buyer / Peak Shorter") to make clear what the engine
    # actually does. Internal strategy value is unchanged.
    strategy_display = (
        "Mean Reversion · Dip Buyer / Peak Shorter"
        if strategy == "Mean Reversion" else strategy)
    st.markdown(
        f"<div class='tiny' style='margin-bottom:6px'>Active engine: "
        f"<b style='color:{theme.ACCENT}'>{strategy_display}</b></div>",
        unsafe_allow_html=True)

    # ── definitions expander (directly under the strategy line) ──
    _render_definitions()
    _render_formulas()

    macro = st.session_state.get("macro_result")
    scanner = st.session_state.get("scanner_result")

    if macro is None:
        st.info("Run the analysis on Page 1 — MarketSense must clear first.")
        return

    # ── Macro Regime banner ──
    regime, regime_color = macro["regime"], macro["color"]
    st.markdown(
        f"""
        <div class='panel' style='text-align:center;border-color:{regime_color}55;
             background:linear-gradient(160deg,{theme.PANEL},#0d1320)'>
          <div class='kicker'>Current Macro Regime</div>
          <div class='regime-pill' style='background:{regime_color}22;
               color:{regime_color};font-size:1.3rem;margin-top:8px;
               padding:10px 30px'>{regime}</div>
        </div>
        """, unsafe_allow_html=True)

    if not macro.get("scanner_enabled", True):
        st.markdown(
            f"""
            <div class='panel' style='text-align:center;padding:44px;
                 border-color:{theme.RED}55'>
              <div style='font-size:2.4rem'>🛡️</div>
              <div class='regime-pill' style='background:{theme.RED}22;
                   color:{theme.RED};margin-top:10px'>SCANNER DISABLED</div>
              <div class='tiny' style='margin-top:12px'>
                Macro score <b>{macro['composite_score']:.0f}</b> — BEAR REGIME.
                The scanner is gated off until conditions improve.</div>
            </div>
            """, unsafe_allow_html=True)
        return

    # ── trigger scan on demand ──
    # Re-run prompt fires if: (a) no scan yet, (b) strategy toggle changed, OR
    # (c) the macro score has moved since this scan (regime blocking depends on
    # the macro score — stale results would show wrong BLOCKED rows).
    macro_score_now = macro.get("composite_score")
    scanner_macro = scanner.get("macro_score") if scanner else None
    macro_changed = (scanner is not None and scanner_macro != macro_score_now)
    needs_rerun = (scanner is None
                   or scanner.get("strategy") != strategy
                   or macro_changed)

    # ── Always-on re-scan controls (independent of Page 1) ──
    # The scanner depends on the cached MarketSense result (for regime-blocking)
    # but can be re-run independently when the cached macro is fresh. Show:
    #  - a "Re-scan now" button to refresh scanner data without touching Page 1
    #  - the age of the cached MarketSense result for context
    #  - a yellow nudge if MarketSense is older than 60 minutes
    if scanner is not None:
        # Compute MarketSense age
        macro_ts_str = macro.get("timestamp")
        macro_age_str = ""
        macro_stale = False
        try:
            if macro_ts_str:
                macro_ts = datetime.fromisoformat(macro_ts_str)
                age_minutes = (datetime.now() - macro_ts).total_seconds() / 60
                if age_minutes < 1:
                    macro_age_str = "just now"
                elif age_minutes < 60:
                    macro_age_str = f"{int(age_minutes)} min ago"
                elif age_minutes < 1440:
                    macro_age_str = f"{int(age_minutes/60)} hr ago"
                else:
                    macro_age_str = f"{int(age_minutes/1440)} day(s) ago"
                macro_stale = age_minutes > 60
        except Exception:
            pass

        col_rescan, col_freshness = st.columns([1, 2])
        with col_rescan:
            if st.button("🔄 Re-scan now", width="stretch",
                          help="Re-run the scanner with the cached "
                               "MarketSense result. Use this when prices "
                               "have moved but the macro regime hasn't "
                               "changed materially."):
                import run_scanner
                import signal_journal as sj
                with st.spinner(f"Re-scanning watchlist · {strategy}…"):
                    st.session_state.scanner_result = run_scanner.run(
                        st.session_state.watchlist, strategy,
                        macro_score=macro_score_now)
                    scan_id = sj.log_scan(st.session_state.scanner_result)
                    st.session_state.current_scan_id = scan_id
                st.rerun()
        with col_freshness:
            # MarketSense freshness banner
            if macro_age_str:
                if macro_stale:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.75rem;color:{theme.YELLOW};"
                        f"padding:6px 10px;background:{theme.YELLOW}11;"
                        f"border-left:3px solid {theme.YELLOW};"
                        f"border-radius:4px;margin-top:2px'>"
                        f"⏰ MarketSense data is <b>{macro_age_str}</b> — "
                        f"consider re-running Page 1 to refresh the "
                        f"regime context before relying on these scans."
                        f"</div>",
                        unsafe_allow_html=True)
                else:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.75rem;color:{theme.MUTED};"
                        f"padding:8px 4px'>"
                        f"MarketSense last updated <b>{macro_age_str}</b>. "
                        f"Re-scan refreshes scanner data only — re-run "
                        f"Page 1 to refresh regime context."
                        f"</div>",
                        unsafe_allow_html=True)

    if needs_rerun:
        if macro_changed:
            st.warning(
                f"⚠️ Scanner results are stale — macro score changed from "
                f"**{scanner_macro}** to **{macro_score_now:.0f}** since the last "
                f"scan. Re-run to refresh the regime-blocking logic.")
        if st.button("▶  Run Watchlist Scan", width="stretch"):
            import run_scanner
            import signal_journal as sj
            with st.spinner(f"Scanning watchlist · {strategy}…"):
                st.session_state.scanner_result = run_scanner.run(
                    st.session_state.watchlist, strategy,
                    macro_score=macro_score_now)
                # Persist to the journal — every scan logged automatically.
                # The scan_id is stashed in session_state so overrides on
                # this page can be linked back to the signal row.
                scan_id = sj.log_scan(st.session_state.scanner_result)
                st.session_state.current_scan_id = scan_id
            scanner = st.session_state.scanner_result
        else:
            if scanner is None:
                st.info("Click 'Run Watchlist Scan' to score your watchlist "
                        "under the active strategy.")
                return

    if not scanner or not scanner.get("rows"):
        st.warning("No scanner results.")
        return

    rows = scanner["rows"]
    ts = datetime.fromisoformat(scanner["timestamp"]).strftime("%Y-%m-%d %H:%M")

    st.markdown("---")
    st.markdown("<div class='kicker'>Ranked Composite Scores</div>",
                unsafe_allow_html=True)
    st.plotly_chart(_score_bar(rows), width="stretch",
                    config={"displayModeBar": False})

    st.markdown("---")
    st.markdown("<div class='kicker'>Multi-Factor Stock Score · "
                "Institutional Flow Weighted (Per-Strategy)</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='tiny' style='margin-bottom:8px'>"
        "The highest-scoring stock is charted by default — click any row to "
        "switch the chart below.</div>", unsafe_allow_html=True)

    import run_scanner
    macro_score = macro.get("composite_score")

    # ── Volume Pace: map the raw RVOL float -> plain-English text ──
    # Derived fresh here from the raw `rvol` float so the column can never
    # show a stale or mis-formatted value (e.g. an old "1.4x" multiplier).
    def _volume_pace_label(rvol):
        if rvol is None:
            return "Unknown"
        try:
            v = float(rvol)
        except (TypeError, ValueError):
            return "Unknown"
        if pd.isna(v):
            return "Unknown"
        if v >= 1.2:
            return "Heavy (Institutional)"
        if v >= 0.8:
            return "Normal"
        return "Quiet (Retail)"

    table_rows = []
    for r in rows:
        tp = r.get("trailing_pe")
        fp = r.get("forward_pe")
        tranche = run_scanner.calculate_tranche_action(
            macro_score, r["total_score"])
        # blocked rows carry composite=-1 (sentinel) — show blank in the Score
        # cell since the BLOCKED status label already conveys the meaning
        raw_score = r["total_score"]
        display_score = None if raw_score is not None and raw_score < 0 \
                         else raw_score
        # Visual-only earnings warning: ⚠️ next to the ticker if earnings are
        # within ~5 trading days. Does NOT affect the tranche action or score —
        # purely a heads-up so the user notices before sizing a position.
        ticker_label = r["ticker"]
        if r.get("earnings_flag"):
            days_away = r.get("earnings_days_away")
            ticker_label = f"⚠️ {r['ticker']}"  # icon prepended for visibility
        # Liquidity warning chip (Tier A audit fix). 💧 prepends names with
        # avg daily dollar volume below the $20M floor. The signal still
        # fires — user added the ticker deliberately — but the chip warns
        # that the trade may not be executable at retail scale without
        # meaningful slippage. The strategy backtest hard-filters these;
        # the live scanner shows them with a warning instead.
        if not r.get("is_liquid", True):
            ticker_label = f"💧 {ticker_label}"
        table_rows.append({
            "Ticker": ticker_label,
            # Conviction Tier is the PRIMARY decision-level signal. The exact
            # integer score is kept for transparency but visually secondary —
            # the tier captures the actual granularity the model can support.
            "Conviction Tier": r["status_label"],
            "Score (raw)": display_score,
            "Tactical Allocation Action": tranche["action"],
            "Price": float(r["price"]) if r.get("price") is not None else 0.0,
            "52W Low": float(r["range_low"]) if r.get("range_low") else 0.0,
            "52W High": float(r["range_high"]) if r.get("range_high") else 0.0,
            # store the raw RVOL; the text column is mapped from it below
            "_rvol": r.get("rvol"),
            "Trailing P/E": float(tp) if tp is not None else None,
            "Fwd P/E": float(fp) if fp is not None else None,
            # Fundamental Grade (A/B/C/D/N/A) — value+quality screen derived
            # from P/E, ROE, and profit margin. Independent from the
            # technical Conviction Tier (which scores price/volume setup).
            "Grade": r.get("fundamental_grade", "N/A"),
            # Analyst consensus (Finnhub) — show "Buy" / "Hold" / etc.
            # Empty string falls back to "—" so the column doesn't render None
            "Analyst": r.get("analyst_consensus") or "—",
            "Next Earnings": r.get("next_earnings") or "—",
        })
    df = pd.DataFrame(table_rows)

    # explicit RVOL -> plain-English mapping via .apply(); drop the raw helper
    df["Volume Pace"] = df["_rvol"].apply(_volume_pace_label)
    df = df.drop(columns=["_rvol"])

    # ── interactive dataframe: row selection + currency formatting ──
    col_config = {
        "Ticker": st.column_config.TextColumn(
            "Ticker", width="small",
            help="⚠️ = earnings within ~5 trading days (heads-up only). "
                 "💧 = average daily dollar volume below $20M floor — "
                 "signal still fires but the trade may not be executable "
                 "at retail scale without meaningful slippage."),
        "Conviction Tier": st.column_config.TextColumn(
            "Conviction Tier", width="medium",
            help="The model's actual decision-level signal. Distinguishing "
                 "an 81 from a 79 raw score is false precision — factor noise "
                 "is much larger than that. The 5 tiers are the granularity "
                 "the engine can defensibly support: HIGH CONVICTION (80+), "
                 "TRADABLE (65-79), NEUTRAL (50-64), CAUTION (35-49), "
                 "AVOID / SHORT (0-34)."),
        "Score (raw)": st.column_config.NumberColumn(
            "Score (raw)", format="%d", width="small",
            help="Exact integer composite for transparency — but the "
                 "Conviction Tier (left) is the decision-level signal. "
                 "Read the tier first."),
        "Tactical Allocation Action": st.column_config.TextColumn(
            "Tactical Allocation Action", width="medium"),
        "Price": st.column_config.NumberColumn("Price", format="$%.2f",
                                               width="small"),
        "52W Low": st.column_config.NumberColumn("52W Low", format="$%.2f"),
        "52W High": st.column_config.NumberColumn("52W High", format="$%.2f"),
        "Volume Pace": st.column_config.TextColumn(
            "Volume Pace", width="medium",
            help="Relative Volume — today's volume vs the 20-day average. "
                 "Heavy (Institutional) ≥ 1.2x · Normal 0.8–1.2x · "
                 "Quiet (Retail) < 0.8x"),
        "Trailing P/E": st.column_config.NumberColumn("Trailing P/E",
                                                      format="%.1f"),
        "Fwd P/E": st.column_config.NumberColumn("Fwd P/E", format="%.1f"),
        "Grade": st.column_config.TextColumn(
            "Grade", width="small",
            help="Fundamental Grade — letter (A/B/C/D) from a 6-Pillar "
                 "Institutional Model. Pillars: "
                 "Valuation 20% (EV/EBITDA + Fwd P/E) · "
                 "Growth 20% (revenue + earnings YoY) · "
                 "Profitability 20% (gross + operating margins) · "
                 "Cash Flow 20% (FCF / market cap) · "
                 "Balance Sheet 10% (current ratio) · "
                 "Efficiency 10% (ROE). "
                 "Each pillar scored 0-100 on absolute thresholds; missing "
                 "data defaults to neutral 50. INDEPENDENT from Conviction "
                 "Tier — Conviction scores the technical setup, Grade scores "
                 "the underlying business. A great company can have a bad "
                 "setup and vice versa. 'N/A' = neither Finnhub nor yfinance "
                 "returned any usable data for this ticker."),
        "Analyst": st.column_config.TextColumn(
            "Analyst", width="small",
            help="Wall Street analyst consensus from Finnhub (Strong Buy / "
                 "Buy / Hold / Sell / Strong Sell). Weighted by # of "
                 "analysts at each rating, computed from the most recent "
                 "month's snapshot. — = no coverage."),
        "Next Earnings": st.column_config.TextColumn("Next Earnings"),
    }

    event = st.dataframe(
        df, width="stretch", hide_index=True,
        column_config=col_config,
        # Per spec — "Grade" sits at Column 2, immediately right of Ticker,
        # because business-quality context belongs alongside the name itself.
        # Conviction Tier (the technical decision signal) follows at col 3.
        # Analyst consensus sits after Tier as a separate institutional check.
        column_order=["Ticker", "Grade", "Conviction Tier", "Analyst",
                      "Score (raw)", "Tactical Allocation Action", "Price",
                      "52W Low", "52W High", "Volume Pace", "Trailing P/E",
                      "Fwd P/E", "Next Earnings"],
        on_select="rerun", selection_mode="single-row",
    )

    # capture the selected row — DEFAULT to [0] (highest-scoring stock) so the
    # chart renders instantly on page load before any user click.
    sel_rows = []
    try:
        sel_rows = list(event.selection.rows)
    except Exception:
        sel_rows = []
    if not sel_rows and len(df) > 0:
        sel_rows = [0]
    st.session_state.selected_rows = sel_rows

    # ── CSV export ──
    # Build a clean export DataFrame: strip the ⚠️ earnings prefix back out,
    # add a dedicated "Earnings Soon" boolean column, and stamp the filename
    # with the timestamp so users can keep a running log.
    export_df = df.copy()
    export_df["Earnings Soon"] = export_df["Ticker"].str.startswith("⚠️")
    export_df["Illiquid"] = export_df["Ticker"].str.contains("💧")
    export_df["Ticker"] = (export_df["Ticker"]
                            .str.replace("⚠️ ", "", regex=False)
                            .str.replace("💧 ", "", regex=False))
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    ts_compact = ts.replace(":", "").replace(" ", "_")[:15] if ts else "scan"
    st.download_button(
        label="📥 Download scanner results (CSV)",
        data=csv_bytes,
        file_name=f"scanner_{strategy.replace(' ','_')}_{ts_compact}.csv",
        mime="text/csv",
        width="content",
    )

    # conviction-tier legend
    legend = [("🟢 HIGH CONVICTION", "80-100", theme.GREEN),
              ("🟢 TRADABLE",         "65-79", "#7fd98a"),
              ("🟡 NEUTRAL",          "50-64", theme.YELLOW),
              ("🟠 CAUTION",          "35-49", theme.ORANGE),
              ("🔴 AVOID / SHORT",    "0-34",  theme.RED)]
    chips = "".join(
        f"<span style='display:inline-block;margin:3px 6px 3px 0;"
        f"padding:5px 14px;border-radius:6px;background:{c}22;color:{c};"
        f"font-family:JetBrains Mono;font-size:0.85rem'>{lbl} · {rng}</span>"
        for lbl, rng, c in legend)
    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)

    # build the weight footnote dynamically so it reflects the active strategy.
    # The string format is locked to spec: per-strategy factor list with weight
    # percentages, then a fixed tail clarifying which columns are context-only.
    import run_scanner
    active_labels = run_scanner.factor_display_labels(strategy)
    label_order = (
        ["Relative Strength", "Momentum", "Volume Surge", "Range Proximity",
         "Options Flow", "Short Interest"] if strategy == "Trend-Following"
        else ["Range Proximity", "Options Flow", "Volume Surge",
              "Short Interest", "Momentum", "Relative Strength"])
    weight_line = " · ".join(active_labels[k] for k in label_order)
    st.markdown(
        f"<div class='tiny' style='margin-top:8px'>Score = <b>Institutional "
        f"Flow weighted</b> sum of 6 factors under the <b>{strategy}</b> "
        f"engine: {weight_line}. P/E, Volume Pace, and 52W Range are "
        f"context-only and NOT part of the score. Last run: {ts}</div>",
        unsafe_allow_html=True)

    # ── manual override (audit trail) ──
    # The system makes recommendations; the trader is accountable. Every
    # override is logged with a free-text reason so Page 5 can later compare
    # system-edge vs override-edge and tell you whether discretion adds alpha.
    _render_override_panel(rows, scanner)

    # ── paper-trade execution form ──
    # Lets the user execute simulated 100-share trades directly from the
    # current scan, persisted to db_manager. Open positions and closed P&L
    # are reviewed on Page 6 (Positions).
    _render_paper_execution(rows, scanner)

    # ── on-demand 1-year technical chart for the selected row ──
    if st.session_state.get("selected_rows"):
        sel_idx = st.session_state.selected_rows[0]
        if 0 <= sel_idx < len(df):
            # strip the optional "⚠️ " earnings and "💧 " illiquid prefixes
            sel_ticker = (df.iloc[sel_idx]["Ticker"]
                          .replace("⚠️ ", "")
                          .replace("💧 ", "")
                          .strip())

            # ── Fundamental Grade card for the focused ticker ──
            # Per-pillar mini-table with a progress bar + qualitative label
            # for each of the 6 pillars. Lets the user scan top-to-bottom
            # and immediately see which pillars are driving the grade up or
            # down — vs. the prior cramped one-line "Val 25 Gro 95 ..." line.
            sel_row = next((r for r in rows if r["ticker"] == sel_ticker),
                            None)
            if sel_row:
                _render_grade_card(sel_row, sel_ticker)
                # Trade Plan — only for tickers in TRADABLE or BUY status,
                # since plans for NEUTRAL/CAUTION/AVOID tickers would just
                # encourage taking bad trades. Plan shows stop/target/sizing
                # so the user knows the risk framework BEFORE placing the
                # trade, not after. Pure decision-support — does not affect
                # the backtest's mechanical 20-day exit.
                _render_trade_plan(sel_row, sel_ticker)
                # Price Projection & Target Generator — three alternative
                # forward-target methodologies (Expected Move / Pivots /
                # Fibonacci) shown below the ATR trade plan. Separate from
                # the ATR logic; these are different lenses on "where might
                # price go." Collapsed by default to keep the view clean.
                _render_price_projection(sel_row, sel_ticker)
                # Analyst Consensus + Earnings Surprises side-by-side —
                # both are analyst-driven institutional checks. Surprises
                # is the "consistently beats/misses" quality dimension.
                ac1, ac2 = st.columns(2)
                with ac1:
                    _render_analyst_panel(sel_row, sel_ticker)
                with ac2:
                    _render_earnings_surprises(sel_ticker)
                # Insider Transactions: full-width because the transaction
                # list itself can be 5-10 rows. Different signal class
                # (corporate-insider behavior, not market consensus).
                _render_insider_panel(sel_ticker)
                # News feed sits last among context panels, before the
                # technical chart. Helps explain WHY scores moved — the
                # most fragile piece of context but often the most useful.
                _render_news_panel(sel_ticker)

            _render_technical_chart(sel_ticker)


# Qualitative score-band labels per pillar. Each pillar has its own range
# semantics — "Strong" for Valuation means cheap, but for Growth it means
# fast-growing. Centralizing here keeps the labels in sync with the scoring
# thresholds defined in run_scanner._score_*_pillar() helpers.
#
# Bands use the same breakpoints as _pillar_color() (35/50/70) so the label
# color and the qualitative word always agree visually.
_PILLAR_BANDS = {
    # (label, min_score_for_this_label)
    "valuation":     [("Very Expensive", 0),  ("Expensive",   35),
                      ("Fair",          50),  ("Cheap",       70)],
    "growth":        [("Contracting",   0),  ("Stagnant",    35),
                      ("Healthy",       50),  ("Strong",      70)],
    "profitability": [("Thin",          0),  ("Weak",        35),
                      ("Solid",         50),  ("Wide-Moat",   70)],
    "cash_flow":     [("Burning",       0),  ("Low",         35),
                      ("Adequate",      50),  ("Strong",      70)],
    "balance_sheet": [("Stressed",      0),  ("Stretched",   35),
                      ("Adequate",      50),  ("Healthy",     70)],
    "efficiency":    [("Weak",          0),  ("Marginal",    35),
                      ("Good",          50),  ("Excellent",   70)],
}

_PILLAR_DISPLAY = [
    ("valuation",     "Valuation",      "20%"),
    ("growth",        "Growth",         "20%"),
    ("profitability", "Profitability",  "20%"),
    ("cash_flow",     "Cash Flow",      "20%"),
    ("balance_sheet", "Balance Sheet",  "10%"),
    ("efficiency",    "Efficiency",     "10%"),
]


def _pillar_band_label(pillar_key: str, score: float) -> str:
    """Map a 0-100 pillar score to its qualitative label.
    e.g. valuation=25 -> "Expensive", growth=95 -> "Strong"."""
    bands = _PILLAR_BANDS.get(pillar_key, [])
    label = "—"
    for name, lo in bands:
        if score >= lo:
            label = name
    return label


def _pillar_color(score: float) -> str:
    """Color for the progress bar based on score band. Green/yellow/red
    matches the conviction-tier color taxonomy used elsewhere."""
    if score >= 70:
        return "#22e08a"   # green — strong
    if score >= 50:
        return "#f5c344"   # yellow — moderate
    if score >= 35:
        return "#ff9442"   # orange — weak
    return "#ff5d6c"       # red — very weak


def _render_earnings_surprises(sel_ticker: str):
    """Compact 4-quarter beat/miss strip. Sits next to the Analyst panel
    as the "operational quality" companion to the "market consensus" view.

    Lazy-fetches from Finnhub on each render (24h cached at the client),
    so this adds ~1 API call per selected-ticker view.
    """
    try:
        import finnhub_client as fh
        surprises = fh.get_earnings_surprises(sel_ticker)
    except Exception:
        surprises = None

    if not surprises:
        # Render nothing if Finnhub has no coverage. Showing an empty panel
        # would suggest "0 quarters of data" when the truth is "we couldn't
        # reach the data source for this ticker".
        return

    streak = surprises.get("streak", "—")
    color = surprises.get("streak_color", "#7d8aa5")
    n_beats = surprises.get("n_beats", 0)
    n_misses = surprises.get("n_misses", 0)
    n_total = surprises.get("n_total", 0)
    avg_surprise = surprises.get("avg_surprise_pct", 0)
    quarters = surprises.get("quarters", [])

    # Build 4 quarter chips — one per quarter, green if beat, red if missed
    chips = []
    for q in quarters[:4]:
        beat = q.get("beat")
        chip_color = "#22e08a" if beat else "#ff5d6c"
        chip_label = "BEAT" if beat else "MISS"
        surprise_pct = q.get("surprise_pct", 0)
        period = q.get("period", "—")
        # Shorten period from "2025-09-30" -> "Q3'25"
        try:
            ym = period[:7]  # YYYY-MM
            yr, m = ym.split("-")
            month_to_q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}
            q_label = f"{month_to_q.get(m, m)}'{yr[2:]}"
        except (ValueError, AttributeError):
            q_label = period[:7] if period else "—"
        chips.append(
            f"<div style=\"flex:1;background:{chip_color}22;"
            f"border:1px solid {chip_color}66;border-radius:6px;"
            f"padding:6px 4px;text-align:center;"
            f"font-family:JetBrains Mono;font-size:0.7rem\">"
            f"<div style=\"color:{chip_color};font-weight:700\">"
            f"{chip_label}</div>"
            f"<div style=\"color:#7d8aa5;font-size:0.62rem;"
            f"margin-top:2px\">{q_label}</div>"
            f"<div style=\"color:#a3a8b8;font-size:0.66rem;"
            f"margin-top:2px\">{surprise_pct:+.1f}%</div>"
            f"</div>"
        )
    # pad with placeholder chips to keep grid aligned
    while len(chips) < 4:
        chips.append(
            f"<div style=\"flex:1;border:1px dashed #3a4356;"
            f"border-radius:6px;padding:6px 4px;text-align:center;"
            f"font-family:JetBrains Mono;font-size:0.66rem;color:#5a6378\">"
            f"—</div>"
        )

    panel = (
        f"<div style=\"background:{color}1c;border:1px solid {color}55;"
        f"border-left:3px solid {color};border-radius:8px;"
        f"padding:10px 16px;margin:8px 0 14px 0\">"
        f"<div style=\"display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:8px\">"
        f"<div>"
        f"<span style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.72rem;font-weight:700;letter-spacing:1px\">"
        f"EARNINGS HISTORY · {sel_ticker}</span><br>"
        f"<span style=\"color:{color};font-family:Sora;font-weight:800;"
        f"font-size:1.05rem\">{streak.title()}</span>"
        f"<span style=\"color:#a3a8b8;font-family:JetBrains Mono;"
        f"font-size:0.78rem;margin-left:10px\">"
        f"({n_beats}/{n_total} beats · avg surprise {avg_surprise:+.2f}%)"
        f"</span>"
        f"</div>"
        f"</div>"
        f"<div style=\"display:flex;gap:6px\">{''.join(chips)}</div>"
        f"</div>"
    )
    st.markdown(panel, unsafe_allow_html=True)


def _render_insider_panel(sel_ticker: str):
    """Insider transactions panel — last 90 days of Form 4 filings.

    Shows a sentiment chip (buying/selling/mixed/quiet) and a table of the
    5 most-recent transactions with name, shares, value.
    """
    try:
        import finnhub_client as fh
        insider = fh.get_insider_transactions(sel_ticker, lookback_days=90)
    except Exception:
        insider = None

    if not insider:
        return  # no panel if no data

    tone = insider.get("tone", "quiet")
    color = insider.get("tone_color", "#7d8aa5")
    n_buys = insider.get("n_buys", 0)
    n_sells = insider.get("n_sells", 0)
    net_value = insider.get("net_value", 0.0)
    lookback = insider.get("lookback_days", 90)
    txns = insider.get("transactions", [])

    # Sign-aware net-value formatter
    if abs(net_value) >= 1_000_000:
        net_str = f"${net_value/1_000_000:+,.1f}M"
    elif abs(net_value) >= 1_000:
        net_str = f"${net_value/1_000:+,.1f}K"
    else:
        net_str = f"${net_value:+,.0f}"

    # Build a tiny table of the 5 most-recent transactions
    rows = []
    for t in txns[:5]:
        shares = t.get("shares", 0)
        value = t.get("value", 0.0)
        direction_color = "#22e08a" if shares > 0 else "#ff5d6c"
        direction = "BUY" if shares > 0 else "SELL"
        # value formatter
        if abs(value) >= 1_000_000:
            v_str = f"${abs(value)/1_000_000:.1f}M"
        elif abs(value) >= 1_000:
            v_str = f"${abs(value)/1_000:.1f}K"
        else:
            v_str = f"${abs(value):.0f}"
        date_str = t.get("transaction_date") or "—"
        name = (t.get("name") or "—")[:30]  # truncate long names
        rows.append(
            f"<div style=\"display:grid;"
            f"grid-template-columns:80px 1fr 60px 90px;"
            f"gap:8px;padding:4px 0;align-items:center;"
            f"font-family:JetBrains Mono;font-size:0.78rem;"
            f"border-bottom:1px solid #2a3144\">"
            f"<span style=\"color:#7d8aa5\">{date_str}</span>"
            f"<span style=\"color:#c0c5d4\">{name}</span>"
            f"<span style=\"color:{direction_color};font-weight:700;"
            f"text-align:right\">{direction}</span>"
            f"<span style=\"color:#a3a8b8;text-align:right\">"
            f"{v_str}</span>"
            f"</div>"
        )

    rows_html = "".join(rows)

    panel = (
        f"<div style=\"background:{color}1c;border:1px solid {color}55;"
        f"border-left:3px solid {color};border-radius:8px;"
        f"padding:10px 16px;margin:8px 0 14px 0\">"
        f"<div style=\"display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:10px\">"
        f"<div>"
        f"<span style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.72rem;font-weight:700;letter-spacing:1px\">"
        f"INSIDER ACTIVITY · {sel_ticker} · {lookback}D</span><br>"
        f"<span style=\"color:{color};font-family:Sora;font-weight:800;"
        f"font-size:1.05rem\">Net {tone.title()}</span>"
        f"<span style=\"color:#a3a8b8;font-family:JetBrains Mono;"
        f"font-size:0.78rem;margin-left:10px\">"
        f"{n_buys} buys · {n_sells} sells · net {net_str}</span>"
        f"</div>"
        f"<div style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.7rem\">Form 4 filings</div>"
        f"</div>"
        f"<div style=\"margin-top:8px\">{rows_html}</div>"
        f"<div style=\"color:#5a6378;font-family:JetBrains Mono;"
        f"font-size:0.66rem;margin-top:8px;line-height:1.5\">"
        f"Sells noisier than buys (options exercises, 10b5-1 plans, "
        f"diversification). Cluster buying is strongest signal."
        f"</div>"
        f"</div>"
    )
    st.markdown(panel, unsafe_allow_html=True)


def _render_news_panel(sel_ticker: str):
    """Top 5 recent news headlines for the selected ticker.

    Mostly useful for context: 'why did the score move?' Renders nothing
    when Finnhub returns no news (smaller-caps may be sparse).
    """
    try:
        import finnhub_client as fh
        items = fh.get_company_news(sel_ticker, n_items=5, lookback_days=7)
    except Exception:
        items = None

    if not items:
        return

    headlines = []
    for n in items:
        title = (n.get("headline") or "—").strip()
        # Truncate overly long headlines
        if len(title) > 130:
            title = title[:127] + "..."
        source = n.get("source") or "—"
        url = n.get("url") or "#"
        ts = n.get("datetime") or ""
        # Format timestamp as "Mar 18" or "5 hrs ago"
        when = ""
        if ts:
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(ts.replace("Z", "+00:00")) \
                    if "T" in ts else _dt.fromisoformat(ts)
                now = _dt.now()
                # naive compare ok for our purposes
                if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                diff = now - dt
                if diff.days >= 1:
                    when = dt.strftime("%b %d")
                elif diff.total_seconds() > 3600:
                    hrs = int(diff.total_seconds() / 3600)
                    when = f"{hrs}h ago"
                else:
                    mins = max(int(diff.total_seconds() / 60), 1)
                    when = f"{mins}m ago"
            except (ValueError, TypeError):
                pass

        headlines.append(
            f"<div style=\"display:grid;"
            f"grid-template-columns:80px 1fr 80px;"
            f"gap:8px;padding:6px 0;align-items:baseline;"
            f"border-bottom:1px solid #2a3144;line-height:1.4\">"
            f"<span style=\"color:#7d8aa5;font-family:JetBrains Mono;"
            f"font-size:0.7rem;text-align:left\">{when}</span>"
            f"<span style=\"color:#c0c5d4;font-size:0.82rem\">"
            f"<a href=\"{url}\" target=\"_blank\" "
            f"style=\"color:#c0c5d4;text-decoration:none\">{title}</a>"
            f"</span>"
            f"<span style=\"color:#7d8aa5;font-family:JetBrains Mono;"
            f"font-size:0.68rem;text-align:right\">{source}</span>"
            f"</div>"
        )

    panel = (
        f"<div style=\"background:#1a2030;border:1px solid #3a4356;"
        f"border-left:3px solid #7d8aa5;border-radius:8px;"
        f"padding:10px 16px;margin:8px 0 14px 0\">"
        f"<div style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.72rem;font-weight:700;letter-spacing:1px;"
        f"margin-bottom:8px\">"
        f"RECENT NEWS · {sel_ticker} · LAST 7 DAYS</div>"
        f"{''.join(headlines)}"
        f"</div>"
    )
    st.markdown(panel, unsafe_allow_html=True)


def _render_analyst_panel(sel_row: dict, sel_ticker: str):
    """Compact analyst-consensus panel sitting below the Fundamental Grade
    card. Shows the breakdown of analyst ratings (counts at each level,
    weighted consensus) from Finnhub.

    Renders nothing when Finnhub returned no coverage — better to show
    nothing than show a panel of zeros that suggests "0 analysts cover this"
    when the truth is "we couldn't reach the data source".
    """
    breakdown = sel_row.get("analyst_breakdown")
    if not breakdown:
        return
    consensus = breakdown.get("consensus", "—")
    color = breakdown.get("consensus_color", "#7d8aa5")
    period = breakdown.get("period", "—")
    total = breakdown.get("total", 0)
    sb = breakdown.get("strong_buy", 0)
    b  = breakdown.get("buy", 0)
    h  = breakdown.get("hold", 0)
    s  = breakdown.get("sell", 0)
    ss = breakdown.get("strong_sell", 0)
    weighted = breakdown.get("weighted_score", 0)

    # 5 horizontal "stacked-bar" segments showing relative analyst counts.
    # Build each segment as a colored block whose width = count/total.
    seg_colors = ["#22e08a", "#7fd98a", "#f5c344", "#ff9442", "#ff5d6c"]
    seg_labels = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
    seg_counts = [sb, b, h, s, ss]
    bar_segments = []
    for col, lbl, cnt in zip(seg_colors, seg_labels, seg_counts):
        if total > 0 and cnt > 0:
            pct = cnt / total * 100
            bar_segments.append(
                f"<div style=\"flex:{cnt};background:{col};height:14px;"
                f"display:flex;align-items:center;justify-content:center;"
                f"font-family:JetBrains Mono;font-size:0.66rem;color:#000;"
                f"font-weight:700\" title=\"{lbl}: {cnt} analysts\">"
                f"{cnt}</div>"
            )
    bar_html = "".join(bar_segments)

    panel = (
        f"<div style=\"background:{color}1c;border:1px solid {color}55;"
        f"border-left:3px solid {color};border-radius:8px;"
        f"padding:10px 16px;margin:8px 0 14px 0\">"
        f"<div style=\"display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:8px\">"
        f"<div>"
        f"<span style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.72rem;font-weight:700;letter-spacing:1px\">"
        f"ANALYST CONSENSUS · {sel_ticker}</span><br>"
        f"<span style=\"color:{color};font-family:Sora;font-weight:800;"
        f"font-size:1.05rem\">{consensus}</span>"
        f"<span style=\"color:#a3a8b8;font-family:JetBrains Mono;"
        f"font-size:0.78rem;margin-left:10px\">"
        f"({total} analysts · weighted {weighted:.2f} / 5.00)</span>"
        f"</div>"
        f"<div style=\"color:#7d8aa5;font-family:JetBrains Mono;"
        f"font-size:0.72rem\">snapshot · {period}</div>"
        f"</div>"
        f"<div style=\"display:flex;gap:1px;border-radius:4px;"
        f"overflow:hidden\">{bar_html}</div>"
        f"<div style=\"display:flex;justify-content:space-between;"
        f"margin-top:6px;font-family:JetBrains Mono;font-size:0.66rem;"
        f"color:#7d8aa5\">"
        f"<span>Strong Buy</span><span>Buy</span><span>Hold</span>"
        f"<span>Sell</span><span>Strong Sell</span>"
        f"</div>"
        f"</div>"
    )
    st.markdown(panel, unsafe_allow_html=True)


def _render_trade_plan(sel_row: dict, sel_ticker: str):
    """Compact Trade Plan card — ATR-based stop, target, position sizing.

    Renders only for tickers in TRADABLE / BUY tier (composite score >= 65).
    Lower-scoring tickers don't get a plan because surfacing one would
    suggest the system endorses taking the trade, which it doesn't for
    NEUTRAL/CAUTION/AVOID tiers.

    The plan is pure decision support — does NOT enforce anything. The
    backtest's mechanical 20-day exit is unchanged. This panel exists so
    that when a user manually places a trade based on a signal, they have
    a defined risk framework before clicking buy.
    """
    score = sel_row.get("total_score")
    if score is None or score < 65:
        # Only show plans for tradable signals
        return

    price = sel_row.get("price")
    if price is None or price <= 0:
        return

    # Determine direction from action label — LONG actions vs SHORT/AVOID
    action = (sel_row.get("status_label") or "").upper()
    if "SHORT" in action:
        direction = "SHORT"
    elif "TRADABLE" in action or "BUY" in action:
        direction = "LONG"
    else:
        return

    # Strategy detection — pull from the same session_state Page 2 uses
    strategy_label = (st.session_state.get("strategy") or "").lower()
    strategy = "mr" if ("mean" in strategy_label or "reversion" in strategy_label) else "trend"

    # Build the plan (pulls ~60d history for ATR computation; cached)
    try:
        import trade_plan as tp
        plan = tp.build_trade_plan(
            ticker=sel_ticker,
            entry_price=float(price),
            strategy=strategy,
            direction=direction,
        )
    except Exception:
        plan = None

    if not plan:
        # Quiet skip if ATR couldn't be computed — likely insufficient
        # history (new IPO, illiquid). Don't show an empty plan panel.
        return

    # Colors: green for upside (target), red for downside (stop)
    stop_color   = theme.RED
    target_color = theme.GREEN
    if direction == "LONG":
        stop_label   = "Stop Loss"
        target_label = "Target"
    else:
        stop_label   = "Stop (above entry)"
        target_label = "Target (below entry)"

    # R:R color — at least 1.5:1 is conventionally "acceptable"
    rr_color = theme.GREEN if plan["rr_ratio"] >= 1.5 else theme.YELLOW

    # Build the panel as a single concatenated HTML string — no multi-line
    # leading whitespace (markdown would otherwise parse as code block).
    panel = (
        f"<div style='background:{theme.PANEL};border:1px solid {theme.BORDER};"
        f"border-radius:8px;padding:14px 16px;margin-top:14px'>"
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;margin-bottom:10px'>"
        f"<span style='font-family:Sora;font-size:1.05rem;font-weight:700;"
        f"color:{theme.TEXT}'>📐 Trade Plan — {direction}</span>"
        f"<span style='font-family:JetBrains Mono;font-size:0.7rem;"
        f"color:{theme.MUTED}'>{plan['method']}</span>"
        f"</div>"
        f"<div style='display:flex;gap:10px;margin-bottom:10px'>"
        f"<div style='flex:1;background:{theme.PANEL_HI};border-radius:6px;"
        f"padding:8px 10px'>"
        f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
        f"color:{theme.MUTED};letter-spacing:0.08em;font-weight:700;"
        f"margin-bottom:2px'>ENTRY</div>"
        f"<div style='font-family:Sora;font-size:1.1rem;font-weight:700;"
        f"color:{theme.TEXT}'>${plan['entry']:,.2f}</div>"
        f"</div>"
        f"<div style='flex:1;background:{stop_color}11;border-radius:6px;"
        f"padding:8px 10px;border-left:3px solid {stop_color}'>"
        f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
        f"color:{stop_color};letter-spacing:0.08em;font-weight:700;"
        f"margin-bottom:2px'>{stop_label.upper()}</div>"
        f"<div style='font-family:Sora;font-size:1.1rem;font-weight:700;"
        f"color:{theme.TEXT}'>${plan['stop']:,.2f}</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.66rem;"
        f"color:{theme.MUTED};margin-top:2px'>"
        f"−{plan['risk_pct']:.1f}% · ${plan['risk_per_share']:,.2f}/sh</div>"
        f"</div>"
        f"<div style='flex:1;background:{target_color}11;border-radius:6px;"
        f"padding:8px 10px;border-left:3px solid {target_color}'>"
        f"<div style='font-family:JetBrains Mono;font-size:0.6rem;"
        f"color:{target_color};letter-spacing:0.08em;font-weight:700;"
        f"margin-bottom:2px'>{target_label.upper()}</div>"
        f"<div style='font-family:Sora;font-size:1.1rem;font-weight:700;"
        f"color:{theme.TEXT}'>${plan['target']:,.2f}</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.66rem;"
        f"color:{theme.MUTED};margin-top:2px'>"
        f"+{plan['reward_pct']:.1f}% · ${plan['reward_per_share']:,.2f}/sh</div>"
        f"</div>"
        f"</div>"
        f"<div style='display:flex;gap:14px;padding-top:8px;"
        f"border-top:1px solid {theme.BORDER};"
        f"font-family:JetBrains Mono;font-size:0.78rem'>"
        f"<div><span style='color:{theme.MUTED}'>Reward/Risk:</span> "
        f"<b style='color:{rr_color}'>{plan['rr_ratio']:.2f}:1</b></div>"
        f"<div><span style='color:{theme.MUTED}'>Shares:</span> "
        f"<b style='color:{theme.TEXT}'>{plan['shares']:,}</b></div>"
        f"<div><span style='color:{theme.MUTED}'>Position:</span> "
        f"<b style='color:{theme.TEXT}'>"
        f"${plan['position_dollars']:,.0f}</b></div>"
        f"<div><span style='color:{theme.MUTED}'>Max loss:</span> "
        f"<b style='color:{stop_color}'>"
        f"−${plan['max_loss_dollars']:,.0f}</b></div>"
        f"<div><span style='color:{theme.MUTED}'>Max gain:</span> "
        f"<b style='color:{target_color}'>"
        f"+${plan['max_gain_dollars']:,.0f}</b></div>"
        f"</div>"
        f"<div style='font-family:JetBrains Mono;font-size:0.65rem;"
        f"color:{theme.MUTED};margin-top:8px;line-height:1.4'>"
        f"⚠ <b>These are calculated risk levels, not price predictions.</b> "
        f"The target is simply entry + 3×ATR and the stop is entry − 2×ATR "
        f"— a volatility-scaled framework that fixes your reward:risk at "
        f"1.5:1. The system is <b>not</b> forecasting the stock will reach "
        f"${plan['target']:,.2f}; it's saying \"if you want 1.5:1 R:R at "
        f"this stock's volatility, here's where the levels fall.\" "
        f"Decision support only — stops gap through (real fills on adverse "
        f"opens are typically worse than displayed), and the backtest uses "
        f"a 20-day mechanical exit that does NOT enforce these levels."
        f"</div>"
        f"</div>"
    )
    st.markdown(panel, unsafe_allow_html=True)


def _render_price_projection(sel_row: dict, sel_ticker: str):
    """Price Projection & Target Generator — three alternative target
    methodologies shown below the ATR Trade Plan.

    Methods (each a different lens, NOT competing predictions):
      1. Options Expected Move — statistical 1-sigma range from IV
      2. Floor Trader Pivots — structural S/R from prior period HLC
      3. Fibonacci Extensions — momentum-continuation targets from a swing

    Auto-populates inputs from real data where available (spot from the
    scan row, IV from the weekly option chain, OHLC from history), and
    lets the user override everything via a collapsed config expander.

    This is purely additive — it does NOT touch the existing ATR trade
    plan or the backtest. Different traders trust different methods;
    surfacing all three lets the user triangulate.
    """
    import price_projection as pp

    price = sel_row.get("price")
    if price is None or price <= 0:
        return
    try:
        spot_default = float(price)
    except (TypeError, ValueError):
        return
    import math as _m
    if not _m.isfinite(spot_default) or spot_default < 0.01:
        return

    # ── Auto-populate inputs from real data ──
    # Horizon: the user can project over different timeframes. The IV is
    # pulled from the option chain CLOSEST to the chosen horizon, because
    # IV term structure means a 28-day option often has different IV than
    # a 7-day option. Default 7d (weekly). Read the chosen horizon from
    # session_state so the fetch below targets the right expiry.
    HORIZON_OPTIONS = {
        "Weekly (7 days)":  7,
        "2 weeks (14 days)": 14,
        "3 weeks (21 days)": 21,
        "4 weeks (28 days)": 28,
    }
    horizon_key = f"proj_horizon_{sel_ticker}"
    horizon_label = st.session_state.get(horizon_key, "Weekly (7 days)")
    target_dte = HORIZON_OPTIONS.get(horizon_label, 7)

    # Detect a horizon change since last render. When the user switches
    # horizon, the IV and DTE inputs should re-seed from the new expiry's
    # data — but Streamlit pins keyed-widget values to session_state and
    # ignores the value= param on reruns. So we explicitly clear those two
    # widget keys when the horizon changes, forcing them to re-initialize
    # from the freshly-fetched defaults below.
    last_horizon_key = f"proj_last_horizon_{sel_ticker}"
    iv_widget_key = f"proj_iv_{sel_ticker}"
    dte_widget_key = f"proj_dte_{sel_ticker}"
    if st.session_state.get(last_horizon_key) != horizon_label:
        st.session_state[last_horizon_key] = horizon_label
        # Clear so the value= default re-seeds from the new horizon
        for k in (iv_widget_key, dte_widget_key):
            if k in st.session_state:
                del st.session_state[k]

    # IV: pull from the option chain closest to the chosen horizon. Use a
    # wider tolerance for longer horizons (monthly expiries are sparser).
    iv_default = 40.0  # fallback if no chain
    dte_default = target_dte
    iv_source = "fallback"   # "chain" | "fallback" — for transparency
    matched_dte = None       # actual DTE of the matched expiry
    chain_status = None
    iv_fail_reason = None     # why IV extraction failed (if chain was ok)
    # ── Diagnostics: record exactly what happened at each step so the
    # "🔍 Data diagnostics" panel can show whether Yahoo failed or our
    # extraction did. Populated throughout the fetch below.
    diag = {
        "ticker": sel_ticker,
        "spot": round(spot_default, 2),
        "horizon_label": horizon_label,
        "target_dte": target_dte,
        "tolerance": None,
        "chain_status": None,
        "chain_error": None,
        "matched_expiry": None,
        "matched_dte": None,
        "puts_rows": None,
        "puts_iv_positive": None,
        "puts_price_usable": None,
        "puts_iv_raw_median": None,
        "calls_iv_raw_median": None,
        "calls_rows": None,
        "calls_iv_positive": None,
        "iv_from_feed": None,
        "iv_from_price": None,
        "realized_raw": None,
        "realized_filtered": None,
        "realized_max_day_move": None,
        "realized_obs_total": None,
        "realized_obs_clean": None,
        "final_iv": None,
        "final_iv_source": None,
    }
    try:
        import data_utils as du
        # Tolerance scales with horizon: weeklies are dense (±4d fine), but
        # 3-4 week targets may only have monthly expiries nearby, so allow
        # a much wider window to avoid silent fallback to the 40% default.
        if target_dte <= 7:
            tol = 4
        elif target_dte <= 14:
            tol = 8
        else:
            tol = 16  # 21-28d target: accept anything from ~5d to ~44d
        diag["tolerance"] = tol
        chain = du.get_weekly_option_chain(
            sel_ticker, target_dte_days=target_dte, dte_tolerance=tol)
        chain_status = chain.get("status") if chain else None
        diag["chain_status"] = chain_status
        diag["chain_error"] = chain.get("error") if chain else None
        diag["matched_expiry"] = chain.get("expiry") if chain else None
        if chain and chain.get("status") == "ok":
            matched_dte = int(chain.get("dte", target_dte)) or target_dte
            dte_default = matched_dte
            diag["matched_dte"] = matched_dte
            # Pull ATM IV. Robust extraction:
            #  - sample MORE strikes (nearest 8, not 3) so a few NaN/0 rows
            #    don't sink the median
            #  - handle BOTH yfinance IV conventions: fraction (0.45) and
            #    already-percentage (45.0). We detect by magnitude.
            #  - try puts first, fall back to calls if puts have no usable IV
            #  - track WHY it failed for an honest banner

            def _extract_iv(df, diag_key=None):
                """Return a sane annualized IV % from an options DataFrame,
                or None if no usable IV found.

                Robust to yfinance scale quirks: only samples strikes truly
                near the money (within ±15% of spot), where IV is meaningful
                and liquid. Deep ITM/OTM strikes often carry distorted or
                near-zero IV that corrupts a naive median.
                """
                if df is None or df.empty or "impliedVolatility" not in df:
                    return None
                if "strike" not in df:
                    return None
                # Restrict to near-the-money strikes (±15% of spot). Deep
                # wings have unreliable IV on the free feed.
                lo_k, hi_k = spot_default * 0.85, spot_default * 1.15
                atm = df[(df["strike"] >= lo_k) & (df["strike"] <= hi_k)]
                if atm.empty:
                    # No strikes near spot — fall back to nearest 8 overall
                    atm = df.iloc[(df["strike"] - spot_default).abs()
                                   .argsort()[:8]]
                iv_vals = atm["impliedVolatility"].dropna()
                # Drop zero/negative AND implausibly-tiny values. yfinance
                # often fills deep-wing or stale strikes with near-zero IV
                # (e.g. 0.001) that drags a naive median toward nonsense.
                # A real equity IV fraction is >= ~0.05 (5%); anything below
                # 0.02 is feed junk. (Values >= 1 are handled as the
                # already-percentage convention downstream.)
                iv_vals = iv_vals[(iv_vals >= 0.02)]
                if iv_vals.empty:
                    return None
                med = float(iv_vals.median())
                # Expose the raw median for diagnostics so scale bugs are
                # visible instead of hidden.
                if diag_key:
                    diag[diag_key] = round(med, 5)
                # Convention detection. yfinance normally returns IV as a
                # FRACTION (0.45 = 45%). Map to a percentage and sanity-check:
                #   - fraction (0.05–3.0)  → ×100  → 5%–300%
                #   - already pct (5–300)  → as-is
                # Anything outside a plausible 5%–300% band after mapping is
                # rejected as a scale error (e.g. the 0.008 → 0.8% bug).
                if med < 3.0:
                    iv_pct = med * 100
                else:
                    iv_pct = med
                iv_pct = round(iv_pct, 1)
                # Plausibility gate: a real equity IV is ~10%–250%. Reject
                # anything below 5% as a feed scale error rather than passing
                # a nonsensical 0.8% through.
                if iv_pct < 5.0 or iv_pct > 300.0:
                    return None
                return iv_pct

            puts = chain.get("puts")
            calls = chain.get("calls")

            # Record raw chain shape for diagnostics
            try:
                if puts is not None and not puts.empty:
                    diag["puts_rows"] = int(len(puts))
                    if "impliedVolatility" in puts:
                        diag["puts_iv_positive"] = int(
                            (puts["impliedVolatility"].fillna(0) > 0).sum())
                    # Count strikes with a usable price (bid/ask or last > 0)
                    price_ok = 0
                    for _, rr in puts.iterrows():
                        b = rr.get("bid") or 0
                        a = rr.get("ask") or 0
                        lp = rr.get("lastPrice") or 0
                        if (b and a) or lp:
                            price_ok += 1
                    diag["puts_price_usable"] = price_ok
                if calls is not None and not calls.empty:
                    diag["calls_rows"] = int(len(calls))
                    if "impliedVolatility" in calls:
                        diag["calls_iv_positive"] = int(
                            (calls["impliedVolatility"].fillna(0) > 0).sum())
            except Exception:
                pass

            iv_candidate = _extract_iv(puts, diag_key="puts_iv_raw_median")
            if iv_candidate is None:
                iv_candidate = _extract_iv(calls, diag_key="calls_iv_raw_median")
            diag["iv_from_feed"] = iv_candidate

            # If yfinance gave no usable IV (common on its free tier — the
            # impliedVolatility column is often 0/NaN), COMPUTE IV ourselves
            # by backing it out of the option's market price via Black-
            # Scholes. This is the genuine definition of implied vol. We use
            # the nearest-to-spot strike's mid price (or last as fallback).
            iv_computed_from_price = False
            if iv_candidate is None:
                def _iv_from_chain_price(df, kind):
                    """Back out IV from the nearest-strike option's price."""
                    if df is None or df.empty or "strike" not in df:
                        return None
                    row = df.iloc[(df["strike"] - spot_default).abs()
                                   .argsort()[:1]]
                    if row.empty:
                        return None
                    r = row.iloc[0]
                    strike = float(r["strike"])
                    # Prefer mid of bid/ask; fall back to lastPrice
                    bid = float(r["bid"]) if "bid" in r and r["bid"] else 0.0
                    ask = float(r["ask"]) if "ask" in r and r["ask"] else 0.0
                    if bid > 0 and ask > 0:
                        opt_price = (bid + ask) / 2.0
                    elif "lastPrice" in r and r["lastPrice"]:
                        opt_price = float(r["lastPrice"])
                    else:
                        return None
                    return pp.implied_vol_from_price(
                        opt_price, spot_default, strike, matched_dte, kind)

                iv_candidate = _iv_from_chain_price(puts, "put")
                if iv_candidate is None:
                    iv_candidate = _iv_from_chain_price(calls, "call")
                if iv_candidate is not None:
                    iv_computed_from_price = True
                diag["iv_from_price"] = iv_candidate

            if iv_candidate is not None and iv_candidate >= 1.0:
                iv_default = iv_candidate
                # Distinguish feed-provided IV from our computed IV so the
                # banner can be honest about the source.
                iv_source = "computed" if iv_computed_from_price else "chain"
            else:
                iv_fail_reason = ("chain had neither usable IV data nor "
                                   "option prices to compute it from")
    except Exception:
        pass

    # ── Historical-volatility fallback ──
    # If options data was entirely unusable (yfinance scrape failures leave
    # IV, bid, ask, and last all empty), fall back to REALIZED (historical)
    # volatility computed from the stock's own price history. This is a
    # legitimate IV proxy — it's how much the stock has actually been
    # moving, annualized. Not identical to IV (which embeds a forward risk
    # premium) but far better than a flat 40% guess because it's grounded
    # in THIS stock's real behavior. Only used when options IV failed.
    if iv_source == "fallback":
        try:
            import data_utils as du
            import numpy as np
            # Use ~90 calendar days (~60 trading sessions) for a more stable
            # vol estimate. 40 days gave only ~26 observations, too few for a
            # reliable stdev (one volatile week dominates).
            hist = du.get_history(sel_ticker, days=90)
            if hist is not None and not hist.empty and "Close" in hist:
                closes = hist["Close"].dropna()
                # Guard against zero/negative closes (corrupt rows) which
                # would make log returns blow up or be undefined.
                closes = closes[closes > 0]
                if len(closes) >= 10:
                    log_ret = np.log(closes / closes.shift(1)).dropna()
                    # Record RAW realized vol (no filter) for diagnostics —
                    # this is the number that reveals whether corruption is
                    # inflating the estimate.
                    if len(log_ret) >= 2:
                        diag["realized_raw"] = round(
                            float(log_ret.std()) * np.sqrt(252) * 100, 1)
                        diag["realized_obs_total"] = int(len(log_ret))
                        # Expose the largest single-day move so we can see if
                        # a few big real days (earnings) drive the estimate vs
                        # uniform noise vs corruption.
                        diag["realized_max_day_move"] = (
                            f"{float(log_ret.abs().max())*100:.1f}%")
                    # ── Corruption filter ──
                    # A single bad tick in yfinance data (e.g. a close that's
                    # 1.5x its neighbors) creates two enormous fake returns
                    # that massively inflate the stdev — this is exactly what
                    # produced an implausible "90% vol" for AMD. Drop any
                    # daily return beyond ±35%: for a large-cap, a real
                    # single-session move that large is a once-in-years event,
                    # so anything bigger is almost certainly a data error.
                    # (Same spirit as the OHLCV cache's 1.5x corruption filter.)
                    clean_ret = log_ret[log_ret.abs() <= 0.35]
                    diag["realized_obs_clean"] = int(len(clean_ret))
                    # Need enough clean observations to trust the estimate
                    if len(clean_ret) >= 10:
                        daily_vol = float(clean_ret.std())
                        hist_vol_pct = round(daily_vol * np.sqrt(252) * 100, 1)
                        diag["realized_filtered"] = hist_vol_pct
                        # than show an implausible number; fall through to the
                        # 40% generic default which at least won't mislead.
                        if 1.0 <= hist_vol_pct <= 120.0:
                            iv_default = hist_vol_pct
                            iv_source = "historical"
        except Exception:
            pass

    # Final clamp: IV input requires >= 1.0; DTE requires >= 1
    iv_default = max(1.0, float(iv_default))
    dte_default = max(1, int(dte_default))
    diag["final_iv"] = round(iv_default, 1)
    diag["final_iv_source"] = iv_source

    # OHLC for pivots + swing detection: pull recent history
    hi_default = lo_default = close_default = None
    swing_hi_default = swing_lo_default = None
    try:
        import data_utils as du
        hist = du.get_history(sel_ticker, days=60)
        if hist is not None and not hist.empty:
            # Prior period (most recent complete bar) for pivots
            last = hist.iloc[-1]
            hi_default = float(last["High"])
            lo_default = float(last["Low"])
            close_default = float(last["Close"])
            # Swing detection: high/low over last 20 sessions for Fibonacci
            recent = hist.tail(20)
            swing_hi_default = float(recent["High"].max())
            swing_lo_default = float(recent["Low"].min())
    except Exception:
        pass

    # Sensible fallbacks if history unavailable
    if hi_default is None:
        hi_default = round(spot_default * 1.02, 2)
        lo_default = round(spot_default * 0.98, 2)
        close_default = spot_default
    if swing_hi_default is None:
        swing_hi_default = round(spot_default * 1.10, 2)
        swing_lo_default = round(spot_default * 0.90, 2)

    # Final guard: all price inputs require >= 0.01 and must be finite.
    # A malformed history row (0, negative, or NaN High/Low/Close) would
    # otherwise crash the number_input min_value=0.01 constraint. Fall
    # back to spot-derived values for any bad field.
    import math as _math
    def _safe_price(val, fallback):
        try:
            v = float(val)
            if v >= 0.01 and _math.isfinite(v):
                return round(v, 2)
        except (TypeError, ValueError):
            pass
        return round(fallback, 2)

    hi_default       = _safe_price(hi_default,       spot_default * 1.02)
    lo_default       = _safe_price(lo_default,       spot_default * 0.98)
    close_default    = _safe_price(close_default,    spot_default)
    swing_hi_default = _safe_price(swing_hi_default, spot_default * 1.10)
    swing_lo_default = _safe_price(swing_lo_default, spot_default * 0.90)

    # Ensure swing_high > swing_low and prior high >= prior low (the math
    # functions require this; bad data could violate it)
    if swing_hi_default <= swing_lo_default:
        swing_hi_default = round(swing_lo_default * 1.05, 2)
    if hi_default < lo_default:
        hi_default, lo_default = lo_default, hi_default

    # ── Section container ──
    with st.expander("🎯 Price Projection & Target Generator", expanded=False):
        st.markdown(
            f"<div style='font-family:JetBrains Mono;font-size:0.76rem;"
            f"color:{theme.MUTED};line-height:1.5;margin-bottom:10px'>"
            f"Three independent ways to estimate price targets for "
            f"<b style='color:{theme.TEXT}'>{sel_ticker}</b>. "
            f"<b style='color:{theme.TEXT}'>They measure different things "
            f"and will disagree — that's expected.</b> Use them together "
            f"to triangulate, not as a single answer. Separate from the "
            f"ATR Trade Plan above."
            f"</div>",
            unsafe_allow_html=True)

        # ── Projection horizon (always visible — it's the primary control) ──
        # Kept OUT of the collapsed config expander so it's always
        # instantiated and its value reliably drives the option-chain fetch.
        # Burying it in a collapsed expander caused stale-state bugs where
        # the chosen horizon didn't propagate to the IV/DTE fetch.
        st.selectbox(
            "Projection horizon",
            options=list(HORIZON_OPTIONS.keys()),
            key=horizon_key,
            help="How far ahead to project. The Expected Move pulls implied "
                 "volatility from the option expiry closest to this horizon. "
                 "Longer horizons = wider expected ranges. This is the main "
                 "control — IV and days-to-expiry below follow it.")

        # ── Config expander (fine-tune inputs) ──
        with st.expander("⚙️ Projection Variables Configuration",
                          expanded=False):
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.72rem;"
                f"color:{theme.MUTED};margin-bottom:8px'>"
                f"Auto-filled from live data where available. Override any "
                f"value to explore scenarios.</div>",
                unsafe_allow_html=True)

            strategy_view = st.selectbox(
                "Strategy lens",
                options=["Trend Long", "Mean Reversion", "Options Premium Selling"],
                key=f"proj_strategy_{sel_ticker}",
                help="Changes which method is emphasized. Trend Long → "
                     "Fibonacci extensions. Mean Reversion → Pivots. "
                     "Options Premium Selling → Expected Move.")

            cfg1, cfg2 = st.columns(2)
            with cfg1:
                spot_in = st.number_input(
                    "Current Price ($)", value=spot_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_spot_{sel_ticker}")
                iv_in = st.number_input(
                    "Implied Volatility (%)", value=iv_default,
                    min_value=1.0, max_value=300.0, step=1.0,
                    key=f"proj_iv_{sel_ticker}",
                    help="Annualized IV. Auto-pulled from the weekly option "
                         "chain's at-the-money strikes when available.")
                dte_in = st.number_input(
                    "Days to Expiration", value=dte_default,
                    min_value=1, max_value=365, step=1,
                    key=f"proj_dte_{sel_ticker}",
                    help="Auto-set from the Projection horizon above. "
                         "Override to fine-tune the exact day count.")
            with cfg2:
                hi_in = st.number_input(
                    "Prior High ($)", value=hi_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_hi_{sel_ticker}",
                    help="Prior period high — used for Floor Trader Pivots.")
                lo_in = st.number_input(
                    "Prior Low ($)", value=lo_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_lo_{sel_ticker}")
                close_in = st.number_input(
                    "Prior Close ($)", value=close_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_close_{sel_ticker}")

            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.72rem;"
                f"color:{theme.MUTED};margin:8px 0 4px 0'>"
                f"Fibonacci swing path (auto-detected from last 20 "
                f"sessions):</div>",
                unsafe_allow_html=True)
            fib1, fib2, fib3 = st.columns(3)
            with fib1:
                swing_hi_in = st.number_input(
                    "Swing High ($)", value=swing_hi_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_swinghi_{sel_ticker}")
            with fib2:
                swing_lo_in = st.number_input(
                    "Swing Low ($)", value=swing_lo_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_swinglo_{sel_ticker}")
            with fib3:
                pullback_in = st.number_input(
                    "Pullback / Entry ($)", value=spot_default,
                    min_value=0.01, step=0.01,
                    key=f"proj_pullback_{sel_ticker}",
                    help="The retracement low, or your entry price, that "
                         "the next leg projects from.")

        # ── Results tabs ──
        tab_em, tab_piv, tab_fib = st.tabs(
            ["Options Expected Move", "Structural Pivots", "Trend Extensions"])

        # Tab 1: Expected Move
        with tab_em:
            # DTE authority: the horizon dropdown is the source of truth.
            # We use the matched expiry's actual DTE when we got a real
            # chain (so the range reflects the real option), otherwise the
            # horizon target. The dte_in number_input is a fine-tune that
            # only overrides if the user explicitly set it away from the
            # horizon default — this avoids the stale-pinned-widget bug
            # where dte_in stuck at 8 even after switching to 28.
            effective_dte = matched_dte if matched_dte is not None else target_dte
            # If the user manually typed a DTE that differs from both the
            # horizon target AND the matched expiry, honor their override.
            if int(dte_in) != target_dte and int(dte_in) != (matched_dte or -1):
                effective_dte = int(dte_in)
            em = pp.expected_move(spot_in, iv_in, int(effective_dte))
            if em is None:
                st.warning("Couldn't compute expected move — check inputs.")
            else:
                # Transparency banner: tell the user EXACTLY what data fed
                # this — whether the IV came from a real option chain (and
                # which expiry), or fell back to the 40% default. This makes
                # "why does it say 8 days when I picked 28" answerable at a
                # glance rather than a mystery.
                if iv_source in ("chain", "computed") and matched_dte is not None:
                    # Note whether IV came straight from the feed or we had
                    # to compute it from option prices (yfinance free tier
                    # frequently ships 0/missing IV — we back it out of the
                    # mid price via Black-Scholes in that case).
                    iv_origin = ("computed from the option's market price"
                                  if iv_source == "computed"
                                  else f"from {sel_ticker}'s option feed")
                    if abs(matched_dte - target_dte) > 5:
                        # Matched expiry is notably off from the requested horizon
                        st.markdown(
                            f"<div style='font-family:JetBrains Mono;"
                            f"font-size:0.72rem;color:{theme.YELLOW};"
                            f"background:{theme.YELLOW}11;padding:6px 10px;"
                            f"border-left:3px solid {theme.YELLOW};"
                            f"border-radius:4px;margin-bottom:8px'>"
                            f"⚠ You requested ~{target_dte}d, but the nearest "
                            f"available option expiry is <b>{matched_dte}d</b> "
                            f"out — IV ({iv_origin}) and range below reflect "
                            f"the {matched_dte}d expiry."
                            f"</div>",
                            unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f"<div style='font-family:JetBrains Mono;"
                            f"font-size:0.72rem;color:{theme.MUTED};"
                            f"margin-bottom:6px'>"
                            f"✓ IV {iv_origin} at the {matched_dte}d expiry "
                            f"(matched your ~{target_dte}d horizon)."
                            f"</div>",
                            unsafe_allow_html=True)
                elif iv_source == "historical":
                    # Options data was unusable, but we derived realized
                    # (historical) volatility from the stock's own price
                    # history — a real, stock-specific estimate. Honest
                    # about the distinction from true implied vol.
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.72rem;color:{theme.YELLOW};"
                        f"background:{theme.YELLOW}11;padding:6px 10px;"
                        f"border-left:3px solid {theme.YELLOW};"
                        f"border-radius:4px;margin-bottom:8px'>"
                        f"⚠ {sel_ticker}'s option chain had no usable IV or "
                        f"price data, so this uses <b>{iv_in:.0f}% realized "
                        f"(historical) volatility</b> — how much {sel_ticker} "
                        f"has actually moved over the last ~60 trading days, "
                        f"annualized. This is grounded in real price action "
                        f"but differs from true <i>implied</i> volatility "
                        f"(which embeds the market's forward expectations + a "
                        f"risk premium). A reasonable estimate; override with "
                        f"your broker's IV for precision."
                        f"</div>",
                        unsafe_allow_html=True)
                else:
                    # Fell back to default IV. Distinguish the two reasons
                    # honestly — "no chain at all" vs "chain was fine but
                    # its IV data was unusable" are different problems.
                    if chain_status == "ok" and iv_fail_reason:
                        # Chain WAS fetched (we even know the expiry), but
                        # IV extraction failed.
                        dte_note = (f"the {matched_dte}d expiry"
                                     if matched_dte else "the matched expiry")
                        reason_txt = (
                            f"⚠ Found {sel_ticker}'s option chain at "
                            f"{dte_note}, but {iv_fail_reason}. "
                            f"Using a <b>{iv_in:.0f}% fallback IV</b> — a "
                            f"generic estimate, NOT {sel_ticker}'s real "
                            f"implied volatility. Override the IV field in "
                            f"config with the true figure (check your broker "
                            f"or the option chain) for an accurate range.")
                    else:
                        # No usable chain at all (no_match / no_expiries / error)
                        reason_txt = (
                            f"⚠ No option chain available near the "
                            f"~{target_dte}d horizon (status: "
                            f"{chain_status or 'unavailable'}). Using a "
                            f"<b>{iv_in:.0f}% fallback IV</b> — a generic "
                            f"estimate, NOT {sel_ticker}'s real implied "
                            f"volatility. Override the IV field in config if "
                            f"you know the real figure.")
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.72rem;color:{theme.YELLOW};"
                        f"background:{theme.YELLOW}11;padding:6px 10px;"
                        f"border-left:3px solid {theme.YELLOW};"
                        f"border-radius:4px;margin-bottom:8px'>"
                        f"{reason_txt}"
                        f"</div>",
                        unsafe_allow_html=True)

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("Lower bound",
                               f"${em['lower_boundary']:,.2f}",
                               delta=f"-{em['move_pct']:.1f}%",
                               delta_color="off")
                with c2:
                    st.metric("Current",
                               f"${em['spot']:,.2f}")
                with c3:
                    st.metric("Upper bound",
                               f"${em['upper_boundary']:,.2f}",
                               delta=f"+{em['move_pct']:.1f}%",
                               delta_color="off")
                # Adapt the wording to the actual IV source. "Market makers
                # price in" is only true when the IV came from options. For
                # realized/historical vol, the range is a statistical
                # projection from past moves, not something the market priced.
                if iv_source in ("chain", "computed"):
                    lead_in = ("Market makers price in a "
                                f"<b style='color:{theme.TEXT}'>~68% chance</b>")
                    vol_label = "implied volatility"
                elif iv_source == "historical":
                    lead_in = ("Based on past price action, there's roughly a "
                                f"<b style='color:{theme.TEXT}'>68% chance</b>")
                    vol_label = "realized (historical) volatility"
                else:
                    lead_in = ("Using a generic estimate, roughly a "
                                f"<b style='color:{theme.TEXT}'>68% chance</b>")
                    vol_label = "fallback volatility"
                st.markdown(
                    f"<div style='font-family:JetBrains Mono;"
                    f"font-size:0.78rem;color:{theme.MUTED};line-height:1.5;"
                    f"margin-top:6px'>"
                    f"{lead_in} the stock stays between "
                    f"<b style='color:{theme.TEXT}'>${em['lower_boundary']:,.2f}</b> "
                    f"and <b style='color:{theme.TEXT}'>"
                    f"${em['upper_boundary']:,.2f}</b> over the next "
                    f"<b>{em['dte']} day{'s' if em['dte'] != 1 else ''}</b> "
                    f"(±${em['move_dollars']:,.2f}, based on {em['iv']:.0f}% "
                    f"{vol_label}). "
                    f"<i>This is an at-expiry figure — the chance of "
                    f"touching a boundary intraday is roughly double.</i>"
                    f"</div>",
                    unsafe_allow_html=True)

                # ── Probability table (ATR-based levels) ──
                # For each price level, the chance of REACHING (touching) it
                # before expiry. Derived from the same IV — honest model
                # probabilities, not made-up numbers.
                #
                # Levels are ±1/±2/±3 ATR from spot, using the SAME ATR(14)
                # as the Trade Plan card above — so the −2 ATR row is your
                # actual stop and the +3 ATR row is your actual target. This
                # directly stress-tests the 1.5:1 trade plan: you can see the
                # probability of your stop being hit vs your target.
                st.markdown(
                    f"<div style='font-family:Sora;font-size:0.9rem;"
                    f"font-weight:600;color:{theme.TEXT};margin:14px 0 4px 0'>"
                    f"Probability of reaching each ATR level</div>",
                    unsafe_allow_html=True)

                # Compute ATR(14) from the same history, matching the Trade
                # Plan card exactly. Fall back to an expected-move-derived
                # proxy if history is too short for ATR.
                atr_val = None
                try:
                    import trade_plan as _tp
                    import data_utils as _du
                    _hist_atr = _du.get_history(sel_ticker, days=60)
                    atr_val = _tp.compute_atr(_hist_atr, window=14)
                except Exception:
                    atr_val = None

                prob_rows = None
                atr_note = ""
                if atr_val and atr_val > 0:
                    # Build ±1/±2/±3 ATR levels
                    atr_levels = [spot_in + m * atr_val
                                   for m in (3, 2, 1, -1, -2, -3)]
                    prob_rows = pp.probability_table(
                        spot_in, iv_in, int(effective_dte), levels=atr_levels)
                    # Tag each row with its ATR multiple for display
                    if prob_rows:
                        for r in prob_rows:
                            mult = round((r["level"] - spot_in) / atr_val)
                            r["atr_mult"] = mult
                    atr_note = (f"Levels are multiples of ATR(14) = "
                                f"${atr_val:,.2f}, the same volatility measure "
                                f"the Trade Plan uses. <b style='color:"
                                f"{theme.RED}'>−2 ATR is your stop</b>, "
                                f"<b style='color:{theme.GREEN}'>+3 ATR is "
                                f"your target</b>.")
                else:
                    # ATR unavailable — fall back to expected-move ladder
                    prob_rows = pp.probability_table(
                        spot_in, iv_in, int(effective_dte))
                    atr_note = ("ATR unavailable (insufficient history); "
                                "showing expected-move levels instead.")

                # Optional custom target — user can add their own level
                custom_tgt = st.number_input(
                    "Add a custom target price ($)",
                    value=float(round(em["upper_boundary"], 2)),
                    min_value=0.01, step=1.0,
                    key=f"proj_prob_target_{sel_ticker}",
                    help="Enter any price to see its reach-probability — "
                         "e.g. a resistance level you're watching.")
                custom_touch = pp.prob_touch_before_expiry(
                    spot_in, custom_tgt, iv_in, int(effective_dte))
                custom_finish = pp.prob_finish_beyond(
                    spot_in, custom_tgt, iv_in, int(effective_dte))

                if prob_rows:
                    # Build the table: level | %from spot | touch | finish
                    has_atr = any("atr_mult" in r for r in prob_rows)
                    label_col = "ATR LEVEL" if has_atr else "PRICE LEVEL"
                    tbl = (
                        "<div style='font-family:JetBrains Mono;"
                        "font-size:0.8rem;margin-top:6px'>"
                        f"<div style='display:flex;gap:8px;padding:4px 0;"
                        f"border-bottom:1px solid {theme.BORDER};"
                        f"color:{theme.MUTED};font-size:0.7rem;"
                        f"font-weight:700'>"
                        f"<span style='flex:1.4'>{label_col}</span>"
                        f"<span style='flex:1;text-align:right'>FROM SPOT</span>"
                        f"<span style='flex:1;text-align:right'>REACH "
                        f"(touch)</span>"
                        f"<span style='flex:1;text-align:right'>CLOSE "
                        f"BEYOND</span></div>")
                    for r in prob_rows:
                        dir_color = (theme.GREEN if r["direction"] == "up"
                                      else theme.RED)
                        arrow = "↑" if r["direction"] == "up" else "↓"
                        # Label: ATR multiple + price, with stop/target tags
                        if "atr_mult" in r:
                            m = r["atr_mult"]
                            sign = "+" if m > 0 else ""
                            tag = ""
                            if m == -2:
                                tag = (f" <span style='color:{theme.RED};"
                                        f"font-size:0.62rem'>STOP</span>")
                            elif m == 3:
                                tag = (f" <span style='color:{theme.GREEN};"
                                        f"font-size:0.62rem'>TARGET</span>")
                            level_label = (
                                f"<span style='color:{theme.MUTED}'>"
                                f"{sign}{m} ATR</span> "
                                f"${r['level']:,.2f}{tag}")
                        else:
                            level_label = f"${r['level']:,.2f}"
                        tbl += (
                            f"<div style='display:flex;gap:8px;padding:5px 0;"
                            f"border-bottom:1px solid {theme.BORDER}44'>"
                            f"<span style='flex:1.4;color:{theme.TEXT};"
                            f"font-weight:600'>{level_label}</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{dir_color}'>{arrow} "
                            f"{abs(r['pct_from_spot']):.1f}%</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{theme.TEXT};font-weight:700'>"
                            f"{r['prob_touch']*100:.0f}%</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{theme.MUTED}'>"
                            f"{r['prob_finish']*100:.0f}%</span></div>")
                    # Custom target row (highlighted)
                    if custom_touch is not None and custom_finish is not None:
                        c_pct = (custom_tgt - spot_in) / spot_in * 100
                        c_dir = theme.GREEN if custom_tgt >= spot_in else theme.RED
                        c_arrow = "↑" if custom_tgt >= spot_in else "↓"
                        tbl += (
                            f"<div style='display:flex;gap:8px;padding:6px 0;"
                            f"margin-top:2px;background:{theme.PANEL_HI};"
                            f"border-radius:4px;border:1px solid "
                            f"{theme.YELLOW}55'>"
                            f"<span style='flex:1.4;color:{theme.YELLOW};"
                            f"font-weight:700'>★ ${custom_tgt:,.2f}</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{c_dir}'>{c_arrow} {abs(c_pct):.1f}%</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{theme.TEXT};font-weight:700'>"
                            f"{custom_touch*100:.0f}%</span>"
                            f"<span style='flex:1;text-align:right;"
                            f"color:{theme.MUTED}'>"
                            f"{custom_finish*100:.0f}%</span></div>")
                    tbl += "</div>"
                    st.markdown(tbl, unsafe_allow_html=True)

                    # ATR context note (which levels are stop/target)
                    if atr_note:
                        st.markdown(
                            f"<div style='font-family:JetBrains Mono;"
                            f"font-size:0.72rem;color:{theme.MUTED};"
                            f"line-height:1.5;margin-top:8px'>{atr_note}</div>",
                            unsafe_allow_html=True)

                    # Honest explainer
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.72rem;color:{theme.MUTED};"
                        f"line-height:1.5;margin-top:8px'>"
                        f"<b style='color:{theme.TEXT}'>Reach (touch)</b> = "
                        f"chance the price trades at that level at <i>any "
                        f"point</i> before expiry. <b style='color:"
                        f"{theme.TEXT}'>Close beyond</b> = chance it "
                        f"<i>finishes</i> past that level at expiry (always "
                        f"lower — touching is easier than closing through). "
                        f"<br><b style='color:{theme.YELLOW}'>These are model "
                        f"probabilities</b> under a zero-drift random walk at "
                        f"{em['iv']:.0f}% {vol_label} — NOT empirical "
                        f"frequencies or forecasts. Real markets have fat "
                        f"tails, momentum, and event risk (earnings!) the "
                        f"model ignores. Treat as rough odds, not guarantees."
                        f"</div>",
                        unsafe_allow_html=True)

        # Tab 2: Pivots
        with tab_piv:
            piv = pp.floor_pivots(hi_in, lo_in, close_in)
            if piv is None:
                st.warning("Couldn't compute pivots — check High ≥ Low and "
                            "all values positive.")
            else:
                # Central pivot prominent
                st.markdown(
                    f"<div style='text-align:center;padding:10px;"
                    f"background:{theme.PANEL_HI};border-radius:8px;"
                    f"margin-bottom:10px'>"
                    f"<div style='font-family:JetBrains Mono;"
                    f"font-size:0.72rem;color:{theme.MUTED}'>CENTRAL PIVOT "
                    f"(fair value)</div>"
                    f"<div style='font-family:Sora;font-size:1.5rem;"
                    f"font-weight:700;color:{theme.TEXT}'>"
                    f"${piv['pivot']:,.2f}</div></div>",
                    unsafe_allow_html=True)
                # R/S levels in a stack
                pv1, pv2 = st.columns(2)
                with pv1:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.8rem;line-height:1.9'>"
                        f"<span style='color:{theme.GREEN}'>R2</span> "
                        f"<b style='color:{theme.TEXT}'>${piv['r2']:,.2f}</b><br>"
                        f"<span style='color:{theme.GREEN}'>R1</span> "
                        f"<b style='color:{theme.TEXT}'>${piv['r1']:,.2f}</b>"
                        f"</div>",
                        unsafe_allow_html=True)
                with pv2:
                    st.markdown(
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.8rem;line-height:1.9'>"
                        f"<span style='color:{theme.RED}'>S1</span> "
                        f"<b style='color:{theme.TEXT}'>${piv['s1']:,.2f}</b><br>"
                        f"<span style='color:{theme.RED}'>S2</span> "
                        f"<b style='color:{theme.TEXT}'>${piv['s2']:,.2f}</b>"
                        f"</div>",
                        unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-family:JetBrains Mono;"
                    f"font-size:0.74rem;color:{theme.MUTED};line-height:1.5;"
                    f"margin-top:8px'>"
                    f"Price above the central pivot leans bullish; below "
                    f"leans bearish. R1/R2 are resistance (upside targets / "
                    f"where rallies may stall); S1/S2 are support (downside "
                    f"levels where dips may bounce). Mean-reversion traders "
                    f"watch these for reaction points."
                    f"</div>",
                    unsafe_allow_html=True)

        # Tab 3: Fibonacci
        with tab_fib:
            fib = pp.fibonacci_extensions(swing_hi_in, swing_lo_in, pullback_in)
            if fib is None:
                st.warning("Couldn't compute extensions — Swing High must "
                            "exceed Swing Low, all values positive.")
            else:
                fb1, fb2 = st.columns(2)
                with fb1:
                    st.markdown(
                        f"<div style='text-align:center;padding:12px;"
                        f"background:{theme.PANEL_HI};border-radius:8px;"
                        f"border:1px solid {theme.GREEN}55'>"
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.7rem;color:{theme.GREEN};"
                        f"font-weight:700'>TARGET 1 · 161.8%</div>"
                        f"<div style='font-family:Sora;font-size:1.4rem;"
                        f"font-weight:700;color:{theme.TEXT}'>"
                        f"${fib['ext_1618']:,.2f}</div></div>",
                        unsafe_allow_html=True)
                with fb2:
                    st.markdown(
                        f"<div style='text-align:center;padding:12px;"
                        f"background:{theme.PANEL_HI};border-radius:8px;"
                        f"border:1px solid {theme.GREEN}55'>"
                        f"<div style='font-family:JetBrains Mono;"
                        f"font-size:0.7rem;color:{theme.GREEN};"
                        f"font-weight:700'>TARGET 2 · 261.8%</div>"
                        f"<div style='font-family:Sora;font-size:1.4rem;"
                        f"font-weight:700;color:{theme.TEXT}'>"
                        f"${fib['ext_2618']:,.2f}</div></div>",
                        unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-family:JetBrains Mono;"
                    f"font-size:0.74rem;color:{theme.MUTED};line-height:1.5;"
                    f"margin-top:8px'>"
                    f"Automated momentum-exhaustion zones projected from the "
                    f"${fib['swing_low']:,.2f} → ${fib['swing_high']:,.2f} "
                    f"swing (leg ${fib['leg_size']:,.2f}). "
                    f"<b style='color:{theme.YELLOW}'>Honest caveat:</b> "
                    f"Fibonacci levels are widely watched but have no proven "
                    f"statistical edge — any value is largely self-fulfilling "
                    f"(many traders watch the same levels). Treat as "
                    f"\"zones others are watching,\" not physics."
                    f"</div>",
                    unsafe_allow_html=True)

        # ── Data diagnostics (collapsed; for debugging data-source issues) ──
        # Shows the exact runtime values from the option-chain fetch and the
        # volatility fallback chain, so you can see at a glance WHETHER the
        # data feed failed or our extraction did — instead of inferring from
        # the output. Answers questions like "is Yahoo really returning no
        # AMD IV, or are we failing to read it?"
        with st.expander("🔍 Data diagnostics (what the feed actually returned)",
                          expanded=False):
            def _fmt(v):
                return "—" if v is None else str(v)

            # Build a plain, scannable diagnostic table
            rows = [
                ("Ticker / spot", f"{diag['ticker']} @ ${_fmt(diag['spot'])}"),
                ("Horizon selected", f"{diag['horizon_label']} "
                                      f"(target {diag['target_dte']}d, "
                                      f"±{_fmt(diag['tolerance'])}d tolerance)"),
                ("Chain status", _fmt(diag["chain_status"])),
                ("Chain error", _fmt(diag["chain_error"])),
                ("Matched expiry", f"{_fmt(diag['matched_expiry'])} "
                                    f"({_fmt(diag['matched_dte'])}d out)"),
                ("Puts: rows / IV>0 / priceable",
                 f"{_fmt(diag['puts_rows'])} / {_fmt(diag['puts_iv_positive'])} "
                 f"/ {_fmt(diag['puts_price_usable'])}"),
                ("Calls: rows / IV>0",
                 f"{_fmt(diag['calls_rows'])} / {_fmt(diag['calls_iv_positive'])}"),
                ("IV from feed", f"{_fmt(diag['iv_from_feed'])}%"
                                  if diag['iv_from_feed'] else "none"),
                ("  ↳ raw median value (pre-scale)",
                 _fmt(diag['puts_iv_raw_median'])
                 if diag['puts_iv_raw_median'] is not None
                 else _fmt(diag['calls_iv_raw_median'])),
                ("IV computed from price",
                 f"{_fmt(diag['iv_from_price'])}%"
                 if diag['iv_from_price'] else "none"),
                ("Realized vol: raw → filtered",
                 f"{_fmt(diag['realized_raw'])}% → "
                 f"{_fmt(diag['realized_filtered'])}%"),
                ("  ↳ largest single-day move",
                 _fmt(diag['realized_max_day_move'])),
                ("Realized obs: total / clean",
                 f"{_fmt(diag['realized_obs_total'])} / "
                 f"{_fmt(diag['realized_obs_clean'])}"),
                ("➡ FINAL IV used",
                 f"{_fmt(diag['final_iv'])}% "
                 f"(source: {_fmt(diag['final_iv_source'])})"),
            ]
            table_html = (
                "<div style='font-family:JetBrains Mono;font-size:0.72rem;"
                f"color:{theme.MUTED};line-height:1.7'>")
            for label, val in rows:
                emphasis = ("color:" + theme.TEXT + ";font-weight:700"
                            if label.startswith("➡") else "")
                table_html += (
                    f"<div style='display:flex;justify-content:space-between;"
                    f"gap:16px;border-bottom:1px solid {theme.BORDER}44;"
                    f"padding:2px 0;{emphasis}'>"
                    f"<span>{label}</span>"
                    f"<span style='text-align:right'>{val}</span></div>")
            table_html += "</div>"
            st.markdown(table_html, unsafe_allow_html=True)

            # Interpretation hints — translate the numbers into a verdict
            st.markdown(
                f"<div style='font-family:JetBrains Mono;font-size:0.7rem;"
                f"color:{theme.MUTED};line-height:1.5;margin-top:10px;"
                f"padding-top:8px;border-top:1px solid {theme.BORDER}'>"
                f"<b style='color:{theme.TEXT}'>How to read this:</b><br>"
                f"• If <b>Chain status = ok</b> but <b>Puts IV>0 = 0</b>, "
                f"Yahoo returned the chain but with no IV data (its known "
                f"weakness) — not our bug.<br>"
                f"• If <b>priceable &gt; 0</b> but IV-from-price is none, "
                f"that's worth flagging — we should've computed it.<br>"
                f"• If <b>raw realized ≫ filtered</b>, corrupt price ticks "
                f"were inflating the estimate (the filter caught them).<br>"
                f"• If <b>Chain status ≠ ok</b>, the fetch itself failed "
                f"(rate limit, after-hours, scrape error) — try again or "
                f"check during market hours."
                f"</div>",
                unsafe_allow_html=True)


def _render_grade_card(sel_row: dict, sel_ticker: str):
    """Visual Fundamental Grade card with a 6-row pillar breakdown.

    Layout (Option A from the design conversation):
        ┌─────────────────────────────────────────────────────────┐
        │ 🟡 Grade: C    AMD                            Score 57  │
        │                                                          │
        │ Valuation       25  ▰▱▱▱▱▱▱▱▱▱  Expensive          20%  │
        │ Growth          95  ▰▰▰▰▰▰▰▰▰▰  Strong             20%  │
        │ Profitability   75  ▰▰▰▰▰▰▰▰▱▱  Wide-Moat          20%  │
        │ Cash Flow       55  ▰▰▰▰▰▰▱▱▱▱  Healthy            20%  │
        │ Balance Sheet   80  ▰▰▰▰▰▰▰▰▱▱  Healthy            10%  │
        │ Efficiency      50  ▰▰▰▰▰▱▱▱▱▱  Marginal           10%  │
        └─────────────────────────────────────────────────────────┘
    """
    grade_color  = sel_row.get("fundamental_grade_color", "#7d8aa5")
    grade        = sel_row.get("fundamental_grade", "N/A")
    grade_score  = sel_row.get("fundamental_grade_score")
    pillars      = sel_row.get("fundamental_grade_pillars", {})
    # Map of pillar_key -> bool. True if the pillar had usable yfinance data;
    # False if it defaulted to neutral 50 because the input fields were null.
    # Drives the inline "(no data)" marker and the footer summary.
    data_present = sel_row.get("fundamental_grade_pillar_data_present", {})

    # N/A short-circuit — no pillars to render
    if grade == "N/A" or not pillars:
        # IMPORTANT: build HTML on a single line. Streamlit's markdown parser
        # treats certain multi-line HTML patterns as plain text (especially
        # when CSS values wrap across lines or nesting is deep), which was
        # the root cause of the "literal HTML rendered as text" bug. Single
        # line = guaranteed correct rendering across all Streamlit versions.
        na_html = (
            f"<div style=\"background:{grade_color}1c;"
            f"border:1px solid {grade_color}55;border-radius:8px;"
            f"padding:14px 18px;margin:14px 0 8px 0;\">"
            f"<div style=\"font-family:Sora;font-weight:800;"
            f"font-size:1.15rem;color:{grade_color}\">"
            f"⚪ Grade: N/A &nbsp;"
            f"<span style=\"font-family:JetBrains Mono;font-size:0.85rem;"
            f"font-weight:400;color:#a3a8b8\">· {sel_ticker}</span>"
            f"</div>"
            f"<div style=\"font-family:JetBrains Mono;font-size:0.85rem;"
            f"color:#a3a8b8;margin-top:6px\">"
            f"Fundamentals unavailable from yfinance for this ticker."
            f"</div>"
            f"</div>"
        )
        st.markdown(na_html, unsafe_allow_html=True)
        return

    # ── header row: grade + ticker + master score ──
    emoji = {"A": "🟢", "B": "🟢", "C": "🟡",
              "D": "🟠", "E": "🔴"}.get(grade, "⚪")
    score_disp = f"{grade_score:.0f}" if grade_score is not None else "—"

    # Data source badge — show which path served the fundamentals
    # ("finnhub" → green chip, "yfinance" → grey fallback chip).
    # Helps the user see at a glance if Finnhub is healthy and serving data,
    # or if we're on yfinance fallback (typically means rate limits or the
    # Finnhub key needs rotating).
    source = sel_row.get("fundamental_source", "unknown")
    if source == "finnhub":
        src_label, src_color = "Finnhub", "#22e08a"
    elif source == "yfinance":
        src_label, src_color = "yfinance fallback", "#7d8aa5"
    else:
        src_label, src_color = source, "#7d8aa5"
    source_chip = (
        f"<span style=\"display:inline-block;margin-left:10px;"
        f"padding:1px 8px;border-radius:10px;font-size:0.7rem;"
        f"font-family:JetBrains Mono;background:{src_color}22;"
        f"color:{src_color};border:1px solid {src_color}66\">"
        f"data · {src_label}</span>"
    )

    # Build header HTML on a single line — see comment above re: parser fragility
    header = (
        f"<div style=\"display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:12px\">"
        f"<div>"
        f"<span style=\"font-family:Sora;font-weight:800;"
        f"font-size:1.25rem;color:{grade_color}\">{emoji} Grade: {grade}</span>"
        f"<span style=\"font-family:JetBrains Mono;font-size:0.92rem;"
        f"color:#a3a8b8;margin-left:14px\">· {sel_ticker}</span>"
        f"{source_chip}"
        f"</div>"
        f"<div style=\"font-family:JetBrains Mono;font-size:0.85rem;"
        f"color:#a3a8b8\">"
        f"Master Score "
        f"<span style=\"color:{grade_color};font-weight:700;font-size:1rem\">"
        f"&nbsp;{score_disp}</span>"
        f"<span style=\"color:#7d8aa5\">&nbsp;/ 100</span>"
        f"</div>"
        f"</div>"
    )

    # ── 6 pillar rows ──
    pillar_rows = []
    for key, name, weight in _PILLAR_DISPLAY:
        score = pillars.get(key, 50.0)
        # Defaulted pillars (no yfinance data) — replace the qualitative
        # band label with an honest "no data — defaulted to 50" marker, and
        # dim the row so the user can see at a glance which scores are
        # imputed vs. computed from real data.
        has_data = data_present.get(key, True)
        if has_data:
            band = _pillar_band_label(key, score)
            bar_color = _pillar_color(score)
            name_color = "#c0c5d4"
            band_color = "#a3a8b8"
            band_style = ""
        else:
            band = "no data · defaulted"
            bar_color = "#5a6378"   # muted slate for the imputed bar
            name_color = "#7d8aa5"  # muted name
            band_color = "#7d8aa5"  # muted band
            band_style = "font-style:italic;"

        # 10-segment progress bar: render as 10 boxes with `score/10` filled
        filled = int(round(score / 10))
        empty  = 10 - filled
        bar = ("<span style='color:" + bar_color + "'>" + ("▰" * filled)
               + "</span>" + "<span style='color:#3a4356'>"
               + ("▱" * empty) + "</span>")

        pillar_rows.append(
            f"<div style=\"display:grid;"
            f"grid-template-columns:130px 38px 1fr 160px 42px;"
            f"gap:10px;align-items:center;padding:4px 0;"
            f"font-family:JetBrains Mono;font-size:0.86rem\">"
            f"<span style=\"color:{name_color}\">{name}</span>"
            f"<span style=\"color:{bar_color};font-weight:700;"
            f"text-align:right\">{score:.0f}</span>"
            f"<span style=\"letter-spacing:1px\">{bar}</span>"
            f"<span style=\"color:{band_color};{band_style}\">{band}</span>"
            f"<span style=\"color:#7d8aa5;font-size:0.76rem;"
            f"text-align:right\">{weight}</span>"
            f"</div>"
        )

    # ── Footer: specific summary of which pillars defaulted ──
    # Names the ACTUAL data source that came back empty — previously this
    # hardcoded "yfinance" which was wrong once the dual-source layer was
    # wired in. Now reads the row's fundamental_source field, which is
    # either "finnhub" (primary worked but returned empty for these fields),
    # "yfinance" (primary failed entirely, fallback also returned empty),
    # or "unknown" (both paths failed).
    missing_names = [name for (key, name, _) in _PILLAR_DISPLAY
                     if not data_present.get(key, True)]
    if missing_names:
        why_hints = {
            "Valuation":     "EV/EBITDA + Forward P/E",
            "Growth":        "revenue growth + earnings growth",
            "Profitability": "gross + operating margins",
            "Cash Flow":     "free cash flow / market cap",
            "Balance Sheet": "current ratio",
            "Efficiency":    "ROE",
        }
        missing_with_hints = " · ".join(
            f"{n} ({why_hints.get(n, '?')})" for n in missing_names)
        # Source-specific language so the user knows where to look when
        # they see a lot of defaulted pillars
        src = sel_row.get("fundamental_source", "unknown")
        if src == "finnhub":
            src_blame = (
                "Finnhub returned these fields as null for this ticker "
                "(may indicate sparse Finnhub coverage on smaller-caps, "
                "ADRs, or recent IPOs — yfinance fallback was NOT engaged "
                "because Finnhub returned a valid response, just incomplete)"
            )
        elif src == "yfinance":
            src_blame = (
                "Finnhub returned nothing (key may be invalid, rate-limited, "
                "or ticker not covered) and yfinance fallback also returned "
                "null for these fields"
            )
        else:
            src_blame = (
                "neither Finnhub nor yfinance returned data — both sources "
                "appear unavailable for this ticker"
            )
        footer_text = (
            f"⚠ <b>{len(missing_names)} of 6 pillars defaulted to neutral 50.</b> "
            f"Missing: {missing_with_hints}. {src_blame}. "
            f"These pillars contributed a neutral score to the Master "
            f"Score — the grade is less informative than it looks for this "
            f"ticker."
        )
        footer_color = "#f5c344"  # yellow — informational warning
    else:
        src = sel_row.get("fundamental_source", "unknown")
        src_label = {"finnhub": "Finnhub",
                     "yfinance": "yfinance fallback"}.get(src, src)
        footer_text = (
            f"All 6 pillars computed from live data ({src_label}). Each "
            f"pillar scored 0-100 from absolute thresholds; weighted sum "
            f"mapped to A/B/C/D."
        )
        footer_color = "#7d8aa5"  # neutral grey — happy path

    card_html = (
        f"<div style=\"background:{grade_color}1c;"
        f"border:1px solid {grade_color}55;border-radius:10px;"
        f"padding:14px 20px;margin:14px 0 10px 0;\">"
        f"{header}"
        f"{''.join(pillar_rows)}"
        f"<div style=\"font-family:JetBrains Mono;font-size:0.78rem;"
        f"color:{footer_color};margin-top:12px;padding-top:10px;"
        f"border-top:1px solid #3a4356;line-height:1.5\">"
        f"{footer_text}"
        f"</div>"
        f"</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)


def _render_paper_execution(rows: list, scanner: dict):
    """Streamlined 100-share paper-trade execution from the current scan.

    Pulls entry price from the scan row (already fetched, so this is fast
    and consistent with what the user is looking at). Active strategy and
    macro_score are captured from session_state for audit context.
    """
    import db_manager as dbm

    # Build a price lookup so we can stamp the entry price without firing
    # another quote request. Skip rows missing a price (yfinance failure).
    price_by_ticker = {r["ticker"]: r.get("price")
                       for r in rows if r.get("price") is not None}
    if not price_by_ticker:
        return  # nothing executable

    strategy = st.session_state.get("strategy", "unknown")
    macro_score = scanner.get("macro_score")

    with st.expander("🟢 Paper Execution"):
        st.markdown(
            "<div class='tiny' style='margin-bottom:8px'>"
            "Execute a 100-share simulated trade against the current scan. "
            "Open positions and realized P&L are tracked on the "
            "<b>Positions</b> page."
            "</div>", unsafe_allow_html=True)

        col_t, col_d = st.columns([2, 1])
        with col_t:
            exec_ticker = st.selectbox(
                "Ticker", options=list(price_by_ticker.keys()),
                key="paper_exec_ticker")
        with col_d:
            exec_dir = st.radio(
                "Direction", options=["Long", "Short"],
                horizontal=True, key="paper_exec_direction")

        # show the entry price the trade will use
        entry_price = price_by_ticker.get(exec_ticker)
        already_open = dbm.has_open_position(exec_ticker, exec_dir)

        info_line = (
            f"<div class='tiny'>Entry price: <b>${entry_price:.2f}</b> "
            f"&middot; size: <b>100 shares</b> "
            f"&middot; strategy: <b>{strategy}</b> "
            f"&middot; macro: <b>{macro_score:.0f}</b>"
            f"</div>" if macro_score is not None else
            f"<div class='tiny'>Entry: <b>${entry_price:.2f}</b> "
            f"&middot; 100 shares &middot; {strategy}</div>"
        )
        st.markdown(info_line, unsafe_allow_html=True)

        if already_open:
            st.warning(
                f"⚠️ You already have an open **{exec_dir}** position in "
                f"**{exec_ticker}**. Close it from the Positions page before "
                f"opening another in the same direction.")

        disabled = already_open or entry_price is None
        if st.button("Execute 100 Shares", key="paper_exec_submit",
                     disabled=disabled, type="primary",
                     width="content"):
            new_id = dbm.execute_trade(
                ticker=exec_ticker, direction=exec_dir,
                entry_price=float(entry_price), quantity=100,
                strategy_engine=strategy, macro_score=macro_score)
            if new_id:
                st.success(
                    f"✅ Executed: **{exec_dir} 100 {exec_ticker}** "
                    f"@ ${entry_price:.2f} (trade #{new_id}). "
                    f"View on the Positions page.")
            else:
                st.error(
                    "Could not record trade — check that paper-trade DB "
                    "is writable, or refresh and try again.")


def _render_override_panel(rows: list, scanner: dict):
    """Manual override UI. Selecting a ticker + action + reason logs an
    override to the journal, tied to the current scan_id."""
    scan_id = st.session_state.get("current_scan_id")
    if not scan_id:
        # Scan happened before journaling was wired up — overrides won't link
        # back to a signal row, so skip the panel rather than save orphans.
        return

    import signal_journal as sj
    with st.expander("✋ Override system recommendation"):
        st.markdown(
            "<div class='tiny' style='margin-bottom:8px'>"
            "Use this when you take action different from what the system "
            "recommends. Every override is logged with your reason — Page 5 "
            "compares your override decisions to the system's track record."
            "</div>", unsafe_allow_html=True)

        col_t, col_a = st.columns([1, 1])
        with col_t:
            ticker_choice = st.selectbox(
                "Ticker", options=[r["ticker"] for r in rows],
                key="override_ticker")
        with col_a:
            action_choice = st.selectbox(
                "Your action",
                options=["MAX LONG", "MID LONG", "PILOT LONG",
                         "HOLD / CASH", "NO TRADE",
                         "PILOT SHORT", "LEAN SHORT", "MAX SHORT"],
                key="override_action")

        # show what the system said for context
        sys_row = next((r for r in rows if r["ticker"] == ticker_choice), None)
        if sys_row:
            tr = run_scanner_module().calculate_tranche_action(
                scanner.get("macro_score"), sys_row["total_score"])
            st.markdown(
                f"<div class='tiny'>System says: <b>{tr['action']}</b> "
                f"&middot; score {sys_row['total_score']} "
                f"&middot; {sys_row['status_label']}</div>",
                unsafe_allow_html=True)

        reason = st.text_input(
            "Reason (optional — strongly encouraged)",
            placeholder="e.g. 'earnings tomorrow', 'reduced size on macro doubt'",
            key="override_reason")

        if st.button("Save override", key="override_save"):
            ok = sj.log_override(scan_id, ticker_choice, action_choice,
                                  reason=reason or None)
            if ok:
                st.success(
                    f"Override saved: {ticker_choice} → {action_choice}. "
                    f"Visible in the Trade Journal (Page 5).")
            else:
                st.error("Could not save override — check journal DB access.")


def run_scanner_module():
    """Late-bound import so the override panel can reference
    calculate_tranche_action without an import cycle at module load."""
    import run_scanner
    return run_scanner


def _render_technical_chart(ticker: str):
    """1-year candlestick + VWAP + pivots, with a 20-day ROC momentum subplot."""
    import run_scanner
    from plotly.subplots import make_subplots

    st.markdown("---")
    st.markdown(f"<div class='kicker'>1-Year Technical Chart · {ticker}</div>",
                unsafe_allow_html=True)

    with st.spinner(f"Loading 1-year chart & studies for {ticker}…"):
        studies = run_scanner.get_chart_studies(ticker)

    if studies.get("status") != "ok":
        st.warning(f"Chart unavailable: {studies.get('error', 'unknown error')}")
        return

    dates = pd.to_datetime(studies["dates"])
    pivots = studies.get("pivots", {})
    LIGHT = "#e0e0e0"

    # 2 rows: price+volume panel (with secondary y for volume) / ROC subplot
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.74, 0.26], vertical_spacing=0.07,
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]])

    # ── main panel: candlesticks ──
    fig.add_trace(go.Candlestick(
        x=dates, open=studies["open"], high=studies["high"],
        low=studies["low"], close=studies["close"], name="Price",
        increasing_line_color=theme.GREEN,
        decreasing_line_color=theme.RED),
        row=1, col=1, secondary_y=False)

    # VWAP overlay
    fig.add_trace(go.Scatter(
        x=dates, y=studies["vwap"], mode="lines", name="VWAP",
        line=dict(color=theme.ACCENT2, width=1.8)),
        row=1, col=1, secondary_y=False)

    # ── color-coded daily volume bars at the bottom of the price panel ──
    volume = studies.get("volume", [])
    if volume:
        fig.add_trace(go.Bar(
            x=dates, y=volume, name="Volume",
            marker_color=studies.get("vol_colors", theme.MUTED),
            opacity=0.5, showlegend=False),
            row=1, col=1, secondary_y=True)
        # keep volume bars to the lower ~22% of the panel
        vmax = max(volume) if max(volume) > 0 else 1
        fig.update_yaxes(range=[0, vmax * 4.5], showgrid=False,
                         showticklabels=False, secondary_y=True,
                         row=1, col=1)

    # ── volume-by-price profile (horizontal bars) + POC ──
    # The profile lives on a DEDICATED axis `xaxis3` — NOT `xaxis2`, because
    # make_subplots already assigns `xaxis2` to the ROC subplot (row 2).
    # Claiming xaxis2 here was overwriting the ROC panel's date axis (which
    # is what blanked its tick labels and squashed the ROC traces).
    prof_prices = studies.get("profile_prices", [])
    prof_vols = studies.get("profile_volumes", [])
    has_profile = bool(prof_prices and prof_vols and max(prof_vols) > 0)
    if has_profile:
        fig.add_trace(go.Bar(
            x=prof_vols, y=prof_prices, orientation="h", name="Vol Profile",
            marker_color=theme.ACCENT, opacity=0.20,
            xaxis="x3", yaxis="y",          # dedicated x-axis, shared price y
            showlegend=False, hoverinfo="skip"))
        # Point of Control — the highest-volume price level
        poc = studies.get("poc")
        if poc is not None:
            fig.add_hline(
                y=poc, line=dict(color=theme.ACCENT, width=1.5, dash="solid"),
                annotation_text=f"POC {poc:.2f}",
                annotation_position="left",
                annotation_font=dict(size=9, color=theme.ACCENT),
                row=1, col=1)

    # ── pivot levels as horizontal dashed lines ──
    pivot_styles = {
        "R2": (theme.RED, "dot"),    "R1": (theme.RED, "dash"),
        "P":  (theme.YELLOW, "dash"),
        "S1": (theme.GREEN, "dash"), "S2": (theme.GREEN, "dot"),
    }
    for lvl, val in pivots.items():
        color, dash = pivot_styles.get(lvl, (theme.MUTED, "dash"))
        fig.add_hline(y=val, line=dict(color=color, width=1, dash=dash),
                      annotation_text=f"{lvl} {val:.2f}",
                      annotation_position="right",
                      annotation_font=dict(size=9, color=color),
                      row=1, col=1)

    # ── enhanced ROC momentum subplot (row 2) ──
    # ROC carries its OWN date axis (leading NaNs already dropped upstream).
    roc = studies.get("roc", [])
    roc_dates = pd.to_datetime(studies.get("roc_dates", []))
    roc_signal = studies.get("roc_signal", [])
    roc_sig_dates = pd.to_datetime(studies.get("roc_signal_dates", []))

    if len(roc) and len(roc_dates):
        # area fill: green where ROC >= 0, red where < 0 (two masked traces)
        roc_pos = [v if v >= 0 else 0 for v in roc]
        roc_neg = [v if v < 0 else 0 for v in roc]
        fig.add_trace(go.Scatter(
            x=roc_dates, y=roc_pos, mode="lines", line=dict(width=0),
            fill="tozeroy", fillcolor="rgba(34,224,138,0.25)",
            showlegend=False, hoverinfo="skip"), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=roc_dates, y=roc_neg, mode="lines", line=dict(width=0),
            fill="tozeroy", fillcolor="rgba(255,93,108,0.25)",
            showlegend=False, hoverinfo="skip"), row=2, col=1)
        # ROC line
        fig.add_trace(go.Scatter(
            x=roc_dates, y=roc, mode="lines", name="20d ROC %",
            line=dict(color=LIGHT, width=1.8)), row=2, col=1)
    # 9-day EMA signal line (dashed)
    if len(roc_signal) and len(roc_sig_dates):
        fig.add_trace(go.Scatter(
            x=roc_sig_dates, y=roc_signal, mode="lines",
            name="ROC Signal (9 EMA)",
            line=dict(color=theme.YELLOW, width=1.4, dash="dash")),
            row=2, col=1)
    # zero line + static +/-10 exhaustion/breakout thresholds
    fig.add_hline(y=0, line=dict(color=theme.MUTED, width=1, dash="dot"),
                  row=2, col=1)
    for thr in (10, -10):
        fig.add_hline(y=thr, line=dict(color=theme.MUTED, width=1,
                                       dash="dash"), row=2, col=1)

    # ── axes — explicit light text, zoomable, dated bottom axis ──
    x_start = studies["dates"][0]
    x_end = studies["dates"][-1]
    weekend_break = [dict(bounds=["sat", "mon"])]

    # price-panel y-axis
    fig.update_yaxes(title_text="Price ($)", row=1, col=1, secondary_y=False,
                     gridcolor=theme.BORDER, color=LIGHT,
                     title_font=dict(color=LIGHT), tickfont=dict(color=LIGHT),
                     fixedrange=False)
    # ROC-panel y-axis
    fig.update_yaxes(title_text="20d ROC %", row=2, col=1,
                     gridcolor=theme.BORDER, color=LIGHT,
                     title_font=dict(color=LIGHT), tickfont=dict(color=LIGHT),
                     fixedrange=False)

    # price-panel x-axis (row 1 = `xaxis`): no tick labels (ROC panel shows
    # them), but still zoomable
    fig.update_xaxes(
        row=1, col=1, gridcolor=theme.BORDER, color=LIGHT,
        rangeslider_visible=False, range=[x_start, x_end],
        rangebreaks=weekend_break, showticklabels=False, fixedrange=False)

    # ROC-panel x-axis (row 2 = `xaxis2`) — THIS is the bottom axis: it MUST
    # show the month/year date labels for the whole chart.
    fig.update_xaxes(
        row=2, col=1, gridcolor=theme.BORDER, color=LIGHT,
        tickfont=dict(color=LIGHT), range=[x_start, x_end],
        rangebreaks=weekend_break,
        showticklabels=True, tickformat="%b %Y", dtick="M1",
        fixedrange=False)

    layout_kwargs = dict(
        template="plotly_dark", barmode="overlay",
        # Lock all chart interactions: no pan, no drag-to-zoom-box, no
        # scroll-wheel zoom (the macOS two-finger trackpad scroll registers
        # as wheel events). Users can still zoom via the modebar's +/-
        # buttons and reset via autoscale. This prevents accidental view
        # changes when scrolling the page past the chart.
        dragmode=False,
        showlegend=True,
        legend=dict(orientation="h", y=1.05, x=0,
                    font=dict(size=10, color=LIGHT)))
    # dedicated, independent x-axis for the volume profile (NOT a datetime
    # axis) — overlays the price panel, never distorts the primary date range.
    if has_profile:
        layout_kwargs["xaxis3"] = dict(
            overlaying="x",          # overlay the primary price x-axis
            side="top",
            range=[max(prof_vols) * 4.2, 0],  # reversed -> bars grow from left
            showgrid=False, showticklabels=False, zeroline=False,
            fixedrange=True)         # profile axis stays fixed by design
    fig.update_layout(**layout_kwargs)

    fig = theme.plotly_layout_dark(fig, height=620)
    st.plotly_chart(fig, width="stretch",
                    config={"displayModeBar": True, "scrollZoom": False},
                    key=f"scanner_tech_chart_{ticker}")

    st.markdown(
        "<div class='tiny'>Studies: cumulative VWAP · floor pivots "
        "(P / R1 / S1 / R2 / S2) · 50-bin volume-by-price profile (independent "
        "axis) with Point of Control (POC) · color-coded daily volume bars · "
        "20-day ROC % with 9-EMA signal line and ±10 thresholds. Drag to zoom; "
        "double-click to reset. All from live yfinance daily candles.</div>",
        unsafe_allow_html=True)
