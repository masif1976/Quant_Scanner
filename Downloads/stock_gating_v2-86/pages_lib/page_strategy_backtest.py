"""
page_strategy_backtest.py — Page: Strategy Backtest.

Watchlist-wide trade simulator. The user clicks Run, the system replays
the scanner across every ticker over the lookback window, emits paper-
trades on signal crossings, and reports portfolio-level outcomes.

This is DISTINCT from "Backtest & Audit" (per-ticker score audit). This
page makes trade decisions; the other page reports score history.

The full rule set is documented in the on-page "Methodology" expander
so readers know exactly what counts as a signal.
"""
from datetime import datetime
import pandas as pd
import streamlit as st

import theme
import data_utils as du
import run_portfolio_backtest as pb
import backtest_archive
from scanner_factors import factors


def _render_methodology():
    """One-time expander describing exactly what the backtest does. Lives
    at the top so the rules are visible BEFORE the user sees results."""
    with st.expander("📖 Methodology — what counts as a signal, how trades exit"):
        st.markdown(f"""
**Window.** Replays the last {pb.DEFAULT_LOOKBACK_DAYS} trading days
(≈3 months) of data for every ticker in your watchlist.

**Entry rules.**
- **LONG** fires when a ticker's composite score *crosses up through 65*
  (TRADABLE tier lower bound) from below.
  - Filter 1: Regime gate — blocked in BEAR regime.
  - Filter 2: Grade gate — blocked if current Fundamental Grade is D
    (don't buy weak businesses on technical setups).
- **SHORT** fires when the score *crosses down through 35* (AVOID tier
  upper bound) from above.
  - Filter 1: Regime gate — **SIDEWAYS regime only**. Both BULL (fighting
    the trend) and BEAR (capitulation trap) are blocked. This rule is
    INVERTED from textbook intuition based on trade-log evidence:
    BEAR-regime shorts in the v1 system entered at oversold lows right
    before dead-cat bounces, producing 33% win rate and -$10,632 of
    losses. SIDEWAYS-only SHORTs avoid the trap.
  - Filter 2: 52-week range gate — blocked if price is below 40% of the
    52-week range. Already deeply oversold = capitulation trap.

**Mean Reversion strategy adds two MORE filters (post-MR-audit):**
Following an MR engine trade log audit that showed mid-range SHORTs lost
-$5,933 across 7 trades (43% win rate) while truly-oversold LONGs won
100% (n=2, avg +10.7%), the MR engine treats range position as a hard
gate, not just a soft scoring input:
- **MR LONG** requires 52-week range position < 30% (genuinely oversold).
  The MR audit found both winning LONGs were at 0-1% of range; mid-range
  "MR LONGs" don't actually mean-revert.
- **MR SHORT** requires 52-week range position > 70% (genuinely overbought).
  The 7 losing MR SHORTs were all at 48-64% — mid-range, not overbought.

**MR SHORTs currently DISABLED.** A follow-up audit on an expanded 23-ticker
watchlist with the new 70% gate in place produced more SHORT-side losses:
- GOOGL SHORT at 72% pos closed at -$2,137 (-22% in 20 days, fighting trend)
- MXL SHORT at 100% pos (the cleanest "true overbought" entry possible) was
  paper-down -$2,700 mid-hold as MXL kept ripping higher
- 3 clustered MRNA SHORTs at 70-75% pos producing correlated bets, not signal

Cumulative MR SHORT P&L across both audit runs: roughly -$10,700 across
8-9 closed/marked trades. The pattern is consistent across multiple range
thresholds (50%, 70%, 100%): in a market regime where most names are
trending up, MR SHORTs fight the tape regardless of how selective the
gate is. MR LONGs (n=7 winners at deep oversold positions) remain enabled.

To re-enable MR SHORTs, set MR_SHORTS_DISABLED = False in
run_portfolio_backtest.py — the 70% range gate stays in place either way.

**TREND SHORTs currently DISABLED.** Same posture as MR SHORTs but for the
trend-following engine. The TREND trade log audit (excluding MXL data
corruption — see below) showed:
- LONG side excellent: n=8, wr=75%, total=+$12,177 (avg +15% per trade)
- SHORT side breakeven: n=13, wr=54%, total=-$2,093 (avg -1.6% per trade)
- Pattern: mid-range (40-80%) SHORTs on Grade-B mega-caps in SIDEWAYS
  regime kept getting overrun as the broader market trended up. The 40%
  range gate prevents capitulation shorts but not "trend resumption" shorts.
TREND LONG breakouts remain enabled and are the system's proven edge.
To re-enable, set TREND_SHORTS_DISABLED = False.

**Same-ticker cooldown.** Both engines enforce a 7-trading-day minimum
between signals on the same ticker. Without it, the TREND engine fired
2 AAPL LONGs within 4 days (5/1 + 5/5) and 2 MRNA SHORTs within 20 days
(4/29 + 5/19). These look like 2 independent trades but represent 1 bet
doubled — the win/loss outcomes are highly correlated. Cooldown ensures
each ticker contributes at most one fresh signal per ~weekly window.

**Price corruption detection.** Any ticker whose price series contains
an adjacent-day move > {pb.PRICE_CORRUPTION_MAX_DAILY_RATIO}x (i.e. 50%
overnight) is excluded from the run entirely with a warning. The TREND
audit caught MXL showing a phantom $16 → $52 jump on 2026-04-28 — almost
certainly mixed split-adjusted and non-adjusted prices from yfinance,
producing a fake -$22k SHORT loss and +$32k LONG win that polluted the
trade log. The exclusion is conservative: real biotech FDA-decision-day
±60% moves get excluded too, but those are noise not signal anyway.

**"Crossing" matters.** A signal fires only on the day a tier change
happens, not every day the score stays in the tier. If a stock is
TRADABLE for 5 days running, you get 1 buy signal, not 5.

**Exit rule.** Fixed 20-day hold. Trades that fire in the last 20 days of
the window are tracked as "still open" — they can't be measured yet, so
they're EXCLUDED from win-rate stats but counted in the trade log.
NO forced close at the window end (preserves measurement integrity —
including trades that only held 4 days because the window ended would
overstate short-term signal accuracy).

**Position sizing.** Equal-dollar exposure (default ${pb.DEFAULT_TARGET_DOLLARS:,.0f}
per trade). Replaces the prior fixed-100-shares sizing which created
2× exposure mismatches between high-priced and low-priced tickers. With
equal dollars, the trade-count win rate is now economically meaningful
— each position represents the same dollar bet.

**Transaction costs.** {pb.DEFAULT_COST_BPS_PER_SIDE} bps per side
charged on entry AND exit (round-trip = {pb.DEFAULT_COST_BPS_PER_SIDE * 2} bps).
Default assumes a zero-commission broker with typical retail spread + slippage.
Deducted from gross P&L — the win rate and total P&L shown are NET of cost.

**Honest gaps.**
- **Historical scoring uses 4 factors, not 6.** Options Flow + Short
  Interest are only available as current snapshots from yfinance — we
  can't replay them historically. The 4 price/volume factors
  (Momentum, Volume Surge, Relative Strength, Range Proximity) are
  re-weighted to sum to 1.0.
- **Sample sizes are small.** 3 months of data on ~23 tickers typically
  emits 30-90 signals. Win-rate confidence intervals at that sample
  are still wide (a 60% win rate from 30 trades has ±18% error bars).
  Treat the numbers as directional, not authoritative.
- **Grade look-ahead caveat.** The Grade filter uses each ticker's
  *current* Fundamental Grade applied to *historical* trades.
  Technically look-ahead bias, but fundamentals change slowly (90-day
  quarterly cadence), so a ticker that's Grade-D/E today was very likely
  Grade-D/E 60-90 days ago. Worth knowing for rigor.
- **Survivorship in the open-trades bucket.** Trades opened in the last
  20 days of the window stay in "still open" status. They might be
  unrepresentative if recent signals are biased (e.g. signals fired
  during a rally vs a crash within those last 20 days). The UI shows
  the count so you can judge.
""")


