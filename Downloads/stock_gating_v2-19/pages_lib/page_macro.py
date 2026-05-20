"""
page_macro.py — Page 1: Macro Gate.

  1. Real-time price banner (instant load — fast_info, no OHLCV)
  2. On-demand "Run Full Macro Analysis" button (heavy calc gated + cached)
  3. Composite Macro Score (7 equal-weighted internal metrics) + 7 gauges
     + decoupled CNN Fear & Greed gauge (reference only)
  4. Benchmark 180-day chart (selectable: SPY/QQQ/RSP/IWM) tinted by regime
  5. "Metric Definitions" educational expander
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

import theme
import data_utils as du


# ── gauge ─────────────────────────────────────────────────────────────────────
# Layman-term labels: maps each internal signal name to a two-line HTML title
# (bold plain-English primary + small grey technical subtitle).
_GAUGE_LABELS = {
    "VIX Level":          ("Current Fear Gauge", "VIX Level"),
    "VIX Term Structure": ("Crash Warning", "VIX Term Structure"),
    "Watchlist Breadth":  ("Uptrend Health", "Watchlist Breadth"),
    "Credit Spreads":     ("Debt Market Stress", "Credit Spreads"),
    "VIX Momentum":       ("Fear Velocity", "VIX 20-Day Momentum"),
    "Factor Crowding":    ("Algorithmic Stability", "Factor Crowding"),
    "Mega-Cap Rotation":  ("Big Money Flow", "MAGS vs SPY Rotation"),
}


def _gauge_title(name: str, stale: bool = False) -> str:
    """Two-line HTML gauge title: bold layman term + grey technical subtitle.
    When stale=True, append a small amber STALE tag so the user sees this
    specific metric is being served from the last-known-good cache."""
    layman, technical = _GAUGE_LABELS.get(name, (name, ""))
    stale_tag = (" <span style='font-size:9px;color:#f5c344'>·STALE</span>"
                 if stale else "")
    return (f"<b>{layman}</b>{stale_tag}<br>"
            f"<span style='font-size:12px; color:gray'>{technical}</span>")


def _gauge(score, name=None, height=185, accent=None, stale=False):
    """
    Macro gauge dial. The layman-term title is rendered INSIDE the indicator
    (two-line HTML) with enough top margin that long names never clip.
    Uses the plotly_dark template so all text is legible on the dark theme.
    """
    color = accent or theme.factor_color(score)
    indicator_kwargs = dict(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 26, "color": color,
                          "family": "JetBrains Mono"}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1,
                     "tickcolor": theme.BORDER,
                     "tickfont": {"color": "#e0e0e0", "size": 9}},
            "bar": {"color": color, "thickness": 0.28},
            "bgcolor": theme.PANEL, "borderwidth": 0,
            "steps": [
                {"range": [0, 40],  "color": "rgba(255,93,108,0.14)"},
                {"range": [40, 70], "color": "rgba(245,195,68,0.12)"},
                {"range": [70, 100], "color": "rgba(34,224,138,0.12)"},
            ],
            "threshold": {"line": {"color": color, "width": 3},
                          "thickness": 0.8, "value": score},
        },
    )
    if name is not None:
        indicator_kwargs["title"] = {
            "text": _gauge_title(name, stale=stale),
            "font": {"color": "#e0e0e0", "family": "Sora", "size": 15},
        }

    fig = go.Figure(go.Indicator(**indicator_kwargs))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e0e0", family="Sora"),
        # generous top margin so the two-line title never clips
        margin=dict(l=16, r=16, t=66 if name else 16, b=8),
        height=height,
    )
    return fig


def _gauge_caption(label, score):
    """HTML caption (kept for the F&G gauge, which has no internal title)."""
    col = theme.factor_color(score)
    return (f"<div style='text-align:center;margin-top:-6px;margin-bottom:6px;"
            f"font-family:Sora;font-weight:600;font-size:0.82rem;"
            f"line-height:1.25;color:{col};min-height:2.2em'>{label}</div>")


def _regime_chart(history: dict, benchmark: str = "SPY"):
    """
    Benchmark price line over the trailing ~180 trading days, with the
    background shaded by the HISTORICAL macro regime in effect on each date.

    The regime segments come from `history` (macro-derived, benchmark-
    independent). The price line is fetched separately for whichever
    benchmark ticker the user selected, aligned to the regime window.
    """
    if not history or history.get("status") != "ok":
        return None

    regime_dates = pd.to_datetime(history.get("dates", []))
    if len(regime_dates) == 0:
        return None

    # fetch the selected benchmark's closes, aligned to the regime window
    bench_series = du.get_close_series(benchmark, days=620)
    if len(bench_series) == 0:
        return None
    bench_window = bench_series.reindex(regime_dates).ffill().dropna()
    if len(bench_window) == 0:
        return None
    bench_dates = bench_window.index
    bench_price = bench_window.round(2).tolist()

    fig = go.Figure()

    # historical regime background bands (vectorized segments -> add_vrect)
    fill_opacity = {"BULL REGIME": 0.16, "SIDEWAYS REGIME": 0.14, "BEAR REGIME": 0.16}
    for seg in history.get("segments", []):
        fig.add_vrect(
            x0=seg["start"], x1=seg["end"],
            fillcolor=seg["color"],
            opacity=fill_opacity.get(seg["regime"], 0.14),
            line_width=0, layer="below",
        )

    # benchmark price line on top — name + hover reflect the selected ticker
    fig.add_trace(go.Scatter(
        x=bench_dates, y=bench_price, mode="lines",
        line=dict(color="#e0e0e0", width=2), name=f"{benchmark} Price",
        hovertemplate=(f"<b>{benchmark}</b>  %{{x|%b %d}}"
                       "<br>$%{y:.2f}<extra></extra>")))

    lo = min(bench_price) * 0.97
    hi = max(bench_price) * 1.03
    fig.update_layout(
        template="plotly_dark",
        xaxis=dict(showgrid=False, color="#e0e0e0"),
        yaxis=dict(showgrid=True, gridcolor=theme.BORDER, color="#e0e0e0",
                   range=[lo, hi]),
        showlegend=True,
        legend=dict(orientation="h", y=1.06, x=0,
                    font=dict(size=10, color="#e0e0e0")),
        annotations=[dict(
            x=0.02, y=0.92, xref="paper", yref="paper",
            text="BACKGROUND = HISTORICAL MACRO REGIME",
            showarrow=False,
            font=dict(family="JetBrains Mono", size=10, color="#e0e0e0"))])
    return theme.plotly_layout_dark(fig, height=320)


def _regime_legend():
    """Small inline legend for the SPY chart's regime bands."""
    items = [("BULL REGIME", "#22e08a"), ("SIDEWAYS REGIME", "#f5c344"),
             ("BEAR REGIME", "#ff5d6c")]
    chips = "".join(
        f"<span style='display:inline-block;margin:3px 8px 3px 0;"
        f"padding:3px 12px;border-radius:6px;background:{c}26;color:{c};"
        f"font-family:JetBrains Mono;font-size:0.7rem'>{lbl}</span>"
        for lbl, c in items)
    return f"<div>{chips}</div>"


