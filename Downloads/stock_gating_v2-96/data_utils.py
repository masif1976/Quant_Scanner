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

# DEFAULT_WATCHLIST seeds a fresh install's watchlist DB on first run.
# Composition is intentionally mixed:
#   - 8 mega-cap trend names: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AMD
#     (good TREND engine candidates — strong momentum names)
#   - 7 mid-cap names with wide ranges: SMCI, CRDO, MXL, MRNA, NOW, RBLX, TTWO
#     (mixed coverage for both engines)
#   - 8 high-volatility names added for MR engine validation: RIVN, COIN, PLTR,
#     SOFI, HOOD, DKNG, CHWY, OKTA. These swing harder between 52-week extremes,
#     producing more genuine MR setups (oversold LONGs at <30% of range,
#     overbought SHORTs at >70%). The mega-cap names rarely hit those extremes;
#     this group does.
#
# Users can add/remove via the sidebar editor — changes persist in
# ~/.stock_gating_v2/watchlist.db. After first run, this default is no longer
# consulted unless the user explicitly clicks "Reset to default."
DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
                     "TSLA", "SMCI", "CRDO", "MXL", "AMD", "MRNA", "NOW",
                     "RBLX", "TTWO",
                     # MR-volatility additions
                     "RIVN", "COIN", "PLTR", "SOFI", "HOOD",
                     "DKNG", "CHWY", "OKTA"]

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

# tracks the most recent fallback event for UI banner display.
# LAST_FALLBACK_INFO holds the latest fallback (back-compat with existing UI);
# FALLBACK_LOG holds a per-key map so each metric can flag itself stale.
LAST_FALLBACK_INFO: dict = {"used": False, "key": None, "fetched_at": None,
                             "reason": None}
FALLBACK_LOG: dict[str, dict] = {}


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
    entry = {"used": True, "key": key, "fetched_at": fetched_at,
             "reason": reason}
    LAST_FALLBACK_INFO.update(entry)
    FALLBACK_LOG[key] = entry


def fallback_for_ticker(ticker: str) -> dict | None:
    """Per-signal staleness query — returns {key, fetched_at, reason} if any
    fetch for this ticker fell back to disk cache, else None.

    Matches any FALLBACK_LOG key that mentions the ticker (e.g. `hist::SPY::620`,
    `quotes::AAPL|MSFT`, `bulk::AAPL|MSFT|TSLA::400`). Used by the per-signal
    staleness clock on Page 1 so each metric can flag itself stale.
    """
    t = ticker.upper()
    for k, entry in FALLBACK_LOG.items():
        # delimiters ensure we match whole tickers (AAPL, not "AA" in "ZAAPL")
        if (f"::{t}::" in k or f"::{t}" == k[-len(t)-2:]
                or f"|{t}|" in k or f"|{t}::" in k
                or f"::{t}|" in k):
            return entry
    return None


