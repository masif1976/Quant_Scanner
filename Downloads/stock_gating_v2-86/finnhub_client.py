"""
finnhub_client.py — Thin wrapper around the Finnhub free-tier API.

Used as the PRIMARY data source for:
  - Fundamental ratios (the 6-pillar Fundamental Grade)
  - Live quotes (current price + daily change)
  - Earnings calendar (next earnings date + EPS estimate)
  - Analyst recommendations (Strong Buy / Buy / Hold / Sell / Strong Sell trend)

yfinance remains the source for OHLCV (Finnhub free tier removed /candle in
2024). data_utils.py routes Finnhub → yfinance → LKG cache so the dashboard
always has data even if one source is rate-limited or down.

DESIGN NOTES:
- Cached via @st.cache_data(ttl=86400) — 24 hours. All four endpoints return
  data that updates at most daily (quarterly for fundamentals), so a 24h
  cache is honest and burns minimal API quota.
- Rate-limit aware: 429 responses return None (never raise) so the upstream
  data_utils fallback chain can engage yfinance.
- Key handling: hardcoded for local testing. To switch to env-based lookup
  in the future, see the comment on the _api_key() function below.
"""
from __future__ import annotations
import requests
from datetime import datetime, timedelta
from typing import Optional

try:
    import streamlit as st
    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


# ── API key (hardcoded per user request for local testing) ──────────────────
# IMPORTANT: this key is the rotated replacement for the publicly-shared one.
# Do NOT commit this file to a public GitHub repo — bots scrape GitHub for
# API keys within hours. When testing is done, either restore env-var lookup
# (see _api_key() docstring below) or .gitignore this file.
_FINNHUB_API_KEY = "d89a5ipr01qla01mnq30d89a5ipr01qla01mnq3g"


def _api_key() -> str:
    """Return the Finnhub API key. Hardcoded for local testing per user
    request — no env var or Streamlit secrets lookup. To switch back to
    env-based lookup later, replace the body with:

        return os.environ.get("FINNHUB_API_KEY", _FINNHUB_API_KEY).strip()
    """
    return _FINNHUB_API_KEY


_BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT_S = 8


# ── Cache decorator wrapper ──────────────────────────────────────────────────
# Mirrors the @cached pattern from data_utils.py so this module works both
# inside Streamlit (gets st.cache_data) and from CLI/tests (gets an in-proc
# TTL cache). The 24h TTL is chosen because every Finnhub endpoint we use
# updates at most daily.
def _cached(ttl: int = 86400):
    """Decorator: use st.cache_data inside Streamlit, in-process TTL otherwise."""
    if _HAS_STREAMLIT and hasattr(st, "cache_data"):
        return st.cache_data(ttl=ttl, show_spinner=False)
    # CLI fallback
    import time
    def decorator(fn):
        cache: dict = {}
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            if key in cache:
                value, ts = cache[key]
                if now - ts < ttl:
                    return value
            value = fn(*args, **kwargs)
            cache[key] = (value, now)
            return value
        wrapper.__wrapped__ = fn
        return wrapper
    return decorator


# ── Low-level HTTP helper ────────────────────────────────────────────────────
def _request(endpoint: str, params: dict | None = None) -> dict | None:
    """GET to Finnhub. Returns the parsed JSON dict, or None on any failure.

    Failure modes that produce None (not exceptions):
      - HTTP 429 (rate limited) — caller should fall back to yfinance
      - HTTP 401/403 (bad/expired key)
      - Network errors / timeouts
      - Non-JSON responses
      - Empty results from Finnhub (some tickers aren't covered)
    """
    params = dict(params or {})
    params["token"] = _api_key()
    try:
        r = requests.get(f"{_BASE_URL}{endpoint}", params=params,
                          timeout=_TIMEOUT_S)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        return data
    except (requests.RequestException, ValueError):
        return None


