"""
ohlcv_cache.py — Persistent OHLCV cache, with corruption protection.

Purpose
-------
yfinance is the dashboard's only source for OHLCV history (Finnhub free tier
has no candles endpoint). yfinance is a scrape — it breaks regularly. The
existing pickle-based last-known-good cache helps but has limits:
  - Stored in /tmp on Linux (wiped on reboot)
  - Keyed by full request signature, so fetches of different windows don't share
  - Whole-DataFrame blobs — no row-level merge, no corruption protection

This module is a proper persistent OHLCV store:
  - SQLite at ~/.stock_gating_v2/ohlcv_cache.db (same dir as other DBs)
  - One row per (ticker, date) — fetches merge into a growing corpus
  - Survives reboots, reinstalls, and across-machine `cp` of the data dir
  - Corruption check at write time — bad rows never enter the cache

Usage pattern (in data_utils.get_history):
  1. Try yfinance live fetch
  2. On success: write good rows to cache, return live result
  3. On failure: read from cache for the requested window

Cache returns None (not empty DF) when no rows are available — caller
decides what to do.

Corruption protection
---------------------
Before any write, scan adjacent-day close ratios for the new rows being
inserted. If any pair shows a ratio > 1.5 or < 0.67 (50%+ overnight move),
SKIP writing those rows. The cache will be slightly incomplete on the days
flanking the corruption — better than caching the MXL phantom $52 forever.

Why the limit is 1.5: real overnight moves up to ~30% happen (earnings, FDA
decisions). 50% is well into "data error" territory. Using the same threshold
as the existing PRICE_CORRUPTION_MAX_DAILY_RATIO in run_portfolio_backtest.

Honest limitations
------------------
- Doesn't backfill historical data unless yfinance has worked at least once
  for that ticker. First-time installs will still depend on yfinance.
- Doesn't help with live quotes — those are still Finnhub-primary.
- Doesn't help with fundamentals — those are still Finnhub-primary with
  the existing pickle LKG as fallback.
- Can never serve future dates — cache is strictly historical replay.
"""
from __future__ import annotations
import os
import sqlite3
import threading
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd


# Same threshold the backtest engine uses to detect MXL-style corruption
_CORRUPTION_MAX_RATIO = 1.5


# ── DB path / connection ────────────────────────────────────────────────────
def _db_path() -> Path:
    """Cache location: ~/.stock_gating_v2/ohlcv_cache.db. Falls back to /tmp
    if the home directory isn't writable (rare; containerized envs)."""
    try:
        base = Path.home() / ".stock_gating_v2"
    except RuntimeError:
        base = Path("/tmp/stock_gating_v2")
    base.mkdir(parents=True, exist_ok=True)
    return base / "ohlcv_cache.db"


# Use a single connection per thread — SQLite connections are not safe to share
_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(str(_db_path()), isolation_level=None)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ticker_date ON ohlcv(ticker, date)")
        _local.conn = c
    return _local.conn