def reset_fallback_info() -> None:
    LAST_FALLBACK_INFO.update({"used": False, "key": None,
                                "fetched_at": None, "reason": None})
    FALLBACK_LOG.clear()


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
    """Single-ticker OHLCV DataFrame with a clean index. Empty DataFrame on failure.

    Resilience layering:
      1. Try yfinance live fetch.
      2. If success → also write rows to the persistent SQLite OHLCV cache
         (corruption-filtered) so future failures can be served from disk.
      3. If yfinance fails → try the SQLite cache (survives reboots).
      4. If cache empty → fall through to the in-memory LKG pickle (for
         backward compatibility with the existing fallback flow).
      5. If everything fails → return empty DataFrame.

    The SQLite cache is what makes the dashboard usable through extended
    yfinance outages (the "Invalid Crumb" 401 episodes we've been fighting).
    It only stores days where the data passed a corruption sanity check —
    so the MXL-style phantom $52 jumps don't get cached and replayed.
    """
    import ohlcv_cache as _cache
    end = datetime.today()
    start = end - timedelta(days=days)

    def _fetch():
        df = yf.download(ticker, start=start, end=end, progress=False,
                         auto_adjust=True, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna()

    # Try yfinance — and if it works, persist a clean copy to disk
    try:
        live = _fetch()
        if not _is_empty_result(live):
            # Persist to SQLite for future yfinance outages. Failures here
            # are non-fatal — the user gets their live result either way.
            try:
                _cache.write(ticker, live)
            except Exception:
                pass
            # Refresh the in-memory LKG too (matches prior behavior for code
            # that reads LAST_FALLBACK_INFO / per-ticker fallback state)
            _lkg_save(f"hist::{ticker}::{days}", live)
            return live
    except Exception as e:
        live_error = str(e) or e.__class__.__name__
    else:
        live_error = "empty result"

    # Live failed — try SQLite cache first (persistent, corruption-filtered)
    try:
        cached_df = _cache.read(ticker, days=days)
    except Exception:
        cached_df = None

    if cached_df is not None and not cached_df.empty:
        # Mark this fetch as fallback for the UI staleness banner
        latest = cached_df.index.max().isoformat()
        _record_fallback(f"hist::{ticker}::{days}", latest,
                          f"yfinance failed ({live_error}); served from SQLite cache")
        return cached_df

    # SQLite cache empty too — fall back to the original pickle LKG path
    cached_pickle, fetched_at = _lkg_load(f"hist::{ticker}::{days}")
    if cached_pickle is not None:
        _record_fallback(f"hist::{ticker}::{days}", fetched_at,
                          f"yfinance failed ({live_error}); served from pickle LKG")
        return cached_pickle

    # Nothing worked
    return pd.DataFrame()


def get_close_series(ticker: str, days: int = 400) -> pd.Series:
    df = get_history(ticker, days)
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float)
    return df["Close"].dropna()


@cached(ttl=3600)
def _get_bulk_history_cached(tickers_tuple: tuple, days: int = 400) -> dict:
    """Cached core — receives a hashable tuple of tickers.

    Same resilience layering as get_history: try yfinance, persist each
    ticker's clean rows to SQLite on success, fall back to SQLite then
    pickle LKG on failure. The dict result lets us partially fall back —
    if yfinance succeeded for 18 of 23 tickers, we can serve the missing
    5 from cache without throwing away the 18 good ones.
    """
    import ohlcv_cache as _cache
    tickers = list(tickers_tuple)
    end = datetime.today()
    start = end - timedelta(days=days)

    def _fetch_live() -> dict:
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

    # Try yfinance — partial success is fine, we patch holes from cache
    try:
        live = _fetch_live()
    except Exception as e:
        live = {}
        live_error = str(e) or e.__class__.__name__
    else:
        live_error = None

    out: dict = {}
    used_cache_for: list[str] = []
    for t in tickers:
        live_df = live.get(t)
        if live_df is not None and not live_df.empty:
            # Live worked for this ticker — persist to SQLite
            try:
                _cache.write(t, live_df)
            except Exception:
                pass
            out[t] = live_df
        else:
            # Live failed (or returned empty) for this ticker — try cache
            cached_df = None
            try:
                cached_df = _cache.read(t, days=days)
            except Exception:
                cached_df = None
            if cached_df is not None and not cached_df.empty:
                out[t] = cached_df
                used_cache_for.append(t)
            else:
                out[t] = pd.DataFrame()

    # If we needed cache for any ticker, mark the bulk fetch as a fallback
    # event so the staleness banner can surface it on Page 1
    if used_cache_for:
        reason = (f"yfinance partial/full failure"
                  + (f" ({live_error})" if live_error else "")
                  + f"; served {len(used_cache_for)} from SQLite cache")
        _record_fallback(
            f"bulk::{'|'.join(sorted(tickers))}::{days}",
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            reason)

    # Persist whole-bulk to pickle LKG too (matches prior behavior for any
    # code that reads LAST_FALLBACK_INFO state)
    if any(not df.empty for df in out.values()):
        _lkg_save(f"bulk::{'|'.join(sorted(tickers))}::{days}", out)

    return out


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


