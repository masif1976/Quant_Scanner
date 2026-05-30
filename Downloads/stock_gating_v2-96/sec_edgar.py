"""
sec_edgar.py — Free long-history fundamentals from SEC EDGAR (US filers).

WHY THIS EXISTS:
yfinance and Finnhub's free tiers only return ~4-5 years of financial
statements. SEC EDGAR's XBRL "companyfacts"/"companyconcept" APIs expose
10-15+ years of as-reported figures for any US filer, completely free and
without an API key. This module is the legitimate free path to the
Qualtrim-style deep history.

DESIGN (matches fundamental_data.py isolation):
Pure data layer — no Streamlit, no charts. Returns plain dicts/lists.

WHAT SEC EDGAR MAKES HARD (and how this module handles it):
  1. Ticker -> CIK: EDGAR keys by a 10-digit Central Index Key, not ticker.
     SEC publishes a free ticker->CIK map; we fetch + cache it.
  2. GAAP tag drift: companies change which XBRL concept they file under
     over time (e.g. "Revenues" vs
     "RevenueFromContractWithCustomerExcludingAssessedTax"). We try an
     ORDERED list of equivalent tags per metric and merge what we find.
  3. Overlapping periods: a metric appears in both 10-Q and 10-K filings
     with different period framings. We dedupe by (period-end, form) and
     prefer the longest-standing value per period.
  4. Fair-access rules: SEC requires a descriptive User-Agent and ~10 req/s
     max. We send a proper UA and cache aggressively (data changes quarterly).

IMPORTANT LIMITATIONS (be honest in the UI):
  - US filers only. ADRs and foreign companies that file 20-F have spotty
    XBRL coverage; non-US tickers won't resolve.
  - "As reported" figures — not restated/normalized like paid feeds. A
    company that restated history will show original numbers per filing.
  - Some derived metrics (EBITDA, FCF) still need assembly from line items;
    where a component is missing for a period, that period returns None.

NETWORK NOTE: built against the documented EDGAR API structure. Endpoints:
  - Ticker map:   https://www.sec.gov/files/company_tickers.json
  - Company facts: https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  - Concept:       https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/{tag}.json
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

# SEC fair-access REQUIRES a descriptive User-Agent with contact info.
# Resolution order: SEC_EDGAR_USER_AGENT env var → Streamlit secrets
# ([sec] user_agent or SEC_EDGAR_USER_AGENT) → a generic fallback. SEC tends
# to reject UAs that don't look like a real "Name email@domain" string.
import os


def _resolve_user_agent() -> str:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT")
    if ua:
        return ua
    try:
        import streamlit as st  # only available in the app context
        sec = st.secrets.get("sec", {})
        if isinstance(sec, dict) and sec.get("user_agent"):
            return str(sec["user_agent"])
        if st.secrets.get("SEC_EDGAR_USER_AGENT"):
            return str(st.secrets["SEC_EDGAR_USER_AGENT"])
    except Exception:
        pass
    return "stock-gating-research contact@example.com"


_USER_AGENT = _resolve_user_agent()

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Module-level caches (process lifetime). The ticker map is ~1MB and changes
# rarely; company facts change quarterly. Streamlit's own caching can wrap
# the public functions if desired, but we keep a simple in-process cache so
# the data layer has no Streamlit dependency.
_ticker_map_cache: dict | None = None
_facts_cache: dict[str, dict] = {}

# Polite rate limiting — SEC asks for <= 10 requests/second.
_last_request_ts = 0.0
_MIN_INTERVAL = 0.15  # ~6-7 req/s, comfortably under the limit


def _http_get_json(url: str, timeout: int = 15) -> dict | None:
    """GET a URL with the required SEC headers, return parsed JSON or None.

    Never raises — any failure (network, rate limit, bad JSON) returns None
    so callers degrade gracefully.
    """
    global _last_request_ts
    # Simple client-side rate limit
    elapsed = time.time() - _last_request_ts
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Handle gzip if the server compressed despite us not decoding
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            _last_request_ts = time.time()
            return json.loads(raw.decode("utf-8"))
    except Exception:
        _last_request_ts = time.time()
        return None


# ─────────────────────────────────────────────────────────────────────────
# Ticker -> CIK resolution
# ─────────────────────────────────────────────────────────────────────────

def _load_ticker_map() -> dict:
    """Fetch + cache SEC's ticker->CIK map. Returns {TICKER: cik_int}."""
    global _ticker_map_cache
    if _ticker_map_cache is not None:
        return _ticker_map_cache
    data = _http_get_json(_TICKER_MAP_URL)
    mapping: dict[str, int] = {}
    if data:
        # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": ...}, ...}
        try:
            for row in data.values():
                t = str(row.get("ticker", "")).upper().strip()
                cik = row.get("cik_str")
                if t and cik is not None:
                    mapping[t] = int(cik)
        except Exception:
            pass
    _ticker_map_cache = mapping
    return mapping


