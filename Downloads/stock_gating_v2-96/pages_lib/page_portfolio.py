"""
page_portfolio.py — PAGE 5: Paper-Trading Blotter.

Three sections:
  A. OPEN POSITIONS — live-marked, with one-click close
  B. CLOSED TRADES — realized P&L history with strategy/regime context
  C. DANGER ZONE   — full DB wipe (confirmation-gated)

Live prices for open-position marking come from data_utils.get_live_quotes,
which has the same rate-limit / last-known-good fallback used elsewhere in
the app. If a quote isn't available we surface that in the row rather than
fabricating a value.
"""

from __future__ import annotations
from datetime import datetime

import pandas as pd
import streamlit as st

import theme
import data_utils as du
import db_manager as dbm


def render():
    st.markdown("<div class='kicker'>PAGE 5 · PAPER PORTFOLIO</div>",
                unsafe_allow_html=True)
    st.markdown("# Trade Blotter")
    st.markdown(
        "<div class='tiny' style='margin-bottom:16px'>"
        "Every paper trade executed from Page 2 lives here. Open positions "
        "are marked to last live price; closed trades show realized P&amp;L "
        "with full audit context (strategy &amp; macro regime at entry)."
        "</div>", unsafe_allow_html=True)

    # ── header stats ─────────────────────────────────────────────────────
    s = dbm.stats()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Open positions", s["open_n"])
    with c2:
        st.metric("Closed trades", s["closed_n"])
    with c3:
        pnl = s["total_realized_pnl"]
        st.metric("Total realized P&L", f"${pnl:,.2f}",
                  delta=None,
                  help="Sum of realized_pnl across all closed trades.")

    if s["open_n"] == 0 and s["closed_n"] == 0:
        st.info(
            "📒 No paper trades yet. Run a scan on Page 2 and use the "
            "**🟢 Paper Execution** form to record a simulated 100-share "
            "trade. It'll show up here for tracking and closing.")
        _danger_zone()
        return

    # ── SECTION A: OPEN POSITIONS ────────────────────────────────────────
    st.markdown("---")
    st.markdown("<div class='kicker'>A · Open Positions</div>",
                unsafe_allow_html=True)
    _render_open_positions()

    # ── SECTION B: CLOSED TRADES ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("<div class='kicker'>B · Closed Trades</div>",
                unsafe_allow_html=True)
    _render_closed_trades()

    # ── SECTION C: DANGER ZONE ───────────────────────────────────────────
    st.markdown("---")
    _danger_zone()


def _render_open_positions():
    open_df = dbm.get_open_positions()
    if open_df.empty:
        st.info("No open positions. Closed trade history is below.")
        return

    # mark every open position to the latest live price
    tickers = tuple(open_df["ticker"].unique().tolist())
    quotes = du.get_live_quotes(tickers) if tickers else {}

    # build the display table
    rows = []
    for _, t in open_df.iterrows():
        q = quotes.get(t["ticker"], {})
        cur_price = q.get("price") if q.get("status") == "ok" else None
        entry = float(t["entry_price"])
        qty = int(t["quantity"])
        unrealized = None
        ret_pct = None
        if cur_price is not None:
            if t["direction"] == "Long":
                unrealized = round((cur_price - entry) * qty, 2)
                ret_pct = round((cur_price - entry) / entry * 100, 2)
            else:  # Short
                unrealized = round((entry - cur_price) * qty, 2)
                ret_pct = round(-(cur_price - entry) / entry * 100, 2)
        rows.append({
            "ID": int(t["id"]),
            "Ticker": t["ticker"],
            "Direction": t["direction"],
            "Shares": qty,
            "Entry $": entry,
            "Live $": cur_price if cur_price is not None else None,
            "Unrealized P&L": unrealized,
            "Return %": ret_pct,
            "Strategy": t["strategy_engine"],
            "Macro @ Entry": (round(float(t["macro_score"]), 0)
                              if t.get("macro_score") is not None
                              and pd.notna(t.get("macro_score")) else None),
            "Entry Time": (str(t["entry_timestamp"])[:16]
                            if t.get("entry_timestamp") else ""),
        })

    display = pd.DataFrame(rows)

    # warn if any live prices are missing
    missing = display["Live $"].isna().sum()
    if missing:
        st.warning(
            f"⚠️ Live quote unavailable for {missing} ticker(s) — yfinance "
            f"may be rate-limited. Refresh in ~60s or close manually with "
            f"a specified exit price (use the form below).")

    st.dataframe(
        display, width="stretch", hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn(format="%d", width="small"),
            "Entry $": st.column_config.NumberColumn(format="$%.2f"),
            "Live $": st.column_config.NumberColumn(format="$%.2f"),
            "Unrealized P&L": st.column_config.NumberColumn(format="$%.2f"),
            "Return %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Macro @ Entry": st.column_config.NumberColumn(format="%d"),
        }
    )

    # ── close-position form ──
    st.markdown("<div class='kicker' style='margin-top:14px'>"
                "Close a position</div>", unsafe_allow_html=True)
    col_id, col_price, col_btn = st.columns([2, 2, 1])

    open_ids = display["ID"].tolist()
    id_labels = {
        int(r["ID"]): f"#{int(r['ID'])} · {r['Direction']} {r['Shares']} "
                       f"{r['Ticker']} @ ${r['Entry $']:.2f}"
        for _, r in display.iterrows()
    }
    with col_id:
        chosen_id = st.selectbox(
            "Trade to close",
            options=open_ids,
            format_func=lambda i: id_labels.get(i, f"#{i}"),
            key="portfolio_close_id")

    # default exit price = current live price for that ticker
    chosen_row = display[display["ID"] == chosen_id].iloc[0]
    default_exit = (float(chosen_row["Live $"])
                    if pd.notna(chosen_row["Live $"]) else
                    float(chosen_row["Entry $"]))
    with col_price:
        exit_px = st.number_input(
            "Exit price ($)", min_value=0.01, value=default_exit,
            step=0.01, format="%.2f",
            key="portfolio_close_price",
            help="Defaults to the current live price; override if you want "
                 "to mark out at a specific level.")
    with col_btn:
        st.write("")  # vertical alignment
        st.write("")
        confirm = st.button("Close trade", key="portfolio_close_btn",
                            type="primary", width="stretch")

    if confirm:
        ok = dbm.close_position(int(chosen_id), float(exit_px))
        if ok:
            # compute the realized P&L locally so we can show it in the toast
            entry = float(chosen_row["Entry $"])
            qty = int(chosen_row["Shares"])
            if chosen_row["Direction"] == "Long":
                pnl = round((exit_px - entry) * qty, 2)
            else:
                pnl = round((entry - exit_px) * qty, 2)
            sign = "🟢 +" if pnl >= 0 else "🔴 "
            st.success(
                f"Closed trade #{chosen_id} ({chosen_row['Direction']} "
                f"{chosen_row['Ticker']}) at ${exit_px:.2f} — "
                f"realized P&L {sign}${pnl:,.2f}")
            st.rerun()
        else:
            st.error(
                "Could not close trade — it may already be closed, or the "
                "DB is read-only. Refresh the page and check again.")


