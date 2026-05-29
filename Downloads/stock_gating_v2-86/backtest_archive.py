"""
backtest_archive.py — Auto-save trade logs from Page 4 (Strategy Backtest).

Why this exists:
  Every time the user runs a watchlist backtest, the result is held in
  session state and shown on-screen. They can download a CSV manually.
  But session state evaporates when Streamlit restarts, and few users
  remember to download every single run.

  Without an archive, audit history is lost. You can't tell whether a
  strategy is genuinely getting better over time, whether yfinance data
  drift is changing your numbers, or whether a particular signal that
  fired last month was a winner or loser.

  This module solves that by automatically saving every backtest's
  trade log to ~/.stock_gating_v2/archives/ as a timestamped CSV.
  No retention policy — disk usage is negligible (~3KB per run).

Storage layout:
  ~/.stock_gating_v2/archives/
    Trend-Following_e3a1f2_2026-05-24_1430.csv
    Mean-Reversion_e3a1f2_2026-05-24_1432.csv
    Trend-Following_e3a1f2_2026-06-14_0915.csv
    ...

Filename encoding:
  {strategy}_{watchlist_hash}_{YYYY-MM-DD_HHMM}.csv

  - strategy: hyphenated form of the toggle ("Trend-Following" or
              "Mean-Reversion") so filenames sort cleanly
  - watchlist_hash: 6-char hash of the sorted watchlist. Two runs on
              the same watchlist produce the same hash, so you can
              compare runs on identical universes without inspecting
              every file. Two runs on different universes have
              different hashes.
  - timestamp: minute-resolution. Two runs in the same minute would
              collide — extraordinarily unlikely in normal use but if
              it happens, the second overwrites the first.

The archive is in addition to the manual download button. The button
remains for users who want a specific file in their Downloads folder
for sharing or analysis. The archive is for the audit-trail use case.
"""
from __future__ import annotations
import hashlib
import os
from datetime import datetime
from pathlib import Path

import pandas as pd


# ── paths ───────────────────────────────────────────────────────────────────
def _archive_dir() -> Path:
    """Return the archive directory, creating it if needed.

    Uses ~/.stock_gating_v2/archives/ — same parent as the existing SQLite
    databases (watchlist.db, journal.db, paper_trades.db). Falls back to
    /tmp on systems where ~ isn't writable (rare; mostly containerized
    environments without a home directory).
    """
    try:
        base = Path.home() / ".stock_gating_v2" / "archives"
    except RuntimeError:
        # Path.home() raises if HOME isn't set
        base = Path("/tmp/stock_gating_v2/archives")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _watchlist_hash(watchlist: list[str]) -> str:
    """6-char hash of the sorted watchlist. Used in filenames to group
    runs by what universe they were on without making filenames absurdly
    long. Two identical watchlists in any order produce the same hash."""
    canonical = ",".join(sorted(t.upper().strip() for t in watchlist if t))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:6]


# ── public API ──────────────────────────────────────────────────────────────
def archive_run(bt_result: dict) -> Path | None:
    """Persist a single backtest run to the archive directory.

    Args:
        bt_result: the dict returned by run_portfolio_backtest.run().
                    Expected keys: 'trades' (list of trade dicts),
                    'strategy', 'watchlist', 'timestamp'.

    Returns:
        Path to the written file on success, None on failure (filesystem
        error, no trades, bad input). Failure NEVER raises — archiving is
        a side benefit, not core functionality, so a write error must not
        crash the user's backtest viewing.
    """
    if not bt_result or "trades" not in bt_result:
        return None
    trades = bt_result.get("trades") or []
    if not trades:
        # nothing to archive — backtest produced no signals
        return None

    try:
        archive_dir = _archive_dir()
    except Exception:
        return None

    # Build the same flat dataframe shape that the manual CSV download produces
    df_rows = []
    for t in trades:
        df_rows.append({
            "Entry Date":  _fmt_date(t.get("entry_date")),
            "Ticker":      t.get("ticker", ""),
            "Direction":   t.get("direction", ""),
            "Entry Score": int(t.get("entry_score", 0))
                          if t.get("entry_score") is not None else "",
            "Regime":      t.get("regime", ""),
            "52W Pos":     round(t["range_pos_52w"], 1)
                          if t.get("range_pos_52w") is not None else "",
            "Grade":       t.get("grade", ""),
            "Shares":      int(t.get("shares", 0)) if t.get("shares") else "",
            "Entry $":     float(t.get("entry_price") or 0),
            "Notional":    float(t.get("notional_entry") or 0),
            "Exit Date":   _fmt_date(t.get("exit_date")) if t.get("exit_date") else "open",
            "Exit $":      float(t["exit_price"]) if t.get("exit_price") else "",
            "Cost $":      round(t["transaction_cost"], 2)
                          if t.get("transaction_cost") is not None else "",
            "P&L %":       round(t["pnl_pct"], 2)
                          if t.get("pnl_pct") is not None else "",
            "P&L $":       round(t["pnl"], 0)
                          if t.get("pnl") is not None else "",
            "Outcome":     _outcome_label(t),
        })
    df = pd.DataFrame(df_rows)

    # Compose filename
    strategy = (bt_result.get("strategy", "Unknown")
                .replace(" · ", "-")
                .replace(" ", "-"))
    wlhash = _watchlist_hash(bt_result.get("watchlist", []))
    # Prefer the run's own timestamp (when the backtest was actually executed)
    # over datetime.now() so the filename matches what's stored in the result
    ts_str = bt_result.get("timestamp")
    try:
        ts = (datetime.fromisoformat(ts_str)
              if ts_str else datetime.now())
    except (ValueError, TypeError):
        ts = datetime.now()
    fname = f"{strategy}_{wlhash}_{ts:%Y-%m-%d_%H%M}.csv"
    fpath = archive_dir / fname

    try:
        df.to_csv(fpath, index=False)
        return fpath
    except OSError:
        return None


def list_archives(limit: int = 50) -> list[Path]:
    """Return up to `limit` most recent archives, newest first.

    Used by the UI to surface "you have 5 archived runs since June 1" so
    users can see the audit trail is accumulating. The actual files live
    on disk — this just lists them.
    """
    try:
        archive_dir = _archive_dir()
    except Exception:
        return []
    files = sorted(archive_dir.glob("*.csv"),
                   key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return files[:limit]


def archive_count() -> int:
    """Total number of archived runs. Cheap to call from the UI for a
    'You have N archived backtests' indicator."""
    try:
        archive_dir = _archive_dir()
    except Exception:
        return 0
    return len(list(archive_dir.glob("*.csv")))


# ── helpers ─────────────────────────────────────────────────────────────────
def _fmt_date(d) -> str:
    """Normalize date-like values to YYYY-MM-DD strings. Handles pandas
    Timestamps, datetime objects, and strings (passed through). Returns
    empty string for None."""
    if d is None or d == "":
        return ""
    try:
        return pd.to_datetime(d).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(d)


def _outcome_label(trade: dict) -> str:
    """Human-readable outcome for the Outcome column. Matches the on-screen
    table so the archive CSV is identical to what the user sees."""
    status = trade.get("status", "")
    if status == "open":
        return "⏳ Open"
    if trade.get("winner") is True:
        return "🟢 Win"
    if status == "closed":
        return "🔴 Loss"
    return status or "—"