def _render_summary_cards(bt: dict):
    """Top-of-page hero cards: overall stats + long stats + short stats."""
    overall = bt["stats_overall"]
    long_  = bt["stats_long"]
    short_ = bt["stats_short"]

    c1, c2, c3 = st.columns(3)
    with c1:
        _render_stat_card("Overall", overall, theme.ACCENT)
    with c2:
        _render_stat_card("LONG trades", long_, theme.GREEN)
    with c3:
        _render_stat_card("SHORT trades", short_, theme.RED)


def _render_stat_card(title: str, stats: dict, color: str):
    """One stat block — n trades, win rate, total P&L."""
    n = stats["n_closed"]
    if n == 0:
        body = (
            f"<div style=\"color:{theme.MUTED};font-family:JetBrains Mono;"
            f"font-size:0.88rem;margin-top:8px\">No closed trades in window.</div>"
        )
        if stats["n_open"]:
            body += (
                f"<div style=\"color:{theme.YELLOW};font-family:JetBrains Mono;"
                f"font-size:0.78rem;margin-top:6px\">"
                f"{stats['n_open']} still open (signal fired but hold period "
                f"extends past today)</div>"
            )
    else:
        win_rate = stats["win_rate"]
        win_color = (theme.GREEN if win_rate >= 60
                     else theme.YELLOW if win_rate >= 45
                     else theme.RED)
        pnl = stats["total_pnl"]
        pnl_color = theme.GREEN if pnl > 0 else theme.RED
        avg_pct = stats["avg_pnl_pct"]
        sample_note = ""
        if n < 10:
            sample_note = (
                f"<div style=\"color:{theme.YELLOW};font-family:JetBrains Mono;"
                f"font-size:0.74rem;margin-top:8px\">"
                f"⚠ n={n} — too few trades for reliable stats</div>"
            )
        elif n < 30:
            sample_note = (
                f"<div style=\"color:{theme.YELLOW};font-family:JetBrains Mono;"
                f"font-size:0.74rem;margin-top:8px\">"
                f"⚠ n={n} — low confidence, wide error bars</div>"
            )
        open_note = ""
        if stats["n_open"]:
            open_note = (
                f"<div style=\"color:{theme.MUTED};font-family:JetBrains Mono;"
                f"font-size:0.74rem;margin-top:4px\">"
                f"+{stats['n_open']} still open</div>"
            )
        body = (
            f"<div style=\"display:flex;justify-content:space-between;"
            f"margin-top:10px\">"
            f"<div><div style=\"color:{theme.MUTED};font-size:0.78rem;"
            f"font-family:JetBrains Mono\">Win Rate</div>"
            f"<div style=\"color:{win_color};font-size:1.6rem;"
            f"font-weight:800;font-family:Sora\">{win_rate:.0f}%</div></div>"
            f"<div><div style=\"color:{theme.MUTED};font-size:0.78rem;"
            f"font-family:JetBrains Mono\">Trades</div>"
            f"<div style=\"color:{theme.TEXT};font-size:1.6rem;"
            f"font-weight:800;font-family:Sora\">{n}</div></div>"
            f"</div>"
            f"<div style=\"margin-top:10px;display:flex;"
            f"justify-content:space-between\">"
            f"<div><div style=\"color:{theme.MUTED};font-size:0.78rem;"
            f"font-family:JetBrains Mono\">Total P&L</div>"
            f"<div style=\"color:{pnl_color};font-size:1.15rem;"
            f"font-weight:700;font-family:JetBrains Mono\">"
            f"${pnl:+,.0f}</div></div>"
            f"<div><div style=\"color:{theme.MUTED};font-size:0.78rem;"
            f"font-family:JetBrains Mono\">Avg/trade</div>"
            f"<div style=\"color:{pnl_color};font-size:1.15rem;"
            f"font-weight:700;font-family:JetBrains Mono\">"
            f"{avg_pct:+.2f}%</div></div>"
            f"</div>"
            f"{sample_note}{open_note}"
        )
    card = (
        f"<div style=\"background:{theme.PANEL};"
        f"border:1px solid {color}66;border-left:3px solid {color};"
        f"border-radius:10px;padding:14px 18px;margin-bottom:10px\">"
        f"<div style=\"color:{color};font-family:JetBrains Mono;"
        f"font-size:0.82rem;font-weight:700;letter-spacing:1px\">{title.upper()}</div>"
        f"{body}"
        f"</div>"
    )
    st.markdown(card, unsafe_allow_html=True)


