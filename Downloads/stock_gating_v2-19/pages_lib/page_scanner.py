"""
page_scanner.py — Page 2: Custom Watchlist Scanner.

  - Strategy & Factor Definitions expander
  - Macro Regime banner
  - Ranked composite-score bar chart
  - Data table: Ticker, Score, Directional Bias, Tactical Allocation Action,
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
    with st.expander("📖 Strategy & Factor Definitions"):
        st.markdown("""
**Strategies**

- **Trend-Following (Strategy):** *"Riding the wave."* Buying stocks that are
  already breaking out and showing strong institutional momentum, expecting the
  trend to continue higher.
- **Mean Reversion (Strategy):** *"The rubber band effect."* Buying stocks that
  have crashed violently and are extremely oversold, expecting a snap-back
  rally to their historical average.

**The 6 scored factors**

- **Momentum:** Checks the 10-day vs 50-day moving averages to see if the
  short-term price action is accelerating.
- **Volume Surge:** Compares recent 5-day volume to 20-day volume. A surge
  indicates large institutional players are actively stepping in.
- **Relative Strength:** Measures how the stock is performing compared to the
  broader S&P 500. Is it leading the market or dragging behind?
- **Range Proximity:** Checks how close the stock is to its 52-week High (used
  in Trend mode) or 52-week Low (used in Mean Reversion mode).
- **Short Interest:** The amount of shares bet against the stock. Declining
  shorts mean bears are giving up. High short interest provides fuel for a
  violent "short squeeze."
- **Options/Volatility:** Looks at Implied Volatility (IV) and Put/Call ratios
  to see how options dealers are pricing fear and risk.

**Context only (not scored)**

- **52W Low / 52W High:** The stock's price range over the past year — context
  for where the current price sits.
- **Volume Pace:** Relative Volume — today's volume vs the 20-day average,
  read as plain English: "Heavy (Institutional)" means big players are
  active, "Normal" is an average session, "Quiet (Retail)" means below-average
  participation.
- **Trailing / Forward P/E:** Valuation context from company fundamentals.
- **⚠️ Earnings Warning Icon:** A ⚠️ next to the Ticker means earnings are
  within ~5 trading days — visual heads-up only. The tranche action and score
  are unaffected; size your position consciously around the event.

**Macro Regimes & Capital Deployment**

The Page 1 Macro Score sets the regime, which dictates how aggressively to size
both Long and Short trades:

- **🟢 BULL REGIME (Score 70 - 100):** Healthy, low-risk bull environment.
  Institutional money is flowing freely.
  - **Longs:** Green light for full-sized positions (Tranche 3).
  - **Shorts:** High risk of short-squeezes. Only take quick scalps.
- **🟡 SIDEWAYS REGIME (Score 40 - 69):** Mixed, turbulent environment. The market is
  digesting risk or rotating sectors.
  - **Longs:** Breakouts are prone to failure. Cut standard position sizes in
    half (Tranche 1 Pilot Longs only).
  - **Shorts:** Favorable environment for shorting weak stocks (Tranche 2 Lean
    Shorts).
- **🔴 BEAR REGIME (Score 0 - 39):** High-risk, algorithmic unwind environment.
  Systemic liquidity is draining.
  - **Longs:** STRICT CASH. Do not buy the dip until the macro score recovers.
  - **Shorts:** Green light for full-sized bearish positions (Tranche 3 Max
    Shorts).
