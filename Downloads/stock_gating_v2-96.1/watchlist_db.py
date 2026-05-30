"""
watchlist_db.py — Persistent watchlist storage with MULTI-LIST support.

Sqlite-backed at ~/.stock_gating_v2/watchlist.db. Independent from the
signal journal and paper-trade DB so wiping one never touches the others.

## Multi-list design

The schema supports multiple NAMED watchlists. A "Default" watchlist
auto-creates on first run with the supplied seed tickers, and at all
times exactly ONE watchlist is marked as ACTIVE — that's the one the
dashboard scans.

Tables:
  - watchlists(name, created_at, is_active)
    one row per named list; exactly one has is_active=1
  - watchlist_tickers(watchlist_name, ticker, added_at)
    one row per (list, ticker) pair; composite PK

Migration: existing single-table installs (one `watchlist` table with no
`watchlist_name` scope) get auto-converted to a "Default" list on first
load via _migrate_legacy_schema. Lossless — all old tickers preserved.

## API conventions

Existing single-list functions (load_watchlist, save_watchlist, add_ticker,
remove_ticker, reset_to_default) operate on the ACTIVE list. New multi-list
functions (list_watchlists, create_watchlist, rename_watchlist, etc.) are
explicit about which list they affect.

Every public function silently degrades on DB failure (returns sensible
defaults rather than crashing) so the dashboard keeps working even if the
disk is unreachable.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_DIR = Path.home() / ".stock_gating_v2"
_DB_PATH = _DB_DIR / "watchlist.db"

# Reserved name for the auto-created first list. Cannot be deleted (the
# delete logic blocks removal of the last list, and Default is always
# the bootstrap one).
DEFAULT_WATCHLIST_NAME = "Default"


def _connect() -> sqlite3.Connection:
    """Open a connection, lazily creating the directory + schema +
    migrating legacy single-list installs."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")  # for cascade deletes
    _ensure_schema(conn)
    _migrate_legacy_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the multi-list tables if they don't exist.

    Two-table design:
    - watchlists: the named lists themselves, with one marked active
    - watchlist_tickers: per-list ticker membership

    Foreign key on watchlist_tickers cascades deletes — drop a list and
    all its tickers go with it, no orphans.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlists (
            name        TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            is_active   INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS watchlist_tickers (
            watchlist_name  TEXT NOT NULL,
            ticker          TEXT NOT NULL,
            added_at        TEXT NOT NULL,
            PRIMARY KEY (watchlist_name, ticker),
            FOREIGN KEY (watchlist_name) REFERENCES watchlists(name)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_watchlist_tickers_name
            ON watchlist_tickers(watchlist_name);
    """)
    conn.commit()


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    """Convert old single-table `watchlist` data to the new multi-list
    schema. Idempotent — safe to run on every connection."""
    # Does the legacy single-list table exist?
    legacy_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist'"
    ).fetchone() is not None
    if not legacy_exists:
        return

    # Are there any rows to migrate?
    legacy_rows = conn.execute(
        "SELECT ticker, added_at FROM watchlist"
    ).fetchall()
    if not legacy_rows:
        # Empty legacy table — just drop it
        with conn:
            conn.execute("DROP TABLE watchlist")
        return

    # Migrate: ensure Default watchlist exists, copy tickers into it
    with conn:
        # Create Default if it doesn't exist; mark active if no list is active
        existing_default = conn.execute(
            "SELECT name FROM watchlists WHERE name = ?",
            (DEFAULT_WATCHLIST_NAME,)
        ).fetchone()
        if existing_default is None:
            # No Default yet — create it and mark active (assuming no
            # other lists exist at this point)
            any_active = conn.execute(
                "SELECT name FROM watchlists WHERE is_active = 1"
            ).fetchone()
            is_active = 0 if any_active else 1
            conn.execute(
                "INSERT INTO watchlists (name, created_at, is_active) "
                "VALUES (?, ?, ?)",
                (DEFAULT_WATCHLIST_NAME, datetime.now().isoformat(), is_active))

        # Copy each legacy ticker into Default (no-op if already present)
        for row in legacy_rows:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist_tickers "
                "(watchlist_name, ticker, added_at) VALUES (?, ?, ?)",
                (DEFAULT_WATCHLIST_NAME, row["ticker"], row["added_at"]))

        # Drop the legacy table once migrated
        conn.execute("DROP TABLE watchlist")


def _ensure_default_exists(conn: sqlite3.Connection,
                             seed_tickers: list[str] | None = None) -> None:
    """Ensure at least one watchlist (Default) exists with at least the
    seed tickers. Called on first-run / empty-DB conditions."""
    count = conn.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0]
    if count > 0:
        return  # Already have lists; nothing to bootstrap

    ts = datetime.now().isoformat()
    with conn:
        conn.execute(
            "INSERT INTO watchlists (name, created_at, is_active) "
            "VALUES (?, ?, 1)",
            (DEFAULT_WATCHLIST_NAME, ts))
        if seed_tickers:
            conn.executemany(
                "INSERT OR IGNORE INTO watchlist_tickers "
                "(watchlist_name, ticker, added_at) VALUES (?, ?, ?)",
                [(DEFAULT_WATCHLIST_NAME, t.upper().strip(), ts)
                 for t in seed_tickers if t.strip()])


def _get_active_name(conn: sqlite3.Connection) -> str:
    """Return the name of the active watchlist, falling back to Default
    if no row has is_active=1 (which shouldn't happen but defensive)."""
    row = conn.execute(
        "SELECT name FROM watchlists WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    if row:
        return row["name"]
    # No active list — pick the first existing one and mark it active
    fallback = conn.execute(
        "SELECT name FROM watchlists ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if fallback:
        with conn:
            conn.execute(
                "UPDATE watchlists SET is_active = 1 WHERE name = ?",
                (fallback["name"],))
        return fallback["name"]
    return DEFAULT_WATCHLIST_NAME  # Shouldn't reach here, but safe


# ─────────────────────────────────────────────────────────────────────────
# LEGACY-COMPATIBLE API — operates on the ACTIVE watchlist.
# Existing call sites (load_watchlist, add_ticker, etc.) keep working
# without changes.
# ─────────────────────────────────────────────────────────────────────────

def load_watchlist(default: list[str] | None = None) -> list[str]:
    """Load tickers from the ACTIVE watchlist. On empty DB (first run) or
    DB failure, seed with `default` and return it.

    Returns: list of upper-case ticker symbols in insertion order.
    """
    try:
        conn = _connect()
        _ensure_default_exists(conn, seed_tickers=default)
        active = _get_active_name(conn)
        rows = conn.execute(
            "SELECT ticker FROM watchlist_tickers "
            "WHERE watchlist_name = ? ORDER BY added_at ASC",
            (active,)
        ).fetchall()
        conn.close()
        return [r["ticker"] for r in rows]
    except (sqlite3.Error, OSError):
        return list(default) if default else []


def save_watchlist(tickers: list[str]) -> bool:
    """Replace the entire ACTIVE watchlist with the supplied list."""
    cleaned = []
    seen = set()
    for t in tickers:
        if not isinstance(t, str):
            continue
        u = t.upper().strip()
        if u and u not in seen:
            seen.add(u)
            cleaned.append(u)

    try:
        conn = _connect()
        _ensure_default_exists(conn)
        active = _get_active_name(conn)
        ts = datetime.now().isoformat()
        with conn:
            conn.execute(
                "DELETE FROM watchlist_tickers WHERE watchlist_name = ?",
                (active,))
            if cleaned:
                conn.executemany(
                    "INSERT INTO watchlist_tickers "
                    "(watchlist_name, ticker, added_at) VALUES (?, ?, ?)",
                    [(active, t, ts) for t in cleaned])
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def add_ticker(ticker: str) -> bool:
    """Add a single ticker to the ACTIVE watchlist."""
    if not isinstance(ticker, str):
        return False
    t = ticker.upper().strip()
    if not t:
        return False
    try:
        conn = _connect()
        _ensure_default_exists(conn)
        active = _get_active_name(conn)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist_tickers "
                "(watchlist_name, ticker, added_at) VALUES (?, ?, ?)",
                (active, t, datetime.now().isoformat()))
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def remove_ticker(ticker: str) -> bool:
    """Remove a single ticker from the ACTIVE watchlist."""
    if not isinstance(ticker, str):
        return False
    t = ticker.upper().strip()
    if not t:
        return False
    try:
        conn = _connect()
        _ensure_default_exists(conn)
        active = _get_active_name(conn)
        with conn:
            conn.execute(
                "DELETE FROM watchlist_tickers "
                "WHERE watchlist_name = ? AND ticker = ?",
                (active, t))
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def reset_to_default(default: list[str]) -> bool:
    """Wipe the ACTIVE watchlist and re-seed from `default`."""
    return save_watchlist(default)


# ─────────────────────────────────────────────────────────────────────────
# MULTI-LIST API — explicit operations on named watchlists.
# ─────────────────────────────────────────────────────────────────────────

def list_watchlists() -> list[dict]:
    """Return all watchlists with their metadata, sorted by created_at.

    Each entry has: {name, created_at, is_active, ticker_count}
    """
    try:
        conn = _connect()
        _ensure_default_exists(conn)
        rows = conn.execute("""
            SELECT
                w.name AS name,
                w.created_at AS created_at,
                w.is_active AS is_active,
                (SELECT COUNT(*) FROM watchlist_tickers wt
                 WHERE wt.watchlist_name = w.name) AS ticker_count
            FROM watchlists w
            ORDER BY w.created_at ASC
        """).fetchall()
        conn.close()
        return [
            {"name": r["name"], "created_at": r["created_at"],
             "is_active": bool(r["is_active"]),
             "ticker_count": r["ticker_count"]}
            for r in rows
        ]
    except (sqlite3.Error, OSError):
        return []


def get_active_watchlist_name() -> str:
    """Return the name of the currently active watchlist."""
    try:
        conn = _connect()
        _ensure_default_exists(conn)
        name = _get_active_name(conn)
        conn.close()
        return name
    except (sqlite3.Error, OSError):
        return DEFAULT_WATCHLIST_NAME


def set_active_watchlist(name: str) -> bool:
    """Switch which watchlist is active. The target list must exist."""
    if not isinstance(name, str) or not name.strip():
        return False
    target = name.strip()
    try:
        conn = _connect()
        # Verify the target exists
        exists = conn.execute(
            "SELECT name FROM watchlists WHERE name = ?", (target,)
        ).fetchone()
        if not exists:
            conn.close()
            return False
        with conn:
            # Clear all active flags, then set the target
            conn.execute("UPDATE watchlists SET is_active = 0")
            conn.execute(
                "UPDATE watchlists SET is_active = 1 WHERE name = ?",
                (target,))
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def create_watchlist(name: str, copy_from: str | None = None) -> bool:
    """Create a new watchlist with the given name. Optionally copy tickers
    from an existing list.

    Validation: name must be non-empty, ≤50 chars, not already taken.
    Empty list is created if copy_from is None.

    Returns True on success, False on validation failure or DB error.
    """
    if not isinstance(name, str):
        return False
    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 50:
        return False
    try:
        conn = _connect()
        # Check name not already used
        existing = conn.execute(
            "SELECT name FROM watchlists WHERE name = ?", (cleaned_name,)
        ).fetchone()
        if existing:
            conn.close()
            return False

        ts = datetime.now().isoformat()
        with conn:
            conn.execute(
                "INSERT INTO watchlists (name, created_at, is_active) "
                "VALUES (?, ?, 0)",
                (cleaned_name, ts))
            if copy_from:
                # Verify source exists, then copy
                src_exists = conn.execute(
                    "SELECT name FROM watchlists WHERE name = ?",
                    (copy_from,)
                ).fetchone()
                if src_exists:
                    conn.execute(
                        "INSERT INTO watchlist_tickers "
                        "(watchlist_name, ticker, added_at) "
                        "SELECT ?, ticker, ? "
                        "FROM watchlist_tickers WHERE watchlist_name = ?",
                        (cleaned_name, ts, copy_from))
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def rename_watchlist(old_name: str, new_name: str) -> bool:
    """Rename a watchlist. The new name must be available and ≤50 chars."""
    if not isinstance(old_name, str) or not isinstance(new_name, str):
        return False
    old = old_name.strip()
    new = new_name.strip()
    if not old or not new or len(new) > 50 or old == new:
        return False
    try:
        conn = _connect()
        # Check old exists and new doesn't
        old_exists = conn.execute(
            "SELECT name FROM watchlists WHERE name = ?", (old,)
        ).fetchone()
        new_taken = conn.execute(
            "SELECT name FROM watchlists WHERE name = ?", (new,)
        ).fetchone()
        if not old_exists or new_taken:
            conn.close()
            return False
        with conn:
            # SQLite enforces FK immediately by default. The natural rename
            # order updates children first (which point to a non-existent
            # parent name temporarily — FK violation) or parent first
            # (which orphans children — also FK violation). Use defer to
            # allow both to land before checking.
            conn.execute("PRAGMA defer_foreign_keys = ON")
            conn.execute(
                "UPDATE watchlist_tickers SET watchlist_name = ? "
                "WHERE watchlist_name = ?", (new, old))
            conn.execute(
                "UPDATE watchlists SET name = ? WHERE name = ?",
                (new, old))
            # defer_foreign_keys auto-resets at transaction end
        conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def delete_watchlist(name: str) -> tuple[bool, str]:
    """Delete a watchlist and all its tickers (CASCADE).

    Guards:
    - Cannot delete the last remaining watchlist (would leave no active list)
    - If deleting the currently-active list, the next-oldest becomes active

    Returns (success, message) where message describes what happened.
    """
    if not isinstance(name, str) or not name.strip():
        return False, "Invalid watchlist name"
    target = name.strip()
    try:
        conn = _connect()
        all_lists = conn.execute(
            "SELECT name, is_active FROM watchlists ORDER BY created_at ASC"
        ).fetchall()
        if not all_lists:
            conn.close()
            return False, "No watchlists exist"
        if len(all_lists) == 1:
            conn.close()
            return False, "Cannot delete the only watchlist"
        # Check target exists
        target_row = next((r for r in all_lists if r["name"] == target), None)
        if not target_row:
            conn.close()
            return False, f"Watchlist '{target}' not found"

        was_active = bool(target_row["is_active"])
        with conn:
            # CASCADE removes the tickers
            conn.execute("DELETE FROM watchlists WHERE name = ?", (target,))
            if was_active:
                # Promote the next-oldest list to active
                next_list = conn.execute(
                    "SELECT name FROM watchlists "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if next_list:
                    conn.execute(
                        "UPDATE watchlists SET is_active = 1 WHERE name = ?",
                        (next_list["name"],))
        conn.close()
        msg = (f"Deleted '{target}'"
                + (f" (active list switched to next-oldest)" if was_active else ""))
        return True, msg
    except (sqlite3.Error, OSError) as e:
        return False, f"DB error: {e}"
