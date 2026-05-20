"""
app.py — Market Deployment Gating System & Quantitative Scanner.

Run with:  streamlit run app.py
"""

import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme
import data_utils as du
from scanner_factors import factors

st.set_page_config(
    page_title="Deployment Gate",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme.inject_css()

# ── Session state ─────────────────────────────────────────────────────────────
if "watchlist" not in st.session_state:
    st.session_state.watchlist = du.DEFAULT_WATCHLIST.copy()
if "strategy" not in st.session_state:
    st.session_state.strategy = factors.TREND
if "macro_result" not in st.session_state:
    st.session_state.macro_result = None
if "macro_history" not in st.session_state:
    st.session_state.macro_history = None
if "scanner_result" not in st.session_state:
    st.session_state.scanner_result = None
if "universe_result" not in st.session_state:
    st.session_state.universe_result = None
if "backtest_cache" not in st.session_state:
    st.session_state.backtest_cache = {}
if "selected_rows" not in st.session_state:
    st.session_state.selected_rows = []
if "page" not in st.session_state:
    st.session_state.page = "Macro Gate"
if "last_strategy" not in st.session_state:
    st.session_state.last_strategy = factors.TREND


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🛰️ Deployment Gate")
        st.markdown("<div class='kicker'>Macro Gate × Quant Scanner</div>",
                    unsafe_allow_html=True)
        st.markdown("---")

        # ── Navigation ──
        page_options = ["Macro Gate", "Scanner & Status", "Backtest & Audit"]
        st.session_state.page = st.radio(
            "View", page_options,
            index=page_options.index(st.session_state.page)
                  if st.session_state.page in page_options else 0,
            label_visibility="collapsed",
        )
        st.markdown("---")

        # ── Strategy engine toggle ──
        st.markdown("### ⚙️ Active Strategy Engine")
        strategy = st.radio(
            "Strategy",
            [factors.TREND, factors.MEAN_REVERSION],
            index=0 if st.session_state.strategy == factors.TREND else 1,
            label_visibility="collapsed",
            help="Trend-Following rewards strength & momentum. "
                 "Mean Reversion rewards oversold capitulation setups.",
        )
        st.session_state.strategy = strategy
        engine_note = ("Rewards strength, momentum, and proximity to highs."
                       if strategy == factors.TREND else
                       "Rewards capitulation, oversold extremes, and squeeze setups.")
        st.markdown(f"<div class='tiny'>{engine_note}</div>",
                    unsafe_allow_html=True)
        st.markdown("---")

        # ── Watchlist manager (tag box) ──
        st.markdown("### 🎯 Target Watchlist")
        wl = st.multiselect(
            "Active tickers",
            options=sorted(set(st.session_state.watchlist) |
                           set(du.DEFAULT_WATCHLIST)),
            default=st.session_state.watchlist,
            label_visibility="collapsed",
            help="Remove tickers by clicking the ✕ on a tag.",
        )
        if wl != st.session_state.watchlist:
            st.session_state.watchlist = [t.upper().strip() for t in wl]

        new = st.text_input("Add ticker(s)", placeholder="e.g. AAPL, GOOGL",
                             label_visibility="collapsed")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("＋ Add", use_container_width=True):
                for raw in new.replace(",", " ").split():
                    t = raw.upper().strip()
                    if t and t not in st.session_state.watchlist:
                        st.session_state.watchlist.append(t)
                st.rerun()
        with c2:
            if st.button("↺ Reset", use_container_width=True):
                st.session_state.watchlist = du.DEFAULT_WATCHLIST.copy()
                st.rerun()

        st.markdown(
            f"<div class='tiny'>{len(st.session_state.watchlist)} tickers active</div>",
            unsafe_allow_html=True)
        st.markdown("---")

        if st.button("🔄  RUN FULL ANALYSIS", use_container_width=True):
            _run_all()

        st.markdown(
            "<div class='tiny'>Data via Yahoo Finance · cached 1 hr<br>"
            "Educational use only — not financial advice.</div>",
            unsafe_allow_html=True)


def _run_all():
    import run_macro_gate
    import run_scanner
    from macro_signals import macro_history
    strategy = st.session_state.strategy

    with st.spinner("Running 7 macro signals…"):
        st.session_state.macro_result = run_macro_gate.run(
            st.session_state.watchlist)
    macro = st.session_state.macro_result

    with st.spinner("Building 180-day historical regime timeline…"):
        st.session_state.macro_history = macro_history.regime_timeseries(
            st.session_state.watchlist)

    if macro.get("scanner_enabled", False):
        with st.spinner("Scanning S&P 500 universe for broad-market Top 5…"):
            st.session_state.universe_result = run_scanner.scan_universe(
                strategy, top_n=5)
        with st.spinner("Scanning custom watchlist…"):
            st.session_state.scanner_result = run_scanner.run(
                st.session_state.watchlist, strategy)
    else:
        st.session_state.universe_result = None
        st.session_state.scanner_result = None

    # strategy changed -> stale backtests must be recomputed
    st.session_state.backtest_cache = {}
    st.session_state.last_strategy = strategy


# ── Route ─────────────────────────────────────────────────────────────────────
render_sidebar()

# if the strategy toggle changed since the last run, flag stale results
if st.session_state.strategy != st.session_state.last_strategy:
    st.session_state.backtest_cache = {}

if st.session_state.page == "Macro Gate":
    from pages_lib import page_macro
    page_macro.render()
elif st.session_state.page == "Scanner & Status":
    from pages_lib import page_scanner
    page_scanner.render()
else:
    from pages_lib import page_backtest
    page_backtest.render()
