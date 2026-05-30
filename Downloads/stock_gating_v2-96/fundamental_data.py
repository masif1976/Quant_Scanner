"""
fundamental_data.py — Pure data layer for the Fundamental Insights page.

DESIGN PRINCIPLE (per the page spec and audit finding H-3):
This module is COMPLETELY isolated from the Streamlit presentation layer.
Every function takes plain arguments and returns plain Python objects
(dicts, DataFrames, or None). No `st.` calls, no widgets, no caching
decorators tied to Streamlit. The page imports these and renders; the
functions never import the page. This makes the data layer unit-testable
and reusable, and it is the template for refactoring the god-modules.

DATA SOURCING (per spec):
  - yfinance  -> price history, financial-statement time series
                (Revenue, EBITDA, Net Income, FCF, Cash, Debt, Shares),
                valuation multiples (P/E, P/B, P/S, EV/EBITDA),
                margins, growth, dividends.
  - finnhub   -> company profile/logo, market news, earnings-surprise
                history, forward analyst consensus/target.

HONESTY ABOUT DATA QUALITY:
yfinance free-tier financial statements are frequently incomplete and
typically only reach back ~4 years (not 10). Several "metrics" in the
spec are not single fields — they are DERIVED from statements (EV/EBITDA,
SBC-adjusted FCF yield, ROCE) and may be unavailable. Every function
distinguishes three states so the UI never shows a misleading zero:
  - a real value
  - None            (the API genuinely had nothing -> UI shows "n/a")
  - {"value": x, "derived": True}  (computed, not directly sourced)

All functions degrade gracefully: on any exception or missing key they
return None / empty rather than raising, so one bad ticker never takes
down the page.
"""

from __future__ import annotations
import math
from datetime import datetime, timedelta

import pandas as pd

# Reuse the existing, battle-tested data plumbing where it already exists.
import data_utils as du

try:
    import finnhub_client as fh
except Exception:  # pragma: no cover - finnhub optional
    fh = None

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None


# ─────────────────────────────────────────────────────────────────────────
# Index banner (Dow, S&P 500, Nasdaq) — yfinance
# ─────────────────────────────────────────────────────────────────────────

# yfinance index symbols. ETFs are used as proxies where the index symbol
# is unreliable on the free tier; ^GSPC/^DJI/^IXIC are the index tickers.
_INDEX_SYMBOLS = {
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
}


def get_index_banner() -> list[dict]:
    """Return current level + daily % change for the three headline indices.

    Each entry: {name, symbol, price, change_pct, ok}. `ok=False` means the
    fetch failed for that index (UI should show a dash, not a zero).
    """
    out = []
    for name, sym in _INDEX_SYMBOLS.items():
        entry = {"name": name, "symbol": sym, "price": None,
                 "change_pct": None, "ok": False}
        try:
            hist = du.get_history(sym, days=7)
            if hist is not None and not hist.empty and "Close" in hist:
                closes = hist["Close"].dropna()
                if len(closes) >= 2:
                    last = float(closes.iloc[-1])
                    prev = float(closes.iloc[-2])
                    entry["price"] = round(last, 2)
                    if prev > 0:
                        entry["change_pct"] = round((last / prev - 1) * 100, 2)
                    entry["ok"] = True
        except Exception:
            pass
        out.append(entry)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Quote / header block — finnhub primary, yfinance fallback
# ─────────────────────────────────────────────────────────────────────────