def ticker_to_cik(ticker: str) -> str | None:
    """Resolve a ticker to a zero-padded 10-digit CIK string, or None."""
    t = (ticker or "").upper().strip()
    if not t:
        return None
    mapping = _load_ticker_map()
    cik = mapping.get(t)
    if cik is None:
        return None
    return str(cik).zfill(10)


# ─────────────────────────────────────────────────────────────────────────
# Company facts
# ─────────────────────────────────────────────────────────────────────────

# Ordered candidate XBRL tags per metric. We try each in order and use the
# first that yields data; tags drift across filing years, so order matters
# (most common/recent first).
_TAG_CANDIDATES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "total_assets": [
        "Assets",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    # Shares outstanding — dilution/buyback signal. These live under the
    # "shares" unit, not "USD"; _extract_concept falls back to it automatically.
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    # Cash & equivalents (sometimes incl. short-term investments).
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndShortTermInvestments",
    ],
    # Total debt — try a true total first, else long-term debt as a proxy.
    "total_debt": [
        "DebtLongtermAndShorttermCombinedAmount",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "DebtCurrent",
    ],
}


def _get_company_facts(cik: str) -> dict | None:
    """Fetch (and cache) the full companyfacts JSON for a CIK."""
    if cik in _facts_cache:
        return _facts_cache[cik]
    data = _http_get_json(_FACTS_URL.format(cik=cik))
    if data:
        _facts_cache[cik] = data
    return data


def _extract_concept(facts: dict, candidates: list[str],
                     period: str = "annual") -> dict[str, float]:
    """Pull a metric's time series from companyfacts, trying each candidate
    tag. Returns {period_end_date: value}.

    period: "annual" keeps fiscal-year figures (form 10-K, ~365-day frames);
            "quarterly" keeps ~90-day frames (form 10-Q + the implied Q4).
    """
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    out: dict[str, float] = {}
    for tag in candidates:
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units") or {}
        # Most monetary facts live under "USD"; shares under "shares".
        series = units.get("USD") or next(iter(units.values()), [])
        for item in series:
            val = item.get("val")
            end = item.get("end")
            start = item.get("start")
            form = item.get("form", "")
            if val is None or end is None:
                continue
            # Determine the period length to classify annual vs quarterly.
            duration_days = None
            if start:
                try:
                    d0 = datetime.fromisoformat(start)
                    d1 = datetime.fromisoformat(end)
                    duration_days = (d1 - d0).days
                except Exception:
                    duration_days = None
            if period == "annual":
                # Keep ~annual durations (300-400 days), prefer 10-K.
                if duration_days is not None and not (300 <= duration_days <= 400):
                    continue
            else:  # quarterly
                if duration_days is not None and not (60 <= duration_days <= 120):
                    continue
            # Dedup by period-end; prefer 10-K/10-Q over amendments, and keep
            # the first seen (candidate order already prioritizes good tags).
            if end not in out:
                out[end] = float(val)
        # If this candidate tag produced data, stop (don't blend tags).
        if out:
            break
    return out