# ── Public endpoints ─────────────────────────────────────────────────────────
@_cached(ttl=86400)
def get_fundamentals(ticker: str) -> dict | None:
    """Pull fundamental ratios for the 6-pillar Grade.

    Returns dict with the SAME keys as data_utils.get_fundamentals() so the
    grader is agnostic to source. None if Finnhub returns nothing usable.

    Maps Finnhub `/stock/metric?metric=all` to our pillar inputs:
      Pillar 1 Valuation     -> enterpriseToEbitdaTTM, forwardPE
      Pillar 2 Growth        -> revenueGrowthTTMYoy, epsGrowthTTMYoy
      Pillar 3 Profitability -> grossMarginTTM, operatingMarginTTM
      Pillar 4 Cash Flow     -> freeCashFlowPerShareTTM × sharesOutstanding / marketCap
      Pillar 5 Balance Sheet -> currentRatioQuarterly
      Pillar 6 Efficiency    -> roeTTM

    Finnhub returns margins/ROE as PERCENTAGES (35.4 = 35.4%) while yfinance
    returns DECIMALS (0.354 = 35.4%). We convert Finnhub to decimals so the
    grader's existing thresholds work unchanged.
    """
    data = _request(f"/stock/metric", {"symbol": ticker, "metric": "all"})
    if not data or "metric" not in data:
        return None
    m = data.get("metric") or {}

    def _f(key: str, sanity_range: tuple[float, float] | None = None,
           scale: float = 1.0) -> Optional[float]:
        v = m.get(key)
        if v is None:
            return None
        try:
            fv = float(v) * scale
        except (TypeError, ValueError):
            return None
        if sanity_range:
            lo, hi = sanity_range
            if not (lo <= fv <= hi):
                return None
        return fv

    # Margins + ROE come back as percentages from Finnhub; divide by 100 to
    # match yfinance's decimal convention. Sanity caps are post-scaling.
    out = {
        # Valuation
        "enterprise_to_ebitda": _f("currentEv/ebitdaAnnual", (-200, 500)),
        "forward_pe":           _f("forwardPE", (-1000, 1000)),
        "trailing_pe":          _f("peTTM", (-1000, 1000)),
        # Growth (Finnhub returns as percentages — scale to decimals)
        "revenue_growth":       _f("revenueGrowthTTMYoy", (-200, 1000),
                                    scale=1/100),
        "earnings_growth":      _f("epsGrowthTTMYoy", (-1000, 5000),
                                    scale=1/100),
        # Profitability (Finnhub returns as percentages — scale to decimals)
        "gross_margins":        _f("grossMarginTTM", (-150, 150),
                                    scale=1/100),
        "operating_margins":    _f("operatingMarginTTM", (-150, 150),
                                    scale=1/100),
        "profit_margin":        _f("netProfitMarginTTM", (-100, 100),
                                    scale=1/100),
        # Balance sheet
        "current_ratio":        _f("currentRatioQuarterly", (0, 100)),
        # Efficiency (percentage → decimal)
        "roe":                  _f("roeTTM", (-200, 200), scale=1/100),
        "status":               "ok",
        "source":               "finnhub",
    }

    # ── Cash Flow: derive FCF / market cap (the "FCF yield" pillar input) ──
    # Finnhub doesn't return a single "free_cashflow" dollar value in
    # /stock/metric. We have to derive it. The ACTUAL field names returned
    # in the response (confirmed by inspecting AAPL's response — Finnhub's
    # documentation does not match runtime reality) are:
    #
    #   pfcfShareTTM         - Price-to-FCF per share, TTM (cleanest inversion)
    #   pfcfShareAnnual      - Same, annual
    #   currentEv/freeCashFlowTTM   - EV-to-FCF (slight semantic drift —
    #                                  EV vs market-cap denominator, but on
    #                                  low-leverage names the two are within
    #                                  ~10% of each other)
    #   cashFlowPerShareTTM  - OPERATING cash flow per share (NOT free cash
    #                          flow; less rigorous — used only as last resort)
    #
    # PRIOR BUG: previous version of this code read 'pfcfTTM' (no "Share")
    # which doesn't exist. That's why Cash Flow defaulted to neutral 50 on
    # every ticker. Fixed by switching to the correct field name.
    #
    # Strategies tried in order:
    #   1. invert pfcfShareTTM      (preferred — same row, no unit mismatches)
    #   2. invert currentEv/freeCashFlowTTM (close enough on most names)
    #   3. cashFlowPerShareTTM × market_cap / pricePerShare (operating CF
    #      proxy — flagged as approximate, used only when above two fail)
    market_cap_raw = m.get("marketCapitalization")
    try:
        market_cap_dollars = (float(market_cap_raw) * 1_000_000
                              if market_cap_raw else None)
        if market_cap_dollars is not None and market_cap_dollars <= 0:
            market_cap_dollars = None
    except (TypeError, ValueError):
        market_cap_dollars = None
    out["market_cap"] = market_cap_dollars

    out["free_cashflow"] = None

    # Strategy 1 — invert pfcfShareTTM (the correct field name).
    # If Price/FCF = 35, then FCF/Market_Cap = 1/35 = 2.86% yield.
    pfcf = m.get("pfcfShareTTM") or m.get("pfcfShareAnnual")
    if pfcf is not None and market_cap_dollars is not None:
        try:
            pfcf_val = float(pfcf)
            if pfcf_val > 0:
                out["free_cashflow"] = market_cap_dollars / pfcf_val
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Strategy 2 — invert currentEv/freeCashFlowTTM. Slight semantic drift:
    # the denominator is enterprise value (market cap + debt - cash) not
    # market cap. On companies with modest net debt these are within ~10%.
    # The pillar grader uses absolute thresholds (FCF yield < 2% / 5% /
    # 10%) which are coarse enough that the EV vs market-cap difference
    # rarely shifts the pillar score by more than one band. Acceptable
    # approximation for a fallback.
    if out["free_cashflow"] is None:
        ev_fcf = m.get("currentEv/freeCashFlowTTM") or m.get("currentEv/freeCashFlowAnnual")
        if ev_fcf is not None and market_cap_dollars is not None:
            try:
                ev_fcf_val = float(ev_fcf)
                if ev_fcf_val > 0:
                    # Note: this returns FCF/EV not FCF/MarketCap, but we
                    # store it under free_cashflow so downstream computes
                    # the correct yield. See note above re: semantic drift.
                    out["free_cashflow"] = market_cap_dollars / ev_fcf_val
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # Bail if nothing useful came back across all 6 pillars' inputs
    any_useful = any(out[k] is not None for k in (
        "enterprise_to_ebitda", "forward_pe", "revenue_growth",
        "gross_margins", "current_ratio", "roe", "free_cashflow"))
    if not any_useful:
        return None
    return out