# ── Liquidity filter ─────────────────────────────────────────────────────────
# Tier A audit fix: prevent the scanner from emitting signals on tickers that
# can't actually be traded at scale. Average daily dollar volume below the
# threshold means slippage will eat the edge. $20M/day is a conservative floor
# — well-funded retail can trade $20-50k positions comfortably, institutional
# desks need much higher (>$200M/day) but this is a retail tool.

MIN_DOLLAR_VOLUME_USD = 20_000_000
DOLLAR_VOLUME_WINDOW = 20  # trading days


def average_dollar_volume(ticker: str,
                          window: int = DOLLAR_VOLUME_WINDOW) -> float | None:
    """Compute average daily dollar volume (Close × Volume) over the last
    `window` trading days. Returns None if data unavailable.

    Cached via the underlying get_history call's TTL, so this adds zero
    extra network cost when called alongside the scanner's existing fetch.
    """
    try:
        hist = get_history(ticker, days=max(60, window + 10))
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    if "Close" not in hist or "Volume" not in hist:
        return None
    recent = hist.tail(window)
    dollar_vol = (recent["Close"] * recent["Volume"]).dropna()
    if dollar_vol.empty:
        return None
    return float(dollar_vol.mean())


def is_liquid(ticker: str,
              threshold_usd: float = MIN_DOLLAR_VOLUME_USD) -> tuple[bool, float | None]:
    """Return (is_liquid, average_dollar_volume_usd).

    True = ticker passes the liquidity floor; False = below threshold (skip
    or warn). None volume = data unavailable (treated as liquid by default —
    don't reject on bad data, just warn upstream).
    """
    adv = average_dollar_volume(ticker)
    if adv is None:
        return True, None  # don't penalize for missing data
    return adv >= threshold_usd, adv


@cached(ttl=3600)
def _get_earnings_date_yfinance(ticker: str):
    """yfinance-sourced earnings — fallback for the dual-source layer.
    Returns next earnings date as a pandas Timestamp, or None.
    """
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


def get_earnings_date(ticker: str):
    """Next earnings date — DUAL-SOURCE router.

    Finnhub /calendar/earnings → yfinance .calendar → None.
    Returns a pandas Timestamp or None.
    """
    # Try Finnhub first
    try:
        import finnhub_client as fh
        fh_result = fh.get_next_earnings(ticker)
        if fh_result and fh_result.get("next_earnings"):
            try:
                return pd.Timestamp(fh_result["next_earnings"])
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    # Fall back to yfinance
    return _get_earnings_date_yfinance(ticker)


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