def _score_history_snapshots(history: dict, n_months: int = 4) -> pd.DataFrame:
    """
    Sample the historical Composite Macro Score at month-end checkpoints over
    the past `n_months`, plus the latest value, for the recent-history table.

    Returns columns: As-of, Score, Regime, Δ vs prev (and a hidden color).
    """
    if not history or history.get("status") != "ok":
        return pd.DataFrame()
    dates = pd.to_datetime(history.get("dates", []))
    scores = history.get("composite", [])
    if len(dates) == 0 or len(scores) == 0:
        return pd.DataFrame()

    s = pd.Series(scores, index=dates).sort_index()

    # month-end snapshots over the available window
    monthly = s.resample("ME").last().dropna()
    if monthly.empty:
        return pd.DataFrame()
    monthly = monthly.iloc[-n_months:]

    # append the most recent value as "Today" if it's after the last month-end
    rows_idx = list(monthly.index)
    rows_val = list(monthly.values)
    last_dt, last_val = s.index[-1], float(s.iloc[-1])
    if not rows_idx or last_dt > rows_idx[-1]:
        rows_idx.append(last_dt)
        rows_val.append(last_val)

    def _regime(v):
        if v >= 70:
            return "🟢 BULL REGIME", "#22e08a"
        if v >= 40:
            return "🟡 SIDEWAYS REGIME", "#f5c344"
        return "🔴 BEAR REGIME", "#ff5d6c"

    rows = []
    prev = None
    for i, (d, v) in enumerate(zip(rows_idx, rows_val)):
        regime, _ = _regime(v)
        label = "Today" if i == len(rows_idx) - 1 and d == last_dt else \
                d.strftime("%b %Y")
        delta = "—" if prev is None else f"{(v - prev):+.0f}"
        rows.append({"As-of": label,
                     "Score": int(round(v)),
                     "Regime": regime,
                     "Δ vs prev": delta})
        prev = v
    return pd.DataFrame(rows)


