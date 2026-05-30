"""
fundamental_charts.py — Pure Plotly chart builders for Fundamental Insights.

DESIGN PRINCIPLE (matches fundamental_data.py):
Isolated from BOTH the data layer and the Streamlit UI. Each function takes
already-fetched plain data (lists, DataFrames) and returns a Plotly Figure.
It never fetches and never calls `st.`.

THEME: light charts (white plot area, clean grid) on the app's dark shell,
matching the Qualtrim reference. One accent colour per metric, generous
margins, NO persistent modebar (titles never get overlapped), single-metric
charts (Revenue alone, EBITDA alone, etc.) laid out 2-up by the page.

Every builder tolerates missing data: if there's nothing to plot it returns
a figure with a centered "no data" note rather than raising.
"""

from __future__ import annotations

import plotly.graph_objects as go

# ── Theme palettes ──
# Charts can render light (Qualtrim-style) or dark (matching a dark app
# shell). set_theme("dark"/"light") flips the active palette; the page calls
# it once per render based on the user's theme selection. Accent colours are
# shared (they read well on both backgrounds).
_LIGHT = {
    "plot_bg": "#ffffff", "paper_bg": "#ffffff",
    "text": "#1f2733", "muted": "#8a94a6",
    "grid": "#eef1f5", "axis": "#d4dae3",
    "control_bg": "#f6f8fa",
}
_DARK = {
    "plot_bg": "#121724", "paper_bg": "#121724",
    "text": "#e6ebf5", "muted": "#7d8aa5",
    "grid": "#1f2940", "axis": "#2a3550",
    "control_bg": "#1a2133",
}
_THEME = dict(_LIGHT)  # active palette (default light)

# Backward-compatible module-level constants pointing at the active theme.
# set_theme() refreshes these so existing references stay valid without
# threading a theme arg through every function.
_PLOT_BG = _THEME["plot_bg"]
_PAPER_BG = _THEME["paper_bg"]
_TEXT = _THEME["text"]
_MUTED = _THEME["muted"]
_GRID = _THEME["grid"]
_AXIS = _THEME["axis"]
_CONTROL_BG = _THEME["control_bg"]


def set_theme(mode: str) -> None:
    """Switch the active chart palette. Call once before building charts."""
    global _THEME, _PLOT_BG, _PAPER_BG, _TEXT, _MUTED, _GRID, _AXIS, _CONTROL_BG
    _THEME = dict(_DARK if str(mode).lower() == "dark" else _LIGHT)
    _PLOT_BG = _THEME["plot_bg"]
    _PAPER_BG = _THEME["paper_bg"]
    _TEXT = _THEME["text"]
    _MUTED = _THEME["muted"]
    _GRID = _THEME["grid"]
    _AXIS = _THEME["axis"]
    _CONTROL_BG = _THEME["control_bg"]


# One accent per metric (read well on both light and dark backgrounds)
_C_PRICE = "#3fb950"      # green area (price)
_C_REVENUE = "#f0a830"    # amber
_C_EBITDA = "#5b9bd5"     # blue
_C_NETINCOME = "#7c9c4f"  # muted green
_C_FCF = "#e8833a"        # orange
_C_EPS_EST = "#c4cbd6"    # grey (estimate)
_C_EPS_ACT = "#3d8bff"    # blue (actual)
_C_SURPRISE = "#e8833a"   # orange line
_C_RATIO = "#5b9bd5"      # blue (deep-dive)
_FONT = "Inter, -apple-system, Segoe UI, sans-serif"


def _base(fig: go.Figure, title: str = "", height: int = 300,
          unified: bool = False) -> go.Figure:
    """Apply the active theme. Title sits above the plot with enough top
    margin that nothing (modebar/legend) ever overlaps it."""
    th = _THEME
    fig.update_layout(
        title=dict(text=title, font=dict(color=th["text"], size=15,
                                          family=_FONT, weight=600),
                   x=0.0, xanchor="left", y=0.97, yanchor="top"),
        paper_bgcolor=th["paper_bg"],
        plot_bgcolor=th["plot_bg"],
        font=dict(color=th["muted"], size=11, family=_FONT),
        hovermode="x unified" if unified else "closest",
        height=height,
        # Generous top margin keeps title clear of the (hover-only) modebar
        margin=dict(l=12, r=16, t=54, b=12),
        showlegend=False,
        xaxis=dict(gridcolor=th["grid"], zerolinecolor=th["grid"],
                   linecolor=th["axis"],
                   tickfont=dict(size=10, color=th["muted"]), showgrid=False),
        yaxis=dict(gridcolor=_GRID, zerolinecolor=_GRID, linecolor=_AXIS,
                   tickfont=dict(size=10, color=_MUTED), showgrid=True),
    )
    return fig