@cached(ttl=900)  # 15-min cache for options chains — they change fast intraday
def get_weekly_option_chain(ticker: str,
                             target_dte_days: int = 7,
                             dte_tolerance: int = 3) -> dict:
    """Fetch the option chain for the expiry closest to `target_dte_days`
    days out (default ~7 = next Friday). Used by the Wheel strategy page.

    Args:
        ticker: stock symbol
        target_dte_days: target days to expiration (7 for weekly Wheel)
        dte_tolerance: how far off target_dte_days is acceptable. ±3 days
                       covers the case where the market is closed for a
                       holiday and the nearest weekly is 4-10 DTE instead
                       of exactly 7.

    Returns dict with:
        ticker, status ("ok" | "no_expiries" | "no_match" | "error"),
        expiry (YYYY-MM-DD or None),
        dte (int days to expiry, calendar),
        calls (DataFrame or None) — columns: strike, bid, ask, lastPrice,
            volume, openInterest, impliedVolatility, delta (added)
        puts (DataFrame or None) — same columns
        error (str or None) — populated when status != "ok"

    The `delta` column is added by approximating from yfinance's inGSTheMoney
    flag and IV/strike relationship — yfinance does NOT provide Greeks
    directly on free tier. For Wheel strategy purposes, we approximate
    delta from the Black-Scholes formula using the available bid/ask
    midpoint and IV.

    Caveats:
      - yfinance options data IS subject to the same scrape failures
        (Invalid Crumb) as OHLCV. Status "error" means we got nothing.
      - Bid-ask spreads can be wide on small caps; the page should warn
        when ask/bid > 1.5
      - Free yfinance options data is 15-min delayed
    """
    result = {
        "ticker": ticker,
        "status": "error",
        "expiry": None,
        "dte": None,
        "calls": None,
        "puts": None,
        "error": None,
    }
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            result["status"] = "no_expiries"
            return result

        # Pick the expiry closest to target_dte_days
        today = datetime.today().date()
        best_exp = None
        best_dte = None
        best_diff = None
        for exp_str in expiries:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte <= 0:
                continue  # already expired
            diff = abs(dte - target_dte_days)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_exp = exp_str
                best_dte = dte

        if best_exp is None or best_diff is None or best_diff > dte_tolerance:
            result["status"] = "no_match"
            result["error"] = (
                f"No expiry within ±{dte_tolerance}d of {target_dte_days}d target. "
                f"Available: {', '.join(expiries[:5])}")
            return result

        try:
            chain = tk.option_chain(best_exp)
        except Exception as e:
            result["error"] = f"option_chain fetch failed: {e}"
            return result

        # Defensive copy + add an approximate delta column. yfinance free-tier
        # does NOT provide Greeks, so we approximate using a simple Black-
        # Scholes-style estimator. For risk-screening (which is what the
        # Wheel page does), approximate delta is sufficient — execution
        # decisions would require real broker Greeks.
        try:
            cur_price = float(tk.history(period="1d")["Close"].iloc[-1])
        except Exception:
            cur_price = None

        calls = chain.calls.copy() if hasattr(chain, "calls") else None
        puts = chain.puts.copy() if hasattr(chain, "puts") else None

        if calls is not None and not calls.empty and cur_price is not None:
            calls["delta_approx"] = _approx_delta(
                spot=cur_price, strikes=calls["strike"],
                iv=calls["impliedVolatility"], dte=best_dte, kind="call")
        if puts is not None and not puts.empty and cur_price is not None:
            puts["delta_approx"] = _approx_delta(
                spot=cur_price, strikes=puts["strike"],
                iv=puts["impliedVolatility"], dte=best_dte, kind="put")

        result["status"] = "ok"
        result["expiry"] = best_exp
        result["dte"] = best_dte
        result["calls"] = calls
        result["puts"] = puts
        result["current_price"] = cur_price
        return result
    except Exception as e:
        result["error"] = str(e) or e.__class__.__name__
        return result


def _approx_delta(spot: float, strikes, iv, dte: int, kind: str) -> pd.Series:
    """Approximate Black-Scholes delta. Free-tier yfinance doesn't ship
    Greeks, so we compute a rough estimate from the available inputs.

    For Wheel strategy purposes (screening, not execution), this is good
    enough. Real Greeks would require either:
      - A broker API (TD Ameritrade, IBKR) that returns them server-side
      - A paid options data feed (Polygon, Tradier)

    Simplification: delta ≈ N(d1) for calls, N(d1) - 1 for puts where
      d1 = (ln(S/K) + (σ²/2)·T) / (σ·√T)

    Uses Abramowitz-Stegun 26.2.17 for the normal CDF (accurate to ~1e-7)
    to avoid a scipy dependency.

    Returns a pd.Series aligned to the strikes input.
    """
    import math
    T = max(dte, 1) / 365.0
    results = []
    for k, sigma in zip(strikes, iv):
        try:
            k = float(k); sigma = float(sigma)
            if k <= 0 or sigma <= 0 or spot <= 0:
                results.append(None)
                continue
            d1 = (math.log(spot / k) + (sigma ** 2) / 2 * T) / (sigma * math.sqrt(T))
            cdf_d1 = _ncdf(d1)
            if kind == "call":
                results.append(float(cdf_d1))
            else:
                results.append(float(cdf_d1 - 1.0))
        except (ValueError, ZeroDivisionError):
            results.append(None)
    return pd.Series(results, index=strikes.index)


