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
    page_title="Trading Dashboard",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────
if "watchlist" not in st.session_state:
    # Load persisted watchlist from disk. On first run (empty DB) this
    # seeds with DEFAULT_WATCHLIST so the user starts with the canonical
    # 23-ticker list. After that, sidebar edits write back to the DB and
    # survive across page refreshes / browser restarts.
    import watchlist_db
    st.session_state.watchlist = watchlist_db.load_watchlist(
        default=du.DEFAULT_WATCHLIST)
if "strategy" not in st.session_state:
    st.session_state.strategy = factors.TREND
if "macro_result" not in st.session_state:
    st.session_state.macro_result = None
if "macro_history" not in st.session_state:
    st.session_state.macro_history = None
if "scanner_result" not in st.session_state:
    st.session_state.scanner_result = None
if "backtest_cache" not in st.session_state:
    st.session_state.backtest_cache = {}
if "selected_rows" not in st.session_state:
    st.session_state.selected_rows = []
if "page" not in st.session_state:
    st.session_state.page = "MarketSense"
if "last_strategy" not in st.session_state:
    st.session_state.last_strategy = factors.TREND
if "horizon" not in st.session_state:
    # Default per spec: Swing Trade System
    st.session_state.horizon = factors.SWING
if "last_horizon" not in st.session_state:
    st.session_state.last_horizon = factors.SWING
if "text_size" not in st.session_state:
    # User-controlled body-text scale: "small" | "medium" (default) | "large".
    # Scales explanatory text (.tiny, .kicker, metric cards, footnotes) only.
    # Headings, gauges, scores, and tables stay at their tuned sizes so charts
    # don't reflow.
    st.session_state.text_size = "medium"