def _render_strategy_recommendation(macro_score):
    """
    Regime-aware strategy recommendation block. Shows BULL / SIDEWAYS / BEAR
    title plus primary/secondary engine guidance using a colored alert box.
    """
    if macro_score is None:
        return

    if macro_score >= 70:
        title = "Market Environment: BULL REGIME"
        body = (
            "**Primary Engine:** Trend-Following — high-conviction breakouts "
            "and momentum continuation.  \n"
            "**Secondary Engine:** Mean Reversion — buying the dip on quality "
            "pullbacks.")
        st.success(f"### 🟢 {title}\n\n{body}")
    elif macro_score >= 40:
        title = "Market Environment: SIDEWAYS REGIME"
        body = (
            "**Primary Engine:** Mean Reversion — range-trading the chop.  \n"
            "**Secondary Engine:** Trend-Following — only for A+ relative "
            "strength setups or via trailing stops.")
        st.warning(f"### 🟡 {title}\n\n{body}")
    else:
        title = "Market Environment: BEAR REGIME"
        body = (
            "**Primary Engine:** Mean Reversion — fading extreme "
            "over-extensions and volatility spikes.  \n"
            "**Secondary Engine:** Cash / Strict Defense — standard "
            "trend-following breakouts will fail.")
        st.error(f"### 🔴 {title}\n\n{body}")


def _render_score_history_table(history: dict):
    """Render the recent monthly snapshots of the Composite Macro Score."""
    df = _score_history_snapshots(history, n_months=4)
    if df.empty:
        st.markdown(
            "<div class='tiny'>Recent history will appear here once the macro "
            "analysis has been run.</div>", unsafe_allow_html=True)
        return

    st.markdown(
        "<div class='kicker' style='margin-top:14px'>"
        "Recent Composite Macro Score · Past 4 Months</div>",
        unsafe_allow_html=True)

    col_config = {
        "As-of": st.column_config.TextColumn("As-of", width="small"),
        "Score": st.column_config.NumberColumn("Score", format="%d",
                                                width="small"),
        "Regime": st.column_config.TextColumn("Regime", width="medium"),
        "Δ vs prev": st.column_config.TextColumn("Δ vs prev", width="small",
            help="Change in score vs the previous snapshot (in points)"),
    }
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config=col_config)
    st.markdown(
        "<div class='tiny' style='margin-top:6px'>Month-end checkpoints "
        "plus today's reading, both pulled from the same 180-day historical "
        "engine that drives the regime chart below.</div>",
        unsafe_allow_html=True)


# ── price banner ──────────────────────────────────────────────────────────────
def _render_price_banner(watchlist):
    # Detect whether the live quotes fetch fell back to last-known-good cache
    # so we can mark the banner stale instead of showing yesterday's prices
    # without any warning.
    info = getattr(du, "LAST_FALLBACK_INFO", {}) or {}
    quotes_stale = (info.get("used") and isinstance(info.get("key"), str)
                    and info["key"].startswith("quotes::"))

    if quotes_stale:
        fetched = info.get("fetched_at") or "unknown"
        try:
            fetched = datetime.fromisoformat(fetched).strftime("%b %d %H:%M")
        except Exception:
            pass
        st.markdown(
            f"<div class='kicker'>Live Watchlist Prices "
            f"<span style='color:#f5c344'>· STALE (last good: {fetched})</span>"
            f"</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='kicker'>Live Watchlist Prices</div>",
                    unsafe_allow_html=True)

    if not watchlist:
        st.info("Watchlist is empty — add tickers in the sidebar.")
        return

    quotes = du.get_live_quotes(tuple(watchlist))

    # render in rows of up to 6 metrics
    for start in range(0, len(watchlist), 6):
        chunk = watchlist[start:start + 6]
        cols = st.columns(len(chunk))
        for col, t in zip(cols, chunk):
            q = quotes.get(t, {})
            with col:
                if q.get("status") == "ok" and q.get("price") is not None:
                    price = f"${q['price']:,.2f}"
                    chg = q.get("change_pct")
                    delta = f"{chg:+.2f}%" if chg is not None else None
                    st.metric(label=t, value=price, delta=delta)
                else:
                    st.metric(label=t, value="—", delta=None)
    caption = ("Quotes via yfinance fast_info — current price &amp; daily "
               "change only (no historical download).")
    if quotes_stale:
        caption += (" <b style='color:#f5c344'>Currently serving last-known-good "
                    "quotes — yfinance is rate-limited.</b>")
    st.markdown(f"<div class='tiny'>{caption}</div>", unsafe_allow_html=True)