def _ncdf(x: float) -> float:
    """Standard-normal CDF via Abramowitz-Stegun 26.2.17. Accurate to ~1e-7
    over the full range — sufficient for delta approximation."""
    import math
    # Constants from A&S 26.2.17
    b1 =  0.319381530
    b2 = -0.356563782
    b3 =  1.781477937
    b4 = -1.821255978
    b5 =  1.330274429
    p  =  0.2316419
    if x >= 0:
        t = 1.0 / (1.0 + p * x)
        phi = math.exp(-x * x / 2.0) / math.sqrt(2 * math.pi)
        return 1.0 - phi * (b1*t + b2*t**2 + b3*t**3 + b4*t**4 + b5*t**5)
    else:
        return 1.0 - _ncdf(-x)



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
def _get_live_quotes_yfinance(tickers_tuple: tuple) -> dict:
    """yfinance-sourced live quote fetch — used as fallback for the dual-
    source quote layer. Same dict shape as the Finnhub source.
    """
    def _fetch():
        out: dict = {}
        for t in tickers_tuple:
            entry = {"price": None, "change_pct": None, "status": "error",
                     "source": "yfinance"}
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
        if all(v.get("status") != "ok" for v in out.values()):
            raise RuntimeError("all live quotes failed")
        return out

    def _empty():
        return {t: {"price": None, "change_pct": None, "status": "error",
                    "source": "yfinance"}
                for t in tickers_tuple}

    return _fetch_with_fallback(
        key=f"quotes::{'|'.join(sorted(tickers_tuple))}",
        fetch_fn=_fetch,
        empty_factory=_empty)


def get_live_quotes(tickers_tuple: tuple) -> dict:
    """Lightweight quote fetch for the Page 1 price banner — DUAL-SOURCE.

    Source priority:
      1. Finnhub /quote (per ticker — cleaner JSON, 5-min cache each)
      2. yfinance fast_info (fallback for tickers Finnhub returned None for)
      3. LKG cache (engaged by yfinance fallback layer)

    Returns {ticker: {price, change_pct, status, source}}. The 'source' field
    lets the UI surface which path served each row.
    """
    # ── 1. Try Finnhub for every ticker ──
    out = {}
    finnhub_failed_tickers = []
    try:
        import finnhub_client as fh
        for t in tickers_tuple:
            q = fh.get_quote(t)
            if q is not None:
                out[t] = q
            else:
                finnhub_failed_tickers.append(t)
    except Exception:
        # entire finnhub layer failed — drop everything to the yfinance path
        finnhub_failed_tickers = list(tickers_tuple)
        out = {}

    if not finnhub_failed_tickers:
        return out  # finnhub covered everything

    # ── 2. Fall back to yfinance for Finnhub-failed tickers only ──
    try:
        yf_quotes = _get_live_quotes_yfinance(tuple(finnhub_failed_tickers))
        out.update(yf_quotes)
    except Exception:
        # last resort — placeholders for the failed tickers
        for t in finnhub_failed_tickers:
            out[t] = {"price": None, "change_pct": None,
                      "status": "error", "source": "none"}
    return out