# Inject CSS AFTER session state init so it can read the text-size preference.
# The scale factor is applied to body-text rules only (Scope A).
_TEXT_SCALE = {"small": 0.88, "medium": 1.0, "large": 1.18}
theme.inject_css(text_scale=_TEXT_SCALE.get(
    st.session_state.text_size, 1.0))


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🧭 Trading Dashboard")
        st.markdown("<div class='kicker'>MarketSense × Quant Scanner</div>",
                    unsafe_allow_html=True)
        st.markdown("---")

        # ── Navigation ──
        page_options = ["MarketSense", "Scanner & Status", "Backtest & Audit",
                        "Strategy Backtest", "Trade Journal", "Positions",
                        "Options Wheel"]
        st.session_state.page = st.radio(
            "View", page_options,
            index=page_options.index(st.session_state.page)
                  if st.session_state.page in page_options else 0,
            label_visibility="collapsed",
        )
        st.markdown("---")

        # ── Strategy engine toggle ──
        st.markdown("### ⚙️ Active Strategy Engine")
        # Use format_func to render a friendlier display label without
        # changing the internal value — every strategy-aware function in
        # the engine identifies the strategy by its canonical name string.
        # Changing the value would break the journal, session state, and
        # strategy-keyed weight dicts.
        _strategy_labels = {
            factors.TREND:          "Trend-Following",
            factors.MEAN_REVERSION: "Mean Reversion · Dip Buyer / Peak Shorter",
        }
        strategy = st.radio(
            "Strategy",
            [factors.TREND, factors.MEAN_REVERSION],
            index=0 if st.session_state.strategy == factors.TREND else 1,
            label_visibility="collapsed",
            format_func=lambda s: _strategy_labels.get(s, s),
            help="Trend-Following rewards strength & momentum. "
                 "Mean Reversion rewards oversold capitulation setups "
                 "(dip-buying on longs, peak-shorting on shorts).",
        )
        st.session_state.strategy = strategy
        engine_note = ("Rewards strength, momentum, and proximity to highs."
                       if strategy == factors.TREND else
                       "Rewards capitulation, oversold extremes, and squeeze setups.")
        st.markdown(f"<div class='tiny'>{engine_note}</div>",
                    unsafe_allow_html=True)
        st.markdown("---")

        # ── Trading Horizon toggle ──
        st.markdown("### ⏱️ Trading Horizon")
        horizon = st.radio(
            "Horizon",
            [factors.SWING, factors.LONG_TERM],
            index=0 if st.session_state.horizon == factors.SWING else 1,
            label_visibility="collapsed",
            help="Switches lookback windows across the engine. Swing uses "
                 "tactical 1-4 week windows (e.g. 10/50 SMA, 5/20 volume). "
                 "Long-Term stretches to position-trade scale (50/200 SMA, "
                 "20/60 volume). Same math; same composite formula; same "
                 "regime rules — only window sizes change.",
        )

        # Detect a horizon switch BEFORE updating session state, so we can
        # invalidate any results that were computed under the prior horizon.
        # Without this, the page would keep displaying Swing numbers under
        # a "Long-Term" header — silently misleading.
        horizon_changed = (st.session_state.horizon != horizon)
        st.session_state.horizon = horizon

        if horizon_changed:
            # invalidate cached engine outputs — fetches will need different
            # history windows under the new horizon
            for key in ("macro_result", "scanner_result", "backtest_cache",
                         "macro_history"):
                if key in st.session_state:
                    del st.session_state[key]
            # also clear the @st.cache_data layer — Long-Term factor windows
            # need more history days than what Swing fetched, so reusing the
            # prior fetches risks short-history calculations
            du.clear_cache()
            st.session_state.last_horizon = horizon

            # Auto-rerun the full analysis under the new horizon. The user
            # asked us to do this rather than make them click "Run Full Macro
            # Analysis" manually. Macro + history + scanner all execute
            # synchronously here as part of the same Streamlit run, so the
            # page that renders next already has fresh horizon-aware results.
            if st.session_state.watchlist:
                st.toast(f"⏱️ Switched to {horizon} — recomputing under "
                          f"the new lookback windows…", icon="🔄")
                _run_all()
            else:
                st.toast(f"⏱️ Switched to {horizon}. Add tickers to "
                          f"recompute.", icon="🔄")

        horizon_note = (
            "Tactical 1-4 week windows: 10/50 EMA, 5/20 volume, 20d RS."
            if horizon == factors.SWING else
            "Position-trade windows: 50/200 EMA, 20/60 volume, 60d RS, "
            "200-SMA sector breadth.")
        st.markdown(f"<div class='tiny'>{horizon_note}</div>",
                    unsafe_allow_html=True)
        st.markdown("---")

        # ── Watchlist manager (tag box) ──
        # Edits in this section write through to the watchlist DB so the
        # list survives across page refreshes and browser restarts.
        import watchlist_db
        st.markdown("### 🎯 Target Watchlist")

        # ── Multi-watchlist switcher ──
        # Dropdown of all named lists. Switching changes the global active
        # list, which propagates to load_watchlist() / save_watchlist() /
        # add_ticker() / remove_ticker() — every existing call site that
        # writes to "the watchlist" now writes to the active list.
        all_lists = watchlist_db.list_watchlists()
        active_name = watchlist_db.get_active_watchlist_name()
        list_names = [l["name"] for l in all_lists]

        if list_names:
            try:
                active_idx = list_names.index(active_name)
            except ValueError:
                active_idx = 0
            picked_list = st.selectbox(
                "Active list",
                options=list_names,
                index=active_idx,
                key="wl_active_picker",
                help="Switch which watchlist the dashboard scans. Each list "
                     "has its own tickers; switching here doesn't lose data "
                     "in the other lists.")
            if picked_list != active_name:
                # User picked a different list — switch active + reload
                watchlist_db.set_active_watchlist(picked_list)
                st.session_state.watchlist = watchlist_db.load_watchlist(
                    default=du.DEFAULT_WATCHLIST)
                # Invalidate Put Finder cached scans (they were scoped to
                # the previous list)
                for k in ("put_finder_results", "put_finder_scanned_tickers",
                            "wheel_selected_tickers"):
                    if k in st.session_state:
                        del st.session_state[k]
                st.rerun()

        # ── Manage watchlists expander (Create / Rename / Delete) ──
        with st.expander("⚙️ Manage watchlists", expanded=False):
            mode = st.radio(
                "Action",
                options=["Create new", "Rename current", "Delete"],
                horizontal=True,
                key="wl_mgmt_mode",
                label_visibility="collapsed")

            if mode == "Create new":
                new_name = st.text_input(
                    "New watchlist name",
                    key="wl_create_name",
                    placeholder="e.g. Tech Plays, Earnings Week",
                    label_visibility="collapsed")
                copy_from = st.selectbox(
                    "Start with tickers from:",
                    options=["(empty)"] + list_names,
                    key="wl_create_copy_from",
                    help="Optionally copy tickers from an existing list. "
                         "Choose '(empty)' to start blank.")
                if st.button("＋ Create", width="stretch",
                              key="wl_create_btn"):
                    src = None if copy_from == "(empty)" else copy_from
                    if watchlist_db.create_watchlist(new_name, copy_from=src):
                        # Switch to the new list immediately
                        watchlist_db.set_active_watchlist(new_name)
                        st.session_state.watchlist = (
                            watchlist_db.load_watchlist(
                                default=du.DEFAULT_WATCHLIST))
                        for k in ("put_finder_results",
                                    "put_finder_scanned_tickers",
                                    "wheel_selected_tickers"):
                            if k in st.session_state:
                                del st.session_state[k]
                        st.rerun()
                    else:
                        st.error(
                            "Couldn't create. Name must be 1-50 chars and "
                            "not already in use.")

            elif mode == "Rename current":
                new_name = st.text_input(
                    f"Rename '{active_name}' to:",
                    key="wl_rename_input",
                    placeholder="new name (1-50 chars)",
                    label_visibility="collapsed")
                if st.button("✎ Rename", width="stretch",
                              key="wl_rename_btn"):
                    if watchlist_db.rename_watchlist(active_name, new_name):
                        st.rerun()
                    else:
                        st.error(
                            "Couldn't rename. New name must be 1-50 chars "
                            "and not already taken.")

            elif mode == "Delete":
                if len(all_lists) == 1:
                    st.info(
                        "Cannot delete the only watchlist. Create another "
                        "first.")
                else:
                    to_delete = st.selectbox(
                        "Watchlist to delete",
                        options=list_names,
                        index=list_names.index(active_name)
                              if active_name in list_names else 0,
                        key="wl_delete_picker")
                    target_count = next(
                        (l["ticker_count"] for l in all_lists
                         if l["name"] == to_delete), 0)
                    st.markdown(
                        f"<div class='tiny' style='color:#ff9442;"
                        f"margin:8px 0'>This deletes <b>{to_delete}</b> "
                        f"and all its <b>{target_count}</b> tickers. "
                        f"Cannot be undone.</div>",
                        unsafe_allow_html=True)
                    confirm = st.checkbox(
                        f"Yes, delete '{to_delete}'",
                        key="wl_delete_confirm")
                    if st.button("🗑 Delete", width="stretch",
                                  key="wl_delete_btn",
                                  disabled=not confirm):
                        ok, msg = watchlist_db.delete_watchlist(to_delete)
                        if ok:
                            # If active list was deleted, reload the new one
                            st.session_state.watchlist = (
                                watchlist_db.load_watchlist(
                                    default=du.DEFAULT_WATCHLIST))
                            for k in ("put_finder_results",
                                        "put_finder_scanned_tickers",
                                        "wheel_selected_tickers"):
                                if k in st.session_state:
                                    del st.session_state[k]
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

        st.markdown(
            f"<div class='tiny' style='color:#7d8aa5;margin:4px 0 8px 0'>"
            f"Editing: <b>{active_name}</b></div>",
            unsafe_allow_html=True)

        wl = st.multiselect(
            "Active tickers",
            options=sorted(set(st.session_state.watchlist) |
                           set(du.DEFAULT_WATCHLIST)),
            default=st.session_state.watchlist,
            label_visibility="collapsed",
            help="Remove tickers by clicking the ✕ on a tag. Changes "
                 "auto-save to disk immediately — your edits persist "
                 "across app restarts and won't be undone by reloading.",
        )
        if wl != st.session_state.watchlist:
            cleaned = [t.upper().strip() for t in wl]
            st.session_state.watchlist = cleaned
            watchlist_db.save_watchlist(cleaned)

        new = st.text_input("Add ticker(s)", placeholder="e.g. AAPL, GOOGL",
                             label_visibility="collapsed")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("＋ Add", width="stretch"):
                changed = False
                for raw in new.replace(",", " ").split():
                    t = raw.upper().strip()
                    if t and t not in st.session_state.watchlist:
                        st.session_state.watchlist.append(t)
                        watchlist_db.add_ticker(t)
                        changed = True
                if changed:
                    st.rerun()
        with c2:
            if st.button("↺ Reset", width="stretch",
                          help="Restore the default 23-ticker watchlist. "
                               "Overwrites the persisted list."):
                st.session_state.watchlist = du.DEFAULT_WATCHLIST.copy()
                watchlist_db.reset_to_default(du.DEFAULT_WATCHLIST)
                st.rerun()

        st.markdown(
            f"<div class='tiny'>{len(st.session_state.watchlist)} tickers active</div>",
            unsafe_allow_html=True)
        st.markdown("---")

        if st.button("🔄  RUN FULL ANALYSIS", width="stretch"):
            _run_all()

        # ── settings expander ──
        # Lives at the bottom of the sidebar so it's discoverable but not
        # visually intrusive. Currently houses just the text-size toggle;
        # future preferences (theme, density, etc.) can land here too.
        with st.expander("⚙️ Settings"):
            new_size = st.radio(
                "Text size",
                options=["small", "medium", "large"],
                index=["small", "medium", "large"].index(
                    st.session_state.text_size),
                format_func=lambda s: {"small": "Small (compact)",
                                        "medium": "Medium (default)",
                                        "large": "Large (high readability)"}[s],
                key="text_size_radio",
                help="Scales body text (descriptions, footnotes, metric "
                     "definitions). Headings, gauges, and tables stay at "
                     "their tuned sizes so charts don't reflow. Browser "
                     "zoom (Cmd/Ctrl +) is also available and scales "
                     "everything including charts.",
            )
            if new_size != st.session_state.text_size:
                st.session_state.text_size = new_size
                st.rerun()

        st.markdown(
            "<div class='tiny'>Data via Yahoo Finance · cached 1 hr<br>"
            "Educational use only — not financial advice.</div>",
            unsafe_allow_html=True)