def _render_closed_trades():
    closed = dbm.get_closed_trades()
    if closed.empty:
        st.info("No closed trades yet — once you close an open position, "
                "the trade history will appear here.")
        return

    # summary stats over closed trades
    n = len(closed)
    wins = (closed["realized_pnl"] > 0).sum()
    losses = (closed["realized_pnl"] < 0).sum()
    hit_rate = round(wins / n * 100, 1) if n else 0
    avg_win = round(closed.loc[closed["realized_pnl"] > 0,
                                "realized_pnl"].mean(), 2) if wins else None
    avg_loss = round(closed.loc[closed["realized_pnl"] < 0,
                                 "realized_pnl"].mean(), 2) if losses else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", n)
    c2.metric("Hit rate", f"{hit_rate}%",
              help=f"{wins} winners / {losses} losers")
    c3.metric("Avg win", f"${avg_win:,.2f}" if avg_win else "—")
    c4.metric("Avg loss", f"${avg_loss:,.2f}" if avg_loss else "—")
    if n < 20:
        st.markdown(
            f"<div class='tiny' style='color:#f5c344'>"
            f"⚠️ Only {n} closed trades — far too few to read meaningfully. "
            f"~30–50 needed before the hit-rate is statistically informative."
            f"</div>", unsafe_allow_html=True)

    # build the display table
    display = pd.DataFrame({
        "ID": closed["id"].astype(int),
        "Ticker": closed["ticker"],
        "Direction": closed["direction"],
        "Shares": closed["quantity"].astype(int),
        "Entry $": closed["entry_price"],
        "Exit $": closed["exit_price"],
        "Return %": closed["return_pct"],
        "Realized P&L": closed["realized_pnl"],
        "Strategy": closed["strategy_engine"],
        "Macro @ Entry": closed["macro_score"].apply(
            lambda v: round(float(v), 0) if pd.notna(v) else None),
        "Entry Time": closed["entry_timestamp"].astype(str).str[:16],
        "Exit Time": closed["exit_timestamp"].astype(str).str[:16],
    })

    st.dataframe(
        display, width="stretch", hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn(format="%d", width="small"),
            "Entry $": st.column_config.NumberColumn(format="$%.2f"),
            "Exit $": st.column_config.NumberColumn(format="$%.2f"),
            "Return %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Realized P&L": st.column_config.NumberColumn(format="$%+,.2f"),
            "Macro @ Entry": st.column_config.NumberColumn(format="%d"),
        }
    )

    # CSV export
    csv_bytes = display.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download closed-trade history (CSV)",
        data=csv_bytes,
        file_name=f"paper_trades_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv")


def _danger_zone():
    with st.expander("⚠️ Danger zone — wipe the paper-trade database"):
        st.warning(
            "Wiping the database permanently removes ALL open and closed "
            "trades. There is no undo. The signal journal (Page 5) is "
            "stored separately and is NOT affected by this action.")
        col_confirm, col_btn = st.columns([2, 1])
        with col_confirm:
            confirm = st.text_input(
                "Type WIPE to confirm", key="portfolio_wipe_confirm")
        with col_btn:
            st.write("")
            st.write("")
            if st.button("Wipe database", key="portfolio_wipe_btn"):
                if confirm == "WIPE":
                    if dbm.wipe_database():
                        st.success("Paper-trade database wiped.")
                        st.rerun()
                    else:
                        st.error("Wipe failed — DB access issue.")
                else:
                    st.error("Type WIPE exactly to confirm.")
