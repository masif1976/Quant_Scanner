"""
data_utils.py — Shared data-fetching helpers.
Centralizes yfinance access, caching, and the S&P 500 sample universe.
"""

from __future__ import annotations
import time
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# Representative S&P 500 sample (60 large-caps across sectors) used for
# breadth and factor-crowding calculations. Swap for full constituents if desired.
SP500_SAMPLE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B", "UNH", "JPM",
    "V", "JNJ", "XOM", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "PEP",
    "KO", "AVGO", "COST", "TMO", "MCD", "WMT", "ACN", "BAC", "LLY", "CSCO",
    "DHR", "TXN", "NEE", "NFLX", "PM", "AMD", "UPS", "AMGN", "QCOM", "HON",
    "IBM", "SBUX", "GE", "CAT", "DE", "LOW", "INTU", "AMAT", "NOW", "CRM",
    "ADBE", "PFE", "VZ", "T", "ABT", "ORCL", "WFC", "DIS", "INTC", "GS",
]

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
                     "TSLA", "SMCI", "CRDO", "MXL"]

# ── caching ───────────────────────────────────────────────────────────────────
# Per the spec, yfinance downloads are wrapped with @st.cache_data(ttl=3600).
# Streamlit's cache only works inside its runtime, so we provide a decorator
# that uses st.cache_data when available and falls back to a simple in-process
# TTL cache otherwise (keeps the engine modules independently testable / CLI-safe).
_CACHE: dict = {}
_FALLBACK_TTL = 3600  # seconds


def _cache_get(key):
    item = _CACHE.get(key)
    if item is None:
        return None
    ts, value = item
    if time.time() - ts > _FALLBACK_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key, value):
    _CACHE[key] = (time.time(), value)


def clear_cache():
    """Clear both the fallback cache and Streamlit's cache_data store."""
    _CACHE.clear()
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass


def cached(ttl: int = 3600):
    """
    Decorator: use st.cache_data(ttl=...) when running inside Streamlit,
    otherwise fall back to the in-process TTL cache above.
    """
    def decorator(fn):
        try:
            import streamlit as st
            # st.cache_data handles its own keying & TTL
            return st.cache_data(ttl=ttl, show_spinner=False)(fn)
        except Exception:
            # CLI / test context — wrap with the simple TTL cache
            def wrapper(*args, **kwargs):
                key = f"{fn.__name__}::{args}::{sorted(kwargs.items())}"
                hit = _cache_get(key)
                if hit is not None:
                    return hit
                result = fn(*args, **kwargs)
                _cache_set(key, result)
                return result
            wrapper.__name__ = fn.__name__
            return wrapper
    return decorator


# ── last-known-good disk cache (rate-limit fallback) ──────────────────────────
# When yfinance returns empty / errors / hits a rate limit, we fall back to the
# most recent SUCCESSFUL fetch from this on-disk cache rather than crashing.
# NO mock data — only real prior results are surfaced, with a staleness banner.
import os, pickle, tempfile

_LKG_DIR = os.path.join(tempfile.gettempdir(), "stock_gating_v2_lkg")
os.makedirs(_LKG_DIR, exist_ok=True)

# tracks the most recent fallback event for UI banner display
LAST_FALLBACK_INFO: dict = {"used": False, "key": None, "fetched_at": None,
                             "reason": None}


def _lkg_path(key: str) -> str:
    # filesystem-safe key
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
    return os.path.join(_LKG_DIR, safe + ".pkl")


def _lkg_save(key: str, payload) -> None:
    try:
        with open(_lkg_path(key), "wb") as f:
            pickle.dump({"ts": time.time(), "payload": payload}, f)
    except Exception:
        pass  # disk-cache failures are never fatal


def _lkg_load(key: str):
    """Return (payload, fetched_at_iso) or (None, None) if no cache exists."""
    try:
        with open(_lkg_path(key), "rb") as f:
            entry = pickle.load(f)
        return entry["payload"], datetime.fromtimestamp(entry["ts"]).isoformat()
    except Exception:
        return None, None


def _is_empty_result(result) -> bool:
    """Treat empty frames / dicts of empty frames / empty series as failures."""
    if result is None:
        return True
    if isinstance(result, pd.DataFrame):
        return result.empty
    if isinstance(result, pd.Series):
        return len(result) == 0
    if isinstance(result, dict):
        if not result:
            return True
        # for {ticker: df}: if EVERY value is empty, treat as a failure
        if all(isinstance(v, pd.DataFrame) and v.empty for v in result.values()):
            return True
    return False