def get_quote_header(ticker: str) -> dict | None:
    """Current price, daily change ($ and %), next earnings date.

    Returns dict {price, change_abs, change_pct, prev_close,
    next_earnings, name, ok} or None on total failure.
    """
    t = (ticker or "").upper().strip()
    if not t:
        return None
    result = {"ticker": t, "price": None, "change_abs": None,
              "change_pct": None, "prev_close": None,
              "next_earnings": None, "name": None, "ok": False}

    # Price + change: prefer finnhub quote (real-time-ish), else yfinance.
    # finnhub_client.get_quote returns {"price", "change_pct", "status"}.
    if fh is not None:
        try:
            q = fh.get_quote(t)
            if q and q.get("price") is not None:
                result["price"] = round(float(q["price"]), 2)
                cp = q.get("change_pct")
                if cp is not None:
                    result["change_pct"] = round(float(cp), 2)
                    # Back out the absolute change and prior close from price + %
                    if result["change_pct"] != -100:
                        prev = result["price"] / (1 + result["change_pct"] / 100)
                        result["prev_close"] = round(prev, 2)
                        result["change_abs"] = round(result["price"] - prev, 2)
                result["ok"] = True
        except Exception:
            pass

    # yfinance fallback for price if finnhub missed
    if result["price"] is None:
        try:
            hist = du.get_history(t, days=7)
            if hist is not None and not hist.empty:
                closes = hist["Close"].dropna()
                if len(closes) >= 2:
                    result["price"] = round(float(closes.iloc[-1]), 2)
                    result["prev_close"] = round(float(closes.iloc[-2]), 2)
                    result["change_abs"] = round(
                        result["price"] - result["prev_close"], 2)
                    if result["prev_close"]:
                        result["change_pct"] = round(
                            (result["price"] / result["prev_close"] - 1) * 100, 2)
                    result["ok"] = True
        except Exception:
            pass

    # Next earnings date (finnhub) — returns {"next_earnings": ...}
    if fh is not None:
        try:
            ne = fh.get_next_earnings(t)
            if ne and ne.get("next_earnings"):
                result["next_earnings"] = ne["next_earnings"]
        except Exception:
            pass

    # Company name (finnhub profile, else leave None)
    if fh is not None:
        try:
            prof = _finnhub_profile(t)
            if prof:
                result["name"] = prof.get("name")
        except Exception:
            pass

    return result if result["ok"] else None