def _render_rejections(rej: dict):
    """Show how many candidate signals were rejected by each filter.

    Transparency note. If LONG signals are scarce, the user can see whether
    the regime gate or the Grade-D/E filter killed them. Similarly for SHORTs."""
    REJ_LABELS = {
        "long_regime_bear":  ("LONG", "blocked by BEAR regime"),
        "long_grade_d":      ("LONG", "blocked by Grade-D/E filter (weak fundamentals)"),
        "short_regime_bull": ("SHORT", "blocked by BULL regime"),
        "short_regime_bear": ("SHORT", "blocked by BEAR regime (capitulation guard)"),
        "short_range_low":   ("SHORT", "blocked by 52-wk range < 40% (oversold guard)"),
        "mr_long_not_oversold": ("LONG",
            "blocked by MR rule: 52-wk range > 30% (not genuinely oversold)"),
        "mr_short_not_overbought": ("SHORT",
            "blocked by MR rule: 52-wk range < 70% (not genuinely overbought)"),
        "mr_short_disabled": ("SHORT",
            "blocked because MR SHORTs are disabled (audit found persistent "
            "losses regardless of range gate — see methodology notes)"),
        "trend_short_disabled": ("SHORT",
            "blocked because TREND SHORTs are disabled (audit found "
            "breakeven SHORT side with mid-range failures in trending "
            "market regime — see methodology notes)"),
        "same_ticker_cooldown": ("BOTH",
            f"blocked by 7-trading-day cooldown between same-ticker "
            f"signals (prevents correlated reruns of the same bet)"),
    }
    rows = []
    for key, count in rej.items():
        if count > 0 and key in REJ_LABELS:
            direction, desc = REJ_LABELS[key]
            if direction == "LONG":
                color = theme.GREEN
            elif direction == "SHORT":
                color = theme.RED
            else:  # BOTH — direction-neutral rejections like cooldown
                color = theme.MUTED
            rows.append(
                f"<div style=\"display:flex;justify-content:space-between;"
                f"padding:4px 0;font-family:JetBrains Mono;font-size:0.85rem\">"
                f"<span style=\"color:{theme.TEXT}\">"
                f"<b style=\"color:{color}\">{direction}</b> · {desc}</span>"
                f"<span style=\"color:{theme.MUTED};font-weight:700\">"
                f"{count} signal(s) rejected</span>"
                f"</div>"
            )
    if not rows:
        return
    st.markdown(
        f"<div style=\"background:{theme.PANEL};border:1px solid {theme.BORDER};"
        f"border-radius:8px;padding:10px 16px;margin:14px 0\">"
        f"<div style=\"color:{theme.MUTED};font-family:JetBrains Mono;"
        f"font-size:0.78rem;font-weight:700;letter-spacing:1px;"
        f"margin-bottom:6px\">ENTRY FILTERS — SIGNALS REJECTED</div>"
        f"{''.join(rows)}"
        f"</div>", unsafe_allow_html=True)