def _record_fallback(key: str, fetched_at: str, reason: str) -> None:
    LAST_FALLBACK_INFO.update({"used": True, "key": key,
                                "fetched_at": fetched_at, "reason": reason})


def reset_fallback_info() -> None:
    LAST_FALLBACK_INFO.update({"used": False, "key": None,
                                "fetched_at": None, "reason": None})


def _fetch_with_fallback(key: str, fetch_fn, empty_factory):
    """
    Run fetch_fn; on exception or empty result, fall back to the last-known-good
    disk cache. If no cache exists either, return empty_factory() so callers can
    detect it via the standard empty-frame path.

    NO MOCK DATA: the only fallback is real data we previously fetched.
    """
    try:
        result = fetch_fn()
        if _is_empty_result(result):
            raise RuntimeError("empty result")
        # success — refresh disk cache for future fallbacks
        _lkg_save(key, result)
        return result
    except Exception as e:
        cached, fetched_at = _lkg_load(key)
        if cached is not None:
            _record_fallback(key, fetched_at, str(e) or e.__class__.__name__)
            return cached
        # no fallback available — surface an empty result; UI degrades gracefully
        return empty_factory()


# ── download helpers ──────────────────────────────────────────────────────────
@cached(ttl=3600)
def get_history(ticker: str, days: int = 400) -> pd.DataFrame:
    """Single-ticker OHLCV DataFrame with a clean index. Empty DataFrame on failure."""
    end = datetime.today()
    start = end - timedelta(days=days)

    def _fetch():
        df = yf.download(ticker, start=start, end=end, progress=False,
                         auto_adjust=True, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna()

    return _fetch_with_fallback(
        key=f"hist::{ticker}::{days}",
        fetch_fn=_fetch,
        empty_factory=pd.DataFrame)


def get_close_series(ticker: str, days: int = 400) -> pd.Series:
    df = get_history(ticker, days)
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float)
    return df["Close"].dropna()


@cached(ttl=3600)
def _get_bulk_history_cached(tickers_tuple: tuple, days: int = 400) -> dict:
    """Cached core — receives a hashable tuple of tickers."""
    tickers = list(tickers_tuple)
    end = datetime.today()
    start = end - timedelta(days=days)

    def _fetch():
        out: dict = {}
        raw = yf.download(
            tickers, start=start, end=end, progress=False,
            auto_adjust=True, group_by="ticker", threads=False,
        )
        if len(tickers) == 1:
            t = tickers[0]
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(-1)
            out[t] = raw.dropna()
        else:
            for t in tickers:
                try:
                    if t in raw.columns.get_level_values(0):
                        out[t] = raw[t].dropna()
                    else:
                        out[t] = pd.DataFrame()
                except Exception:
                    out[t] = pd.DataFrame()
        return out

    def _empty():
        return {t: pd.DataFrame() for t in tickers}

    return _fetch_with_fallback(
        key=f"bulk::{'|'.join(sorted(tickers))}::{days}",
        fetch_fn=_fetch,
        empty_factory=_empty)


def get_bulk_history(tickers: tuple | list, days: int = 400) -> dict:
    """
    Bulk download; returns {ticker: DataFrame}. Resilient to partial failures.
    Normalizes the ticker list to a sorted tuple so the cache key is stable
    and hashable (st.cache_data requires hashable arguments).
    """
    norm = tuple(sorted(t.upper().strip() for t in tickers if t.strip()))
    if not norm:
        return {}
    return _get_bulk_history_cached(norm, days)


@cached(ttl=3600)
def get_earnings_date(ticker: str):
    """Return next earnings date as a pandas Timestamp, or None."""
    result = None
    try:
        tk = yf.Ticker(ticker)
        cal = None
        try:
            cal = tk.calendar
        except Exception:
            cal = None

        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                if isinstance(ed, (list, tuple)) and ed:
                    result = pd.Timestamp(ed[0])
                else:
                    result = pd.Timestamp(ed)
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            val = cal.loc["Earnings Date"].iloc[0]
            result = pd.Timestamp(val)

        if result is None:
            try:
                ed_df = tk.get_earnings_dates(limit=8)
                if ed_df is not None and not ed_df.empty:
                    future = ed_df.index[ed_df.index >= pd.Timestamp.today()]
                    if len(future) > 0:
                        result = pd.Timestamp(min(future))
            except Exception:
                pass
    except Exception:
        result = None
    return result


