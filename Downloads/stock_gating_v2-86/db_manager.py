"""
db_manager.py — Paper-trade persistence layer.

A small, sqlite-backed CRUD helper for tracking simulated trade execution.
Lives in its own DB file (`~/.stock_gating_v2/paper_trades.db`) so it's
independent of the signal journal — wiping one never touches the other.

This module is deliberately NOT a trading library. It records what the user
*decided* to execute and what the close price was. It does NOT compute
position sizing (caller passes shares directly), does NOT manage margin or
borrow cost, and does NOT enforce any portfolio rules. Those are upstream
concerns.

Every public function silently degrades to a safe return value on DB error
so the dashboard never crashes if the disk is unreachable.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── storage paths ────────────────────────────────────────────────────────────
_DB_DIR = Path.home() / ".stock_gating_v2"
_DB_PATH = _DB_DIR / "paper_trades.db"


def _connect() -> sqlite3.Connection:
    """Open a connection, lazily creating the directory + schema."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation. Forward-compatible: adding columns later
    via ALTER TABLE is safe — existing rows just get NULL for new columns."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('Long', 'Short')),
            entry_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            strategy_engine TEXT NOT NULL,
            macro_score REAL,
            status TEXT NOT NULL DEFAULT 'Open'
                CHECK (status IN ('Open', 'Closed')),
            entry_timestamp TEXT NOT NULL,
            exit_price REAL,
            exit_timestamp TEXT,
            realized_pnl REAL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
        CREATE INDEX IF NOT EXISTS idx_trades_entry_ts
            ON trades(entry_timestamp);
    """)
    conn.commit()


# ── CRUD ─────────────────────────────────────────────────────────────────────
def execute_trade(ticker: str, direction: str, entry_price: float,
                  quantity: int, strategy_engine: str,
                  macro_score: float | None = None) -> int | None:
    """
    Record a new Open paper trade. Returns the new row's `id`, or None
    on any validation / DB failure.

    Validations:
      - ticker must be non-empty
      - direction must be exactly 'Long' or 'Short'
      - entry_price must be > 0
      - quantity must be > 0
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None
    if direction not in ("Long", "Short"):
        return None
    try:
        entry_price = float(entry_price)
        quantity = int(quantity)
    except (TypeError, ValueError):
        return None
    if entry_price <= 0 or quantity <= 0:
        return None

    try:
        conn = _connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO trades (ticker, direction, entry_price, "
                "quantity, strategy_engine, macro_score, status, "
                "entry_timestamp) VALUES (?, ?, ?, ?, ?, ?, 'Open', ?)",
                (ticker, direction, entry_price, quantity,
                 strategy_engine or "unknown",
                 float(macro_score) if macro_score is not None else None,
                 datetime.now().isoformat()),
            )
            new_id = cur.lastrowid
        conn.close()
        return int(new_id) if new_id else None
    except (sqlite3.Error, OSError):
        return None


def has_open_position(ticker: str, direction: str) -> bool:
    """True if there's already an Open trade for this exact ticker+direction.
    Used by the UI to prevent accidental double-execution from rapid clicks."""
    ticker = (ticker or "").upper().strip()
    if not ticker or direction not in ("Long", "Short"):
        return False
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker = ? AND direction = ? "
            "AND status = 'Open'",
            (ticker, direction))
        n = cur.fetchone()[0] or 0
        conn.close()
        return n > 0
    except (sqlite3.Error, OSError):
        return False


def get_open_positions() -> pd.DataFrame:
    """All trades with status='Open', most-recent-first.

    Returns an empty DataFrame on DB failure or no open positions.
    Columns: id, ticker, direction, entry_price, quantity, strategy_engine,
    macro_score, entry_timestamp.
    """
    try:
        conn = _connect()
        df = pd.read_sql_query(
            "SELECT id, ticker, direction, entry_price, quantity, "
            "strategy_engine, macro_score, entry_timestamp "
            "FROM trades WHERE status = 'Open' "
            "ORDER BY entry_timestamp DESC",
            conn)
        conn.close()
        return df
    except (sqlite3.Error, OSError):
        return pd.DataFrame()


