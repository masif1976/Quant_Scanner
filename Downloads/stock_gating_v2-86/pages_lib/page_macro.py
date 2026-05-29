"""
page_macro.py — Page 1: MarketSense.

  1. Real-time price banner (instant load — fast_info, no OHLCV)
  2. On-demand "Run Full Macro Analysis" button (heavy calc gated + cached)
  3. Composite Macro Score (Institutional Flow weighted model, 7 internal
     metrics) + 7 gauges + decoupled CNN Fear & Greed gauge (reference only)
  4. Benchmark 180-day chart (selectable: SPY/QQQ/RSP/IWM) tinted by regime
  5. "Metric Definitions" educational expander
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

import theme
import data_utils as du
import market_context
import market_cap_groups


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    """Convert '#rrggbb' to 'rgba(r,g,b,a)' for Plotly color args.

    Plotly's color validator only accepts standard 6-char hex, named CSS
    colors, or rgb/rgba/hsl/hsv strings — it REJECTS the 8-char hex+alpha
    form that browsers accept (e.g. '#22e08a55'). Use this whenever you
    need a translucent fill or marker tint with a Plotly call.

    Falls back to the input string unchanged if the format isn't '#rrggbb'
    so this is safe to call on already-rgba colors or named CSS colors.
    """
    if not isinstance(hex_color, str):
        return hex_color
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color  # already rgba(), named color, or malformed
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return hex_color
    a = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r},{g},{b},{a:.3f})"


# ── gauge ─────────────────────────────────────────────────────────────────────
# Layman-term labels: maps each internal signal name to a two-line HTML title
# (bold plain-English primary + small grey technical subtitle).
_GAUGE_LABELS = {
    "VIX Level":          ("Current Fear Gauge", "VIX Level"),
    "VIX Term Structure": ("Crash Warning", "VIX Term Structure"),
    "Sector Breadth":     ("Sector Participation", "Sector Breadth"),
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
    Two-row stacked chart over the trailing ~180 trading days:

      ROW 1 (70% height): Benchmark price line with the historical macro
                          regime shaded in the background (Green/Yellow/Red).
      ROW 2 (30% height): The raw 0-100 Composite Macro Score as an
                          oscillator, with reference lines at y=70 (Bull
                          threshold) and y=40 (Bear threshold).

    Both rows share an x-axis so zooming/panning stays synced, and a unified
    x-hover crosshair shows the benchmark price AND macro score for the same
    date simultaneously — the institutional-platform layout pattern.
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

    # macro composite series aligned to the same regime dates — every entry
    # corresponds 1:1 with a regime_date by construction (built in
    # macro_history.regime_timeseries).
    composite = history.get("composite", [])

    # ── build the 2-row subplot ──
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,       # very tight — connects the two visually
        row_heights=[0.70, 0.30],    # ~70/30 price / oscillator split
    )

    # ── ROW 1: regime bands + benchmark price ──
    # vrects span the full chart height by default (yref="paper"). With a
    # subplot layout we explicitly attach them to row=1 by giving y0/y1 in
    # the row-1 axis range — but the simpler and equally clean way is to
    # constrain the vrect to row 1's y-axis using `row` parameter, which
    # make_subplots understands natively.
    fill_opacity = {"BULL REGIME": 0.16, "SIDEWAYS REGIME": 0.14, "BEAR REGIME": 0.16}
    for seg in history.get("segments", []):
        fig.add_vrect(
            x0=seg["start"], x1=seg["end"],
            fillcolor=seg["color"],
            opacity=fill_opacity.get(seg["regime"], 0.14),
            line_width=0, layer="below",
            row=1, col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=bench_dates, y=bench_price, mode="lines",
            line=dict(color="#e0e0e0", width=2),
            name=f"{benchmark} Price",
            hovertemplate=f"<b>{benchmark}</b> $%{{y:.2f}}<extra></extra>",
        ),
        row=1, col=1,
    )

    # ── ROW 2: macro oscillator ──
    fig.add_trace(
        go.Scatter(
            x=regime_dates, y=composite, mode="lines",
            line=dict(color="#9bb1d4", width=1.8),
            name="Macro Score",
            hovertemplate="<b>Macro Score</b> %{y:.1f}<extra></extra>",
        ),
        row=2, col=1,
    )

    # reference threshold lines at the regime boundaries (70 = BULL, 40 = BEAR).
    # add_hline with row=2 keeps them constrained to the oscillator pane.
    fig.add_hline(y=70, line=dict(color="#22e08a", width=1, dash="dot"),
                  opacity=0.55, row=2, col=1,
                  annotation_text="BULL ≥ 70", annotation_position="top left",
                  annotation_font_size=9, annotation_font_color="#22e08a")
    fig.add_hline(y=40, line=dict(color="#ff5d6c", width=1, dash="dot"),
                  opacity=0.55, row=2, col=1,
                  annotation_text="BEAR < 40", annotation_position="bottom left",
                  annotation_font_size=9, annotation_font_color="#ff5d6c")

    # ── axis & layout polish ──
    # Top row: hide its own x-tick labels (shared with bottom) — only the
    # bottom row shows dates, which is the standard institutional pattern.
    lo, hi = min(bench_price) * 0.97, max(bench_price) * 1.03
    fig.update_yaxes(
        title=None, range=[lo, hi],
        showgrid=True, gridcolor=theme.BORDER, color="#e0e0e0",
        row=1, col=1,
    )
    fig.update_yaxes(
        title=None, range=[0, 100], tickvals=[0, 40, 70, 100],
        showgrid=True, gridcolor=theme.BORDER, color="#e0e0e0",
        row=2, col=1,
    )
    fig.update_xaxes(
        showgrid=False, color="#e0e0e0", showticklabels=False, row=1, col=1,
    )
    fig.update_xaxes(
        showgrid=False, color="#e0e0e0", row=2, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        hovermode="x unified",       # crosshair: one tooltip across both rows
        hoverlabel=dict(
            bgcolor="rgba(15,18,26,0.95)",
            bordercolor=theme.BORDER,
            font=dict(family="JetBrains Mono", size=11, color="#e0e0e0"),
        ),
        showlegend=True,
        legend=dict(orientation="h", y=1.08, x=0,
                    font=dict(size=10, color="#e0e0e0")),
        margin=dict(l=10, r=10, t=42, b=24),
        # `fig.layout.annotations` can be None if no add_hline/add_annotation
        # has run yet — wrap in a tuple-or-empty before extending.
        annotations=list(fig.layout.annotations or ()) + [
            dict(x=0.02, y=0.985, xref="paper", yref="paper",
                 text="BACKGROUND = HISTORICAL MACRO REGIME",
                 showarrow=False,
                 font=dict(family="JetBrains Mono", size=10, color="#e0e0e0")),
        ],
    )
    # plotly_layout_dark sets paper/plot bgcolor and a few global defaults
    # without overriding subplot-specific axes we've configured above.
    return theme.plotly_layout_dark(fig, height=460)


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


def _render_upcoming_events(watchlist: list[str]):
    """Compact panel showing high-impact US macro events + watchlist earnings
    in the next 7 days. Used on Page 1 below the ATH context line.

    Hides entirely if:
      - Finnhub returns nothing for both endpoints (rate limit, API down)
      - No high-impact events AND no watchlist earnings in the window
        (which is normal during quiet weeks — better to hide than show
         "no events" clutter)

    Each event has a corner badge style: dark panel, colored left border
    by event type (yellow = macro, blue = earnings), single-line layout
    for at-a-glance scanning across multiple events.
    """
    # Fetch both calendars in parallel by intent — actually sequential here
    # but Finnhub caches both for 6hr so steady-state cost is zero
    econ_events = du.get_economic_calendar(days_ahead=7) or []
    earn_events = du.get_market_earnings_calendar(
        days_ahead=7, ticker_filter=watchlist) or []

    # Bail out early if there's nothing to show — better than showing an
    # empty panel
    if not econ_events and not earn_events:
        return

    today = datetime.now().date()

    def _days_label(date_str: str) -> str:
        """Convert YYYY-MM-DD to 'today' / 'tomorrow' / 'in N days'."""
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return date_str or "—"
        delta = (d - today).days
        if delta == 0: return "today"
        if delta == 1: return "tomorrow"
        if delta < 0:  return f"{abs(delta)}d ago"
        return f"in {delta}d"

    def _weekday(date_str: str) -> str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            return d.strftime("%a %b %d")
        except (ValueError, TypeError):
            return date_str or "—"

    # Build the rows. Cap at ~6 macro + ~8 earnings to avoid overflow on
    # busy weeks. Sorted by date already (Finnhub does this).
    rows_html = []

    # ── macro events ──
    for ev in econ_events[:6]:
        date_str = ev.get("date") or ""
        time_str = ev.get("time") or ""
        event_name = ev.get("event") or "Event"
        days = _days_label(date_str)
        when = _weekday(date_str)
        if time_str:
            when = f"{when} · {time_str} ET"
        # Defensive truncation for absurdly long event names
        if len(event_name) > 60:
            event_name = event_name[:57] + "..."
        rows_html.append(
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;padding:6px 10px;"
            f"border-left:3px solid {theme.YELLOW};"
            f"background:{theme.YELLOW}0a;margin-bottom:4px;"
            f"border-radius:4px;font-family:JetBrains Mono;"
            f"font-size:0.78rem'>"
            f"<span><b style='color:{theme.YELLOW}'>📊 MACRO</b> · "
            f"<span style='color:{theme.TEXT}'>{event_name}</span></span>"
            f"<span style='color:{theme.MUTED};font-size:0.72rem'>"
            f"{when} · {days}</span>"
            f"</div>"
        )

    # ── watchlist earnings ──
    # hour codes: "bmo" (before market open), "amc" (after market close), "" (unknown)
    hour_labels = {"bmo": "BMO", "amc": "AMC", "": ""}
    for ev in earn_events[:8]:
        ticker = ev.get("ticker") or "?"
        date_str = ev.get("date") or ""
        hour_code = (ev.get("hour") or "").lower()
        hour_label = hour_labels.get(hour_code, hour_code.upper())
        eps_est = ev.get("eps_estimate")
        days = _days_label(date_str)
        when = _weekday(date_str)
        if hour_label:
            when = f"{when} {hour_label}"
        eps_str = f" · EPS est ${eps_est:.2f}" if eps_est is not None else ""
        rows_html.append(
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;padding:6px 10px;"
            f"border-left:3px solid {theme.MUTED};"
            f"background:{theme.PANEL_HI};margin-bottom:4px;"
            f"border-radius:4px;font-family:JetBrains Mono;"
            f"font-size:0.78rem'>"
            f"<span><b style='color:{theme.MUTED}'>📅 EARN</b> · "
            f"<span style='color:{theme.TEXT};font-weight:700'>{ticker}</span>"
            f"{eps_str}</span>"
            f"<span style='color:{theme.MUTED};font-size:0.72rem'>"
            f"{when} · {days}</span>"
            f"</div>"
        )

    # Counts for the header
    summary_bits = []
    if econ_events:
        summary_bits.append(f"{len(econ_events)} macro event"
                            f"{'s' if len(econ_events) != 1 else ''}")
    if earn_events:
        summary_bits.append(f"{len(earn_events)} watchlist earnings")
    summary = " · ".join(summary_bits)

    # Wrap everything in an expander to keep Page 1 compact when no urgent
    # events exist. Default: expanded if anything happens in next 2 days,
    # collapsed otherwise. (Avoids stealing attention when nothing is
    # imminent.)
    has_urgent = any(
        (_days_label((ev.get("date") or "")) in ("today", "tomorrow"))
        for ev in (econ_events + earn_events)
    )
    expander_title = f"📅 Upcoming Events — {summary}"
    if has_urgent:
        expander_title += "  ⚠ urgent"

    with st.expander(expander_title, expanded=has_urgent):
        st.markdown("".join(rows_html), unsafe_allow_html=True)
        st.caption(
            "High-impact US macro events (FOMC, CPI, NFP, PCE, GDP) + "
            "earnings reports for tickers in your watchlist. Both pulled "
            "from Finnhub, cached for 6 hours. Use to avoid holding "
            "positions through major event risk.")


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
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config=col_config)
    st.markdown(
        "<div class='tiny' style='margin-top:6px'>Month-end checkpoints "
        "plus today's reading, both pulled from the same 180-day historical "
        "engine that drives the regime chart below.</div>",
        unsafe_allow_html=True)


# ── price banner ──────────────────────────────────────────────────────────────
def _render_price_banner(watchlist):
    """Render compact ticker cards inside a collapsed-by-default expander.

    The expander title shows a smart one-line summary (count + biggest mover
    + biggest loser) so the user gets the key takeaway without expanding.
    Inside: per-ticker cards with sparklines, plus a ↻ refresh button for
    manually forcing fresh quotes (which otherwise live in a 1-hour cache).
    """
    if not watchlist:
        st.markdown("<div class='kicker'>Live Watchlist Prices</div>",
                    unsafe_allow_html=True)
        st.info("Watchlist is empty — add tickers in the sidebar.")
        return

    # Detect whether live quotes are stale (LKG fallback engaged)
    info = getattr(du, "LAST_FALLBACK_INFO", {}) or {}
    quotes_stale = (info.get("used") and isinstance(info.get("key"), str)
                    and info["key"].startswith("quotes::"))

    # Always fetch quotes (they're cached) — needed for the summary line
    quotes = du.get_live_quotes(tuple(watchlist))

    # Build the one-line summary that becomes the expander title.
    # Find top mover (most positive % change) and top loser (most negative).
    # If quotes are sparse we degrade gracefully — the summary just gets
    # shorter rather than crashing.
    valid = [(t, q.get("change_pct"))
             for t, q in ((tk, quotes.get(tk, {})) for tk in watchlist)
             if q.get("status") == "ok" and q.get("change_pct") is not None]
    summary_bits = [f"{len(watchlist)} tickers"]
    if valid:
        top = max(valid, key=lambda x: x[1])
        bot = min(valid, key=lambda x: x[1])
        # only show movers if there's meaningful spread — avoid noise when
        # everything is between -0.5% and +0.5%
        if top[1] > 0.5:
            summary_bits.append(f"📈 {top[0]} {top[1]:+.2f}%")
        if bot[1] < -0.5:
            summary_bits.append(f"📉 {bot[0]} {bot[1]:+.2f}%")
    # Gap-down ticker count — alerts user to news-driven moves on names
    # they're tracking. Shows up only when there's actually something to
    # flag (avoids constant "0 gap-downs" noise on quiet days).
    gap_tickers = [t for t in watchlist
                    if market_context.is_gap_down(quotes.get(t, {}))]
    if gap_tickers:
        summary_bits.append(
            f"🚨 {len(gap_tickers)} gap-down ({', '.join(gap_tickers[:3])}"
            f"{'...' if len(gap_tickers) > 3 else ''})")
    if quotes_stale:
        summary_bits.append("⚠ STALE")
    summary = " · ".join(summary_bits)
    expander_title = f"📊 Live Watchlist Prices — {summary}"

    # Collapsed by default — the macro composite and regime are the primary
    # signal on this page; live tickers are reference info, not headline.
    with st.expander(expander_title, expanded=False):
        # refresh button sits inside the expander too (no point taking
        # vertical space outside a closed panel)
        _, btn_col = st.columns([5, 1])
        with btn_col:
            if st.button("↻ Refresh", key="refresh_prices",
                          help="Clear cache and refetch live quotes + "
                               "sparklines. Use sparingly — both Finnhub "
                               "and yfinance are rate-limited on free tier."):
                du.clear_cache()
                du.reset_fallback_info()
                st.rerun()

        # bulk-fetch ~1 year of history for the 52-week range bars
        try:
            history = du.get_bulk_history(tuple(watchlist), days=260)
        except Exception:
            history = {}

        # ── fetch market caps for all watchlist tickers ──
        # Used to group cards into Mega/Large/Mid/Small sections. Pulls from
        # the same Finnhub get_fundamentals cache the Grade card uses on
        # Page 2, so the cost is one-time (24h TTL). Tickers that return no
        # market cap go into an "Unclassified" section rather than being
        # guessed into a tier.
        market_caps: dict[str, float | None] = {}
        for t in watchlist:
            try:
                fund = du.get_fundamentals(t)
                market_caps[t] = fund.get("market_cap") if fund else None
            except Exception:
                market_caps[t] = None
        groups = market_cap_groups.group_watchlist(watchlist, market_caps)

        # ── render each tier as its own section with a header + sub-grid ──
        PER_ROW = 5
        for tier_key, tier_label, tier_range in market_cap_groups.CAP_TIERS:
            tickers_in_tier = groups.get(tier_key, [])
            if not tickers_in_tier:
                continue
            # Section header — counts how many tickers are in this tier and
            # the cap range it represents. Helps the user reason about each
            # section's behavior class (mega-caps vs small-caps move differently).
            st.markdown(
                f"<div style='margin:14px 0 6px 0;"
                f"padding-bottom:4px;"
                f"border-bottom:1px solid {theme.BORDER};"
                f"font-family:JetBrains Mono;font-size:0.78rem;"
                f"font-weight:700;color:{theme.TEXT};"
                f"letter-spacing:0.06em;'>"
                f"{tier_label.upper()} · "
                f"<span style='color:{theme.MUTED};font-weight:500'>"
                f"{len(tickers_in_tier)} ticker"
                f"{'s' if len(tickers_in_tier) != 1 else ''} · "
                f"{tier_range}</span>"
                f"</div>",
                unsafe_allow_html=True
            )
            # Grid within this section — tickers in this tier are sorted by
            # market cap descending (biggest first within each section)
            for start in range(0, len(tickers_in_tier), PER_ROW):
                chunk = tickers_in_tier[start:start + PER_ROW]
                cols = st.columns(len(chunk))
                for col, t in zip(cols, chunk):
                    with col:
                        _render_sparkline_card(t, quotes.get(t, {}),
                                                history.get(t),
                                                market_cap=market_caps.get(t))

        # Honest source attribution — count tickers by data source so the
        # user can see which path actually served their quotes. Previously
        # this caption hardcoded "yfinance" which was incorrect once the
        # dual-source router was wired in. Sparklines still come from
        # yfinance because Finnhub's free tier removed /candle in 2024.
        source_counts = {}
        for t in watchlist:
            src = (quotes.get(t) or {}).get("source", "none")
            source_counts[src] = source_counts.get(src, 0) + 1
        src_bits = []
        if source_counts.get("finnhub"):
            src_bits.append(
                f"<b style='color:#22e08a'>{source_counts['finnhub']} from "
                f"Finnhub</b>")
        if source_counts.get("yfinance"):
            src_bits.append(
                f"<b style='color:#7d8aa5'>{source_counts['yfinance']} from "
                f"yfinance fallback</b>")
        if source_counts.get("none"):
            src_bits.append(
                f"<b style='color:#ff5d6c'>{source_counts['none']} failed</b>")
        sources_summary = " · ".join(src_bits) if src_bits else "no data"
        caption = (f"Quotes: {sources_summary} (15-min delayed on free tier). "
                   f"52-week range bars show price position from low to "
                   f"high; OVERSOLD label fires &lt;30% of range, OVERBOUGHT "
                   f"&gt;70%. Tickers grouped by market-cap tier.")
        if quotes_stale:
            caption += (" <b style='color:#f5c344'>Currently serving "
                         "last-known-good quotes — both data sources "
                         "rate-limited.</b>")
        # OHLCV cache health — surfaces the SQLite resilience layer. Only
        # shown when there's something interesting (cache is populated and
        # either being actively used or has recent data).
        try:
            import ohlcv_cache as _ohlcv
            cache_stats = _ohlcv.coverage_stats()
            if cache_stats["tickers"] > 0:
                cache_msg = (f" 💾 OHLCV cache: {cache_stats['tickers']} "
                              f"tickers, {cache_stats['rows']:,} bars "
                              f"(thru {cache_stats['latest_date']}).")
                # Highlight if cache is actively serving fallback
                hist_fallbacks = sum(
                    1 for k in du.FALLBACK_LOG.keys() if k.startswith("hist::") or k.startswith("bulk::"))
                if hist_fallbacks > 0:
                    cache_msg += (f" <b style='color:#f5c344'>Serving "
                                   f"{hist_fallbacks} fallback(s) from cache.</b>")
                caption += cache_msg
        except Exception:
            pass
        st.markdown(f"<div class='tiny'>{caption}</div>",
                     unsafe_allow_html=True)


def _render_sparkline_card(ticker: str, quote: dict, hist_df,
                            market_cap: float | None = None):
    """Compact ticker card (v4 — restructured per UX feedback):

    Layout (top to bottom):
      [TICKER] [cap chip]                          [🚨 GAP if any]
      $price
      ▲/▼ % change
      ════════●═════════════ (range bar with blue marker)
      $LO      89% · OVERBOUGHT          $HI       (compact 3-value strip)

    Differences from v3:
      - Removed grey OVERSOLD/OVERBOUGHT pill from top-right (redundant
        with the centered label below the bar)
      - Replaced the labeled "MARKET CAP / 52W RANGE" 2-column metadata
        row with a compact 3-value strip below the bar (lo · pos% · hi)
      - Market cap moves inline next to the ticker as a small grey chip
      - Result: less vertical space, less visual repetition, clearer focus
        on the range bar as the centerpiece of the card

    Color discipline (unchanged):
      - Green/red ONLY for daily % change
      - Blue accent for range marker + extreme labels
      - Grey for everything secondary
      - Red gap badge stays (genuine warning)
    """
    has_quote = (quote.get("status") == "ok"
                 and quote.get("price") is not None)
    price = f"${quote['price']:,.2f}" if has_quote else "—"
    chg = quote.get("change_pct") if has_quote else None

    # color by daily change — green if up, red if down, muted if no data
    if chg is None:
        delta_color = theme.MUTED
        delta_str = "—"
        delta_arrow = ""
    elif chg >= 0:
        delta_color = theme.GREEN
        delta_str = f"+{chg:.2f}%"
        delta_arrow = "▲"
    else:
        delta_color = theme.RED
        delta_str = f"{chg:.2f}%"
        delta_arrow = "▼"

    # ── compute 52-week range position ──
    lo, hi, cur, pos_pct = None, None, None, None
    if (hist_df is not None and not hist_df.empty
        and "Close" in hist_df and has_quote):
        closes_52w = hist_df["Close"].dropna().tail(252)
        if len(closes_52w) >= 30:
            lo = float(closes_52w.min())
            hi = float(closes_52w.max())
            cur = float(quote["price"])
            if hi > lo:
                pos_pct = max(0.0, min(100.0, (cur - lo) / (hi - lo) * 100))

    # ── gap-down badge (top-right corner, alone — no more status pill) ──
    # Only the actual warning lives in the top-right now. Removed the grey
    # OVERSOLD/OVERBOUGHT pill that used to sit here — that info now appears
    # only below the bar, eliminating the visual repetition.
    gap_badge = ""
    if market_context.is_gap_down(quote):
        gap_badge = (
            f"<span style='font-family:JetBrains Mono;font-size:0.62rem;"
            f"font-weight:800;background:{theme.RED}cc;color:#000;"
            f"padding:2px 6px;border-radius:8px;"
            f"box-shadow:0 0 6px {theme.RED}66'"
            f" title='Daily change {chg:+.2f}% — news may have moved this'>"
            f"🚨 GAP</span>"
        )

    # ── market-cap chip (inline next to ticker) ──
    # Small grey badge — provides "what size is this company" context
    # without taking a full labeled row. Hidden if cap data unavailable.
    cap_chip = ""
    if market_cap:
        cap_str = market_cap_groups.format_market_cap(market_cap)
        cap_chip = (
            f"<span style='font-family:JetBrains Mono;font-size:0.62rem;"
            f"color:{theme.MUTED};margin-left:8px;padding:1px 6px;"
            f"border:1px solid {theme.BORDER};border-radius:6px;"
            f"font-weight:600'>"
            f"{cap_str}</span>"
        )

    # ── compact 3-value range strip (below the bar) ──
    # Layout: $LO ··· (pos% · OVERSOLD/OVERBOUGHT) ··· $HI
    # All on one row, flex-spaced. The center cell carries the extreme
    # label too (when applicable) to save vertical space — no separate
    # centered line below.
    def _fmt_p(x):
        if x is None: return "—"
        if x >= 1000: return f"${x:.0f}"
        if x >= 10:   return f"${x:.1f}"
        return f"${x:.2f}"

    range_block_html = ""
    if pos_pct is not None:
        # Center label: position % + OVERSOLD/OVERBOUGHT suffix at extremes.
        # All in muted grey (theme.MUTED), matching the market cap chip
        # color — keeps the entire bar+strip a uniform secondary tone so it
        # doesn't compete visually with the price (white) or % change (g/r).
        if pos_pct < 30:
            center_label = (f"<span style='color:{theme.MUTED};"
                            f"font-weight:700'>{pos_pct:.0f}% · OVERSOLD</span>")
        elif pos_pct > 70:
            center_label = (f"<span style='color:{theme.MUTED};"
                            f"font-weight:700'>{pos_pct:.0f}% · OVERBOUGHT</span>")
        else:
            center_label = (f"<span style='color:{theme.MUTED};"
                            f"font-weight:700'>{pos_pct:.0f}%</span>")

        # Marker uses the same muted grey (theme.MUTED = #7d8aa5) as
        # the cap chip — keeps the entire card monochrome with only
        # the price (white) and daily % (g/r) standing out.
        marker_color = theme.MUTED
        range_block_html = (
            # bar itself
            f"<div style='margin-top:10px;padding:0 2px'>"
            f"<div style='position:relative;height:6px;"
            f"background:{theme.BORDER};border-radius:3px'>"
            f"<div title='Current price ${cur:.2f} = "
            f"{pos_pct:.1f}% of 52W range' "
            f"style='position:absolute;top:-4px;"
            f"left:calc({pos_pct:.1f}% - 7px);"
            f"width:14px;height:14px;background:{marker_color};"
            f"border:2px solid {theme.PANEL};border-radius:50%'></div>"
            f"</div>"
            # 3-value strip below
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-top:6px;"
            f"font-family:JetBrains Mono;font-size:0.66rem;"
            f"color:{theme.MUTED}'>"
            f"<span>{_fmt_p(lo)}</span>"
            f"{center_label}"
            f"<span>{_fmt_p(hi)}</span>"
            f"</div>"
            f"</div>"
        )

    # ── full card assembly ──
    card_html = (
        # outer container
        f"<div style='position:relative;background:{theme.PANEL};"
        f"border:1px solid {theme.BORDER};border-radius:8px;"
        f"padding:12px 14px;margin-bottom:0'>"
        # Top row: ticker + cap chip on left, gap badge on right (or empty)
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;gap:6px;min-height:22px'>"
        f"<span style='display:flex;align-items:center'>"
        f"<span style='font-family:Sora;font-size:1.05rem;"
        f"font-weight:700;color:{theme.TEXT};letter-spacing:0.04em'>"
        f"{ticker}</span>"
        f"{cap_chip}"
        f"</span>"
        f"{gap_badge}"
        f"</div>"
        # Price (large)
        f"<div style='font-family:Sora;font-weight:700;font-size:1.3rem;"
        f"color:{theme.TEXT};line-height:1.2;margin-top:4px'>"
        f"{price}</div>"
        # % change (smaller, below price)
        f"<div style='font-family:JetBrains Mono;font-size:0.78rem;"
        f"font-weight:700;color:{delta_color};margin-top:2px'>"
        f"{delta_arrow} {delta_str}</div>"
        # Range bar + 3-value strip
        f"{range_block_html}"
        f"</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)

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
    ("Sector Breadth", "Sector Participation",
     "How many of the 11 SPDR Select Sector ETFs (XLB, XLC, XLE, XLF, XLI, "
     "XLK, XLP, XLRE, XLU, XLV, XLY) are trading strictly above their "
     "50-day Simple Moving Average. Score = (count above ÷ 11) × 100. "
     "This is a true market-wide breadth signal — independent of which "
     "tickers you put in your watchlist.",
     "3 or fewer sectors above their 50-SMA (≤27 score) — broad-based "
     "weakness, classic distribution.",
     "9 or more sectors above their 50-SMA (≥82 score) — broad-based "
     "strength, classic risk-on participation.",
     "≤ 3/11 sectors", "≥ 9/11 sectors"),
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
                 low_label, high_label, weight_pct=None):
    """Render one metric definition with a red→green visual scale.
    If `weight_pct` is provided, show it prominently in the header so the
    user sees the metric's contribution to the composite at a glance."""
    weight_tag = (
        f"<span style='float:right;font-family:JetBrains Mono;"
        f"font-size:0.88rem;color:{theme.ACCENT};font-weight:700;"
        f"background:{theme.ACCENT}22;padding:3px 10px;border-radius:6px'>"
        f"{weight_pct:.0f}% weight</span>"
        if weight_pct is not None else "")
    return f"""
    <div style='background:{theme.PANEL};border:1px solid {theme.BORDER};
         border-radius:12px;padding:16px 18px;margin-bottom:12px'>
      <div style='font-family:Sora;font-weight:800;font-size:1.15rem;
           color:{theme.TEXT}'>
        {title}
        <span style='color:{theme.MUTED};font-weight:600;font-size:0.92rem'>
          · {subtitle}</span>
        {weight_tag}
      </div>
      <div class='tiny' style='margin-top:6px;color:{theme.MUTED};
           font-size:0.92rem'>{desc}</div>

      <!-- red -> green gradient scale -->
      <div style='margin-top:14px;height:9px;border-radius:5px;
           background:linear-gradient(90deg,{theme.RED} 0%,
           {theme.YELLOW} 50%,{theme.GREEN} 100%)'></div>
      <div style='display:flex;justify-content:space-between;margin-top:4px'>
        <span style='font-family:JetBrains Mono;font-size:0.8rem;
              color:{theme.RED}'>● 0 · {low_label}</span>
        <span style='font-family:JetBrains Mono;font-size:0.8rem;
              color:{theme.GREEN}'>{high_label} · 100 ●</span>
      </div>

      <!-- red / green light conditions -->
      <div style='display:flex;gap:10px;margin-top:14px'>
        <div style='flex:1;background:{theme.RED}14;border:1px solid {theme.RED}44;
             border-radius:8px;padding:10px 12px'>
          <div style='font-family:JetBrains Mono;font-size:0.8rem;
               color:{theme.RED};font-weight:700'>🔴 0 SCORE · RED LIGHT</div>
          <div class='tiny' style='margin-top:4px;font-size:0.88rem'>
            {red_cond}</div>
        </div>
        <div style='flex:1;background:{theme.GREEN}14;
             border:1px solid {theme.GREEN}44;border-radius:8px;
             padding:10px 12px'>
          <div style='font-family:JetBrains Mono;font-size:0.8rem;
               color:{theme.GREEN};font-weight:700'>
               🟢 100 SCORE · GREEN LIGHT</div>
          <div class='tiny' style='margin-top:4px;font-size:0.88rem'>
            {green_cond}</div>
        </div>
      </div>
    </div>
    """