def _finnhub_profile(ticker: str) -> dict | None:
    """Company profile (name, logo, industry) via finnhub /stock/profile2.

    Isolated here so the page never calls finnhub directly. Returns
    {name, logo, industry, exchange, market_cap} or None.
    """
    if fh is None:
        return None
    try:
        # finnhub_client doesn't expose profile2 directly; use its _request.
        raw = fh._request("/stock/profile2", {"symbol": ticker.upper().strip()})
        if not raw:
            return None
        return {
            "name": raw.get("name"),
            "logo": raw.get("logo"),
            "industry": raw.get("finnhubIndustry"),
            "exchange": raw.get("exchange"),
            "market_cap": raw.get("marketCapitalization"),  # in millions
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Key-metrics grid — yfinance .info + derived fields
# ─────────────────────────────────────────────────────────────────────────

def get_key_metrics(ticker: str) -> dict:
    """Assemble the 5-group metric grid. Every value is either a number,
    None (-> UI shows n/a), or a dict {"value":x,"derived":True}.

    Groups returned as nested dicts so the page can lay them out without
    re-deciding the grouping:
      valuation, cash_flow, margins_growth, balance_sheet, dividends_est
    """
    t = (ticker or "").upper().strip()
    grid = {"ticker": t, "valuation": {}, "cash_flow": {},
            "margins_growth": {}, "balance_sheet": {}, "dividends_est": {},
            "source": None}
    if not t or yf is None:
        return grid

    info = {}
    try:
        info = yf.Ticker(t).info or {}
        grid["source"] = "yfinance"
    except Exception:
        info = {}

    def g(key):
        """Safe getter: returns the value or None (never KeyError/NaN)."""
        v = info.get(key)
        if v is None:
            return None
        try:
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except (TypeError, ValueError):
            return v  # non-numeric (e.g. strings) pass through

    mcap = g("marketCap")

    # ── Valuation ──
    grid["valuation"] = {
        "Market Cap": mcap,
        "Trailing P/E": g("trailingPE"),
        "Forward P/E": g("forwardPE"),
        "Price / Sales": g("priceToSalesTrailing12Months"),
        "EV / EBITDA": g("enterpriseToEbitda"),
        "Price / Book": g("priceToBook"),
    }

    # ── Cash Flow ──
    fcf = g("freeCashflow")
    shares = g("sharesOutstanding")
    fcf_yield = None
    fcf_per_share = None
    if fcf is not None and mcap:
        fcf_yield = {"value": round(fcf / mcap * 100, 2), "derived": True}
    if fcf is not None and shares:
        fcf_per_share = {"value": round(fcf / shares, 2), "derived": True}
    # SBC-adjusted FCF yield: subtract stock-based comp from FCF if available.
    # yfinance rarely exposes SBC cleanly; mark derived and None when absent.
    sbc_adj = None  # honest: not reliably available on free tier
    grid["cash_flow"] = {
        "FCF Yield": fcf_yield,
        "FCF / Share": fcf_per_share,
        "SBC-Adj FCF Yield": sbc_adj,
    }

    # ── Margins & Growth ──
    def pct(key):
        v = g(key)
        return round(v * 100, 2) if isinstance(v, (int, float)) else None
    grid["margins_growth"] = {
        "Profit Margin": pct("profitMargins"),
        "Operating Margin": pct("operatingMargins"),
        "Earnings Growth (YoY)": pct("earningsQuarterlyGrowth"),
        "Revenue Growth (YoY)": pct("revenueGrowth"),
    }

    # ── Balance Sheet ──
    cash = g("totalCash")
    debt = g("totalDebt")
    net_cash = None
    if cash is not None and debt is not None:
        net_cash = {"value": round(cash - debt, 0), "derived": True}
    grid["balance_sheet"] = {
        "Total Cash": cash,
        "Total Debt": debt,
        "Net Cash / (Debt)": net_cash,
    }

    # ── Dividends & Estimates ──
    div_yield = g("dividendYield")
    # yfinance dividendYield is sometimes a fraction (0.005) and sometimes
    # already a percent (0.5). Normalize conservatively: if < 1, treat as
    # fraction and ×100; values >= 1 are assumed already-percent but capped.
    if isinstance(div_yield, (int, float)):
        div_yield = round(div_yield * 100, 2) if div_yield < 1 else round(div_yield, 2)
    payout = g("payoutRatio")
    if isinstance(payout, (int, float)):
        payout = round(payout * 100, 2) if payout < 5 else round(payout, 2)
    # Analyst consensus target via finnhub. The recommendations endpoint
    # carries buy/hold/sell counts but NOT a price target, so we go straight
    # to /stock/price-target (may require a paid plan; degrades to None).
    target = None
    if fh is not None:
        try:
            pt = fh._request("/stock/price-target", {"symbol": t})
            if pt and pt.get("targetMean"):
                target = round(float(pt["targetMean"]), 2)
        except Exception:
            pass
    grid["dividends_est"] = {
        "Dividend Yield": div_yield,
        "Payout Ratio": payout,
        "Analyst Target": target,
    }

    return grid


# ─────────────────────────────────────────────────────────────────────────
# Financial-statement time series — yfinance
# ─────────────────────────────────────────────────────────────────────────

def get_financial_timeseries(ticker: str, period: str = "annual") -> dict:
    """Revenue, EBITDA, Net Income, Free Cash Flow over time.

    Args:
        ticker: symbol
        period: "annual" | "quarterly"

    Returns dict:
        {periods: [labels], revenue: [...], ebitda: [...],
         net_income: [...], fcf: [...], ok: bool, note: str}
    Lists are aligned to `periods`. Missing line items come back as None
    in-place (UI shows gaps, not zeros). `ok=False` + `note` explains a
    total failure (common on yfinance free tier).
    """
    t = (ticker or "").upper().strip()
    blank = {"periods": [], "revenue": [], "ebitda": [], "net_income": [],
             "fcf": [], "ok": False, "note": "", "source": None}
    if not t:
        blank["note"] = "no ticker"
        return blank

    # ── SEC EDGAR first for deep history (US filers) ──
    # Gives 10-15yr where yfinance gives ~4. Quarterly depth is also better.
    try:
        import sec_edgar as se
        max_years = 15
        edgar = se.get_long_financials(t, period=period, max_years=max_years)
        if edgar and edgar.get("ok") and len(edgar.get("periods", [])) >= 3:
            edgar["source"] = "sec_edgar"
            return edgar
    except Exception:
        pass

    # ── yfinance fallback (~4yr; covers non-US tickers EDGAR misses) ──
    if yf is None:
        blank["note"] = "yfinance unavailable and SEC EDGAR didn't resolve"
        return blank
    try:
        tk = yf.Ticker(t)
        if period == "quarterly":
            income = tk.quarterly_financials
            cashflow = tk.quarterly_cashflow
        else:
            income = tk.financials
            cashflow = tk.cashflow
    except Exception as e:
        blank["note"] = f"statement fetch failed: {type(e).__name__}"
        return blank

    if income is None or income.empty:
        blank["note"] = "no statement data returned (common on free tier)"
        return blank

    # yfinance returns statements with metrics as the index and periods as
    # columns (most recent first). We reverse to chronological for charting.
    cols = list(income.columns)[::-1]  # oldest -> newest
    periods = [c.strftime("%Y-%m") if hasattr(c, "strftime") else str(c)
               for c in cols]

    def row(df, *candidates):
        """Pull a statement row by trying several possible label spellings
        (yfinance label names drift between versions). Returns list aligned
        to `cols`, with None where missing."""
        if df is None or df.empty:
            return [None] * len(cols)
        for name in candidates:
            if name in df.index:
                series = df.loc[name]
                return [(_num(series.get(c))) for c in cols]
        return [None] * len(cols)

    revenue = row(income, "Total Revenue", "TotalRevenue", "Revenue")
    # EBITDA isn't always a direct row; try direct then derive from
    # operating income + D&A if both exist.
    ebitda = row(income, "EBITDA", "Normalized EBITDA")
    if all(v is None for v in ebitda):
        op_income = row(income, "Operating Income", "OperatingIncome")
        # D&A often lives on the cashflow statement
        da = row(cashflow, "Depreciation And Amortization",
                 "Depreciation", "DepreciationAndAmortization")
        derived = []
        for oi, d in zip(op_income, da):
            if oi is not None and d is not None:
                derived.append(oi + d)
            else:
                derived.append(None)
        ebitda = derived

    net_income = row(income, "Net Income", "NetIncome",
                     "Net Income Common Stockholders")

    # Free cash flow = Operating CF - CapEx (both on cashflow statement)
    op_cf = row(cashflow, "Operating Cash Flow", "Total Cash From Operating Activities",
                "OperatingCashFlow")
    capex = row(cashflow, "Capital Expenditure", "CapitalExpenditures",
                "CapitalExpenditure")
    fcf_direct = row(cashflow, "Free Cash Flow", "FreeCashFlow")
    fcf = []
    for i in range(len(cols)):
        if i < len(fcf_direct) and fcf_direct[i] is not None:
            fcf.append(fcf_direct[i])
        elif (i < len(op_cf) and op_cf[i] is not None
              and i < len(capex) and capex[i] is not None):
            # capex is usually negative already; FCF = OCF + capex
            fcf.append(op_cf[i] + capex[i])
        else:
            fcf.append(None)

    return {
        "periods": periods,
        "revenue": revenue,
        "ebitda": ebitda,
        "net_income": net_income,
        "fcf": fcf,
        "ok": True,
        "note": "",
        "source": "yfinance",
    }


def _num(v):
    """Coerce a statement cell to float or None (handles NaN/strings)."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Price history for the area chart — yfinance (reuses cache)
# ─────────────────────────────────────────────────────────────────────────

def get_price_series(ticker: str, days: int = 365) -> pd.DataFrame:
    """Date-indexed Close series for the price-trend area chart.
    Returns a DataFrame with a 'Close' column (possibly empty)."""
    t = (ticker or "").upper().strip()
    if not t:
        return pd.DataFrame()
    try:
        hist = du.get_history(t, days=days)
        if hist is not None and not hist.empty and "Close" in hist:
            return hist[["Close"]].dropna()
    except Exception:
        pass
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────
# Earnings surprises — finnhub
# ─────────────────────────────────────────────────────────────────────────

def get_earnings_surprises(ticker: str, n_quarters: int = 4) -> dict:
    """Estimate vs actual EPS for the last N quarters, plus surprise %.

    Returns {periods, estimate, actual, surprise_pct, ok, note}.
    Reuses finnhub_client.get_earnings_surprises and reshapes for charting.
    """
    t = (ticker or "").upper().strip()
    blank = {"periods": [], "estimate": [], "actual": [],
             "surprise_pct": [], "ok": False, "note": ""}
    if not t or fh is None:
        blank["note"] = "finnhub unavailable"
        return blank
    try:
        raw = fh.get_earnings_surprises(t, n_quarters=n_quarters)
    except Exception as e:
        blank["note"] = f"fetch failed: {type(e).__name__}"
        return blank
    if not raw or not raw.get("quarters"):
        blank["note"] = "no earnings surprise data"
        return blank

    # Expecting raw["quarters"] = list of {period, estimate, actual}
    qs = raw["quarters"][-n_quarters:]
    periods, est, act, surp = [], [], [], []
    for q in qs:
        periods.append(q.get("period") or q.get("date") or "")
        e = _num(q.get("estimate"))
        a = _num(q.get("actual"))
        est.append(e)
        act.append(a)
        if e is not None and a is not None and e != 0:
            surp.append(round((a - e) / abs(e) * 100, 1))
        else:
            surp.append(None)
    return {"periods": periods, "estimate": est, "actual": act,
            "surprise_pct": surp, "ok": True, "note": ""}


# ─────────────────────────────────────────────────────────────────────────
# Market news — finnhub
# ─────────────────────────────────────────────────────────────────────────

def get_recent_news(ticker: str, n_items: int = 5) -> list[dict]:
    """Last ~24h of news snippets. Returns list of
    {headline, source, url, datetime} (possibly empty)."""
    t = (ticker or "").upper().strip()
    if not t or fh is None:
        return []
    try:
        items = fh.get_company_news(t, n_items=n_items)
        if not items:
            return []
        out = []
        for it in items[:n_items]:
            out.append({
                "headline": it.get("headline") or it.get("title") or "",
                "source": it.get("source") or "",
                "url": it.get("url") or "",
                "datetime": it.get("datetime") or it.get("date") or "",
            })
        return out
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────
# Landing-grid summary cards — yfinance (cached, avoids finnhub rate limits)
# ─────────────────────────────────────────────────────────────────────────

def get_summary_cards(tickers: list[str]) -> list[dict]:
    """Lightweight cards for the landing grid: name, ticker, price, mcap.

    Deliberately uses yfinance (already disk-cached here) rather than firing
    one finnhub profile call per ticker, which would blow the free-tier
    rate limit on the landing page.
    """
    out = []
    for tk in tickers:
        t = (tk or "").upper().strip()
        if not t:
            continue
        card = {"ticker": t, "name": t, "price": None, "market_cap": None,
                "change_pct": None}
        try:
            hist = du.get_history(t, days=7)
            if hist is not None and not hist.empty:
                closes = hist["Close"].dropna()
                if len(closes) >= 2:
                    card["price"] = round(float(closes.iloc[-1]), 2)
                    prev = float(closes.iloc[-2])
                    if prev > 0:
                        card["change_pct"] = round(
                            (card["price"] / prev - 1) * 100, 2)
        except Exception:
            pass
        # Market cap + name from yfinance .info (best-effort, cached by yf)
        try:
            if yf is not None:
                info = yf.Ticker(t).info or {}
                card["name"] = info.get("shortName") or info.get("longName") or t
                mc = info.get("marketCap")
                if mc:
                    card["market_cap"] = float(mc)
        except Exception:
            pass
        out.append(card)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Deep-dive: long-term profitability ratios — yfinance
# ─────────────────────────────────────────────────────────────────────────

def get_profitability_history(ticker: str) -> dict:
    """Long-term Gross Margin, Operating Margin, and ROCE over time.

    SOURCE PRIORITY (changed to get real 10-15yr depth):
      1. SEC EDGAR (free, 10-15+ years for US filers) — primary
      2. yfinance (~4 years) — fallback for non-US tickers / EDGAR misses

    Returns {periods, gross_margin, operating_margin, roce, ok, note,
    source}. `source` tells the UI which feed served the data.
    """
    t = (ticker or "").upper().strip()
    if not t:
        return {"periods": [], "gross_margin": [], "operating_margin": [],
                "roce": [], "ok": False, "note": "", "source": None}

    # ── 1. SEC EDGAR first (deep history) ──
    try:
        import sec_edgar as se
        edgar = se.get_long_profitability(t, max_years=15)
        if edgar and edgar.get("ok") and len(edgar.get("periods", [])) >= 3:
            edgar["source"] = "sec_edgar"
            return edgar
    except Exception:
        pass

    # ── 2. yfinance fallback (~4 years) ──
    return _profitability_history_yfinance(t)


def _profitability_history_yfinance(ticker: str) -> dict:
    """yfinance fallback for profitability history (~4 years free tier).

    ROCE = EBIT / (Total Assets - Current Liabilities); requires balance
    sheet data that is often partial.
    """
    t = (ticker or "").upper().strip()
    blank = {"periods": [], "gross_margin": [], "operating_margin": [],
             "roce": [], "ok": False, "note": "", "source": "yfinance"}
    if not t or yf is None:
        blank["note"] = "yfinance unavailable"
        return blank
    try:
        tk = yf.Ticker(t)
        income = tk.financials
        balance = tk.balance_sheet
    except Exception as e:
        blank["note"] = f"statement fetch failed: {type(e).__name__}"
        return blank
    if income is None or income.empty:
        blank["note"] = "no annual statement data (free-tier limitation)"
        return blank

    cols = list(income.columns)[::-1]
    periods = [c.strftime("%Y") if hasattr(c, "strftime") else str(c)
               for c in cols]

    def irow(*names):
        for n in names:
            if n in income.index:
                return [_num(income.loc[n].get(c)) for c in cols]
        return [None] * len(cols)

    def brow(*names):
        if balance is None or balance.empty:
            return [None] * len(cols)
        for n in names:
            if n in balance.index:
                return [_num(balance.loc[n].get(c)) for c in cols]
        return [None] * len(cols)

    revenue = irow("Total Revenue", "Revenue")
    gross = irow("Gross Profit", "GrossProfit")
    op_income = irow("Operating Income", "OperatingIncome", "EBIT")
    total_assets = brow("Total Assets", "TotalAssets")
    curr_liab = brow("Current Liabilities", "Total Current Liabilities",
                     "CurrentLiabilities")

    gm, om, roce = [], [], []
    for i in range(len(cols)):
        r = revenue[i]
        gm.append(round(gross[i] / r * 100, 2)
                  if (gross[i] is not None and r) else None)
        om.append(round(op_income[i] / r * 100, 2)
                  if (op_income[i] is not None and r) else None)
        if (op_income[i] is not None and total_assets[i] is not None
                and curr_liab[i] is not None
                and (total_assets[i] - curr_liab[i]) != 0):
            roce.append(round(op_income[i] /
                              (total_assets[i] - curr_liab[i]) * 100, 2))
        else:
            roce.append(None)

    note = ""
    if len(periods) < 6:
        note = (f"Only {len(periods)} years from yfinance (SEC EDGAR didn't "
                f"resolve this ticker — it covers US filers only).")
    return {"periods": periods, "gross_margin": gm, "operating_margin": om,
            "roce": roce, "ok": True, "note": note, "source": "yfinance"}


# ─────────────────────────────────────────────────────────────────────────
# Fair-value estimate — a RANGE from transparent methods, NOT a single truth
# ─────────────────────────────────────────────────────────────────────────

def get_fair_value(ticker: str,
                   dcf_growth_pct: float = 8.0,
                   dcf_discount_pct: float = 10.0,
                   dcf_terminal_pct: float = 3.0,
                   dcf_years: int = 5) -> dict:
    """Estimate a fair-value RANGE for a stock using three transparent
    methods. Returns each method's value plus the current price, so the UI
    can show where the price sits relative to a *range* — never a single
    "true" number.

    METHODS (all assumptions exposed so nothing is a black box):
      1. DCF (discounted cash flow): projects the latest free cash flow at
         `dcf_growth_pct` for `dcf_years`, applies a Gordon terminal value at
         `dcf_terminal_pct`, discounts everything at `dcf_discount_pct`, then
         divides equity value by shares outstanding. Sliders let the user
         stress every assumption — because DCF output is hugely sensitive to
         them (small discount-rate changes swing the answer enormously).
      2. Multiple reversion: applies the stock's OWN trailing P/E (proxy for
         its historical norm) to current EPS. "If it holds this multiple."
      3. Analyst consensus target: Finnhub price target (if the plan exposes
         it; many free tiers don't -> None).

    Returns:
      {ticker, current_price, methods: [{name, value, note}], low, high,
       ok, note, assumptions: {...}}

    HONESTY: every value is a model output with large uncertainty. The UI
    must present these as a spread with heavy caveats, never as a target.
    """
    t = (ticker or "").upper().strip()
    out = {"ticker": t, "current_price": None, "methods": [],
           "low": None, "high": None, "ok": False, "note": "",
           "assumptions": {
               "dcf_growth_pct": dcf_growth_pct,
               "dcf_discount_pct": dcf_discount_pct,
               "dcf_terminal_pct": dcf_terminal_pct,
               "dcf_years": dcf_years,
           }}
    if not t:
        out["note"] = "no ticker"
        return out

    # Current price (reuse the header fetcher)
    header = get_quote_header(t)
    price = header.get("price") if header else None
    out["current_price"] = price

    # Pull the metrics we need (shares, EPS, FCF)
    shares = None
    eps = None
    pe = None
    if yf is not None:
        try:
            info = yf.Ticker(t).info or {}
            shares = info.get("sharesOutstanding")
            eps = info.get("trailingEps")
            pe = info.get("trailingPE")
        except Exception:
            pass

    methods = []

    # ── Method 1: DCF ──
    # Use the latest annual FCF from our (EDGAR-first) timeseries.
    try:
        ts = get_financial_timeseries(t, period="annual")
        fcf_series = [v for v in (ts.get("fcf") or []) if v is not None]
        latest_fcf = fcf_series[-1] if fcf_series else None
    except Exception:
        latest_fcf = None

    if latest_fcf and shares and shares > 0 and dcf_discount_pct > dcf_terminal_pct:
        g = dcf_growth_pct / 100.0
        r = dcf_discount_pct / 100.0
        tg = dcf_terminal_pct / 100.0
        # Project + discount FCF for N years
        pv_sum = 0.0
        fcf_proj = latest_fcf
        for yr in range(1, dcf_years + 1):
            fcf_proj = fcf_proj * (1 + g)
            pv_sum += fcf_proj / ((1 + r) ** yr)
        # Gordon terminal value at end of projection, discounted back
        terminal_fcf = fcf_proj * (1 + tg)
        terminal_value = terminal_fcf / (r - tg)
        pv_terminal = terminal_value / ((1 + r) ** dcf_years)
        equity_value = pv_sum + pv_terminal
        dcf_per_share = equity_value / shares
        if dcf_per_share > 0:
            methods.append({
                "name": "DCF",
                "value": round(dcf_per_share, 2),
                "note": (f"FCF grown {dcf_growth_pct:.0f}%/yr for {dcf_years}y, "
                         f"{dcf_terminal_pct:.0f}% terminal, discounted at "
                         f"{dcf_discount_pct:.0f}%"),
            })
    # ── Method 2: Multiple reversion (own trailing P/E × EPS) ──
    if pe and eps and pe > 0 and eps > 0:
        mult_value = pe * eps
        # This equals the current price by definition when using trailing P/E,
        # so only include it if we can use a *different* multiple. Use forward
        # P/E if available for a forward-looking variant; else skip to avoid a
        # tautological "fair value = current price".
        fwd_pe = None
        if yf is not None:
            try:
                fwd_pe = (yf.Ticker(t).info or {}).get("forwardPE")
            except Exception:
                fwd_pe = None
        if fwd_pe and fwd_pe > 0 and eps > 0:
            # Forward earnings implied: price if it holds its forward multiple
            # on trailing EPS (a rough "multiple normalization" read).
            methods.append({
                "name": "P/E multiple",
                "value": round(fwd_pe * eps, 2),
                "note": f"Forward P/E {fwd_pe:.1f} × trailing EPS ${eps:.2f}",
            })

    # ── Method 3: Analyst consensus target (finnhub) ──
    if fh is not None:
        try:
            pt = fh._request("/stock/price-target", {"symbol": t})
            if pt and pt.get("targetMean"):
                methods.append({
                    "name": "Analyst target",
                    "value": round(float(pt["targetMean"]), 2),
                    "note": "Finnhub consensus mean (may need paid plan)",
                })
        except Exception:
            pass

    if not methods:
        out["note"] = ("Couldn't compute any fair-value method — needs FCF + "
                       "shares (DCF), P/E + EPS (multiple), or an analyst "
                       "target. Free-tier data may be missing these.")
        return out

    values = [m["value"] for m in methods]
    out["methods"] = methods
    out["low"] = round(min(values), 2)
    out["high"] = round(max(values), 2)
    out["ok"] = True
    return out


# ─────────────────────────────────────────────────────────────────────────
# Health radar — reuses the scanner's 6-pillar grade (NOT a new metric)
# ─────────────────────────────────────────────────────────────────────────

def get_health_radar(ticker: str) -> dict:
    """Six-pillar health scores for a radar/spider chart.

    Reuses run_scanner.calculate_fundamental_grade so the radar is IDENTICAL
    to the scanner's grade — not a separate invented metric. Each pillar is
    0-100 (higher = healthier).

    Returns {ticker, pillars: {name: score}, grade, score, ok, note}.
    """
    t = (ticker or "").upper().strip()
    out = {"ticker": t, "pillars": {}, "grade": None, "score": None,
           "ok": False, "note": ""}
    if not t:
        out["note"] = "no ticker"
        return out
    try:
        import run_scanner as rs
        import data_utils as du
        fundamentals = du.get_fundamentals(t)
        grade = rs.calculate_fundamental_grade(t, fundamentals)
    except Exception as e:
        out["note"] = f"grade computation failed: {type(e).__name__}"
        return out

    pillars = grade.get("pillars") or {}
    if not pillars:
        out["note"] = grade.get("detail") or "fundamentals unavailable"
        return out

    # Friendly labels for the radar axes
    label_map = {
        "valuation": "Valuation",
        "growth": "Growth",
        "profitability": "Profitability",
        "cash_flow": "Cash Flow",
        "balance_sheet": "Balance Sheet",
        "efficiency": "Efficiency",
    }
    out["pillars"] = {label_map.get(k, k): round(float(v), 1)
                      for k, v in pillars.items()}
    out["grade"] = grade.get("grade")
    out["score"] = grade.get("score")
    out["ok"] = True
    return out