def _empty(title: str, msg: str = "No data available",
           height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False,
                       font=dict(color=_MUTED, size=13, family=_FONT))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base(fig, title, height=height)


def _humanize(v) -> str:
    """Format a number as $X.XB / $X.XM / $X.XK for hover display."""
    if v is None:
        return "n/a"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "n/a"
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e12:
        return f"{sign}${a/1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.2f}K"
    return f"{sign}${a:.2f}"


# ─────────────────────────────────────────────────────────────────────────
# 1. Price trend — filled area chart with range slider
# ─────────────────────────────────────────────────────────────────────────

def price_area_chart(price_df, ticker: str = "") -> go.Figure:
    if price_df is None or len(price_df) == 0 or "Close" not in price_df:
        return _empty("Price", "No price history available")

    dates = list(price_df.index)
    closes = [float(c) for c in price_df["Close"]]
    # Period return for the little header stat (Qualtrim shows ▲ +19.69%)
    pct = None
    if len(closes) >= 2 and closes[0]:
        pct = (closes[-1] / closes[0] - 1) * 100
    title = "Price"
    if pct is not None:
        arrow = "▲" if pct >= 0 else "▼"
        title = f"Price  {arrow} {abs(pct):.2f}%"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=closes, mode="lines", name="Close",
        line=dict(color=_C_PRICE, width=2),
        fill="tozeroy", fillcolor="rgba(63,185,80,0.12)",
        hovertemplate="$%{y:,.2f}<br>%{x|%b %d, %Y}<extra></extra>",
    ))
    fig = _base(fig, title, height=300)
    fig.update_xaxes(
        rangeslider=dict(visible=True, thickness=0.05,
                         bgcolor=_CONTROL_BG, bordercolor=_AXIS),
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor=_CONTROL_BG, activecolor=_C_PRICE,
            font=dict(color=_TEXT, size=10), x=0, y=1.0, yanchor="bottom",
        ),
    )
    fig.update_yaxes(tickprefix="$", autorange=True)
    return fig


# ─────────────────────────────────────────────────────────────────────────
# Single-metric bar chart (Qualtrim-style) — used for Revenue, EBITDA,
# Net Income, FCF each on their own.
# ─────────────────────────────────────────────────────────────────────────

def single_metric_bar(periods, values, title: str, color: str,
                       sign_color: bool = False, height: int = 300) -> go.Figure:
    """One metric, one colour, room to breathe. `sign_color=True` paints
    negative bars a contrasting red (for net income / FCF dips)."""
    if not periods or values is None or all(v is None for v in values):
        return _empty(title, "No data")

    if sign_color:
        bar_colors = ["#e5544b" if (v is not None and v < 0) else color
                      for v in values]
    else:
        bar_colors = color

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=periods, y=values, marker_color=bar_colors,
        customdata=[_humanize(v) for v in values],
        hovertemplate="%{customdata}<br>%{x}<extra></extra>",
    ))
    fig = _base(fig, title, height=height)
    _apply_money_yaxis(fig, values)
    return fig


def _apply_money_yaxis(fig, values):
    """Label the y-axis with finance-style $ suffixes (K/M/B/T) instead of
    Plotly's SI default, which renders billions as 'G' (e.g. $100G). Builds
    a handful of evenly-spaced ticks across the data range and formats each
    with _humanize so the axis reads like a financial statement."""
    nums = [float(v) for v in values if v is not None]
    if not nums:
        fig.update_yaxes(tickprefix="$")
        return
    lo = min(0.0, min(nums))   # include zero baseline for bar charts
    hi = max(nums)
    if hi <= lo:
        hi = lo + 1
    # ~5 evenly spaced ticks across [lo, hi]
    n = 5
    step = (hi - lo) / n
    tickvals = [lo + step * i for i in range(n + 1)]
    ticktext = [_humanize_axis(v) if v != 0 else "$0" for v in tickvals]
    fig.update_yaxes(tickvals=tickvals, ticktext=ticktext,
                     tickprefix="", tickformat="")