def _run_all():
    import run_macro_gate
    import run_scanner
    from macro_signals import macro_history
    strategy = st.session_state.strategy
    horizon = st.session_state.get("horizon", "Swing Trade System")

    with st.spinner("Running 7 macro signals…"):
        st.session_state.macro_result = run_macro_gate.run(
            st.session_state.watchlist, horizon=horizon)
    macro = st.session_state.macro_result

    with st.spinner("Building 180-day historical regime timeline…"):
        st.session_state.macro_history = macro_history.regime_timeseries(
            st.session_state.watchlist)

    if macro.get("scanner_enabled", False):
        m_score = macro.get("composite_score")
        # NOTE: scan_universe() was removed — it computed a "Broad Market Top 5"
        # ranking using only 45% of the live engine's weights (skipped Options
        # Flow + Short Interest, the two highest-weighted factors), and the
        # result was never displayed in any UI. Bringing it back honestly would
        # require historical SI + options flow series, which yfinance can't
        # provide for free. Custom watchlist scan still runs with full weights.
        with st.spinner("Scanning custom watchlist…"):
            st.session_state.scanner_result = run_scanner.run(
                st.session_state.watchlist, strategy,
                macro_score=m_score, horizon=horizon)
            # journal the scan so Page 5 has data to evaluate
            import signal_journal as sj
            st.session_state.current_scan_id = sj.log_scan(
                st.session_state.scanner_result)
    else:
        st.session_state.scanner_result = None

    # strategy changed -> stale backtests must be recomputed
    st.session_state.backtest_cache = {}
    st.session_state.last_strategy = strategy
    st.session_state.last_horizon = horizon