@cached(ttl=3600)
def _get_fundamentals_yfinance(ticker: str) -> dict:
    """yfinance-sourced fundamentals — used as fallback when Finnhub is
    unavailable or doesn't cover the ticker. This is the legacy
    implementation kept intact for the dual-source data layer.

    Returns the same dict shape as the Finnhub source (see Finnhub docstring
    in finnhub_client.get_fundamentals) so the grader is source-agnostic.
    Adds "source": "yfinance" to the returned dict.
    """
    def _fetch():
        out = {
            "enterprise_to_ebitda": None, "forward_pe": None,
            "revenue_growth": None, "earnings_growth": None,
            "gross_margins": None, "operating_margins": None,
            "free_cashflow": None, "market_cap": None,
            "current_ratio": None, "roe": None,
            "trailing_pe": None, "profit_margin": None,
            "status": "error",
        }
        info = yf.Ticker(ticker).get_info()

        def _f(key, sanity_range=None):
            """Pull a field, coerce to float, apply optional sanity bounds.
            Out-of-range or non-numeric values become None."""
            v = info.get(key)
            if v is None:
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            if sanity_range is not None:
                lo, hi = sanity_range
                if not (lo <= fv <= hi):
                    return None
            return fv

        # Valuation (decimal multiples — sanity-cap to filter yfinance glitches)
        out["enterprise_to_ebitda"] = _f("enterpriseToEbitda", (-200, 500))
        out["forward_pe"]           = _f("forwardPE", (-1000, 1000))
        out["trailing_pe"]          = _f("trailingPE", (-1000, 1000))
        # round display-only fields to 2 decimals
        for k in ("enterprise_to_ebitda", "forward_pe", "trailing_pe"):
            if out[k] is not None:
                out[k] = round(out[k], 2)

        # Growth (decimal — yfinance reports as 0.15 for 15% YoY)
        out["revenue_growth"]  = _f("revenueGrowth", (-2.0, 10.0))
        out["earnings_growth"] = _f("earningsGrowth", (-10.0, 50.0))

        # Profitability (decimals 0..1 normally; cap at +/- 1.5 to allow
        # rare oddities without admitting nonsense values)
        out["gross_margins"]     = _f("grossMargins", (-1.5, 1.5))
        out["operating_margins"] = _f("operatingMargins", (-1.5, 1.5))
        out["profit_margin"]     = _f("profitMargins", (-1.0, 1.0))

        # Cash flow + market cap (raw dollar values, can be negative for FCF)
        out["free_cashflow"] = _f("freeCashflow")
        out["market_cap"]    = _f("marketCap")
        # Don't allow zero/neg market cap (would break FCF yield computation)
        if out["market_cap"] is not None and out["market_cap"] <= 0:
            out["market_cap"] = None

        # Balance sheet — current assets / current liabilities. Banks/REITs
        # legitimately don't have this; null is expected for financial sector.
        out["current_ratio"] = _f("currentRatio", (0, 100))

        # Efficiency — ROE, cap at +/- 2.0 to filter neg-equity oddities
        out["roe"] = _f("returnOnEquity", (-2.0, 2.0))
        if out["roe"] is not None:
            out["roe"] = round(out["roe"], 4)

        out["status"] = "ok"
        # only raise if literally nothing came back
        any_data = any(
            out[k] is not None for k in (
                "enterprise_to_ebitda", "forward_pe", "trailing_pe",
                "revenue_growth", "earnings_growth",
                "gross_margins", "operating_margins", "profit_margin",
                "free_cashflow", "current_ratio", "roe"))
        if not any_data:
            raise RuntimeError("no fundamentals returned")
        return out

    def _empty():
        return {
            "enterprise_to_ebitda": None, "forward_pe": None,
            "revenue_growth": None, "earnings_growth": None,
            "gross_margins": None, "operating_margins": None,
            "free_cashflow": None, "market_cap": None,
            "current_ratio": None, "roe": None,
            "trailing_pe": None, "profit_margin": None,
            "status": "error",
            "source": "yfinance",
        }

    result = _fetch_with_fallback(
        key=f"fund::{ticker}",
        fetch_fn=_fetch,
        empty_factory=_empty)
    # tag every successful yfinance result so the UI can show the data path
    if result.get("status") == "ok" and "source" not in result:
        result["source"] = "yfinance"
    return result