# ── definitions expander ──────────────────────────────────────────────────────
# Each metric: (title, plain-English description, red-light condition,
#               green-light condition, low-end label, high-end label)
_METRIC_DEFS = [
    ("VIX Level", "The Fear Gauge",
     "Shows how worried investors are right now.",
     "The VIX spikes above 30 (Panic).",
     "The VIX drops below 15 (Complacency / Calm).",
     "VIX > 30", "VIX < 15"),
    ("VIX Term Structure", "Short-Term vs Long-Term Fear",
     "Compares panic today versus panic expected in the future using a ratio.",
     "The ratio hits 1.15 or higher (Immediate market shock / Backwardation).",
     "The ratio sits at 0.85 or lower (Normal conditions / Contango).",
     "Ratio ≥ 1.15", "Ratio ≤ 0.85"),
    ("Watchlist Breadth", "Team Participation",
     "What percentage of your specific stocks are trading above their "
     "200-day moving average.",
     "30% or fewer of your stocks are above their average "
     "(Fragile / Failing trend).",
     "80% or more of your stocks are above their average "
     "(Broad, healthy rally).",
     "≤ 30% above", "≥ 80% above"),
    ("Credit Spreads", "The Bond Market Warning",
     "Measures if lenders are nervous about corporate defaults using a "
     "historical Z-score.",
     "Spreads widen by 2 standard deviations (+2 Z-score = Bond market fear).",
     "Spreads are exceptionally tight (-2 Z-score = Bond market trust).",
     "+2 Z-score", "-2 Z-score"),
    ("VIX Momentum", "Volatility Velocity",
     "Tracks how fast fear is rising: the 20-day rate of change of the VIX. "
     "Rapidly accelerating volatility usually precedes equity selloffs.",
     "VIX has surged +50% over 20 days (fear accelerating into panic).",
     "VIX has dropped -30% over 20 days (fear receding into calm).",
     "+50% VIX ROC", "-30% VIX ROC"),
    ("Factor Crowding", "The Ticking Time Bomb",
     "Checks if Wall Street trading computers are crowded into the same "
     "trades using correlation metrics.",
     "Correlation hits -0.8 (Extreme crowding / Reversal risk).",
     "Correlation is at +0.3 (Normal, healthy positioning).",
     "Corr -0.8", "Corr +0.3"),
    ("Mega-Cap Rotation", "Following the Big Money",
     "Tracks the rate of change of the MAGS ETF compared to the broader "
     "SPY index.",
     "The MAGS ETF is actively underperforming SPY "
     "(Institutions pulling money out of mega-caps).",
     "The MAGS ETF is heavily outperforming SPY "
     "(Institutions aggressively buying mega-caps).",
     "MAGS lagging", "MAGS leading"),
]