def _render_per_ticker_table(bt: dict):
    """One row per ticker that had at least one trade."""
    rows = bt["stats_per_ticker"]
    if not rows:
        return
    st.markdown("<div class='kicker'>Per-Ticker Breakdown</div>",
                unsafe_allow_html=True)
    df_rows = []
    for r in rows:
        win = r["win_rate"]
        wr_str = f"{win:.0f}%" if win is not None else "—"
        df_rows.append({
            "Ticker": r["ticker"],
            "Trades": r["n_trades"],
            "Open": r["n_open"],
            "Winners": r["n_winners"],
            "Win Rate": wr_str,
            "Total P&L": float(r["total_pnl"]),
        })
    df = pd.DataFrame(df_rows)
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_config={
            "Trades": st.column_config.NumberColumn("Trades", width="small"),
            "Open": st.column_config.NumberColumn("Open", width="small"),
            "Winners": st.column_config.NumberColumn("Winners",
                                                      width="small"),
            "Win Rate": st.column_config.TextColumn("Win Rate",
                                                     width="small"),
            "Total P&L": st.column_config.NumberColumn("Total P&L ($)",
                                                        format="$%.0f"),
        },
    )


def _render_trades_table(bt: dict):
    """Full trade log: every entry, exit, P&L."""
    trades = bt["trades"]
    if not trades:
        return
    st.markdown("<div class='kicker'>Trade Log</div>", unsafe_allow_html=True)

    df_rows = []
    for t in trades:
        df_rows.append({
            "Entry Date":  pd.to_datetime(t["entry_date"]).strftime("%Y-%m-%d"),
            "Ticker":      t["ticker"],
            "Direction":   t["direction"],
            "Entry Score": int(t["entry_score"]),
            "Regime":      _short_regime(t["regime"]),
            "52W Pos":     (round(t["range_pos_52w"], 0)
                            if t.get("range_pos_52w") is not None else None),
            "Grade":       t.get("grade", "—"),
            "Shares":      int(t.get("shares", 0)),
            "Entry $":     float(t.get("entry_price") or 0),
            "Notional":    float(t.get("notional_entry") or 0),
            "Exit Date":   (pd.to_datetime(t["exit_date"]).strftime("%Y-%m-%d")
                            if t.get("exit_date") else "open"),
            "Exit $":      (float(t["exit_price"])
                            if t.get("exit_price") else None),
            "Cost $":      (round(t["transaction_cost"], 2)
                            if t.get("transaction_cost") is not None else None),
            "P&L %":       (round(t["pnl_pct"], 2)
                            if t.get("pnl_pct") is not None else None),
            "P&L $":       (round(t["pnl"], 0)
                            if t.get("pnl") is not None else None),
            "Outcome":     ("🟢 Win" if t.get("winner") else
                             "🔴 Loss" if t.get("status") == "closed" else
                             "⏳ Open"),
        })
    df = pd.DataFrame(df_rows)
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_config={
            "Shares":   st.column_config.NumberColumn("Shares", format="%d",
                                                       width="small",
                                                       help="Derived from "
                                                       "target $ / entry price "
                                                       "(equal-dollar sizing)"),
            "Entry $":  st.column_config.NumberColumn("Entry $",
                                                       format="$%.2f"),
            "Notional": st.column_config.NumberColumn("Notional",
                                                       format="$%.0f",
                                                       help="shares × entry "
                                                       "price — the dollar "
                                                       "exposure at entry"),
            "Exit $":   st.column_config.NumberColumn("Exit $",
                                                       format="$%.2f"),
            "Cost $":   st.column_config.NumberColumn("Cost $",
                                                       format="$%.2f",
                                                       help="Round-trip "
                                                       "transaction cost "
                                                       "(both sides)"),
            "P&L %":    st.column_config.NumberColumn("P&L %",
                                                       format="%.2f%%"),
            "P&L $":    st.column_config.NumberColumn("P&L $",
                                                       format="$%.0f",
                                                       help="NET P&L after "
                                                       "transaction costs"),
            "Entry Score": st.column_config.NumberColumn("Entry Score",
                                                          format="%d"),
            "52W Pos":  st.column_config.NumberColumn(
                "52W Pos",
                format="%d%%",
                help="Where the price sits in its 52-week range at entry "
                     "(0% = at low, 100% = at high). SHORT entries below "
                     "40% are blocked — too deeply oversold."),
            "Grade":    st.column_config.TextColumn(
                "Grade", width="small",
                help="Current Fundamental Grade (A=excellent → E=structurally "
                     "broken). LONG entries on Grade-D and Grade-E names are "
                     "blocked."),
        },
    )

    # CSV export
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇  Download trade log (CSV)", data=csv,
                       file_name=f"strategy_backtest_"
                                  f"{datetime.now():%Y%m%d_%H%M%S}.csv",
                       mime="text/csv")
    # Archive indicator — surface where prior runs are saved
    n_archived = backtest_archive.archive_count()
    if n_archived > 0:
        archive_path = backtest_archive._archive_dir()
        st.caption(
            f"📦 This run was auto-archived to `{archive_path}`. "
            f"You have **{n_archived}** total archived run(s) "
            f"— accumulating audit history across backtests.")


