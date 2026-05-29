"""
signal_journal.py — Persistent log of every scanner signal and every user
override, with deferred forward-return evaluation.

This is the *substrate* for any real performance measurement: without a
durable record of what the system said at the moment a trade decision was
made, you can never honestly answer "did this signal work?" months later.

Storage: SQLite via stdlib (no extra deps). DB file lives at
    ~/.stock_gating_v2/journal.db
so it survives app restarts, working-directory changes, and zip refreshes.

The journal NEVER mocks or backfills missing data. If yfinance can't price a
historical date, the forward-return row stays NULL until a real price arrives.
"""

from __future__ import annotations
import os
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import data_utils as du

# ── paths ─────────────────────────────────────────────────────────────────────
_DB_DIR = Path.home() / ".stock_gating_v2"
_DB_PATH = _DB_DIR / "journal.db"


def _connect() -> sqlite3.Connection:
    """Open a connection. Lazily creates the directory + schema on first use."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create tables. Schema is forward-compatible — adding
    columns is safe via ALTER TABLE if needed in future versions."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scans (
            scan_id TEXT PRIMARY KEY,
            scan_timestamp TEXT NOT NULL,
            strategy TEXT NOT NULL,
            macro_score REAL,
            regime TEXT,
            watchlist TEXT NOT NULL          -- JSON array
        );

        CREATE TABLE IF NOT EXISTS signals (
            scan_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            composite_score REAL NOT NULL,    -- may be -1 for blocked rows
            status_label TEXT,
            tranche_action TEXT,
            price_at_signal REAL,             -- the close on the scan date
            factor_scores TEXT,               -- JSON dict
            earnings_flag INTEGER DEFAULT 0,
            earnings_days_away INTEGER,
            PRIMARY KEY (scan_id, ticker),
            FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
        );

        CREATE TABLE IF NOT EXISTS overrides (
            scan_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            override_action TEXT NOT NULL,
            override_reason TEXT,
            override_timestamp TEXT NOT NULL,
            PRIMARY KEY (scan_id, ticker),
            FOREIGN KEY (scan_id, ticker) REFERENCES signals(scan_id, ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_scans_timestamp ON scans(scan_timestamp);
    """)
    conn.commit()


# ── write path ────────────────────────────────────────────────────────────────
def log_scan(scanner_result: dict) -> str | None:
    """
    Log a single `run_scanner.run()` result. Returns the scan_id (UUID) used,
    or None if the result was empty / errored — we never write garbage rows.

    Idempotent if called twice on the same scanner_result dict: we generate a
    fresh scan_id each call, so two identical scans produce two log entries
    (intended — the user clicked "Run" twice). If you want dedupe, that's a
    UI concern, not a journal concern.
    """
    if not scanner_result or not scanner_result.get("rows"):
        return None

    scan_id = str(uuid.uuid4())
    ts = scanner_result.get("timestamp") or datetime.now().isoformat()
    strategy = scanner_result.get("strategy", "unknown")
    macro_score = scanner_result.get("macro_score")
    watchlist = scanner_result.get("watchlist", [])

    # derive a regime label from the macro_score (single source of truth lives
    # in scanner_factors.factors.regime_of, but we don't import it here to keep
    # signal_journal a leaf module)
    if macro_score is None:
        regime = None
    elif macro_score >= 70:
        regime = "BULL REGIME"
    elif macro_score >= 40:
        regime = "SIDEWAYS REGIME"
    else:
        regime = "BEAR REGIME"

    try:
        conn = _connect()
        with conn:
            conn.execute(
                "INSERT INTO scans (scan_id, scan_timestamp, strategy, "
                "macro_score, regime, watchlist) VALUES (?, ?, ?, ?, ?, ?)",
                (scan_id, ts, strategy, macro_score, regime,
                 json.dumps(watchlist))
            )
            for row in scanner_result["rows"]:
                # status_label may already encode the tranche; we want the
                # tactical-action string too. The scanner row carries
                # 'status_label' but not 'tranche_action' directly — the
                # page builds it. We reconstruct here so the journal is
                # self-contained and doesn't depend on UI code.
                import run_scanner
                tr = run_scanner.calculate_tranche_action(
                    macro_score, row["total_score"])
                conn.execute(
                    "INSERT OR IGNORE INTO signals (scan_id, ticker, "
                    "composite_score, status_label, tranche_action, "
                    "price_at_signal, factor_scores, earnings_flag, "
                    "earnings_days_away) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scan_id,
                        row["ticker"],
                        float(row.get("total_score", 0)),
                        row.get("status_label"),
                        tr.get("action"),
                        float(row["price"]) if row.get("price") is not None
                        else None,
                        json.dumps(row.get("factor_scores", {})),
                        1 if row.get("earnings_flag") else 0,
                        row.get("earnings_days_away"),
                    )
                )
        conn.close()
        return scan_id
    except (sqlite3.Error, OSError):
        # Journal failures must NEVER break the app. Silent fail; the user
        # will see they have no data on Page 5 (Performance Journal) and can investigate.
        return None