def _metric_card(title, subtitle, desc, red_cond, green_cond,
                 low_label, high_label):
    """Render one metric definition with a red→green visual scale."""
    return f"""
    <div style='background:{theme.PANEL};border:1px solid {theme.BORDER};
         border-radius:12px;padding:16px 18px;margin-bottom:12px'>
      <div style='font-family:Sora;font-weight:800;font-size:1rem;
           color:{theme.TEXT}'>
        {title}
        <span style='color:{theme.MUTED};font-weight:600;font-size:0.82rem'>
          · {subtitle}</span>
      </div>
      <div class='tiny' style='margin-top:5px;color:{theme.MUTED};
           font-size:0.8rem'>{desc}</div>

      <!-- red -> green gradient scale -->
      <div style='margin-top:12px;height:9px;border-radius:5px;
           background:linear-gradient(90deg,{theme.RED} 0%,
           {theme.YELLOW} 50%,{theme.GREEN} 100%)'></div>
      <div style='display:flex;justify-content:space-between;margin-top:4px'>
        <span style='font-family:JetBrains Mono;font-size:0.66rem;
              color:{theme.RED}'>● 0 · {low_label}</span>
        <span style='font-family:JetBrains Mono;font-size:0.66rem;
              color:{theme.GREEN}'>{high_label} · 100 ●</span>
      </div>

      <!-- red / green light conditions -->
      <div style='display:flex;gap:10px;margin-top:12px'>
        <div style='flex:1;background:{theme.RED}14;border:1px solid {theme.RED}44;
             border-radius:8px;padding:8px 10px'>
          <div style='font-family:JetBrains Mono;font-size:0.66rem;
               color:{theme.RED};font-weight:700'>🔴 0 SCORE · RED LIGHT</div>
          <div class='tiny' style='margin-top:3px;font-size:0.74rem'>
            {red_cond}</div>
        </div>
        <div style='flex:1;background:{theme.GREEN}14;
             border:1px solid {theme.GREEN}44;border-radius:8px;
             padding:8px 10px'>
          <div style='font-family:JetBrains Mono;font-size:0.66rem;
               color:{theme.GREEN};font-weight:700'>
               🟢 100 SCORE · GREEN LIGHT</div>
          <div class='tiny' style='margin-top:3px;font-size:0.74rem'>
            {green_cond}</div>
        </div>
      </div>
    </div>
    """


def _render_definitions():
    with st.expander("📖 Metric Definitions"):
        st.markdown(
            f"<div class='tiny' style='margin-bottom:10px'>"
            f"The <b>Composite Macro Score</b> is the equal-weighted average of "
            f"these 7 internal metrics. Each is scored 0–100 on a red-light → "
            f"green-light scale. The CNN Fear &amp; Greed Index is shown "
            f"alongside for reference but is <b>excluded</b> from the score."
            f"</div>", unsafe_allow_html=True)
        for d in _METRIC_DEFS:
            st.markdown(_metric_card(*d), unsafe_allow_html=True)