""")


def render():
    st.markdown("<div class='kicker'>PAGE 2 · CUSTOM WATCHLIST SCANNER</div>",
                unsafe_allow_html=True)
    st.markdown("# Directional Trade Scanner")

    strategy = st.session_state.get("strategy", "Trend-Following")
    st.markdown(
        f"<div class='tiny' style='margin-bottom:6px'>Active engine: "
        f"<b style='color:{theme.ACCENT}'>{strategy}</b></div>",
        unsafe_allow_html=True)

    # ── definitions expander (directly under the strategy line) ──
    _render_definitions()

    macro = st.session_state.get("macro_result")
    scanner = st.session_state.get("scanner_result")

    if macro is None:
        st.info("Run the analysis on Page 1 — the Macro Gate must clear first.")
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

    if needs_rerun:
        if macro_changed:
            st.warning(
                f"⚠️ Scanner results are stale — macro score changed from "
                f"**{scanner_macro}** to **{macro_score_now:.0f}** since the last "
                f"scan. Re-run to refresh the regime-blocking logic.")
        if st.button("▶  Run Watchlist Scan", use_container_width=True):
            import run_scanner
            with st.spinner(f"Scanning watchlist · {strategy}…"):
                st.session_state.scanner_result = run_scanner.run(
                    st.session_state.watchlist, strategy,
                    macro_score=macro_score_now)
            scanner = st.session_state.scanner_result
        else:
            if scanner is None:
                st.info("Click ‘Run Watchlist Scan’ to score your watchlist "
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
    st.plotly_chart(_score_bar(rows), use_container_width=True,
                    config={"displayModeBar": False})

    st.markdown("---")
    st.markdown("<div class='kicker'>Multi-Factor Scan · Institutional Flow "
                "Score</div>", unsafe_allow_html=True)
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
        table_rows.append({
            "Ticker": ticker_label,
            "Score": display_score,
            "Directional Bias": r["status_label"],
            "Tactical Allocation Action": tranche["action"],
            "Price": float(r["price"]) if r.get("price") is not None else 0.0,
            "52W Low": float(r["range_low"]) if r.get("range_low") else 0.0,
            "52W High": float(r["range_high"]) if r.get("range_high") else 0.0,
            # store the raw RVOL; the text column is mapped from it below
            "_rvol": r.get("rvol"),
            "Trailing P/E": float(tp) if tp is not None else None,
            "Fwd P/E": float(fp) if fp is not None else None,
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
            help="⚠️ icon indicates earnings within ~5 trading days — "
                 "visual heads-up only, does not change the tranche action"),
        "Score": st.column_config.NumberColumn("Score", format="%d",
                                               width="small"),
        "Directional Bias": st.column_config.TextColumn("Directional Bias"),
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
        "Next Earnings": st.column_config.TextColumn("Next Earnings"),
    }

    event = st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config=col_config,
        column_order=["Ticker", "Score", "Directional Bias",
                      "Tactical Allocation Action", "Price", "52W Low",
                      "52W High", "Volume Pace", "Trailing P/E", "Fwd P/E",
                      "Next Earnings"],
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
    export_df["Ticker"] = export_df["Ticker"].str.replace("⚠️ ", "", regex=False)
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    ts_compact = ts.replace(":", "").replace(" ", "_")[:15] if ts else "scan"
    st.download_button(
        label="📥 Download scanner results (CSV)",
        data=csv_bytes,
        file_name=f"scanner_{strategy.replace(' ','_')}_{ts_compact}.csv",
        mime="text/csv",
        use_container_width=False,
    )

    # directional-bias legend
    legend = [("🟢 STRONG LONG", "80-100", theme.GREEN),
              ("🟢 LEAN LONG", "65-79", "#7fd98a"),
              ("🟡 HOLD / CASH", "50-64", theme.YELLOW),
              ("🟠 WATCH SHORT", "35-49", theme.ORANGE),
              ("🔴 LEAN SHORT", "20-34", "#ff7a6c"),
              ("🔴 STRONG SHORT", "0-19", theme.RED)]
    chips = "".join(
        f"<span style='display:inline-block;margin:3px 6px 3px 0;"
        f"padding:4px 12px;border-radius:6px;background:{c}22;color:{c};"
        f"font-family:JetBrains Mono;font-size:0.72rem'>{lbl} · {rng}</span>"
        for lbl, rng, c in legend)
    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='tiny' style='margin-top:8px'>Score = <b>Institutional "
        "Flow weighted</b> sum of 6 factors: Options Flow 30%, Big Money "
        "Volume 25%, Price Speed 15%, Market Leader 10%, Squeeze Fuel 10%, "
        "Chart Position 10%. P/E, Volume Pace and the 52W columns are context "
        f"only and NOT part of the score. Last run: {ts}</div>",
        unsafe_allow_html=True)

    # ── on-demand 1-year technical chart for the selected row ──
    if st.session_state.get("selected_rows"):
        sel_idx = st.session_state.selected_rows[0]
        if 0 <= sel_idx < len(df):
            # strip the optional "⚠️ " earnings prefix to get the raw ticker
            sel_ticker = df.iloc[sel_idx]["Ticker"].replace("⚠️ ", "").strip()
            _render_technical_chart(sel_ticker)


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
        dragmode="zoom",                       # enable drag-to-zoom box
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
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": True, "scrollZoom": True},
                    key=f"scanner_tech_chart_{ticker}")

    st.markdown(
        "<div class='tiny'>Studies: cumulative VWAP · floor pivots "
        "(P / R1 / S1 / R2 / S2) · 50-bin volume-by-price profile (independent "
        "axis) with Point of Control (POC) · color-coded daily volume bars · "
        "20-day ROC % with 9-EMA signal line and ±10 thresholds. Drag to zoom; "
        "double-click to reset. All from live yfinance daily candles.</div>",
        unsafe_allow_html=True)