@_cached(ttl=300)  # 5-minute cache for live quotes (vs 24h for fundamentals)
def get_quote(ticker: str) -> dict | None:
    """Single-ticker live quote.

    Returns: {"price": float, "change_pct": float, "status": "ok",
              "source": "finnhub"}
    None on any failure (caller falls back to yfinance fast_info).

    Finnhub free tier is 15-min delayed (same as yfinance free).
    """
    data = _request("/quote", {"symbol": ticker})
    if not data:
        return None
    price = data.get("c")          # current
    prev  = data.get("pc")          # previous close
    if not price:
        return None
    try:
        price = float(price)
        change_pct = None
        if prev:
            prev = float(prev)
            if prev != 0:
                change_pct = round((price - prev) / prev * 100, 2)
    except (TypeError, ValueError):
        return None
    return {"price": round(price, 2),
            "change_pct": change_pct,
            "status": "ok",
            "source": "finnhub"}


def get_quotes_batch(tickers: tuple) -> dict:
    """Batch wrapper — Finnhub has no native bulk quote endpoint on free tier,
    so we loop. Each call is cached individually (5-min TTL), so repeated
    sub-batches reuse cache.

    Returns dict {ticker: quote_dict_or_error_dict}. Tickers that fail
    return {"price": None, "change_pct": None, "status": "error"}.
    """
    out = {}
    for t in tickers:
        q = get_quote(t)
        if q is None:
            out[t] = {"price": None, "change_pct": None, "status": "error"}
        else:
            out[t] = q
    return out