def log_override(scan_id: str, ticker: str, override_action: str,
                 reason: str | None = None) -> bool:
    """Record a user override on a specific (scan_id, ticker) signal.
    Returns True on success, False on any DB error."""
    if not scan_id or not ticker or not override_action:
        return False
    try:
        conn = _connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO overrides (scan_id, ticker, "
                "override_action, override_reason, override_timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (scan_id, ticker.upper(), override_action, reason or "",
                 datetime.now().isoformat())
            )
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


# ── read path ─────────────────────────────────────────────────────────────────
def load_signals(days: int = 90) -> pd.DataFrame:
    """
    Return all journaled signals from the last `days` days as a flat DataFrame.

    Columns: scan_id, scan_timestamp, ticker, strategy, regime, macro_score,
    composite_score, status_label, tranche_action, price_at_signal,
    factor_scores (parsed dict), earnings_flag, override_action,
    override_reason.
    """
    try:
        conn = _connect()
        sql = """
            SELECT
                s.scan_id, s.scan_timestamp, s.strategy, s.regime, s.macro_score,
                sg.ticker, sg.composite_score, sg.status_label, sg.tranche_action,
                sg.price_at_signal, sg.factor_scores, sg.earnings_flag,
                sg.earnings_days_away,
                o.override_action, o.override_reason, o.override_timestamp
            FROM scans s
            JOIN signals sg ON s.scan_id = sg.scan_id
            LEFT JOIN overrides o ON sg.scan_id = o.scan_id
                                  AND sg.ticker = o.ticker
            WHERE datetime(s.scan_timestamp) >= datetime('now', ?)
            ORDER BY s.scan_timestamp DESC, sg.composite_score DESC
        """
        df = pd.read_sql_query(sql, conn, params=(f"-{int(days)} days",))
        conn.close()
        if not df.empty and "factor_scores" in df.columns:
            df["factor_scores"] = df["factor_scores"].apply(
                lambda s: json.loads(s) if s else {})
        return df
    except (sqlite3.Error, OSError):
        return pd.DataFrame()