@cached(ttl=3600)
def get_options_metrics(ticker: str) -> dict:
    """
    Best-effort options metrics from yfinance option chains.
    Returns put/call OI ratio and a rough IV reading. Degrades gracefully.
    """
    metrics = {"pc_oi_ratio": None, "avg_iv": None, "status": "unavailable"}
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if expiries:
            use = expiries[:2]
            total_call_oi = total_put_oi = 0
            ivs = []
            for exp in use:
                try:
                    chain = tk.option_chain(exp)
                    calls, puts = chain.calls, chain.puts
                    total_call_oi += float(calls["openInterest"].fillna(0).sum())
                    total_put_oi += float(puts["openInterest"].fillna(0).sum())
                    ivs.extend(calls["impliedVolatility"].dropna().tolist())
                    ivs.extend(puts["impliedVolatility"].dropna().tolist())
                except Exception:
                    continue
            if total_call_oi > 0:
                metrics["pc_oi_ratio"] = round(total_put_oi / total_call_oi, 3)
            if ivs:
                metrics["avg_iv"] = round(float(np.median(ivs)), 4)
            metrics["status"] = "ok"
    except Exception:
        pass
    return metrics


# ── indicator helpers ─────────────────────────────────────────────────────────
def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


@cached(ttl=3600)
def get_live_quotes(tickers_tuple: tuple) -> dict:
    """
    Lightweight quote fetch for the Page 1 price banner — current price and
    daily % change ONLY. Uses yfinance fast_info (no historical OHLCV download)
    with a 1-day history fallback. Returns {ticker: {price, change_pct, status}}.
    """
    def _fetch():
        out: dict = {}
        for t in tickers_tuple:
            entry = {"price": None, "change_pct": None, "status": "error"}
            try:
                tk = yf.Ticker(t)
                price = prev = None

                # primary: fast_info (no OHLCV download)
                try:
                    fi = tk.fast_info
                    price = fi.get("lastPrice") or fi.get("last_price")
                    prev = (fi.get("previousClose")
                            or fi.get("previous_close")
                            or fi.get("regularMarketPreviousClose"))
                except Exception:
                    price = prev = None

                # fallback: a 2-row 1-day history (still tiny)
                if price is None or prev is None:
                    h = tk.history(period="2d", interval="1d")
                    if not h.empty and "Close" in h:
                        closes = h["Close"].dropna()
                        if len(closes) >= 1:
                            price = (float(closes.iloc[-1])
                                     if price is None else price)
                        if len(closes) >= 2 and prev is None:
                            prev = float(closes.iloc[-2])

                if price is not None:
                    entry["price"] = round(float(price), 2)
                    if prev:
                        entry["change_pct"] = round(
                            (float(price) - float(prev)) / float(prev) * 100, 2)
                    entry["status"] = "ok"
            except Exception:
                pass
            out[t] = entry
        # treat "every ticker errored" the same as an empty fetch so the
        # fallback layer surfaces the last good quotes instead
        if all(v.get("status") != "ok" for v in out.values()):
            raise RuntimeError("all live quotes failed")
        return out

    def _empty():
        return {t: {"price": None, "change_pct": None, "status": "error"}
                for t in tickers_tuple}

    return _fetch_with_fallback(
        key=f"quotes::{'|'.join(sorted(tickers_tuple))}",
        fetch_fn=_fetch,
        empty_factory=_empty)


@cached(ttl=3600)
def get_fundamentals(ticker: str) -> dict:
    """Trailing & forward P/E from yfinance. Graceful None on failure."""
    def _fetch():
        out = {"trailing_pe": None, "forward_pe": None, "status": "error"}
        info = yf.Ticker(ticker).get_info()
        tp = info.get("trailingPE")
        fp = info.get("forwardPE")
        out["trailing_pe"] = round(float(tp), 2) if tp else None
        out["forward_pe"] = round(float(fp), 2) if fp else None
        out["status"] = "ok"
        if out["trailing_pe"] is None and out["forward_pe"] is None:
            # nothing useful came back — let the fallback layer take over
            raise RuntimeError("no fundamentals returned")
        return out

    def _empty():
        return {"trailing_pe": None, "forward_pe": None, "status": "error"}

    return _fetch_with_fallback(
        key=f"fund::{ticker}",
        fetch_fn=_fetch,
        empty_factory=_empty)