def _humanize_axis(v) -> str:
    """Compact $ label for axis ticks: '$100B', '$1.5T', '$50M' — fewer
    decimals than the hover label so the axis stays uncluttered."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return ""
    sign = "-" if n < 0 else ""
    a = abs(n)
    def trim(x):
        # one decimal, but drop a trailing '.0'
        s = f"{x:.1f}"
        return s[:-2] if s.endswith(".0") else s
    if a >= 1e12:
        return f"{sign}${trim(a/1e12)}T"
    if a >= 1e9:
        return f"{sign}${trim(a/1e9)}B"
    if a >= 1e6:
        return f"{sign}${trim(a/1e6)}M"
    if a >= 1e3:
        return f"{sign}${trim(a/1e3)}K"
    return f"{sign}${a:.0f}"


# ─────────────────────────────────────────────────────────────────────────
# Earnings surprise — bars (est vs actual) + surprise % line overlay
# ─────────────────────────────────────────────────────────────────────────

def earnings_surprise_chart(es: dict, ticker: str = "") -> go.Figure:
    if not es or not es.get("ok") or not es.get("periods"):
        return _empty("Earnings Surprise (EPS)",
                      es.get("note") or "No earnings data")

    periods = es["periods"]
    est = es.get("estimate") or []
    act = es.get("actual") or []
    surp = es.get("surprise_pct") or []

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=periods, y=est, name="Est. EPS", marker_color=_C_EPS_EST,
        hovertemplate="Est: $%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=periods, y=act, name="Actual EPS", marker_color=_C_EPS_ACT,
        hovertemplate="Actual: $%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=periods, y=surp, name="Surprise %", mode="lines+markers",
        yaxis="y2", line=dict(color=_C_SURPRISE, width=2),
        marker=dict(size=7, color=_C_SURPRISE),
        hovertemplate="Surprise: %{y:.1f}%<extra></extra>"))
    fig = _base(fig, "Earnings Surprise (EPS)", height=320, unified=True)
    fig.update_layout(
        barmode="group",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right",
                    x=1, font=dict(size=10, color=_MUTED)),
        yaxis=dict(title="EPS ($)", tickprefix="$", gridcolor=_GRID,
                   tickfont=dict(size=10, color=_MUTED)),
        yaxis2=dict(title="Surprise %", overlaying="y", side="right",
                    ticksuffix="%", showgrid=False,
                    tickfont=dict(size=10, color=_MUTED),
                    zeroline=True, zerolinecolor=_AXIS),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────
# Deep-dive: long-term profitability with an in-chart dropdown
# ─────────────────────────────────────────────────────────────────────────

def profitability_dropdown_chart(hist: dict, ticker: str = "") -> go.Figure:
    if not hist or not hist.get("ok") or not hist.get("periods"):
        return _empty("Long-Term Profitability",
                      hist.get("note") or "No long-term statement data",
                      height=440)

    periods = hist["periods"]
    gm = hist.get("gross_margin") or []
    om = hist.get("operating_margin") or []
    roce = hist.get("roce") or []

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=periods, y=roce, name="ROCE", marker_color=_C_RATIO,
        visible=True, hovertemplate="ROCE: %{y:.1f}%<extra></extra>"))
    fig.add_trace(go.Bar(
        x=periods, y=gm, name="Gross Margin", marker_color="#5cb85c",
        visible=False, hovertemplate="Gross Margin: %{y:.1f}%<extra></extra>"))
    fig.add_trace(go.Bar(
        x=periods, y=om, name="Operating Margin", marker_color="#f0a830",
        visible=False, hovertemplate="Op Margin: %{y:.1f}%<extra></extra>"))

    fig = _base(fig, "Return on Capital Employed", height=440)
    fig.update_yaxes(ticksuffix="%")
    fig.update_layout(
        updatemenus=[dict(
            type="buttons", direction="right", showactive=True,
            x=0.0, xanchor="left", y=1.14, yanchor="top",
            bgcolor=_CONTROL_BG, bordercolor=_AXIS,
            font=dict(color=_TEXT, size=11, family=_FONT),
            pad=dict(l=4, r=4, t=2, b=2),
            buttons=[
                dict(label="ROCE", method="update",
                     args=[{"visible": [True, False, False]},
                           {"title.text": "Return on Capital Employed"}]),
                dict(label="Gross Margin", method="update",
                     args=[{"visible": [False, True, False]},
                           {"title.text": "Gross Margin"}]),
                dict(label="Operating Margin", method="update",
                     args=[{"visible": [False, False, True]},
                           {"title.text": "Operating Margin"}]),
            ],
        )],
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────
# Plotly config — modebar ON HOVER ONLY (fixes the title-overlap), PNG export
# ─────────────────────────────────────────────────────────────────────────

def chart_config() -> dict:
    """Config for st.plotly_chart. The modebar shows on hover (the 54px top
    margin keeps it clear of the title), giving zoom / pan / autoscale /
    reset / download tools. Streamlit's own fullscreen ⛶ button (top-right
    on hover) also lets users expand any chart to full screen."""
    return {
        "displayModeBar": "hover",        # toolbar appears on hover only
        "displaylogo": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2},
        "scrollZoom": False,
        "responsive": True,
    }


# ─────────────────────────────────────────────────────────────────────────
# Fair-value range — horizontal markers per method + current price line
# ─────────────────────────────────────────────────────────────────────────

def fair_value_chart(fv: dict, ticker: str = "") -> go.Figure:
    """Horizontal scatter showing each method's fair-value estimate and the
    current price as a reference line. Communicates a RANGE, never a single
    'target' — that's the honest way to show model-derived value."""
    if not fv or not fv.get("ok") or not fv.get("methods"):
        return _empty("Fair Value Estimate",
                      fv.get("note") or "Insufficient data for fair value",
                      height=260)

    th = _THEME
    methods = fv["methods"]
    names = [m["name"] for m in methods]
    values = [m["value"] for m in methods]
    price = fv.get("current_price")

    # Color each marker by whether it's above (green, undervalued) or below
    # (red, overvalued) the current price.
    if price:
        colors = [_C_PRICE if v >= price else "#e5544b" for v in values]
    else:
        colors = [_C_EBITDA] * len(values)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=values, y=names, mode="markers",
        marker=dict(size=16, color=colors, line=dict(width=1, color=th["axis"])),
        customdata=[m["note"] for m in methods],
        hovertemplate="%{y}: $%{x:.2f}<br>%{customdata}<extra></extra>",
    ))
    fig = _base(fig, "Fair Value Estimate (range)", height=260)
    # Current price reference line
    if price:
        fig.add_vline(x=price, line=dict(color=th["text"], width=2,
                                          dash="dash"),
                      annotation_text=f"Price ${price:.2f}",
                      annotation_position="top",
                      annotation_font=dict(color=th["text"], size=11))
    fig.update_xaxes(tickprefix="$", title="")
    fig.update_yaxes(showgrid=False)
    return fig