# ── Route ─────────────────────────────────────────────────────────────────────
render_sidebar()

# if the strategy toggle changed since the last run, the scanner_result is
# stale (TREND vs MR produce very different scores from the same underlying
# data) and the backtest cache needs to recompute too. macro_result is NOT
# strategy-dependent so it stays valid.
if st.session_state.strategy != st.session_state.last_strategy:
    for key in ("scanner_result", "backtest_cache"):
        if key in st.session_state:
            if key == "backtest_cache":
                st.session_state[key] = {}
            else:
                del st.session_state[key]
    st.session_state.last_strategy = st.session_state.strategy

if st.session_state.page == "MarketSense":
    from pages_lib import page_macro
    page_macro.render()
elif st.session_state.page == "Scanner & Status":
    from pages_lib import page_scanner
    page_scanner.render()
elif st.session_state.page == "Strategy Backtest":
    from pages_lib import page_strategy_backtest
    page_strategy_backtest.render()
elif st.session_state.page == "Trade Journal":
    from pages_lib import page_journal
    page_journal.render()
elif st.session_state.page == "Positions":
    from pages_lib import page_portfolio
    page_portfolio.render()
elif st.session_state.page == "Options Wheel":
    from pages_lib import page_options_wheel
    page_options_wheel.render()
else:
    from pages_lib import page_backtest
    page_backtest.render()