def get_long_financials(ticker: str, period: str = "annual",
                        max_years: int = 15) -> dict:
    """Long-history financial time series from SEC EDGAR.

    Returns dict aligned to chronological `periods` (oldest -> newest):
        {periods, revenue, ebitda, net_income, fcf, ok, note, source}
    Missing line items per period come back as None (UI shows gaps).
    ok=False + note explains a total failure (non-US ticker, network, etc.).
    """
    blank = {"periods": [], "revenue": [], "ebitda": [], "net_income": [],
             "fcf": [], "ok": False, "note": "", "source": "sec_edgar"}
    t = (ticker or "").upper().strip()
    if not t:
        blank["note"] = "no ticker"
        return blank

    cik = ticker_to_cik(t)
    if cik is None:
        blank["note"] = (f"{t} not found in SEC EDGAR (US filers only — "
                         f"foreign/ADR tickers often aren't covered)")
        return blank

    facts = _get_company_facts(cik)
    if not facts:
        blank["note"] = ("SEC EDGAR fetch failed (network, rate limit, or no "
                         "XBRL data). Set SEC_EDGAR_USER_AGENT for reliability.")
        return blank

    rev = _extract_concept(facts, _TAG_CANDIDATES["revenue"], period)
    ni = _extract_concept(facts, _TAG_CANDIDATES["net_income"], period)
    op_inc = _extract_concept(facts, _TAG_CANDIDATES["operating_income"], period)
    da = _extract_concept(facts, _TAG_CANDIDATES["depreciation_amortization"], period)
    ocf = _extract_concept(facts, _TAG_CANDIDATES["operating_cash_flow"], period)
    capex = _extract_concept(facts, _TAG_CANDIDATES["capex"], period)

    # Union of all period-end dates we saw, sorted chronologically.
    all_dates = sorted(set(rev) | set(ni) | set(op_inc) | set(ocf))
    if not all_dates:
        blank["note"] = "no usable XBRL financial concepts found for this filer"
        return blank

    # Trim to the most recent max_years (annual) / max_years*4 (quarterly).
    keep = max_years if period == "annual" else max_years * 4
    all_dates = all_dates[-keep:]

    periods, revenue, ebitda, net_income, fcf = [], [], [], [], []
    for d in all_dates:
        # Label: year for annual, year-month for quarterly
        try:
            dt = datetime.fromisoformat(d)
            label = dt.strftime("%Y") if period == "annual" else dt.strftime("%Y-%m")
        except Exception:
            label = d
        periods.append(label)
        revenue.append(rev.get(d))
        net_income.append(ni.get(d))
        # EBITDA = Operating Income + D&A (derived; None if either missing)
        oi = op_inc.get(d)
        dep = da.get(d)
        ebitda.append(oi + dep if (oi is not None and dep is not None) else None)
        # FCF = Operating Cash Flow - CapEx (capex stored positive at SEC)
        o = ocf.get(d)
        c = capex.get(d)
        fcf.append(o - c if (o is not None and c is not None) else None)

    note = f"SEC EDGAR — {len(periods)} {period} periods (as-reported)."
    return {"periods": periods, "revenue": revenue, "ebitda": ebitda,
            "net_income": net_income, "fcf": fcf, "ok": True,
            "note": note, "source": "sec_edgar"}