def _short_regime(r: str) -> str:
    """BULL REGIME -> BULL etc."""
    if not r:
        return "—"
    return r.replace(" REGIME", "").strip()


def render():
    st.markdown("<div class='kicker'>PAGE · STRATEGY BACKTEST</div>",
                unsafe_allow_html=True)
    st.markdown("# Watchlist Trade Simulator")
    st.markdown(
        "<div class='tiny' style='margin-bottom:14px'>"
        "Replay the scanner across every ticker for the last ~3 months. "
        "Emit paper-trades on signal crossings, hold 20 days, report "
        "win rate and total P&L.</div>", unsafe_allow_html=True)

    watchlist = st.session_state.get("watchlist", [])
    strategy = st.session_state.get("strategy", "Trend-Following")
    horizon = st.session_state.get("horizon", "Swing Trade System")
    macro_history = st.session_state.get("macro_history")

    # status banner — what's the current scope?
    st.markdown(
        f"<div style=\"background:{theme.PANEL};border:1px solid {theme.BORDER};"
        f"border-radius:8px;padding:10px 16px;margin-bottom:14px;"
        f"font-family:JetBrains Mono;font-size:0.85rem;color:{theme.TEXT}\">"
        f"<b style=\"color:{theme.ACCENT}\">{strategy}</b> engine · "
        f"<b style=\"color:{theme.ACCENT}\">{horizon}</b> · "
        f"<b style=\"color:{theme.TEXT}\">{len(watchlist)} tickers</b>"
        f"</div>", unsafe_allow_html=True)

    _render_methodology()

    if not watchlist:
        st.warning("Add tickers to the watchlist in the sidebar to run the backtest.")
        return

    # macro_history is optional but recommended — without it, regime gates
    # are effectively neutral (no trades blocked by regime)
    if macro_history is None:
        st.info("ℹ Macro history not loaded. Trades will not be regime-gated. "
                 "Run the full analysis from Page 1 to enable regime filtering.")

    # ── run button ──
    cache_key = f"strategy_bt::{strategy}::{len(watchlist)}"
    if st.button("▶  Run Watchlist Backtest", type="primary",
                  width="stretch"):
        with st.spinner(f"Replaying {len(watchlist)} tickers over the last "
                         f"~3 months…"):
            bt = pb.run(watchlist, strategy,
                         macro_history=macro_history,
                         lookback_days=pb.DEFAULT_LOOKBACK_DAYS)
        st.session_state[cache_key] = bt
        # Auto-archive — see backtest_archive.py module docstring for why.
        # Silent on failure: archiving is a side benefit, not core flow.
        try:
            archived_path = backtest_archive.archive_run(bt)
            if archived_path:
                st.toast(f"📦 Archived: {archived_path.name}", icon="📦")
        except Exception:
            pass

    bt = st.session_state.get(cache_key)
    if not bt:
        st.info("Click **Run Watchlist Backtest** above to simulate trades "
                 "across your watchlist.")
        # Even when no backtest is loaded, surface the archive history so
        # users know prior runs are saved. Encourages building up an
        # audit trail across weeks.
        n_archived = backtest_archive.archive_count()
        if n_archived > 0:
            archive_path = backtest_archive._archive_dir()
            st.caption(
                f"📦 **{n_archived}** archived backtest run(s) saved to "
                f"`{archive_path}` — accumulating audit history "
                f"across runs.")
        return

    # ── warnings (data gaps, no signals, etc.) ──
    for w in bt.get("warnings", []):
        st.warning(w)

    # ── summary cards ──
    if bt["stats_overall"]["n_closed"] + bt["stats_overall"]["n_open"] > 0:
        # ── survivorship banner ──
        # Make it visible up-front how many signals are EXCLUDED from
        # win-rate stats due to incomplete forward data. With a 3-month
        # window and 20-day hold, ~1/3 of all signals fire in the last
        # 20 days and can't be measured. Users need to see what % of the
        # sample is being scored vs deferred.
        n_closed = bt["stats_overall"]["n_closed"]
        n_open = bt["stats_overall"]["n_open"]
        n_total = n_closed + n_open
        if n_open > 0:
            open_pct = n_open / n_total * 100
            measured_pct = n_closed / n_total * 100
            if open_pct > 40:
                banner_color = theme.YELLOW
                banner_severity = "⚠"
                banner_note = (
                    "Large fraction of signals deferred — win-rate sample "
                    "is small. Consider extending lookback for more matured "
                    "trades, or wait 20 trading days and re-run."
                )
            elif open_pct > 25:
                banner_color = theme.MUTED
                banner_severity = "ℹ"
                banner_note = (
                    "Trades opened in the last 20 days are tracked but not "
                    "yet scored. Their outcomes determine future revisions."
                )
            else:
                banner_color = theme.MUTED
                banner_severity = "ℹ"
                banner_note = ""
            banner_html = (
                f"<div style=\"background:{banner_color}1c;"
                f"border:1px solid {banner_color}55;border-left:3px solid {banner_color};"
                f"border-radius:8px;padding:10px 16px;margin-bottom:14px;"
                f"font-family:JetBrains Mono;font-size:0.84rem\">"
                f"<b style=\"color:{banner_color}\">{banner_severity} Survivorship breakdown:</b>"
                f" {n_closed} of {n_total} signals ({measured_pct:.0f}%) are "
                f"<b>measured</b> (full 20-day hold completed). "
                f"{n_open} ({open_pct:.0f}%) are <b>still open</b> and excluded "
                f"from win-rate stats. Trade log below shows both."
                f"{('<br><span style=\"color:' + theme.MUTED + ';font-size:0.78rem\">' + banner_note + '</span>') if banner_note else ''}"
                f"</div>"
            )
            st.markdown(banner_html, unsafe_allow_html=True)

        st.markdown("<div class='kicker'>Summary</div>",
                    unsafe_allow_html=True)
        _render_summary_cards(bt)

        # window meta + run timestamp
        ws = bt["window_start"]
        we = bt["window_end"]
        run_ts = datetime.fromisoformat(bt["timestamp"]).strftime("%Y-%m-%d %H:%M")
        if ws and we:
            target_d = bt.get("target_dollars", pb.DEFAULT_TARGET_DOLLARS)
            cost_bps = bt.get("cost_bps_per_side", pb.DEFAULT_COST_BPS_PER_SIDE)
            st.markdown(
                f"<div class='tiny' style='margin-top:4px;color:{theme.MUTED}'>"
                f"Window: <b>{pd.to_datetime(ws).strftime('%Y-%m-%d')}</b> "
                f"→ <b>{pd.to_datetime(we).strftime('%Y-%m-%d')}</b> · "
                f"Hold period: {bt['hold_days']} days · "
                f"Size: <b>${target_d:,.0f}</b> per trade (equal-dollar) · "
                f"Costs: <b>{cost_bps:.1f} bps/side</b> "
                f"({cost_bps*2:.1f} bps round-trip) · "
                f"Run: {run_ts}"
                f"</div>", unsafe_allow_html=True)

        # ── filter-rejection transparency ──
        # Show how many candidate signals were rejected by each filter so
        # the user can see whether the filters are too strict or about right.
        rej = bt.get("rejections", {})
        if rej and any(v > 0 for v in rej.values()):
            _render_rejections(rej)

        # per-ticker breakdown
        _render_per_ticker_table(bt)
        _render_trades_table(bt)

        # final honest footer
        st.markdown(
            f"<div class='tiny' style='margin-top:16px;color:{theme.MUTED};"
            f"font-size:0.78rem;line-height:1.6'>"
            f"<b>Reminder:</b> commissions and slippage are NOT modeled — "
            f"real-world execution adds 5-10 bps per trade. Historical "
            f"scoring uses 4 of the 6 live factors (Options Flow + Short "
            f"Interest can't be replayed historically). Educational tool, "
            f"not financial advice."
            f"</div>", unsafe_allow_html=True)