# ─────────────────────────────────────────────────────────────────────────
# Health radar — 6-pillar spider chart
# ─────────────────────────────────────────────────────────────────────────

def health_radar_chart(radar: dict, ticker: str = "") -> go.Figure:
    """Spider/radar chart of the 6 fundamental-health pillars (0-100).
    Reuses the scanner's grade, so it's consistent with the scanner page."""
    if not radar or not radar.get("ok") or not radar.get("pillars"):
        return _empty("Fundamental Health",
                      radar.get("note") or "Health scores unavailable",
                      height=340)

    th = _THEME
    pillars = radar["pillars"]
    cats = list(pillars.keys())
    vals = list(pillars.values())
    # Close the loop for a clean polygon
    cats_closed = cats + [cats[0]]
    vals_closed = vals + [vals[0]]

    grade = radar.get("grade") or ""
    score = radar.get("score")
    title = "Fundamental Health"
    if grade:
        title = f"Fundamental Health — Grade {grade}"
        if score is not None:
            title += f" ({score:.0f}/100)"

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=vals_closed, theta=cats_closed, fill="toself",
        fillcolor="rgba(61,139,255,0.18)",
        line=dict(color=_C_EPS_ACT, width=2),
        marker=dict(size=6, color=_C_EPS_ACT),
        hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(color=th["text"], size=15,
                                          family=_FONT, weight=600),
                   x=0.0, xanchor="left"),
        paper_bgcolor=th["paper_bg"],
        plot_bgcolor=th["plot_bg"],
        font=dict(color=th["muted"], size=11, family=_FONT),
        height=340,
        margin=dict(l=40, r=40, t=54, b=30),
        showlegend=False,
        polar=dict(
            bgcolor=th["plot_bg"],
            radialaxis=dict(visible=True, range=[0, 100],
                            gridcolor=th["grid"], linecolor=th["axis"],
                            tickfont=dict(size=9, color=th["muted"]),
                            tickvals=[20, 40, 60, 80, 100]),
            angularaxis=dict(gridcolor=th["grid"], linecolor=th["axis"],
                             tickfont=dict(size=11, color=th["text"])),
        ),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────
# Shares outstanding — dilution (rising) vs buyback (falling)
# ─────────────────────────────────────────────────────────────────────────