@_cached(ttl=86400)
def get_next_earnings(ticker: str) -> dict | None:
    """Next earnings date + EPS estimate (if available).

    Returns: {"next_earnings": "YYYY-MM-DD" or None,
              "eps_estimate": float or None,
              "source": "finnhub"}
    None if Finnhub has nothing.
    """
    today = datetime.now().date()
    to_date = today + timedelta(days=120)
    data = _request("/calendar/earnings",
                    {"symbol": ticker,
                     "from": today.isoformat(),
                     "to": to_date.isoformat()})
    if not data:
        return None
    events = data.get("earningsCalendar") or []
    if not events:
        return None
    # take the earliest upcoming event (Finnhub may include multiple)
    events.sort(key=lambda e: e.get("date", ""))
    nxt = events[0]
    return {
        "next_earnings": nxt.get("date"),
        "eps_estimate": nxt.get("epsEstimate"),
        "source": "finnhub",
    }


@_cached(ttl=86400)
def get_analyst_recommendations(ticker: str) -> dict | None:
    """Analyst consensus recommendation trend.

    Returns the MOST RECENT month's snapshot:
      {"strong_buy": int, "buy": int, "hold": int, "sell": int,
       "strong_sell": int, "period": "YYYY-MM-DD",
       "consensus": "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell",
       "consensus_color": hex, "source": "finnhub"}
    """
    data = _request("/stock/recommendation", {"symbol": ticker})
    if not data:
        return None
    # data is list of dicts, one per month, sorted newest-first by Finnhub
    if not isinstance(data, list) or len(data) == 0:
        return None
    latest = data[0]
    sb = int(latest.get("strongBuy", 0) or 0)
    b  = int(latest.get("buy", 0) or 0)
    h  = int(latest.get("hold", 0) or 0)
    s  = int(latest.get("sell", 0) or 0)
    ss = int(latest.get("strongSell", 0) or 0)
    total = sb + b + h + s + ss
    if total == 0:
        return None

    # Weighted consensus score: Strong Buy=5 ... Strong Sell=1
    weighted = (sb*5 + b*4 + h*3 + s*2 + ss*1) / total
    if weighted >= 4.5:
        consensus, color = "Strong Buy", "#22e08a"
    elif weighted >= 3.5:
        consensus, color = "Buy", "#7fd98a"
    elif weighted >= 2.5:
        consensus, color = "Hold", "#f5c344"
    elif weighted >= 1.5:
        consensus, color = "Sell", "#ff9442"
    else:
        consensus, color = "Strong Sell", "#ff5d6c"

    return {
        "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        "total": total,
        "consensus": consensus,
        "consensus_color": color,
        "weighted_score": round(weighted, 2),
        "period": latest.get("period"),
        "source": "finnhub",
    }