# ── main ──────────────────────────────────────────────────────────────────────
def render():
    watchlist = st.session_state.get("watchlist", [])

    st.markdown("<div class='kicker'>PAGE 1 · MACRO GATE</div>",
                unsafe_allow_html=True)
    st.markdown("# Capital Deployment Gate")
    st.markdown(
        "<div class='tiny' style='margin-bottom:16px'>"
        "Should I be deploying capital right now?</div>",
        unsafe_allow_html=True)

    # ── staleness banner (visible only when last-known-good cache was used) ──
    info = getattr(du, "LAST_FALLBACK_INFO", {}) or {}
    if info.get("used"):
        fetched = info.get("fetched_at") or "unknown"
        # render a friendlier timestamp if it parses
        try:
            fetched = datetime.fromisoformat(fetched).strftime(
                "%b %d, %Y %H:%M")
        except Exception:
            pass
        st.warning(
            f"⚠️ **Showing last-known-good data from {fetched}** — yfinance "
            f"is rate-limited or unreachable right now. All values displayed "
            f"are real prior results, not placeholders. Retry in a few "
            f"minutes for fresh data.")

    # ── 1. price banner (instant) ──
    _render_price_banner(watchlist)
    st.markdown("---")

    # ── 2. on-demand analysis button ──
    if st.button("▶  Run Full Macro Analysis", use_container_width=True):
        import run_macro_gate
        from macro_signals import macro_history
        # reset the staleness flag so a successful retry clears the banner
        du.reset_fallback_info()
        with st.spinner("Running 7 macro signals + Fear & Greed…"):
            st.session_state.macro_result = run_macro_gate.run(watchlist)
        with st.spinner("Building 180-day historical regime timeline…"):
            st.session_state.macro_history = macro_history.regime_timeseries(
                watchlist)
        # scanner/backtest depend on macro — clear stale downstream results
        st.session_state.scanner_result = None
        st.session_state.backtest_cache = {}

    result = st.session_state.get("macro_result")
    if result is None:
        st.markdown(
            "<div class='panel' style='text-align:center;padding:50px'>"
            "<div style='font-size:2.4rem'>🛰️</div>"
            "<div class='kicker' style='margin-top:10px'>"
            "Click ‘Run Full Macro Analysis’ to compute the deployment gate</div>"
            "</div>", unsafe_allow_html=True)
        return

    composite = result["composite_score"]
    regime = result["regime"]
    color = result["color"]
    signals = result["signals"]
    fg = result.get("fear_greed", {})
    ts = datetime.fromisoformat(result["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

    # ── 3. composite score + decoupled F&G + SPY chart ──
    c_score, c_fg = st.columns([1, 1], gap="large")

    with c_score:
        st.markdown(
            f"""
            <div class='panel' style='text-align:center;border-color:{color}55;
                 background:linear-gradient(160deg,{theme.PANEL},#0d1320)'>
              <div class='kicker'>Master Composite Macro Score</div>
              <div class='big-score' style='font-size:5rem;color:{color};
                   margin:8px 0'>{composite:.0f}</div>
              <div class='regime-pill' style='background:{color}22;color:{color}'>
                {regime}</div>
              <div class='tiny' style='margin-top:12px'>
                Scanner: {"ENABLED" if result["scanner_enabled"] else
                          "DISABLED (defensive)"} ·
                7 equal-weighted internal metrics
              </div>
            </div>
            """, unsafe_allow_html=True)

    with c_fg:
        fg_score = fg.get("score", 50)
        fg_ok = fg.get("status") == "ok"
        # F&G is "fear (red) -> greed (green)" — use score-color directly
        fg_color = theme.score_color(fg_score) if fg_ok else theme.MUTED
        st.markdown("<div class='kicker'>CNN Fear &amp; Greed Index · "
                    "Reference Only (not in score)</div>",
                    unsafe_allow_html=True)
        st.plotly_chart(_gauge(fg_score, height=210, accent=fg_color),
                        use_container_width=True,
                        config={"displayModeBar": False},
                        key="macro_gauge_fear_greed")
        st.markdown(
            "<div style='text-align:center;margin-top:-8px;font-family:Sora;"
            f"font-weight:600;font-size:0.85rem;color:{fg_color}'>"
            "Fear ↔ Greed</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='tiny' style='text-align:center'>{fg.get('detail','—')}"
            f"</div>", unsafe_allow_html=True)

    st.markdown(
        f"<div class='tiny' style='padding:4px 6px'>{result['message']} · "
        f"Last run: {ts}</div>", unsafe_allow_html=True)

    # ── regime-aware strategy recommendation (BULL / SIDEWAYS / BEAR) ──
    _render_strategy_recommendation(result.get("composite_score"))

    # ── recent monthly snapshots of the Composite Macro Score ──
    _render_score_history_table(st.session_state.get("macro_history"))

    st.markdown("---")

    # ── 7 internal signal gauges ──
    # The layman-term title is rendered inside each gauge (two-line HTML); a
    # unique `key` on every st.plotly_chart call prevents StreamlitDuplicate
    # ElementId conflicts between the many gauges on this page.
    st.markdown("<div class='kicker'>7 Internal Macro Signals</div>",
                unsafe_allow_html=True)
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    names = list(signals.keys())

    # Map each macro metric to the yfinance tickers it relies on, so we can
    # mark a specific gauge stale when ONLY its data source fell back.
    _METRIC_TICKERS = {
        "VIX Level":          ["^VIX"],
        "VIX Term Structure": ["^VIX", "^VIX3M"],
        "Watchlist Breadth":  [],   # depends on the user's tickers — checked separately
        "Credit Spreads":     ["HYG", "TLT"],
        "VIX Momentum":       ["^VIX"],
        "Factor Crowding":    [],   # S&P sample — too many tickers to track individually
        "Mega-Cap Rotation":  ["MAGS", "SPY"],
    }

    def _metric_is_stale(name: str) -> bool:
        for tk in _METRIC_TICKERS.get(name, []):
            if du.fallback_for_ticker(tk):
                return True
        return False

    def _render_gauge_cell(name):
        sig = signals[name]
        st.plotly_chart(
            _gauge(sig["score"], name=name, height=185,
                   stale=_metric_is_stale(name)),
            use_container_width=True,
            config={"displayModeBar": False},
            key=f"macro_gauge_{name.replace(' ', '_').replace('/', '_')}")

    # row 1: first 4 signals
    row1 = st.columns(4, gap="medium")
    for i in range(min(4, len(names))):
        with row1[i]:
            _render_gauge_cell(names[i])

    # row 2: remaining 3 signals — render in a 4-col grid (last cell blank)
    # so each gauge keeps the SAME width as row 1 instead of stretching wide.
    row2 = st.columns(4, gap="medium")
    for i in range(4, len(names)):
        with row2[i - 4]:
            _render_gauge_cell(names[i])

    # ── 4. definitions expander ──
    _render_definitions()

    st.markdown("---")

    # ── benchmark regime chart with 180-day historical regime shading ──
    st.markdown("<div class='kicker'>Benchmark · 180-Day Trend · "
                "Historical Macro Regime</div>", unsafe_allow_html=True)

    # dynamic benchmark selector
    benchmark = st.selectbox(
        "Compare Against Benchmark:",
        options=["SPY", "QQQ", "RSP", "IWM"],
        index=0,
        key="benchmark_selector")

    # benchmark definitions
    with st.expander("ℹ️ Benchmark Definitions"):
        st.markdown("""
- **SPY (S&P 500):** The standard market baseline. Represents the 500 largest
  U.S. companies, heavily weighted toward mega-caps.
- **QQQ (Nasdaq 100):** The tech baseline. Highly concentrated in technology
  and high-growth stocks. Best used if your scanner targets high-beta tech
  breakouts.
- **RSP (S&P 500 Equal Weight):** The "lie detector." The exact same 500
  companies as SPY, but every stock gets an equal 0.2% weight. Use this to
  check market breadth (i.e., if SPY is hitting highs but RSP is crashing, the
  rally is a trap).
- **IWM (Russell 2000):** The liquidity baseline. Represents 2,000 small-cap
  U.S. companies. Highly sensitive to interest rates and domestic economic
  health.
""")

    history = st.session_state.get("macro_history")
    regime_fig = _regime_chart(history, benchmark)
    if regime_fig:
        st.plotly_chart(regime_fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key="macro_regime_chart")
        st.markdown(_regime_legend(), unsafe_allow_html=True)
        metrics_used = (history or {}).get("metrics_used", [])
        st.markdown(
            f"<div class='tiny'>White line = <b>{benchmark}</b> price. "
            f"Background bands show the Composite Macro Score regime on each "
            f"past date — recomputed daily across {len(metrics_used)} metrics "
            f"over the trailing 180 trading days.</div>",
            unsafe_allow_html=True)
    elif history and history.get("status") == "error":
        st.warning(f"Historical regime chart unavailable: "
                   f"{history.get('error', 'unknown error')}")
    else:
        st.warning(f"{benchmark} chart data unavailable.")

    st.markdown("---")

    # ── signal breakdown ──
    st.markdown("<div class='kicker'>Signal Breakdown</div>",
                unsafe_allow_html=True)
    weights = result["weights"]
    for name in names:
        sig = signals[name]
        s = sig["score"]
        col = theme.factor_color(s)
        ok = sig.get("status") == "ok"
        dot = theme.GREEN if ok else theme.RED
        w = weights.get(name, 0)
        st.markdown(
            f"""
            <div class='signal-row' style='border-left-color:{col}'>
              <div style='display:flex;justify-content:space-between;
                   align-items:center'>
                <div><span class='dot' style='background:{dot}'></span>
                  <b>{name}</b>
                  <span class='tiny' style='margin-left:8px'>
                    weight {w*100:.1f}%</span></div>
                <div class='mono' style='font-size:1.3rem;color:{col};
                     font-weight:700'>{s:.0f}</div>
              </div>
              <div class='tiny' style='margin-top:5px'>{sig.get("detail","—")}</div>
            </div>
            """, unsafe_allow_html=True)