def get_closed_trades(limit: int | None = None) -> pd.DataFrame:
    """All Closed trades, most-recent-first.

    Columns include the entry fields plus exit_price, exit_timestamp,
    realized_pnl, and a derived return_pct.
    """
    try:
        conn = _connect()
        sql = (
            "SELECT id, ticker, direction, entry_price, quantity, "
            "strategy_engine, macro_score, entry_timestamp, "
            "exit_price, exit_timestamp, realized_pnl "
            "FROM trades WHERE status = 'Closed' "
            "ORDER BY exit_timestamp DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        df = pd.read_sql_query(sql, conn)
        conn.close()
        if df.empty:
            return df
        # derived: return_pct (direction-aware)
        def _ret(row):
            if (row["entry_price"] and row["exit_price"]
                    and row["entry_price"] > 0):
                pct = (row["exit_price"] - row["entry_price"]) \
                      / row["entry_price"] * 100
                if row["direction"] == "Short":
                    pct = -pct
                return round(pct, 2)
            return None
        df["return_pct"] = df.apply(_ret, axis=1)
        return df
    except (sqlite3.Error, OSError):
        return pd.DataFrame()


def close_position(trade_id: int, exit_price: float) -> bool:
    """
    Close an Open trade by id. Computes realized P&L direction-aware:
        Long  PnL = (exit - entry) * qty
        Short PnL = (entry - exit) * qty

    Returns True on success; False if the trade doesn't exist, isn't Open,
    or the DB write fails. Atomic — either the row updates fully or not at all.
    """
    if not trade_id:
        return False
    try:
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return False
    if exit_price <= 0:
        return False

    try:
        conn = _connect()
        with conn:
            cur = conn.execute(
                "SELECT direction, entry_price, quantity, status "
                "FROM trades WHERE id = ?", (int(trade_id),))
            row = cur.fetchone()
            if not row or row["status"] != "Open":
                conn.close()
                return False

            entry = float(row["entry_price"])
            qty = int(row["quantity"])
            if row["direction"] == "Long":
                pnl = (exit_price - entry) * qty
            else:  # Short
                pnl = (entry - exit_price) * qty
            pnl = round(float(pnl), 2)

            conn.execute(
                "UPDATE trades SET status='Closed', exit_price=?, "
                "exit_timestamp=?, realized_pnl=? "
                "WHERE id=? AND status='Open'",
                (round(exit_price, 4), datetime.now().isoformat(),
                 pnl, int(trade_id)))
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def wipe_database() -> bool:
    """Permanently delete ALL trades (open and closed). Returns True on
    success. This is irreversible — caller is responsible for confirmation."""
    try:
        conn = _connect()
        with conn:
            conn.execute("DELETE FROM trades")
            # reset the AUTOINCREMENT counter so the next trade is id=1
            conn.execute("DELETE FROM sqlite_sequence WHERE name='trades'")
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


# ── stats (used by the blotter page header) ──────────────────────────────────
def stats() -> dict:
    """Quick summary stats for the Portfolio page header."""
    try:
        conn = _connect()
        cur = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN status='Open' THEN 1 ELSE 0 END) AS open_n, "
            "  SUM(CASE WHEN status='Closed' THEN 1 ELSE 0 END) AS closed_n, "
            "  COALESCE(SUM(CASE WHEN status='Closed' THEN realized_pnl END), 0) "
            "    AS total_pnl "
            "FROM trades")
        row = cur.fetchone()
        conn.close()
        return {
            "open_n": int(row["open_n"] or 0),
            "closed_n": int(row["closed_n"] or 0),
            "total_realized_pnl": round(float(row["total_pnl"] or 0), 2),
            "db_path": str(_DB_PATH),
        }
    except (sqlite3.Error, OSError):
        return {"open_n": 0, "closed_n": 0, "total_realized_pnl": 0.0,
                "db_path": str(_DB_PATH)}