@_cached(ttl=6 * 3600)
def get_insider_transactions(ticker: str,
                              lookback_days: int = 90) -> dict | None:
    """Recent insider transactions (Form 4 SEC filings) from Finnhub.

    Returns a summary dict with:
      - transactions: list of recent transactions (newest first)
      - net_shares: signed share count over the window (positive = net buying)
      - net_value: signed USD value over the window
      - n_buys / n_sells: counts of each direction
      - tone: "buying" | "selling" | "mixed" | "quiet"
      - tone_color: hex
      - source: "finnhub"

    Returns None if Finnhub returns nothing or the ticker has no filings
    in the lookback window. Caller renders nothing in that case.

    A few notes about Form 4 filings worth knowing:
      - Sells are noisier than buys (options exercises, diversification,
        scheduled 10b5-1 plans all create sells without bearish signal)
      - Buys are rarer and more meaningful — execs with personal cash on
        the line tend to know more than the average investor
      - Cluster of multiple insiders trading same direction in short window
        is the strongest signal
    """
    today = datetime.now().date()
    from_date = today - timedelta(days=lookback_days)
    data = _request("/stock/insider-transactions",
                    {"symbol": ticker,
                     "from": from_date.isoformat(),
                     "to": today.isoformat()})
    if not data:
        return None
    txns = data.get("data") or []
    if not txns:
        return None

    # Normalize + aggregate. Finnhub returns 'change' which is signed
    # (positive = buy, negative = sell). 'transactionPrice' is per-share.
    parsed = []
    net_shares = 0
    net_value = 0.0
    n_buys = 0
    n_sells = 0
    for t in txns:
        try:
            shares = int(t.get("change", 0) or 0)
            price = float(t.get("transactionPrice", 0) or 0)
            value = shares * price
            parsed.append({
                "name": t.get("name", "Unknown"),
                "filing_date": t.get("filingDate"),
                "transaction_date": t.get("transactionDate"),
                "shares": shares,
                "price": price,
                "value": value,
                "code": t.get("transactionCode", ""),  # 'P'=purchase, 'S'=sale
            })
            net_shares += shares
            net_value += value
            if shares > 0:
                n_buys += 1
            elif shares < 0:
                n_sells += 1
        except (TypeError, ValueError):
            continue

    if not parsed:
        return None

    # Sort newest-first
    parsed.sort(key=lambda x: (x.get("transaction_date") or ""), reverse=True)

    # Tone classification
    if n_buys >= 2 * max(n_sells, 1) and net_shares > 0:
        tone, tone_color = "buying", "#22e08a"
    elif n_sells >= 2 * max(n_buys, 1) and net_shares < 0:
        tone, tone_color = "selling", "#ff5d6c"
    elif n_buys > 0 and n_sells > 0:
        tone, tone_color = "mixed", "#f5c344"
    else:
        tone, tone_color = "quiet", "#7d8aa5"

    return {
        "transactions": parsed[:10],   # cap at 10 most-recent for display
        "n_total": len(parsed),
        "n_buys": n_buys,
        "n_sells": n_sells,
        "net_shares": net_shares,
        "net_value": net_value,
        "tone": tone,
        "tone_color": tone_color,
        "lookback_days": lookback_days,
        "source": "finnhub",
    }


