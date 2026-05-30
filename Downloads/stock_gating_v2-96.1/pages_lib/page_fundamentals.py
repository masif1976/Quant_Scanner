"""
page_fundamentals.py — Fundamental Insights page (presentation layer ONLY).

This is the THIN layer: it calls fundamental_data (fetch) and
fundamental_charts (build figures), then lays everything out with Streamlit.
It contains no financial math and no data fetching of its own — that lives
in the two pure modules, exactly as the spec's isolation rule requires.

Layout (per spec):
  1. Global index banner (Dow / S&P 500 / Nasdaq) — always visible
  2. Default landing state (no ticker): big "Insights" title, search bar,
     category pills, responsive grid of summary cards
  3. Stock detail state (ticker entered): price header + 24h news,
     5-group metric grid, timeframe toggle, 3-col chart grid, deep-dive
"""

from __future__ import annotations

import streamlit as st

import theme
import fundamental_data as fd
import fundamental_charts as fc


# Landing-grid default tickers per category (kept small to respect free-tier
# rate limits — these load via yfinance, which is cached).
_CATEGORY_TICKERS = {
    "S&P 500": ["NVDA", "MSFT", "AAPL", "META"],
    "Most Trending": ["NVDA", "TSLA", "PLTR", "COIN"],
    "Growth": ["NVDA", "AMD", "CRWD", "NOW"],
    "Dividend Growth": ["AAPL", "MSFT", "JNJ", "PG"],
}


def _fmt_big(v) -> str:
    """Format a large dollar number as $X.XXT / $X.XXB / $X.XXM."""
    if v is None:
        return "n/a"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "n/a"
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1e12:
        return f"{sign}${a/1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.2f}K"
    return f"{sign}${a:,.2f}"


def _fmt_metric(v, kind="num") -> tuple[str, bool]:
    """Format a metric value for display.

    Returns (display_string, is_derived). Handles the three states:
      - None                       -> ("n/a", False)
      - {"value":x,"derived":True} -> (formatted x, True)
      - plain number               -> (formatted, False)
    """
    derived = False
    if isinstance(v, dict) and "value" in v:
        derived = bool(v.get("derived"))
        v = v["value"]
    if v is None:
        return ("n/a", False)
    try:
        n = float(v)
    except (TypeError, ValueError):
        return (str(v), derived)
    if kind == "big":
        return (_fmt_big(n), derived)
    if kind == "pct":
        return (f"{n:.2f}%", derived)
    if kind == "x":
        return (f"{n:.2f}×", derived)
    if kind == "money":
        return (f"${n:,.2f}", derived)
    return (f"{n:,.2f}", derived)


# ─────────────────────────────────────────────────────────────────────────
# 1. Global index banner
# ─────────────────────────────────────────────────────────────────────────

