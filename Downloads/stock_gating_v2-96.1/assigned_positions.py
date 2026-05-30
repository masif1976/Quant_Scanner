"""
assigned_positions.py — Persistent ledger of Wheel-strategy assigned positions.

When a cash-secured put is assigned (stock drops below strike at expiry,
shares are PUT to you), the position transitions from "selling puts" to
"holding 100 shares per contract with a cost basis of K_assigned." This
module tracks those positions so the Wheel Manager UI can recommend
covered call strikes to recover capital.

Schema:
  ticker:              stock symbol (e.g. "RIVN")
  assigned_date:       ISO date the put was assigned
  assigned_strike:     K_assigned — the strike price you were put at
  original_put_premium: P_put_original — the per-share premium you
                       collected when you sold the put (NOT total dollars)
  shares:              typically 100 per contract; allow override for
                       multi-contract positions
  notes:               free text (e.g. "RIVN earnings missed", "macro tape down")
  status:              "open" or "closed"
  closed_date:         ISO date the position was unwound (None while open)
  closed_via:          "call_assigned" | "manual_sell" | "put_expired_otm" | "other"
                       — Wheel terminology for the exit
  closed_price:        per-share price received when closed
  realized_pnl:        total realized P&L in dollars across the wheel cycle
                       (put premium + share P&L + call premium if applicable)

Why we track closed positions too: lets the user audit their wheel
performance over time — average P&L per cycle, win rate, total realized.
That's a "phase 3" view for the future.

Why a separate DB file instead of reusing existing ones: clean separation
of concerns. The Performance Journal tracks signal events; the Portfolio
tracks paper-trade equity positions; this tracks options-wheel state.
Mixing them in one DB would muddy the schemas.
"""
from __future__ import annotations
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path


# ── DB path / connection ────────────────────────────────────────────────────
def _db_path() -> Path:
    try:
        base = Path.home() / ".stock_gating_v2"
    except RuntimeError:
        base = Path("/tmp/stock_gating_v2")
    base.mkdir(parents=True, exist_ok=True)
    return base / "assigned_positions.db"


_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect(str(_db_path()), isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("""
            CREATE TABLE IF NOT EXISTS assigned_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                assigned_date TEXT NOT NULL,
                assigned_strike REAL NOT NULL,
                original_put_premium REAL NOT NULL,
                shares INTEGER NOT NULL DEFAULT 100,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                closed_date TEXT,
                closed_via TEXT,
                closed_price REAL,
                realized_pnl REAL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON assigned_positions(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON assigned_positions(ticker)")
        _local.conn = c
    return _local.conn


# ── public API ──────────────────────────────────────────────────────────────
def mark_assigned(ticker: str,
                   assigned_strike: float,
                   original_put_premium: float,
                   assigned_date: str | None = None,
                   shares: int = 100,
                   notes: str = "") -> int | None:
    """Record a new assigned put position.

    Args:
        ticker: stock symbol
        assigned_strike: the put strike K (per share)
        original_put_premium: per-share premium you collected on the original
                              put sale (NOT total dollars — just bid price)
        assigned_date: ISO YYYY-MM-DD; defaults to today
        shares: typically 100 per contract
        notes: optional free text

    Returns the new row's id, or None on failure.
    """
    if not ticker or assigned_strike <= 0 or original_put_premium < 0:
        return None
    if assigned_date is None:
        assigned_date = date.today().isoformat()
    try:
        c = _conn()
        cursor = c.execute(
            "INSERT INTO assigned_positions "
            "(ticker, assigned_date, assigned_strike, original_put_premium, "
            " shares, notes, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (ticker.upper().strip(), assigned_date, float(assigned_strike),
             float(original_put_premium), int(shares), notes or "",
             datetime.utcnow().isoformat(timespec="seconds") + "Z"))
        return cursor.lastrowid
    except sqlite3.Error:
        return None


def list_open() -> list[dict]:
    """Return all open (not-yet-unwound) assigned positions, newest first."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM assigned_positions "
            "WHERE status = 'open' "
            "ORDER BY assigned_date DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def list_closed(limit: int = 50) -> list[dict]:
    """Return recently-closed positions, newest first. Used for the
    history panel that audits wheel cycle outcomes."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM assigned_positions "
            "WHERE status = 'closed' "
            "ORDER BY closed_date DESC, id DESC "
            "LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def close_position(position_id: int,
                    closed_via: str,
                    closed_price: float,
                    call_premium_collected: float = 0.0,
                    closed_date: str | None = None) -> bool:
    """Mark a position as closed and compute realized P&L.

    Args:
        position_id: the row id from mark_assigned
        closed_via: "call_assigned" | "manual_sell" | "put_expired_otm" | "other"
        closed_price: per-share exit price (= call strike if call_assigned,
                      or current price if manual_sell)
        call_premium_collected: per-share total of any call premiums collected
                                during the holding period. 0 if none.
        closed_date: ISO YYYY-MM-DD; defaults to today

    Realized P&L formula (per share, multiply by shares):
        original_put_premium  (collected when sold)
        + (closed_price - assigned_strike)  (P&L on the shares themselves)
        + call_premium_collected  (any covered call premiums)

    Returns True on success.
    """
    if closed_date is None:
        closed_date = date.today().isoformat()
    try:
        c = _conn()
        row = c.execute(
            "SELECT * FROM assigned_positions WHERE id = ?",
            (position_id,)).fetchone()
        if row is None or row["status"] != "open":
            return False
        # Compute realized P&L (in dollars, not per-share)
        shares = row["shares"]
        share_pnl = (closed_price - row["assigned_strike"]) * shares
        put_p = row["original_put_premium"] * shares
        call_p = call_premium_collected * shares
        total = round(put_p + share_pnl + call_p, 2)
        c.execute(
            "UPDATE assigned_positions SET "
            "status = 'closed', closed_date = ?, closed_via = ?, "
            "closed_price = ?, realized_pnl = ? "
            "WHERE id = ?",
            (closed_date, closed_via, float(closed_price), total, position_id))
        return True
    except sqlite3.Error:
        return False


def delete_position(position_id: int) -> bool:
    """Hard-delete a position. Used for correcting data entry mistakes
    BEFORE the position has any meaningful history.

    For a position that's been managed for weeks and you want to remove
    from the active ledger, use close_position instead — that preserves
    the audit trail. delete_position is for "I typo'd the strike, this
    row is wrong" situations."""
    try:
        c = _conn()
        c.execute("DELETE FROM assigned_positions WHERE id = ?", (position_id,))
        return True
    except sqlite3.Error:
        return False


def cost_basis(position: dict) -> float:
    """Net break-even cost basis per share for a position.
    CB = K_assigned - P_put_original
    """
    return position["assigned_strike"] - position["original_put_premium"]


def coverage_stats() -> dict:
    """Diagnostic for the UI: counts of open vs closed positions, total
    realized P&L across closed positions."""
    try:
        c = _conn()
        n_open = c.execute(
            "SELECT COUNT(*) FROM assigned_positions WHERE status = 'open'"
        ).fetchone()[0]
        n_closed = c.execute(
            "SELECT COUNT(*) FROM assigned_positions WHERE status = 'closed'"
        ).fetchone()[0]
        realized = c.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM assigned_positions "
            "WHERE status = 'closed'"
        ).fetchone()[0]
        return {"open": n_open, "closed": n_closed,
                "total_realized_pnl": float(realized or 0.0),
                "db_path": str(_db_path())}
    except sqlite3.Error:
        return {"open": 0, "closed": 0, "total_realized_pnl": 0.0,
                "db_path": str(_db_path())}