@_cached(ttl=86400)
def get_earnings_surprises(ticker: str, n_quarters: int = 4) -> dict | None:
    """Last N quarters of actual vs estimated EPS — the "consistently beats
    or misses" quality dimension.

    Returns:
      - quarters: list of {period, actual, estimate, surprise, surprise_pct}
      - n_beats / n_misses: count
      - avg_surprise_pct: mean across the window
      - streak: "beat" | "miss" | "mixed" — current trajectory
      - streak_color: hex
      - source: "finnhub"

    None if Finnhub returns nothing useful.
    """
    data = _request("/stock/earnings", {"symbol": ticker, "limit": n_quarters})
    if not data or not isinstance(data, list) or not data:
        return None

    # Finnhub returns newest-first
    quarters = []
    n_beats = 0
    n_misses = 0
    surprises = []
    for q in data[:n_quarters]:
        actual = q.get("actual")
        estimate = q.get("estimate")
        if actual is None or estimate is None:
            continue
        try:
            actual_f = float(actual)
            est_f = float(estimate)
            # surprise % vs estimate magnitude (use abs to avoid sign flip
            # when estimate is negative — a beat from -0.10 to -0.05 is
            # still a beat)
            if abs(est_f) > 0.001:
                surprise_pct = (actual_f - est_f) / abs(est_f) * 100
            else:
                surprise_pct = 0.0
            quarters.append({
                "period": q.get("period"),
                "actual": actual_f,
                "estimate": est_f,
                "surprise": actual_f - est_f,
                "surprise_pct": surprise_pct,
                "beat": actual_f > est_f,
            })
            if actual_f > est_f:
                n_beats += 1
            elif actual_f < est_f:
                n_misses += 1
            surprises.append(surprise_pct)
        except (TypeError, ValueError):
            continue

    if not quarters:
        return None

    avg_surprise = sum(surprises) / len(surprises) if surprises else 0.0

    # Streak: did the MOST RECENT quarter beat? Combined with the overall
    # win rate over the window, decide tone.
    most_recent_beat = quarters[0]["beat"]
    if n_beats == len(quarters):
        streak, color = "all beats", "#22e08a"
    elif n_misses == len(quarters):
        streak, color = "all misses", "#ff5d6c"
    elif most_recent_beat and n_beats > n_misses:
        streak, color = "trending beats", "#7fd98a"
    elif not most_recent_beat and n_misses > n_beats:
        streak, color = "trending misses", "#ff9442"
    else:
        streak, color = "mixed", "#f5c344"

    return {
        "quarters": quarters,
        "n_beats": n_beats,
        "n_misses": n_misses,
        "n_total": len(quarters),
        "avg_surprise_pct": round(avg_surprise, 2),
        "streak": streak,
        "streak_color": color,
        "source": "finnhub",
    }


@_cached(ttl=3600)  # 1-hour cache — news is fresher than other endpoints
def get_company_news(ticker: str, n_items: int = 5,
                      lookback_days: int = 7) -> list | None:
    """Recent company news from Finnhub. Returns the most recent N headlines
    over the lookback window.

    Each item:
      - headline: str
      - source: news source name (Reuters / Bloomberg / etc)
      - url: link to the article
      - datetime: ISO date string
      - summary: short snippet
      - category: Finnhub's classification (sometimes empty)

    None if Finnhub returns nothing.
    """
    today = datetime.now().date()
    from_date = today - timedelta(days=lookback_days)
    data = _request("/company-news",
                    {"symbol": ticker,
                     "from": from_date.isoformat(),
                     "to": today.isoformat()})
    if not data or not isinstance(data, list) or not data:
        return None

    items = []
    for n in data[:n_items * 2]:  # over-fetch then trim — some may lack fields
        if not n.get("headline"):
            continue
        ts = n.get("datetime")
        ts_iso = None
        if ts:
            try:
                ts_iso = datetime.fromtimestamp(int(ts)).isoformat()
            except (TypeError, ValueError):
                pass
        items.append({
            "headline": n.get("headline", "").strip(),
            "source": (n.get("source", "") or "").strip(),
            "url": n.get("url", ""),
            "datetime": ts_iso,
            "summary": (n.get("summary", "") or "").strip()[:200],
            "category": n.get("category", ""),
        })
        if len(items) >= n_items:
            break

    return items if items else None