def get_fundamentals(ticker: str) -> dict:
    """6-pillar fundamental snapshot — DUAL-SOURCE router.

    Source priority:
      1. Finnhub /stock/metric?metric=all     (primary — cleaner data, 99%
                                                coverage on US large-caps)
      2. yfinance .info                       (fallback — when Finnhub returns
                                                nothing, hits rate limit, or
                                                doesn't cover the ticker)
      3. LKG (last-known-good) disk cache     (engaged by both via
                                                _fetch_with_fallback)

    The returned dict shape is identical regardless of source — only the
    'source' field changes ("finnhub" / "yfinance" / "lkg") so the UI can
    show which data path served each ticker.

    Pillar fields (semantics unchanged from prior yfinance-only version):
      Pillar 1 Valuation     -> enterprise_to_ebitda, forward_pe
      Pillar 2 Growth        -> revenue_growth, earnings_growth (decimals)
      Pillar 3 Profitability -> gross_margins, operating_margins (decimals)
      Pillar 4 Cash Flow     -> free_cashflow / market_cap (FCF yield)
      Pillar 5 Balance Sheet -> current_ratio
      Pillar 6 Efficiency    -> roe (decimal)
      Display-only: trailing_pe, profit_margin
    """
    # ── 1. Try Finnhub first ──
    try:
        import finnhub_client as fh
        fh_result = fh.get_fundamentals(ticker)
    except Exception:
        fh_result = None

    if fh_result is not None:
        # Ensure every pillar key exists (downstream code expects them all)
        for k in ("enterprise_to_ebitda", "forward_pe", "trailing_pe",
                  "revenue_growth", "earnings_growth",
                  "gross_margins", "operating_margins", "profit_margin",
                  "free_cashflow", "market_cap",
                  "current_ratio", "roe"):
            fh_result.setdefault(k, None)
        # Finnhub doesn't carry all of trailing_pe/profit_margin perfectly —
        # opportunistically backfill from yfinance ONLY if Finnhub left them
        # null and we have a cheap cached yfinance read. This is purely
        # opportunistic — failures here don't break the result.
        missing_legacy = (fh_result.get("trailing_pe") is None
                          or fh_result.get("profit_margin") is None)
        if missing_legacy:
            try:
                yf_result = _get_fundamentals_yfinance(ticker)
                if yf_result.get("trailing_pe") is not None:
                    fh_result.setdefault("trailing_pe",
                                          yf_result["trailing_pe"])
                    if fh_result.get("trailing_pe") is None:
                        fh_result["trailing_pe"] = yf_result["trailing_pe"]
                if yf_result.get("profit_margin") is not None and \
                   fh_result.get("profit_margin") is None:
                    fh_result["profit_margin"] = yf_result["profit_margin"]
            except Exception:
                pass
        fh_result["source"] = "finnhub"
        return fh_result

    # ── 2. Fall back to yfinance ──
    return _get_fundamentals_yfinance(ticker)



# ── market-wide calendars (Finnhub-only — no yfinance equivalent) ───────────
def get_economic_calendar(days_ahead: int = 7) -> list[dict] | None:
    """Thin wrapper around finnhub_client.get_economic_calendar(). Returns
    high-impact US economic events in the next `days_ahead` days, or None
    if Finnhub returns nothing. No yfinance fallback — economic calendar
    is a Finnhub-only feature in our data layer."""
    try:
        import finnhub_client as fh
        return fh.get_economic_calendar(days_ahead=days_ahead)
    except Exception:
        return None


def get_market_earnings_calendar(days_ahead: int = 7,
                                   ticker_filter: list[str] | None = None) -> list[dict] | None:
    """Market-wide earnings calendar.

    Args:
        days_ahead: lookahead window in calendar days.
        ticker_filter: if provided, only return events for these tickers
                       (typically the user's watchlist). If None, all events.

    Returns sorted list of {ticker, date, hour, eps_estimate, rev_estimate}
    or None.
    """
    try:
        import finnhub_client as fh
        events = fh.get_market_earnings_calendar(days_ahead=days_ahead)
    except Exception:
        return None
    if not events:
        return None
    if ticker_filter:
        wl = {t.upper() for t in ticker_filter}
        events = [e for e in events if (e.get("ticker") or "").upper() in wl]
    return events or None
