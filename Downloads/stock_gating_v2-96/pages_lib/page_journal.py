"""
page_journal.py — PAGE 5: Trade Journal.

Honest accounting of every signal the system has produced, the user's
overrides, and the actual forward returns of both. This is the page that
tells you "does any of this actually work?"

If the journal is empty, the page just says so — no fake numbers, no
demo data. Every metric is computed from real signals logged at scan
time and real prices fetched via yfinance.
"""

from __future__ import annotations
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import theme
import data_utils as du
import signal_journal as sj


def render():
    st.markdown("<div class='kicker'>PAGE 4 · PERFORMANCE JOURNAL</div>",
                unsafe_allow_html=True)
    st.markdown("# Signal Track Record")
    st.markdown(
        "<div class='tiny' style='margin-bottom:16px'>"
        "Every scanner run is logged. Forward returns are pulled from real "
        "yfinance closes — signals that haven't yet matured show blank."
        "</div>", unsafe_allow_html=True)

    stats = sj.journal_stats()

    # ── empty-state ──
    if stats["n_scans"] == 0:
        st.info(
            "📒 The journal is empty. Run a scanner on Page 2 (or click "
            "**RUN FULL ANALYSIS** in the sidebar) to start collecting data. "
            "After ~30 signals across a few weeks, this page will show "
            "whether the system has real edge."
        )
        with st.expander("Where is the journal stored?"):
            st.markdown(f"**Database file:** `{stats['db_path']}`")
            st.markdown(
                "It's a local SQLite file. It survives app restarts. "
                "It's never uploaded anywhere. To start fresh, click the "
                "Reset button at the bottom of this page (only available "
                "once you have data).")
        return

    # ── header stats ──
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Scans logged", stats["n_scans"])
    with col2:
        st.metric("Signals logged", stats["n_signals"])
    with col3:
        st.metric("Overrides logged", stats["n_overrides"])
    with col4:
        first = stats["first_scan"]
        if first:
            days = (datetime.now() - datetime.fromisoformat(first)).days
            st.metric("Days of history", f"{days}d")

    # ── filter controls ──
    st.markdown("---")
    c1, c2 = st.columns([1, 2])
    with c1:
        lookback_days = st.selectbox(
            "Lookback window", options=[30, 60, 90, 180, 365],
            index=2, key="journal_lookback")
    with c2:
        horizon = st.selectbox(
            "Forward-return horizon",
            options=["fwd_ret_5", "fwd_ret_10", "fwd_ret_20", "fwd_ret_60"],
            format_func=lambda s: f"{s.split('_')[-1]} trading days",
            index=2, key="journal_horizon")

    # ── load + attach forward returns ──
    with st.spinner("Computing forward returns from real price history…"):
        signals = sj.load_signals(days=lookback_days)
        if signals.empty:
            st.warning(
                f"No signals in the last {lookback_days} days. Try a wider "
                f"lookback window or run a scan to add fresh data.")
            return
        signals = sj.attach_forward_returns(signals)

    # ── maturation status ──
    horizon_days = int(horizon.split("_")[-1])
    matured = int(signals[horizon].notna().sum())
    total = len(signals)
    pct_matured = (matured / total * 100) if total else 0
    if matured < 20:
        st.warning(
            f"⚠️ Only **{matured} of {total}** signals have matured at the "
            f"{horizon_days}-day horizon. Statistics on fewer than ~50 "
            f"signals are unreliable — treat these numbers as preliminary."
        )

    # ── performance by tier ──
    st.markdown("---")
    st.markdown("<div class='kicker'>Performance by Conviction Tier</div>",
                unsafe_allow_html=True)
    perf = sj.performance_by_tier(signals, horizon_col=horizon)
    if perf.empty:
        st.info(f"No matured signals at the {horizon_days}-day horizon yet. "
                f"Come back when more signals have matured.")
    else:
        # rename for display
        display = perf.rename(columns={
            "status_label": "Tier",
            "n": "Signals",
            "hit_rate_pct": "Hit Rate %",
            "avg_ret_pct": "Avg Return %",
            "avg_win_pct": "Avg Win %",
            "avg_loss_pct": "Avg Loss %",
            "edge_pct": "Edge % (direction-adjusted)",
        })
        st.dataframe(
            display, width="stretch", hide_index=True,
            column_config={
                "Signals": st.column_config.NumberColumn(format="%d"),
                "Hit Rate %": st.column_config.NumberColumn(format="%.1f%%"),
                "Avg Return %": st.column_config.NumberColumn(format="%.2f%%"),
                "Avg Win %": st.column_config.NumberColumn(format="%.2f%%"),
                "Avg Loss %": st.column_config.NumberColumn(format="%.2f%%"),
                "Edge % (direction-adjusted)": st.column_config.NumberColumn(
                    format="%.2f%%"),
            }
        )
        st.markdown(
            "<div class='tiny'>"
            "<b>Hit rate</b>: % of signals where the direction-adjusted return "
            "was positive (LONG tier wants ret>0, SHORT tier wants ret<0). "
            "<b>Edge</b>: mean direction-adjusted return. Positive edge = the "
            "tier was predictive. Sample sizes matter — &gt;50 is the rough "
            "minimum for statistical confidence.</div>",
            unsafe_allow_html=True)

    # ── system vs override comparison ──
    st.markdown("---")
    st.markdown(
        "<div class='kicker'>System vs Override — Did Your Discretion Add "
        "Alpha?</div>", unsafe_allow_html=True)

    sysov = sj.system_vs_override(signals, horizon_col=horizon)
    f, o, spread = sysov["followed"], sysov["overrode"], sysov["spread_pct"]

    if not o or o["n"] == 0:
        st.info(
            "No overrides logged yet in this window. When you override a "
            "system recommendation on Page 2 (✋ Override system "
            "recommendation), it'll appear here so you can compare your "
            "discretionary decisions against the system's track record.")
    else:
        col_f, col_o, col_s = st.columns(3)
        with col_f:
            st.metric(
                "Followed system",
                f"{f['edge_pct']:+.2f}%" if f else "—",
                help=f"n = {f['n'] if f else 0} signals, "
                     f"hit rate {f['hit_rate_pct'] if f else 0}%")
        with col_o:
            st.metric(
                "Overrode system",
                f"{o['edge_pct']:+.2f}%" if o else "—",
                help=f"n = {o['n']} overrides, "
                     f"hit rate {o['hit_rate_pct']}%")
        with col_s:
            if spread is not None:
                verdict = ("✅ overrides helped" if spread > 0.5
                           else "⚠️ overrides hurt" if spread < -0.5
                           else "≈ neutral")
                st.metric("Override edge", f"{spread:+.2f}pp", help=verdict)
        # honest caveat
        n_overrides = o["n"] if o else 0
        if n_overrides < 20:
            st.markdown(
                f"<div class='tiny' style='color:#f5c344'>"
                f"⚠️ Only {n_overrides} overrides — far too few for a "
                f"reliable read. Need ~30-50 before this comparison means "
                f"anything statistically.</div>",
                unsafe_allow_html=True)

    # ── raw journal browser ──
    st.markdown("---")
    with st.expander("📋 Raw signal log"):
        # show the most useful columns; drop the noisy ones
        keep = ["scan_timestamp", "ticker", "strategy", "regime", "macro_score",
                "composite_score", "status_label", "tranche_action",
                "price_at_signal", horizon, "override_action",
                "override_reason"]
        keep = [c for c in keep if c in signals.columns]
        show = signals[keep].copy()
        # truncate timestamps to YYYY-MM-DD HH:MM
        if "scan_timestamp" in show.columns:
            show["scan_timestamp"] = show["scan_timestamp"].str[:16]
        st.dataframe(show, width="stretch", hide_index=True,
                     height=400)
        csv_bytes = show.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download full journal (CSV)",
            data=csv_bytes,
            file_name=f"journal_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv")

    # ── danger zone ──
    st.markdown("---")
    with st.expander("⚠️ Danger zone — reset journal"):
        st.warning(
            "Clearing the journal removes ALL logged signals and overrides "
            "permanently. There is no undo. The system will start collecting "
            "from scratch on the next scan.")
        confirm = st.text_input(
            "Type RESET to confirm",
            key="journal_reset_confirm")
        if st.button("Clear journal", key="journal_reset_btn"):
            if confirm == "RESET":
                if sj.clear_journal():
                    st.success("Journal cleared. The next scan will rebuild it.")
                else:
                    st.error("Could not clear journal — DB access issue.")
            else:
                st.error("You must type RESET exactly to confirm.")