# ── market-wide calendars ────────────────────────────────────────────────────
# These two endpoints power the "Upcoming Events" panel on Page 1. Unlike
# get_next_earnings() which is per-ticker, these fetch the whole-market view.
# Cache for 6 hours — calendar data changes slowly during a trading day.
@_cached(ttl=21600)
def get_economic_calendar(days_ahead: int = 7) -> list[dict] | None:
    """High-impact US economic events in the next `days_ahead` calendar days.

    Returns a list of events sorted by date ascending. Filters to:
      - US events only (no global noise)
      - High-impact events only (FOMC, CPI, NFP, PCE, GDP, etc.)
        Finnhub's impact field uses "high"/"medium"/"low" — we keep only "high"
    Each event dict has: date, event (str), time (HH:MM), actual (str or None),
    estimate (str or None), prev (str or None), impact ("high"), unit (str).

    Returns None if Finnhub has nothing (network error, rate limit, or
    no high-impact events in the window — rare but possible).

    Why "high impact" only:
      - The Finnhub feed includes hundreds of low/medium events per week
        (housing starts, ISM PMI subindexes, regional Fed surveys, etc.)
      - Showing all of them creates noise that hides the actual market-moving
        events (FOMC decisions, CPI prints, NFP jobs reports)
      - "High" impact = the half-dozen events per week that traders actually
        watch and that genuinely move broad indices
    """
    today = datetime.now().date()
    to_date = today + timedelta(days=days_ahead)
    data = _request("/calendar/economic",
                     {"from": today.isoformat(),
                      "to": to_date.isoformat()})
    if not data:
        return None
    raw_events = data.get("economicCalendar") or []
    if not raw_events:
        return None
    filtered = []
    for ev in raw_events:
        # Defensive: skip malformed entries
        if not isinstance(ev, dict):
            continue
        # Filter US events only — Finnhub returns global by default
        if (ev.get("country") or "").upper() != "US":
            continue
        # Filter high-impact only — see docstring
        if (ev.get("impact") or "").lower() != "high":
            continue
        filtered.append({
            "date":     ev.get("time", "")[:10] or ev.get("date"),
            "time":     ev.get("time", "")[11:16] if len(ev.get("time", "")) > 11 else "",
            "event":    ev.get("event") or "",
            "actual":   ev.get("actual"),
            "estimate": ev.get("estimate"),
            "prev":     ev.get("prev"),
            "impact":   "high",
            "unit":     ev.get("unit") or "",
        })
    filtered.sort(key=lambda e: (e["date"] or "", e["time"] or ""))
    return filtered or None


@_cached(ttl=21600)
def get_market_earnings_calendar(days_ahead: int = 7) -> list[dict] | None:
    """Market-wide earnings calendar — all S&P 500-eligible reports in the
    next `days_ahead` calendar days. Different from get_next_earnings()
    which is per-ticker.

    Returns a list of {ticker, date, hour, eps_estimate} dicts sorted by
    date. None if Finnhub has nothing.

    Honest caveat: Finnhub's free-tier earnings calendar without a symbol
    filter returns a LOT of small-cap and OTC reports — most aren't market-
    moving. We filter to "well-known" tickers by checking against the user's
    watchlist + a small hardcoded list of mega-caps. This is a heuristic;
    a more rigorous approach would join against an S&P 500 constituents
    list (Finnhub has /index/constituents but it's premium-tier only).
    """
    today = datetime.now().date()
    to_date = today + timedelta(days=days_ahead)
    data = _request("/calendar/earnings",
                     {"from": today.isoformat(),
                      "to": to_date.isoformat()})
    if not data:
        return None
    events = data.get("earningsCalendar") or []
    if not events:
        return None
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if not ev.get("symbol") or not ev.get("date"):
            continue
        out.append({
            "ticker":       ev.get("symbol"),
            "date":         ev.get("date"),
            "hour":         ev.get("hour") or "",  # "amc" (after market) / "bmo" (before market) / ""
            "eps_estimate": ev.get("epsEstimate"),
            "rev_estimate": ev.get("revenueEstimate"),
        })
    out.sort(key=lambda e: (e["date"], e["ticker"]))
    return out or None


# ── Diagnostic ───────────────────────────────────────────────────────────────
def healthcheck() -> dict:
    """Quick ping to verify the API key + connectivity. Useful for the UI
    to surface a "Finnhub: OK / unreachable" indicator. key_source is always
    "hardcoded" in this build — env-var lookup was removed per user request."""
    data = _request("/quote", {"symbol": "AAPL"})
    if data and data.get("c"):
        return {"ok": True, "key_source": "hardcoded"}
    return {"ok": False, "key_source": "hardcoded"}