def attach_forward_returns(signals_df: pd.DataFrame,
                           horizons: tuple = (5, 10, 20, 60)) -> pd.DataFrame:
    """
    For each row in `signals_df`, look up the forward return at each horizon
    (in trading days) from the scan date, using REAL closing prices via
    du.get_close_series.

    Forward returns that haven't matured yet (e.g. a signal from 3 days ago
    won't have a 20-day forward return until 17 more trading days pass)
    are left as NaN — never mocked.

    Columns added: fwd_ret_5, fwd_ret_10, fwd_ret_20, fwd_ret_60 (all in %).
    """
    if signals_df.empty:
        return signals_df

    out = signals_df.copy()
    for h in horizons:
        out[f"fwd_ret_{h}"] = pd.NA

    # group by ticker so we fetch each price series once
    for ticker, group in signals_df.groupby("ticker"):
        try:
            # 620 days covers the longest horizon (60) + ~year of journal
            prices = du.get_close_series(ticker, days=620)
            if prices.empty:
                continue
            prices.index = pd.to_datetime(prices.index)
        except Exception:
            continue

        for idx, row in group.iterrows():
            try:
                scan_date = pd.to_datetime(row["scan_timestamp"]).normalize()
                entry_price = row.get("price_at_signal")
                if entry_price is None or pd.isna(entry_price) or entry_price <= 0:
                    continue

                # find the price-series position closest to the scan date
                # without going past it (we want a date <= scan_date)
                pos_arr = prices.index.searchsorted(scan_date, side="right") - 1
                if pos_arr < 0:
                    continue
                start_pos = int(pos_arr)

                for h in horizons:
                    end_pos = start_pos + h
                    if end_pos >= len(prices):
                        continue  # signal hasn't matured yet -> NaN
                    fwd_price = float(prices.iloc[end_pos])
                    pct = (fwd_price - float(entry_price)) / float(entry_price) * 100
                    out.at[idx, f"fwd_ret_{h}"] = round(pct, 2)
            except Exception:
                continue

    return out