def _render_index_banner():
    indices = fd.get_index_banner()
    cols = st.columns(len(indices))
    for col, idx in zip(cols, indices):
        with col:
            if not idx["ok"] or idx["price"] is None:
                col.markdown(
                    f"<div style='background:{theme.PANEL};border:1px solid "
                    f"{theme.BORDER};border-radius:8px;padding:8px 14px'>"
                    f"<span style='color:{theme.MUTED};font-size:0.78rem'>"
                    f"{idx['name']}</span><br>"
                    f"<span style='color:{theme.MUTED}'>—</span></div>",
                    unsafe_allow_html=True)
                continue
            chg = idx["change_pct"]
            chg_color = theme.GREEN if (chg or 0) >= 0 else theme.RED
            arrow = "▲" if (chg or 0) >= 0 else "▼"
            chg_txt = f"{arrow} {abs(chg):.2f}%" if chg is not None else "—"
            col.markdown(
                f"<div style='background:{theme.PANEL};border:1px solid "
                f"{theme.BORDER};border-radius:8px;padding:8px 14px'>"
                f"<span style='color:{theme.MUTED};font-size:0.78rem'>"
                f"{idx['name']}</span><br>"
                f"<span style='color:{theme.TEXT};font-size:1.1rem;"
                f"font-weight:700'>{idx['price']:,.2f}</span> "
                f"<span style='color:{chg_color};font-size:0.85rem'>"
                f"{chg_txt}</span></div>",
                unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────
# 2. Landing state
# ─────────────────────────────────────────────────────────────────────────

def _render_landing():
    st.markdown(
        f"<div style='text-align:center;margin:32px 0 8px 0'>"
        f"<span style='font-family:Sora;font-size:2.6rem;font-weight:800;"
        f"color:{theme.TEXT};letter-spacing:-1px'>Insights</span></div>",
        unsafe_allow_html=True)
    st.markdown(
        f"<div style='text-align:center;color:{theme.MUTED};"
        f"font-size:0.9rem;margin-bottom:20px'>"
        f"Search a ticker for a full fundamental breakdown</div>",
        unsafe_allow_html=True)

    # Search bar + button
    c1, c2, c3 = st.columns([1, 3, 1])
    with c2:
        sc1, sc2 = st.columns([4, 1])
        with sc1:
            query = st.text_input(
                "Search ticker", key="fund_search_input",
                placeholder="e.g. NVDA, MSFT, AAPL…",
                label_visibility="collapsed")
        with sc2:
            if st.button("Search", width="stretch", type="primary"):
                if query and query.strip():
                    st.session_state.fund_ticker = query.upper().strip()
                    st.rerun()

    # Category pills
    cat = st.radio(
        "Category", list(_CATEGORY_TICKERS.keys()),
        horizontal=True, label_visibility="collapsed",
        key="fund_category")

    # Responsive grid of summary cards
    tickers = _CATEGORY_TICKERS.get(cat, [])
    cards = fd.get_summary_cards(tickers)
    if not cards:
        st.info("Summary cards unavailable right now (data feed).")
        return
    grid = st.columns(len(cards))
    for col, card in zip(grid, cards):
        with col:
            chg = card.get("change_pct")
            chg_color = theme.GREEN if (chg or 0) >= 0 else theme.RED
            chg_txt = (f"{'+' if (chg or 0) >= 0 else ''}{chg:.2f}%"
                       if chg is not None else "—")
            price_txt = (f"${card['price']:,.2f}"
                         if card.get("price") is not None else "n/a")
            if st.button(f"{card['ticker']}", key=f"card_{card['ticker']}",
                          width="stretch"):
                st.session_state.fund_ticker = card["ticker"]
                st.rerun()
            st.markdown(
                f"<div style='background:{theme.PANEL};border:1px solid "
                f"{theme.BORDER};border-radius:8px;padding:10px 12px;"
                f"margin-top:-8px'>"
                f"<div style='color:{theme.MUTED};font-size:0.72rem;"
                f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>"
                f"{card.get('name', card['ticker'])}</div>"
                f"<div style='color:{theme.TEXT};font-size:1.05rem;"
                f"font-weight:700'>{price_txt}</div>"
                f"<div style='color:{chg_color};font-size:0.8rem'>{chg_txt}</div>"
                f"<div style='color:{theme.MUTED};font-size:0.72rem;"
                f"margin-top:2px'>MCap {_fmt_big(card.get('market_cap'))}</div>"
                f"</div>",
                unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────
# 3. Detail state
# ─────────────────────────────────────────────────────────────────────────

def _render_metric_row(label, value, kind="num"):
    disp, derived = _fmt_metric(value, kind)
    flag = (f" <span style='color:{theme.YELLOW};font-size:0.62rem' "
            f"title='Derived, not directly sourced'>◆</span>"
            if derived else "")
    color = theme.MUTED if disp == "n/a" else theme.TEXT
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"padding:3px 0;border-bottom:1px solid {theme.BORDER}44'>"
        f"<span style='color:{theme.MUTED};font-size:0.78rem'>{label}</span>"
        f"<span style='color:{color};font-size:0.82rem;font-weight:600'>"
        f"{disp}{flag}</span></div>",
        unsafe_allow_html=True)


def _render_detail(ticker: str):
    # Back button
    if st.button("← Back to Insights", key="fund_back"):
        st.session_state.pop("fund_ticker", None)
        st.rerun()

    header = fd.get_quote_header(ticker)
    if header is None:
        st.error(f"Couldn't load data for **{ticker}**. Check the symbol or "
                 f"try again — the data feed may be rate-limited.")
        return

    # ── Price header ──
    price = header.get("price")
    chg_abs = header.get("change_abs")
    chg_pct = header.get("change_pct")
    chg_color = theme.GREEN if (chg_pct or 0) >= 0 else theme.RED
    name = header.get("name") or ticker
    ne = header.get("next_earnings")

    hc1, hc2 = st.columns([3, 2])
    with hc1:
        st.markdown(
            f"<div style='font-family:Sora;font-size:1.6rem;font-weight:800;"
            f"color:{theme.TEXT}'>{ticker} "
            f"<span style='color:{theme.MUTED};font-size:0.9rem;"
            f"font-weight:400'>{name}</span></div>"
            f"<div><span style='color:{theme.TEXT};font-size:1.8rem;"
            f"font-weight:700'>"
            f"{'$%.2f' % price if price is not None else 'n/a'}</span> "
            f"<span style='color:{chg_color};font-size:1rem'>"
            f"{('%+.2f' % chg_abs) if chg_abs is not None else ''} "
            f"({('%+.2f%%' % chg_pct) if chg_pct is not None else 'n/a'})"
            f"</span></div>",
            unsafe_allow_html=True)
    with hc2:
        st.markdown(
            f"<div style='text-align:right'>"
            f"<span style='color:{theme.MUTED};font-size:0.78rem'>"
            f"Next earnings</span><br>"
            f"<span style='color:{theme.TEXT};font-size:1rem;font-weight:600'>"
            f"{ne if ne else 'n/a'}</span></div>",
            unsafe_allow_html=True)

    # ── 24h news ──
    news = fd.get_recent_news(ticker, n_items=4)
    if news:
        st.markdown(
            f"<div style='color:{theme.MUTED};font-size:0.78rem;"
            f"margin:10px 0 4px 0'>Recent news</div>", unsafe_allow_html=True)
        for n in news:
            headline = n.get("headline", "")
            src = n.get("source", "")
            url = n.get("url", "")
            if not headline:
                continue
            link = (f"<a href='{url}' target='_blank' "
                    f"style='color:{theme.ACCENT};text-decoration:none'>"
                    f"{headline}</a>") if url else headline
            st.markdown(
                f"<div style='font-size:0.8rem;color:{theme.TEXT};"
                f"padding:2px 0'>• {link} "
                f"<span style='color:{theme.MUTED};font-size:0.7rem'>"
                f"{src}</span></div>", unsafe_allow_html=True)

    st.markdown("---")

    # ── Key-metrics grid (5 groups) ──
    metrics = fd.get_key_metrics(ticker)
    st.markdown(
        f"<div style='font-family:Sora;font-size:1rem;font-weight:600;"
        f"color:{theme.TEXT};margin-bottom:6px'>Key Metrics "
        f"<span style='color:{theme.YELLOW};font-size:0.66rem'>◆ = derived"
        f"</span></div>", unsafe_allow_html=True)

    g1, g2, g3, g4, g5 = st.columns(5)
    val = metrics["valuation"]
    cf = metrics["cash_flow"]
    mg = metrics["margins_growth"]
    bs = metrics["balance_sheet"]
    de = metrics["dividends_est"]

    with g1:
        st.markdown(f"<b style='color:{theme.ACCENT};font-size:0.8rem'>"
                    f"Valuation</b>", unsafe_allow_html=True)
        _render_metric_row("Market Cap", val.get("Market Cap"), "big")
        _render_metric_row("Trailing P/E", val.get("Trailing P/E"), "x")
        _render_metric_row("Forward P/E", val.get("Forward P/E"), "x")
        _render_metric_row("Price/Sales", val.get("Price / Sales"), "x")
        _render_metric_row("EV/EBITDA", val.get("EV / EBITDA"), "x")
        _render_metric_row("Price/Book", val.get("Price / Book"), "x")
    with g2:
        st.markdown(f"<b style='color:{theme.ACCENT};font-size:0.8rem'>"
                    f"Cash Flow</b>", unsafe_allow_html=True)
        _render_metric_row("FCF Yield", cf.get("FCF Yield"), "pct")
        _render_metric_row("FCF/Share", cf.get("FCF / Share"), "money")
        _render_metric_row("SBC-Adj FCF Yld", cf.get("SBC-Adj FCF Yield"), "pct")
    with g3:
        st.markdown(f"<b style='color:{theme.ACCENT};font-size:0.8rem'>"
                    f"Margins & Growth</b>", unsafe_allow_html=True)
        _render_metric_row("Profit Margin", mg.get("Profit Margin"), "pct")
        _render_metric_row("Op Margin", mg.get("Operating Margin"), "pct")
        _render_metric_row("Earnings Grw", mg.get("Earnings Growth (YoY)"), "pct")
        _render_metric_row("Revenue Grw", mg.get("Revenue Growth (YoY)"), "pct")
    with g4:
        st.markdown(f"<b style='color:{theme.ACCENT};font-size:0.8rem'>"
                    f"Balance Sheet</b>", unsafe_allow_html=True)
        _render_metric_row("Total Cash", bs.get("Total Cash"), "big")
        _render_metric_row("Total Debt", bs.get("Total Debt"), "big")
        _render_metric_row("Net Cash/(Debt)", bs.get("Net Cash / (Debt)"), "big")
    with g5:
        st.markdown(f"<b style='color:{theme.ACCENT};font-size:0.8rem'>"
                    f"Dividends & Est.</b>", unsafe_allow_html=True)
        _render_metric_row("Div Yield", de.get("Dividend Yield"), "pct")
        _render_metric_row("Payout Ratio", de.get("Payout Ratio"), "pct")
        _render_metric_row("Analyst Target", de.get("Analyst Target"), "money")

    st.markdown("---")

    # ── Health radar + Fair-value range (side by side) ──
    cfg = fc.chart_config()
    hv1, hv2 = st.columns(2)
    with hv1:
        radar = fd.get_health_radar(ticker)
        st.plotly_chart(fc.health_radar_chart(radar, ticker),
                        use_container_width=True, config=cfg)
        if radar.get("note") and not radar.get("ok"):
            st.caption(f"ℹ Health: {radar['note']}")
        else:
            st.caption("ℹ Health pillars reuse the Scanner's 6-pillar grade "
                       "— same scoring, shown as a radar.")
    with hv2:
        # Fair-value with adjustable DCF assumptions (sliders) — because DCF
        # output swings hugely with these, the user MUST be able to stress them.
        with st.expander("⚙️ DCF assumptions", expanded=False):
            fc1, fc2 = st.columns(2)
            with fc1:
                g = st.slider("FCF growth %/yr", 0.0, 25.0,
                              st.session_state.get("fv_growth", 8.0), 0.5,
                              key="fv_growth")
                term = st.slider("Terminal growth %", 0.0, 5.0,
                                 st.session_state.get("fv_terminal", 3.0), 0.25,
                                 key="fv_terminal")
            with fc2:
                disc = st.slider("Discount rate %", 5.0, 20.0,
                                 st.session_state.get("fv_discount", 10.0), 0.5,
                                 key="fv_discount")
                yrs = st.slider("Projection years", 3, 10,
                                st.session_state.get("fv_years", 5), 1,
                                key="fv_years")
        fv = fd.get_fair_value(ticker, dcf_growth_pct=g, dcf_discount_pct=disc,
                               dcf_terminal_pct=term, dcf_years=yrs)
        st.plotly_chart(fc.fair_value_chart(fv, ticker),
                        use_container_width=True, config=cfg)
        if fv.get("ok") and fv.get("low") is not None:
            price = fv.get("current_price")
            stance = ""
            if price and fv["low"] and fv["high"]:
                if price < fv["low"]:
                    stance = "below every estimate (potentially undervalued)"
                elif price > fv["high"]:
                    stance = "above every estimate (potentially overvalued)"
                else:
                    stance = "within the estimate range (roughly in-line)"
            st.caption(f"ℹ Range ${fv['low']:,.2f}–${fv['high']:,.2f}. "
                       f"Price is {stance}. These are model outputs, not "
                       f"targets — DCF especially is highly assumption-driven.")
        elif fv.get("note"):
            st.caption(f"ℹ Fair value: {fv['note']}")

    st.markdown("---")

    # ── Timeframe toggle ──
    tf = st.radio(
        "Statement period",
        ["Annually", "Quarterly"],
        horizontal=True, key="fund_timeframe",
        help="Switch the financial-statement charts between annual and "
             "quarterly periods.")
    period = "quarterly" if tf == "Quarterly" else "annual"

    # ── Chart grid: 2 columns, ONE metric per chart (Qualtrim-style) ──
    # The previous 3-column paired layout crammed everything; matching the
    # reference, each metric gets its own chart with room to breathe.
    ts = fd.get_financial_timeseries(ticker, period=period)
    price_df = fd.get_price_series(ticker, days=365)
    es = fd.get_earnings_surprises(ticker, n_quarters=4)
    periods = ts.get("periods") or []

    # Row 1: Price | Revenue
    r1c1, r1c2 = st.columns(2)
    with r1c1:
        st.plotly_chart(fc.price_area_chart(price_df, ticker),
                        use_container_width=True, config=cfg)
    with r1c2:
        st.plotly_chart(
            fc.single_metric_bar(periods, ts.get("revenue"),
                                  "Revenue", fc._C_REVENUE),
            use_container_width=True, config=cfg)

    # Row 2: EBITDA | Net Income
    r2c1, r2c2 = st.columns(2)
    with r2c1:
        st.plotly_chart(
            fc.single_metric_bar(periods, ts.get("ebitda"),
                                  "EBITDA", fc._C_EBITDA),
            use_container_width=True, config=cfg)
    with r2c2:
        st.plotly_chart(
            fc.single_metric_bar(periods, ts.get("net_income"),
                                  "Net Income", fc._C_NETINCOME,
                                  sign_color=True),
            use_container_width=True, config=cfg)

    # Row 3: Free Cash Flow | Earnings Surprise
    r3c1, r3c2 = st.columns(2)
    with r3c1:
        st.plotly_chart(
            fc.single_metric_bar(periods, ts.get("fcf"),
                                  "Free Cash Flow", fc._C_FCF,
                                  sign_color=True),
            use_container_width=True, config=cfg)
    with r3c2:
        st.plotly_chart(fc.earnings_surprise_chart(es, ticker),
                        use_container_width=True, config=cfg)

    if ts.get("note"):
        st.caption(f"ℹ Statement data: {ts['note']}")
    elif ts.get("source") == "sec_edgar":
        st.caption("ℹ Statement data: SEC EDGAR (as-reported, deep history)")
    if es.get("note"):
        st.caption(f"ℹ Earnings data: {es['note']}")

    # ── Deep-dive (expander with in-chart dropdown) ──
    with st.expander("🔬 Deep-dive: long-term profitability (ROCE / margins)"):
        hist = fd.get_profitability_history(ticker)
        st.plotly_chart(fc.profitability_dropdown_chart(hist, ticker),
                        use_container_width=True, config=cfg)
        if hist.get("note"):
            st.caption(f"ℹ {hist['note']}")
        st.caption("Use the buttons inside the chart to switch between "
                   "ROCE, Gross Margin, and Operating Margin.")


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def render():
    """Page entry point called by app.py dispatch."""
    # Sync the chart palette with the app's active theme so charts match
    # the shell (light charts on light shell, dark on dark).
    fc.set_theme(theme.get_mode())

    _render_index_banner()
    st.markdown("")

    ticker = st.session_state.get("fund_ticker")
    if ticker:
        _render_detail(ticker)
    else:
        _render_landing()

    st.markdown(
        f"<div style='color:{theme.MUTED};font-size:0.7rem;margin-top:20px;"
        f"text-align:center'>Educational tool, not financial advice. "
        f"Data from yfinance &amp; Finnhub — free-tier coverage is "
        f"incomplete; ◆ marks derived values, “n/a” marks unavailable "
        f"data.</div>", unsafe_allow_html=True)