def shares_outstanding_chart(bal: dict, ticker: str = "") -> go.Figure:
    """Shares outstanding over time. A rising line = dilution; a falling
    line = buybacks. Coloured by the net trend so the read is instant."""
    if not bal or not bal.get("ok") or not bal.get("periods"):
        return _empty("Shares Outstanding",
                      bal.get("note") or "No share-count data")
    periods = bal["periods"]
    shares = bal.get("shares") or []
    pts = [(str(p), s) for p, s in zip(periods, shares) if s is not None]
    if len(pts) < 2:
        return _empty("Shares Outstanding", "Not enough share-count history")
    xs = [p for p, _ in pts]
    ys = [s for _, s in pts]
    trend_up = ys[-1] > ys[0]
    line_col = "#e8a838" if trend_up else _C_PRICE
    # Show the % change over the window so a tiny-looking move has context.
    pct = (ys[-1] / ys[0] - 1) * 100 if ys[0] else 0
    label = (f"▲ diluting +{pct:.1f}%" if trend_up
             else f"▼ buying back {pct:.1f}%")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines+markers",
        line=dict(color=line_col, width=2.5),
        marker=dict(size=7, color=line_col),
        # NOTE: no fill-to-zero — that compresses small year-over-year moves
        # into an unreadable band at the top. We zoom the y-axis to the
        # actual data range instead, so the trend is visible.
        customdata=[_humanize_shares(v) for v in ys],
        hovertemplate="%{customdata} shares<br>%{x}<extra></extra>",
    ))
    fig = _base(fig, f"Shares Outstanding  {label}", height=300)
    # X as categories so years render as 2022/2023 (not 2,022.5 interpolated)
    fig.update_xaxes(type="category")
    _apply_shares_yaxis(fig, ys)
    return fig


def _humanize_shares(v, decimals: int = 2) -> str:
    """Format a share count as 1.23B / 456.7M shares."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "n/a"
    a = abs(n)
    if a >= 1e9:
        return f"{n/1e9:.{decimals}f}B"
    if a >= 1e6:
        return f"{n/1e6:.1f}M"
    if a >= 1e3:
        return f"{n/1e3:.0f}K"
    return f"{n:.0f}"


def _apply_shares_yaxis(fig, values):
    """Zoom the y-axis to the actual share-count range with 3 well-spaced,
    non-overlapping tick labels. Small year-over-year share moves are real
    signal (buybacks are gradual), so we DON'T anchor to zero — we frame the
    true range with modest padding."""
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return
    lo, hi = min(nums), max(nums)
    span = hi - lo
    if span <= 0:
        # Flat series — synthesize a small band so the line sits mid-chart.
        span = hi * 0.04 or 1
    pad = span * 0.6           # generous pad so the line isn't glued to edges
    y0, y1 = lo - pad, hi + pad
    # 3 ticks (bottom / mid / top) — few enough that labels never collide.
    tickvals = [y0, (y0 + y1) / 2, y1]
    # Enough precision that the three labels are distinct even for tiny ranges.
    rng = y1 - y0
    dec = 2 if rng >= 1e8 else 3
    ticktext = [_humanize_shares(v, decimals=dec) for v in tickvals]
    fig.update_yaxes(range=[y0, y1], tickvals=tickvals, ticktext=ticktext)


# ─────────────────────────────────────────────────────────────────────────
# Cash vs Debt — grouped bars (net cash position read)
# ─────────────────────────────────────────────────────────────────────────

def cash_debt_chart(bal: dict, ticker: str = "") -> go.Figure:
    """Cash (green) vs Debt (red) over time, grouped bars. Lets the user
    see whether the balance sheet is strengthening (cash up / debt down)."""
    if not bal or not bal.get("ok") or not bal.get("periods"):
        return _empty("Cash vs Debt",
                      bal.get("note") or "No balance-sheet data")
    periods = bal["periods"]
    cash = bal.get("cash") or []
    debt = bal.get("debt") or []
    if all(c is None for c in cash) and all(d is None for d in debt):
        return _empty("Cash vs Debt", "No cash/debt data for this filer")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=periods, y=cash, name="Cash", marker_color=_C_PRICE,
        customdata=[_humanize(v) for v in cash],
        hovertemplate="Cash: %{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(
        x=periods, y=debt, name="Debt", marker_color="#e5544b",
        customdata=[_humanize(v) for v in debt],
        hovertemplate="Debt: %{customdata}<extra></extra>"))
    fig = _base(fig, "Cash vs Debt", height=300, unified=True)
    fig.update_layout(
        barmode="group",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right",
                    x=1, font=dict(size=10, color=_THEME["muted"])))
    _apply_money_yaxis(fig, [v for v in (cash + debt) if v is not None])
    return fig