def _render_definitions():
    """Render the Metric Definitions expander, with per-metric weights pulled
    live from run_macro_gate.WEIGHTS (single source of truth — if weights are
    rebalanced in the engine, this UI updates automatically)."""
    import run_macro_gate as rmg
    # render in descending weight order so the most-impactful metrics appear
    # first — readers learn the model better when the big drivers are on top
    sorted_defs = sorted(
        _METRIC_DEFS,
        key=lambda d: -rmg.WEIGHTS.get(d[0], 0))
    with st.expander("📖 Metric Definitions"):
        st.markdown(
            f"<div class='tiny' style='margin-bottom:10px'>"
            f"The <b>Composite Macro Score</b> is the <b>Institutional Flow "
            f"weighted</b> sum of these 7 internal metrics. Each is scored "
            f"0–100 on a red-light → green-light scale, then multiplied by "
            f"its weight (shown in the badge on each card) to produce the "
            f"final composite. Weights sum to exactly 1.0. The CNN Fear &amp; "
            f"Greed Index is shown alongside for reference but is "
            f"<b>excluded</b> from the score."
            f"</div>", unsafe_allow_html=True)
        for d in sorted_defs:
            metric_name = d[0]
            weight = rmg.WEIGHTS.get(metric_name, 0) * 100
            st.markdown(_metric_card(*d, weight_pct=weight),
                         unsafe_allow_html=True)