# ── corruption check ────────────────────────────────────────────────────────
def _filter_corrupt_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose Close ratio to the prior row is > 1.5 or < 0.67.

    Both the corrupt row AND the prior row are dropped to be safe — we can't
    tell which one is wrong, only that one of them is. Adjacent good rows
    are retained.

    Returns a (possibly smaller) DataFrame. Empty input returns empty.
    """
    if df is None or df.empty or "Close" not in df.columns:
        return df
    if len(df) < 2:
        return df
    closes = df["Close"].dropna()
    if len(closes) < 2:
        return df
    upper = _CORRUPTION_MAX_RATIO
    lower = 1.0 / _CORRUPTION_MAX_RATIO
    ratios = closes / closes.shift(1)
    bad_mask = ((ratios > upper) | (ratios < lower)).fillna(False)
    if not bad_mask.any():
        return df  # clean
    # Drop the bad day AND the day before (we don't know which is wrong)
    drop_idx = set()
    for i, is_bad in enumerate(bad_mask):
        if is_bad:
            drop_idx.add(closes.index[i])
            if i > 0:
                drop_idx.add(closes.index[i - 1])
    return df.drop(index=list(drop_idx), errors="ignore")


# ── public API ───────────────────────────────────────────────────────────────
def write(ticker: str, df: pd.DataFrame) -> int:
    """Persist OHLCV rows for `ticker` from `df`. Returns number of rows
    written (after corruption filtering). Safe to call repeatedly — the
    primary key (ticker, date) means duplicate dates get UPDATED with the
    newer data (which is what we want — later fetches override earlier ones).

    df must have a DatetimeIndex and OHLCV columns. Missing columns are OK
    (we'll insert NULL); missing rows are OK (just less data cached).
    """
    if df is None or df.empty:
        return 0
    if not isinstance(df.index, pd.DatetimeIndex):
        # Try to coerce — yfinance returns DatetimeIndex but defensive anyway
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        except Exception:
            return 0

    clean = _filter_corrupt_rows(df)
    if clean is None or clean.empty:
        return 0

    ticker = ticker.upper().strip()
    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    rows = []
    for idx, r in clean.iterrows():
        rows.append((
            ticker,
            idx.strftime("%Y-%m-%d"),
            _safe_float(r.get("Open")),
            _safe_float(r.get("High")),
            _safe_float(r.get("Low")),
            _safe_float(r.get("Close")),
            _safe_float(r.get("Volume")),
            fetched_at,
        ))
    if not rows:
        return 0

    try:
        c = _conn()
        c.executemany(
            "INSERT OR REPLACE INTO ohlcv "
            "(ticker, date, open, high, low, close, volume, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows)
        return len(rows)
    except sqlite3.Error:
        return 0


def read(ticker: str, days: int = 400) -> pd.DataFrame | None:
    """Read up to `days` calendar days of cached history for `ticker`,
    ending at the most recent available date in the cache.

    Returns a DataFrame with OHLCV columns and a DatetimeIndex, sorted
    ascending by date. None if no rows are cached at all for this ticker.

    Empty rows (all-NULL OHLCV) are excluded — they're not useful and
    were probably partial inserts.
    """
    ticker = ticker.upper().strip()
    try:
        c = _conn()
        cutoff = (datetime.today().date() - timedelta(days=days)).isoformat()
        cursor = c.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM ohlcv "
            "WHERE ticker = ? AND date >= ? "
            "  AND close IS NOT NULL "
            "ORDER BY date ASC",
            (ticker, cutoff))
        records = cursor.fetchall()
    except sqlite3.Error:
        return None

    if not records:
        return None

    df = pd.DataFrame(records,
                       columns=["date", "Open", "High", "Low", "Close", "Volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df.index.name = None
    return df


def has_recent_data(ticker: str, max_age_days: int = 7) -> bool:
    """True if the cache has at least one row for `ticker` newer than
    `max_age_days` ago. Used to decide whether to trust the cache for
    "current" use — a cache with only 6-month-old data is stale relative
    to today's market.

    Note: this asks about how recent the cached PRICE DATA is, not when
    we cached it (which would be fetched_at). The relevant question is
    "does the cache cover up to roughly today's market?"
    """
    ticker = ticker.upper().strip()
    cutoff = (datetime.today().date() - timedelta(days=max_age_days)).isoformat()
    try:
        c = _conn()
        cursor = c.execute(
            "SELECT 1 FROM ohlcv WHERE ticker = ? AND date >= ? LIMIT 1",
            (ticker, cutoff))
        return cursor.fetchone() is not None
    except sqlite3.Error:
        return False


def coverage_stats() -> dict:
    """Diagnostic: number of tickers cached, total row count, earliest/latest
    dates. Used by the UI to report cache health."""
    try:
        c = _conn()
        n_tickers = c.execute("SELECT COUNT(DISTINCT ticker) FROM ohlcv").fetchone()[0]
        n_rows = c.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        date_range = c.execute(
            "SELECT MIN(date), MAX(date) FROM ohlcv").fetchone()
        return {
            "tickers": n_tickers or 0,
            "rows":    n_rows or 0,
            "earliest_date": date_range[0] if date_range else None,
            "latest_date":   date_range[1] if date_range else None,
            "db_path": str(_db_path()),
        }
    except sqlite3.Error:
        return {"tickers": 0, "rows": 0, "earliest_date": None,
                "latest_date": None, "db_path": str(_db_path())}


# ── helpers ──────────────────────────────────────────────────────────────────
def _safe_float(x):
    """None / NaN-tolerant float coercion for the SQLite columns."""
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None