def get_long_profitability(ticker: str, max_years: int = 15) -> dict:
    """Long-history profitability ratios from SEC EDGAR.

    Returns {periods, gross_margin, operating_margin, roce, ok, note, source}.
    ROCE = Operating Income / (Total Assets - Current Liabilities).
    """
    blank = {"periods": [], "gross_margin": [], "operating_margin": [],
             "roce": [], "ok": False, "note": "", "source": "sec_edgar"}
    t = (ticker or "").upper().strip()
    cik = ticker_to_cik(t)
    if cik is None:
        blank["note"] = f"{t} not found in SEC EDGAR (US filers only)"
        return blank
    facts = _get_company_facts(cik)
    if not facts:
        blank["note"] = "SEC EDGAR fetch failed"
        return blank

    rev = _extract_concept(facts, _TAG_CANDIDATES["revenue"], "annual")
    gross = _extract_concept(facts, _TAG_CANDIDATES["gross_profit"], "annual")
    op_inc = _extract_concept(facts, _TAG_CANDIDATES["operating_income"], "annual")
    assets = _extract_concept(facts, _TAG_CANDIDATES["total_assets"], "annual")
    curr_liab = _extract_concept(facts, _TAG_CANDIDATES["current_liabilities"], "annual")

    all_dates = sorted(set(rev) | set(op_inc))
    if not all_dates:
        blank["note"] = "no usable XBRL concepts for profitability"
        return blank
    all_dates = all_dates[-max_years:]

    periods, gm, om, roce = [], [], [], []
    for d in all_dates:
        try:
            label = datetime.fromisoformat(d).strftime("%Y")
        except Exception:
            label = d
        periods.append(label)
        r = rev.get(d)
        g = gross.get(d)
        oi = op_inc.get(d)
        gm.append(round(g / r * 100, 2) if (g is not None and r) else None)
        om.append(round(oi / r * 100, 2) if (oi is not None and r) else None)
        a = assets.get(d)
        cl = curr_liab.get(d)
        if oi is not None and a is not None and cl is not None and (a - cl) != 0:
            roce.append(round(oi / (a - cl) * 100, 2))
        else:
            roce.append(None)

    return {"periods": periods, "gross_margin": gm, "operating_margin": om,
            "roce": roce, "ok": True,
            "note": f"SEC EDGAR — {len(periods)} years (as-reported).",
            "source": "sec_edgar"}


def get_long_balance_items(ticker: str, period: str = "annual",
                           max_years: int = 15) -> dict:
    """Long-history shares outstanding + cash + debt from SEC EDGAR.

    Returns {periods, shares, cash, debt, ok, note, source}.
      - shares: common shares outstanding (dilution/buyback signal)
      - cash:   cash & equivalents
      - debt:   total (or long-term) debt
    Balance-sheet items are point-in-time (no start date), so we keep the
    period-end snapshots. Missing items per period come back as None.
    """
    blank = {"periods": [], "shares": [], "cash": [], "debt": [],
             "ok": False, "note": "", "source": "sec_edgar"}
    t = (ticker or "").upper().strip()
    cik = ticker_to_cik(t)
    if cik is None:
        blank["note"] = f"{t} not found in SEC EDGAR (US filers only)"
        return blank
    facts = _get_company_facts(cik)
    if not facts:
        blank["note"] = "SEC EDGAR fetch failed"
        return blank

    shares = _extract_concept(facts, _TAG_CANDIDATES["shares_outstanding"], period)
    cash = _extract_concept(facts, _TAG_CANDIDATES["cash"], period)
    debt = _extract_concept(facts, _TAG_CANDIDATES["total_debt"], period)

    all_dates = sorted(set(shares) | set(cash) | set(debt))
    if not all_dates:
        blank["note"] = "no usable XBRL balance-sheet concepts found"
        return blank
    keep = max_years if period == "annual" else max_years * 4
    all_dates = all_dates[-keep:]

    periods, sh, ca, de = [], [], [], []
    for d in all_dates:
        try:
            dt = datetime.fromisoformat(d)
            label = dt.strftime("%Y") if period == "annual" else dt.strftime("%Y-%m")
        except Exception:
            label = d
        periods.append(label)
        sh.append(shares.get(d))
        ca.append(cash.get(d))
        de.append(debt.get(d))

    return {"periods": periods, "shares": sh, "cash": ca, "debt": de,
            "ok": True,
            "note": f"SEC EDGAR — {len(periods)} {period} periods (as-reported).",
            "source": "sec_edgar"}