# ── main ──────────────────────────────────────────────────────────────────────
def render():
    watchlist = st.session_state.get("watchlist", [])

    st.markdown("<div class='kicker'>PAGE 1 · MARKETSENSE</div>",
                unsafe_allow_html=True)
    st.markdown("# 🧭 MarketSense")
    # active-horizon badge — same display on Page 2 so the user always knows
    # which lookback family is driving the numbers
    active_horizon = st.session_state.get("horizon", "Swing Trade System")
    st.markdown(theme.horizon_pill_html(active_horizon),
                unsafe_allow_html=True)
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
    if st.button("▶  Run Full Macro Analysis", width="stretch"):
        import run_macro_gate
        from macro_signals import macro_history
        horizon = st.session_state.get("horizon", "Swing Trade System")
        # reset the staleness flag so a successful retry clears the banner
        du.reset_fallback_info()
        with st.spinner("Running 7 macro signals + Fear & Greed…"):
            st.session_state.macro_result = run_macro_gate.run(
                watchlist, horizon=horizon)
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
                Institutional Flow-Weighted Macro Composite
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
                        width="stretch",
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

    # ── SPY all-time-high context ──
    # Adds market-distance-from-peak info that the regime composite doesn't
    # directly capture. A market at ATH is a different posture than a market
    # that's been below ATH for 6+ months even if both score the same on
    # credit spreads / VIX / etc. Cached for 1hr so it's effectively free
    # on subsequent renders.
    ath_ctx = market_context.get_spy_ath_context()
    caption = market_context.format_ath_caption(ath_ctx)
    if caption:
        # Color the indicator by drawdown severity:
        # - At ATH (within 0.5%): green (frothy)
        # - 0-5% from ATH: muted (normal)
        # - 5-10% from ATH: yellow (caution)
        # - >10% from ATH: red (correction territory)
        drawdown = ath_ctx.get("drawdown_pct", 0)
        if ath_ctx.get("at_ath"):
            ath_color = theme.GREEN
        elif drawdown > -5:
            ath_color = theme.MUTED
        elif drawdown > -10:
            ath_color = theme.YELLOW
        else:
            ath_color = theme.RED
        st.markdown(
            f"<div style='padding:6px 10px;margin:6px 0;"
            f"border-left:3px solid {ath_color};"
            f"background:{ath_color}11;border-radius:4px;"
            f"font-family:JetBrains Mono;font-size:0.82rem;"
            f"color:{theme.TEXT}'>"
            f"📏 <b>{caption}</b>"
            f"</div>", unsafe_allow_html=True)

    # ── upcoming events panel ──
    # High-impact US macro events + watchlist earnings in the next 7 days.
    # Helps the user avoid being surprised by event-driven volatility on
    # positions held through major releases (FOMC, CPI, NFP) or earnings
    # on watchlist names. Both endpoints cached for 6 hours.
    _render_upcoming_events(watchlist)

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
        "Sector Breadth":     ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                                "XLP", "XLRE", "XLU", "XLV", "XLY"],
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
            width="stretch",
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
        st.plotly_chart(regime_fig, width="stretch",
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