# ── aggregation helpers ───────────────────────────────────────────────────────
def performance_by_tier(signals_df: pd.DataFrame,
                        horizon_col: str = "fwd_ret_20") -> pd.DataFrame:
    """
    Group signals by Conviction Tier and report:
        n            — number of matured signals
        hit_rate     — % positive (LONG: ret>0, SHORT: ret<0)
        avg_ret      — mean forward return %
        avg_win      — mean of the positive returns
        avg_loss     — mean of the negative returns
        win_loss     — avg_win / |avg_loss| (lower is dangerous)
        edge         — directionally-adjusted edge in %

    `horizon_col` picks which forward-return column to evaluate against.

    Historical signals may carry legacy 6-tier labels (STRONG LONG / LEAN
    LONG / HOLD / WATCH SHORT / LEAN SHORT / STRONG SHORT) — they're
    normalized to the current 5-tier conviction names via
    `run_scanner.normalize_tier_label()` so they group correctly alongside
    new signals.
    """
    if signals_df.empty or horizon_col not in signals_df.columns:
        return pd.DataFrame()

    df = signals_df.copy()
    # drop rows where the chosen horizon hasn't matured
    df = df[df[horizon_col].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    # normalize legacy tier labels so old + new journal rows group together
    try:
        from run_scanner import normalize_tier_label
        df["status_label"] = df["status_label"].apply(normalize_tier_label)
    except ImportError:
        pass  # run_scanner not importable in some test envs — just skip

    # normalize: for SHORT tiers we want negative returns to count as wins
    df["_direction"] = df["status_label"].apply(_direction_of)
    df["_directional_ret"] = df.apply(
        lambda r: r[horizon_col] if r["_direction"] == "LONG"
        else (-r[horizon_col] if r["_direction"] == "SHORT" else 0),
        axis=1
    )

    def _row(g):
        rets = g[horizon_col].astype(float)
        dir_rets = g["_directional_ret"].astype(float)
        wins = dir_rets[dir_rets > 0]
        losses = dir_rets[dir_rets < 0]
        n = len(g)
        return pd.Series({
            "n": n,
            "hit_rate_pct": round((dir_rets > 0).sum() / n * 100, 1)
                            if n else 0.0,
            "avg_ret_pct": round(rets.mean(), 2),
            "avg_win_pct": round(wins.mean(), 2) if len(wins) else None,
            "avg_loss_pct": round(losses.mean(), 2) if len(losses) else None,
            "edge_pct": round(dir_rets.mean(), 2),
        })

    summary = df.groupby("status_label", dropna=False).apply(
        _row, include_groups=False).reset_index()
    return summary


def system_vs_override(signals_df: pd.DataFrame,
                       horizon_col: str = "fwd_ret_20") -> dict:
    """
    Compare forward returns of signals where the user FOLLOWED the system
    vs those where they OVERRODE it.

    Returns a dict:
        {"followed": {"n": int, "edge_pct": float, "hit_rate_pct": float},
         "overrode": {"n": int, "edge_pct": float, "hit_rate_pct": float},
         "spread_pct": float}  # overrode_edge - followed_edge
    """
    if signals_df.empty or horizon_col not in signals_df.columns:
        return {"followed": None, "overrode": None, "spread_pct": None}

    df = signals_df[signals_df[horizon_col].notna()].copy()
    if df.empty:
        return {"followed": None, "overrode": None, "spread_pct": None}

    df["_direction"] = df["status_label"].apply(_direction_of)
    df["_dir_ret"] = df.apply(
        lambda r: r[horizon_col] if r["_direction"] == "LONG"
        else (-r[horizon_col] if r["_direction"] == "SHORT" else 0),
        axis=1
    )

    overrode = df[df["override_action"].notna()
                  & (df["override_action"] != "")]
    followed = df[df["override_action"].isna()
                  | (df["override_action"] == "")]

    def _stats(g):
        if len(g) == 0:
            return None
        rets = g["_dir_ret"].astype(float)
        return {
            "n": int(len(g)),
            "edge_pct": round(float(rets.mean()), 2),
            "hit_rate_pct": round((rets > 0).sum() / len(g) * 100, 1),
        }

    f_stats = _stats(followed)
    o_stats = _stats(overrode)
    spread = None
    if f_stats and o_stats:
        spread = round(o_stats["edge_pct"] - f_stats["edge_pct"], 2)
    return {"followed": f_stats, "overrode": o_stats, "spread_pct": spread}


def _direction_of(status_label: str | None) -> str:
    """Map a status label to LONG / SHORT / NEUTRAL.

    Handles both the current 5-tier conviction labels (HIGH CONVICTION,
    TRADABLE, NEUTRAL, CAUTION, AVOID / SHORT) and the legacy 6-tier
    directional-bias labels (STRONG LONG, LEAN LONG, HOLD / CASH, WATCH
    SHORT, LEAN SHORT, STRONG SHORT) so historical journal rows still
    classify correctly.
    """
    if not status_label:
        return "NEUTRAL"
    s = str(status_label).upper()
    if "BLOCKED" in s:
        return "NEUTRAL"
    # current 5-tier conviction names — HIGH CONVICTION and TRADABLE are LONG
    # signals; CAUTION and AVOID/SHORT are SHORT signals
    if "HIGH CONVICTION" in s or "TRADABLE" in s:
        return "LONG"
    if "AVOID" in s or "CAUTION" in s:
        return "SHORT"
    # legacy 6-tier names still recognized so old journal rows work
    if "LONG" in s:
        return "LONG"
    if "SHORT" in s:
        return "SHORT"
    return "NEUTRAL"


def journal_stats() -> dict:
    """Quick summary stats for the Page 5 header."""
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT COUNT(*) as n_scans, MIN(scan_timestamp) as first, "
            "MAX(scan_timestamp) as last FROM scans"
        )
        row = cur.fetchone()
        cur2 = conn.execute("SELECT COUNT(*) FROM signals")
        n_signals = cur2.fetchone()[0]
        cur3 = conn.execute("SELECT COUNT(*) FROM overrides")
        n_overrides = cur3.fetchone()[0]
        conn.close()
        return {
            "n_scans": row["n_scans"] or 0,
            "n_signals": n_signals or 0,
            "n_overrides": n_overrides or 0,
            "first_scan": row["first"],
            "last_scan": row["last"],
            "db_path": str(_DB_PATH),
        }
    except (sqlite3.Error, OSError):
        return {"n_scans": 0, "n_signals": 0, "n_overrides": 0,
                "first_scan": None, "last_scan": None,
                "db_path": str(_DB_PATH)}


def clear_journal() -> bool:
    """Wipe ALL journaled data. Used for tests and the user-facing reset
    button on Page 5. Confirms via the truthy return value."""
    try:
        conn = _connect()
        with conn:
            conn.execute("DELETE FROM overrides")
            conn.execute("DELETE FROM signals")
            conn.execute("DELETE FROM scans")
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False
