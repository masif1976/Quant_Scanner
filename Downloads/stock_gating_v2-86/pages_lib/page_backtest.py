"""
page_backtest.py — Page 3: Visual Backtest & Audit.

  - Ticker dropdown (active watchlist)
  - Main chart: 12-month price line with status-shaded background zones
  - Sub-chart: composite scanner score (0-100) over 12 months
  - Performance table: avg 20d forward return by status, best/worst signal
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

import theme


def _backtest_chart(bt: dict):
    """Two stacked charts: price w/ status shading, and the score trend."""
    dates = pd.to_datetime(bt["dates"])
    price = bt["price"]
    score = bt["score"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.66, 0.34], vertical_spacing=0.07,
        subplot_titles=("", ""),
    )

    # ── status background shading (top chart) ──
    for seg in bt["segments"]:
        fig.add_vrect(
            x0=seg["start"], x1=seg["end"],
            fillcolor=seg["color"], opacity=0.16,
            line_width=0, row=1, col=1,
        )

    # price line
    fig.add_trace(go.Scatter(
        x=dates, y=price, mode="lines",
        line=dict(color=theme.TEXT, width=2),
        name="Price", hovertemplate="%{x|%b %d}<br>$%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    # ── score line (bottom chart), colored by tier bands ──
    fig.add_trace(go.Scatter(
        x=dates, y=score, mode="lines",
        line=dict(color=theme.ACCENT, width=2),
        name="Score", hovertemplate="%{x|%b %d}<br>score %{y:.0f}<extra></extra>",
    ), row=2, col=1)

    # directional-bias reference bands on the score chart
    for lo, hi, col in [(80, 100, theme.GREEN), (65, 80, "#7fd98a"),
                        (50, 65, theme.YELLOW), (35, 50, theme.ORANGE),
                        (20, 35, "#ff7a6c"), (0, 20, theme.RED)]:
        fig.add_hrect(y0=lo, y1=hi, fillcolor=col, opacity=0.07,
                      line_width=0, row=2, col=1)

    fig.update_yaxes(title_text="Price ($)", row=1, col=1,
                     gridcolor=theme.BORDER, color=theme.MUTED)
    fig.update_yaxes(title_text="Score", range=[0, 100], row=2, col=1,
                     gridcolor=theme.BORDER, color=theme.MUTED)
    fig.update_xaxes(gridcolor=theme.BORDER, color=theme.MUTED, row=1, col=1)
    fig.update_xaxes(gridcolor=theme.BORDER, color=theme.MUTED, row=2, col=1)

    fig.update_layout(showlegend=False)
    return theme.plotly_layout_dark(fig, height=560)


def render():
    st.markdown("<div class='kicker'>PAGE 3 · VISUAL BACKTEST & AUDIT</div>",
                unsafe_allow_html=True)
    st.markdown("# Signal Backtest")
    st.markdown(
        "<div class='tiny' style='margin-bottom:16px'>"
        "Is the analysis correct? How did the scanner signals perform over the "
        "last 12 months?</div>", unsafe_allow_html=True)

    macro = st.session_state.get("macro_result")

    if macro is None:
        st.info("Run the analysis from the sidebar — MarketSense must clear first.")
        return

    # Defensive regime disables pages 2 & 3
    if not macro.get("scanner_enabled", True):
        st.markdown(
            f"""
            <div class='panel' style='text-align:center;padding:46px;
                 border-color:{theme.RED}55'>
              <div style='font-size:2.6rem'>🛡️</div>
              <div class='regime-pill' style='background:{theme.RED}22;
                   color:{theme.RED};margin-top:12px'>BACKTEST DISABLED</div>
              <div class='tiny' style='margin-top:14px'>
                Macro composite score is
                <b>{macro['composite_score']:.0f}</b> — BEAR REGIME.<br>
                Pages 2 &amp; 3 are gated off until macro conditions improve.
              </div>
            </div>
            """, unsafe_allow_html=True)
        return

    watchlist = st.session_state.get("watchlist", [])
    if not watchlist:
        st.warning("Watchlist is empty — add tickers in the sidebar.")
        return

    strategy = st.session_state.get("strategy", "Trend-Following")
    st.markdown(
        f"<div class='tiny' style='margin-bottom:10px'>Active engine: "
        f"<b style='color:{theme.ACCENT}'>{strategy}</b> — the backtest replays "
        f"this strategy across 252 trading days.</div>",
        unsafe_allow_html=True)

    # ── ticker selector ──
    col_sel, col_run = st.columns([3, 1])
    with col_sel:
        ticker = st.selectbox("Select a ticker to audit", watchlist,
                              label_visibility="collapsed")
    with col_run:
        run_bt = st.button("▶  RUN BACKTEST", width="stretch")

    # cache per (ticker, strategy) so a strategy switch recomputes
    if "backtest_cache" not in st.session_state:
        st.session_state.backtest_cache = {}
    cache_key = f"{ticker}::{strategy}"

    if run_bt:
        import run_backtest
        with st.spinner(f"Reconstructing 12 months of {strategy} signals "
                        f"for {ticker}…"):
            st.session_state.backtest_cache[cache_key] = run_backtest.run(
                ticker, strategy,
                macro_history=st.session_state.get("macro_history"))

    bt = st.session_state.backtest_cache.get(cache_key)

    if bt is None:
        st.markdown(
            "<div class='panel' style='text-align:center;padding:50px'>"
            "<div style='font-size:2.4rem'>📊</div>"
            f"<div class='kicker' style='margin-top:10px'>"
            f"Click RUN BACKTEST to audit {ticker}</div></div>",
            unsafe_allow_html=True)
        return

    if bt.get("status") != "ok":
        st.error(f"Backtest failed: {bt.get('error', 'unknown error')}")
        return

    # ── current snapshot ──
    cur_status = bt["current_status"]
    cur_score = bt["current_score"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Ticker", bt["ticker"])
    c2.metric("Latest Backtest Score", f"{cur_score:.0f}")
    c3.metric("Latest Status", cur_status)

    st.markdown("---")

    # ── main + sub chart ──
    st.markdown("<div class='kicker'>12-Month Price & Score — "
                "Background Shaded by Status</div>", unsafe_allow_html=True)
    st.plotly_chart(_backtest_chart(bt), width="stretch",
                    config={"displayModeBar": False})

    # conviction-tier legend
    legend = [("🟢 HIGH CONVICTION", theme.GREEN),
              ("🟢 TRADABLE",         "#7fd98a"),
              ("🟡 NEUTRAL",          theme.YELLOW),
              ("🟠 CAUTION",          theme.ORANGE),
              ("🔴 AVOID / SHORT",    theme.RED)]
    chips = "".join(
        f"<span style='display:inline-block;margin:3px 6px 3px 0;"
        f"padding:5px 14px;border-radius:6px;background:{c}22;color:{c};"
        f"font-family:JetBrains Mono;font-size:0.85rem'>{lbl}</span>"
        for lbl, c in legend)
    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)

    st.markdown("---")

    # ── performance table ──
    st.markdown("<div class='kicker'>Performance Audit — "
                f"Avg {bt['fwd_window']}-Day Forward Return by Status</div>",
                unsafe_allow_html=True)
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    buckets = bt["performance"]["buckets"]
    perf_rows = []
    for label, b in buckets.items():
        # Sample size flagging: rows with very few days are not statistically
        # meaningful. Append a visual note so users don't read a -4% return
        # over 2 days as "the model is broken at this tier."
        n = b["days"]
        if n == 0:
            stat_note = " (no signals)"
        elif n < 10:
            stat_note = f" ⚠ n={n}, too few"
        elif n < 30:
            stat_note = f" ⚠ n={n}, low confidence"
        else:
            stat_note = ""

        perf_rows.append({
            "Status": label,
            "Days in Status": n,
            f"Avg {bt['fwd_window']}d Fwd Return": (
                f"{b['avg_fwd_ret']:+.2f}%{stat_note}"
                if b["avg_fwd_ret"] is not None
                else "—"),
            "Win Rate": (f"{b['win_rate']:.0f}%"
                         if b["win_rate"] is not None else "—"),
        })
    perf_df = pd.DataFrame(perf_rows)

    # Note above the table so the warnings have context
    low_sample_count = sum(1 for b in buckets.values()
                            if 0 < b["days"] < 30)
    if low_sample_count:
        st.markdown(
            f"<div class='tiny' style='margin-bottom:6px;color:{theme.YELLOW}'>"
            f"⚠ {low_sample_count} tier(s) have fewer than 30 observations — "
            f"those forward-return averages are statistically noisy. "
            f"Rule of thumb: <b>n &lt; 10</b> is too few to interpret; "
            f"<b>10 ≤ n &lt; 30</b> is low confidence; <b>n ≥ 30</b> starts "
            f"becoming meaningful."
            f"</div>", unsafe_allow_html=True)

    def _style_status(val):
        s = str(val)
        if "HIGH CONVICTION" in s: return f"color:{theme.GREEN};font-weight:700"
        if "TRADABLE" in s:        return "color:#7fd98a;font-weight:700"
        if "NEUTRAL" in s:         return f"color:{theme.YELLOW};font-weight:700"
        if "CAUTION" in s:         return f"color:{theme.ORANGE};font-weight:700"
        if "AVOID" in s:           return f"color:{theme.RED};font-weight:700"
        return ""

    styled = perf_df.style.map(_style_status, subset=["Status"])
    st.dataframe(styled, width="stretch", hide_index=True)

    # LONG vs SHORT signal edge callout
    perf = bt["performance"]
    edge = perf["long_vs_short_edge"]
    long_ret = perf["long_avg"]
    short_ret = perf["short_avg"]
    if edge is not None:
        edge_color = theme.GREEN if edge > 0 else theme.RED
        st.markdown(
            f"""
            <div class='panel' style='border-color:{edge_color}55'>
              <div class='kicker'>Signal Edge — LONG vs SHORT</div>
              <div style='margin-top:8px;font-size:0.9rem'>
                When the scanner flagged <b style='color:{theme.GREEN}'>LONG</b>
                ({perf['long_days']} days), the {bt['fwd_window']}-day forward
                return averaged <b style='color:{theme.GREEN}'>
                {long_ret:+.2f}%</b>.
                When it flagged <b style='color:{theme.RED}'>SHORT</b>
                ({perf['short_days']} days), it averaged
                <b style='color:{theme.RED}'>{short_ret:+.2f}%</b>.
                <br>Directional edge: <b class='mono' style='color:{edge_color};
                font-size:1.1rem'>{edge:+.2f} pp</b>
                <span class='tiny'>(positive = LONG signals outperformed SHORT
                signals — the system has directional skill)</span>
              </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown(
            "<div class='tiny'>LONG vs SHORT edge unavailable — the ticker did "
            "not trigger both signal groups within the 12-month window.</div>",
            unsafe_allow_html=True)

    st.markdown("---")

    # ── best / worst signal ──
    st.markdown("<div class='kicker'>Best & Worst Signal Days</div>",
                unsafe_allow_html=True)
    # IMPORTANT context: these are single-observation cards (n=1 each). A
    # "Best Signal" with a negative forward return is NOT evidence the model
    # is broken — it's an anecdote. Tier-level stats in the Performance
    # Audit table above are what determine real edge.
    st.markdown(
        "<div class='tiny' style='margin-bottom:8px;color:#a3a8b8'>"
        "⚠ These are <b>single-observation snapshots</b> (n=1 each). The "
        "score is the model's <b>input</b> on that day; the forward return "
        "is what the market <b>actually did</b> over the next "
        f"{bt['fwd_window']} days. They can — and often do — disagree on "
        "any single day. Use the <b>Performance Audit</b> table above for "
        "statistically meaningful tier-level edge."
        "</div>", unsafe_allow_html=True)
    bcol, wcol = st.columns(2)
    best, worst = bt["best_signal"], bt["worst_signal"]

    # how many days were in the same tier as the best signal, for context
    buckets = bt["performance"]["buckets"]
    best_tier_n = buckets.get(best["status"], {}).get("days", 0)
    worst_tier_n = buckets.get(worst["status"], {}).get("days", 0)

    with bcol:
        fr = (f"{best['fwd_ret']:+.2f}%" if best["fwd_ret"] is not None
               else "—")
        # color the forward return honestly: green if positive, red if not
        fr_color = (theme.GREEN if best["fwd_ret"] is not None
                     and best["fwd_ret"] > 0 else theme.RED)
        tier_warn = ("<div class='tiny' style='margin-top:8px;"
                      f"color:{theme.YELLOW}'>⚠ Only {best_tier_n} day(s) in "
                      f"this tier across the backtest — not enough to be "
                      f"meaningful.</div>" if best_tier_n < 10 else "")
        st.markdown(
            f"""
            <div class='panel' style='border-color:{theme.GREEN}55'>
              <div class='kicker'>🏆 Best Signal — Highest Score</div>
              <div class='big-score' style='font-size:2.4rem;
                   color:{theme.GREEN};margin:8px 0'>{best['score']:.0f}
                <span style='font-size:1rem;color:{theme.MUTED};
                       font-weight:400'> / 100</span></div>
              <div class='tiny' style='line-height:1.9'>
                Date &nbsp;<b style='color:{theme.TEXT}'>{best['date']}</b><br>
                Status &nbsp;<b>{best['status']}</b><br>
                Entry price &nbsp;<b style='color:{theme.TEXT}'>${best['price']:,.2f}</b><br>
                <span style='color:{theme.MUTED}'>What actually happened
                  next:</span><br>
                {bt['fwd_window']}d forward return &nbsp;
                <b style='color:{fr_color}'>{fr}</b>
              </div>
              {tier_warn}
            </div>
            """, unsafe_allow_html=True)

    with wcol:
        fr = (f"{worst['fwd_ret']:+.2f}%" if worst["fwd_ret"] is not None
               else "—")
        fr_color = (theme.GREEN if worst["fwd_ret"] is not None
                     and worst["fwd_ret"] > 0 else theme.RED)
        tier_warn = ("<div class='tiny' style='margin-top:8px;"
                      f"color:{theme.YELLOW}'>⚠ Only {worst_tier_n} day(s) in "
                      f"this tier — interpret cautiously.</div>"
                      if worst_tier_n < 10 else "")
        st.markdown(
            f"""
            <div class='panel' style='border-color:{theme.RED}55'>
              <div class='kicker'>⚠️ Worst Signal — Lowest Score</div>
              <div class='big-score' style='font-size:2.4rem;
                   color:{theme.RED};margin:8px 0'>{worst['score']:.0f}
                <span style='font-size:1rem;color:{theme.MUTED};
                       font-weight:400'> / 100</span></div>
              <div class='tiny' style='line-height:1.9'>
                Date &nbsp;<b style='color:{theme.TEXT}'>{worst['date']}</b><br>
                Status &nbsp;<b>{worst['status']}</b><br>
                Entry price &nbsp;<b style='color:{theme.TEXT}'>${worst['price']:,.2f}</b><br>
                <span style='color:{theme.MUTED}'>What actually happened
                  next:</span><br>
                {bt['fwd_window']}d forward return &nbsp;
                <b style='color:{fr_color}'>{fr}</b>
              </div>
              {tier_warn}
            </div>
            """, unsafe_allow_html=True)

    # methodology note
    st.markdown(
        f"""
        <div class='tiny' style='margin-top:14px'>
        <b>Methodology:</b> the historical score replays the four price/volume
        factors (Momentum, Volume Surge, Relative Strength, Range Proximity)
        under the <b>{bt.get('strategy','Trend-Following')}</b> engine,
        re-weighted to sum to 1.0. Short Interest and Options Flow are only
        available as a current snapshot from yfinance and cannot be replayed
        historically. {bt['n_days']} trading days audited.<br>
        <b>Look-ahead audit:</b> all factor windows use causal rolling
        operations (data at day T comes only from days ≤ T); forward returns
        use <code>close.shift(-{bt['fwd_window']})</code> which only references
        days &gt; T. Verified clean.<br>
        Run: {datetime.fromisoformat(bt['timestamp']).strftime('%Y-%m-%d %H:%M')}
        </div>
        """, unsafe_allow_html=True)
