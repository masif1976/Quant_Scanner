"""
market_cap_groups.py — Group watchlist tickers by market-cap tier.

Used on Page 1 to section the Live Watchlist Prices grid by company size.
Mega-caps and small-caps behave very differently — different volatility
profiles, different liquidity, different news sensitivity. Mixing them
in a single alphabetical grid makes it hard to reason about each tier.

Thresholds (industry-standard conventions, with one practical adjustment):
  - Mega-cap:  > $200B
  - Large-cap: $10B - $200B
  - Mid-cap:   $2B - $10B
  - Small-cap: $300M - $2B
  - Micro-cap: < $300M

Honest caveat: market cap is a single point-in-time number that can shift
around the boundary as prices move. SMCI in particular oscillates between
mid and large-cap depending on the week. We accept that and don't try to
be cleverer than the threshold — what matters for the user is "behavior
class," not perfect category accuracy.

Tickers Finnhub doesn't return market_cap for go into an "Unclassified"
bucket rather than being silently guessed.
"""
from __future__ import annotations
from typing import Iterable


# ── thresholds (in raw dollars, NOT millions) ───────────────────────────────
# Tuned for the 2026 tech-concentrated market where $200B is no longer
# "rare giant" territory. Bumping mega to $400B captures the actual
# truly-massive tier (AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, BRK)
# without sweeping in moderately-large names like AMD, COIN, JPM, JNJ.
# The other thresholds shift up correspondingly so the relative
# distribution of names across tiers stays balanced.
MEGA_CAP_THRESHOLD   = 400_000_000_000  # $400B  (was $200B)
LARGE_CAP_THRESHOLD  = 50_000_000_000   # $50B   (was $10B)
MID_CAP_THRESHOLD    = 10_000_000_000   # $10B   (was $2B)
SMALL_CAP_THRESHOLD  = 2_000_000_000    # $2B    (was $300M)
# micro = below SMALL_CAP_THRESHOLD ($2B); no lower bound


# Display order — section headers will appear in this order on the page.
# Mega first because users typically focus on positions in larger names.
CAP_TIERS = [
    ("mega",         "Mega-Cap",       "≥ $400B"),
    ("large",        "Large-Cap",      "$50B – $400B"),
    ("mid",          "Mid-Cap",        "$10B – $50B"),
    ("small",        "Small-Cap",      "$2B – $10B"),
    ("micro",        "Micro-Cap",      "< $2B"),
    ("unclassified", "Unclassified",   "no market cap data"),
]


def classify_cap(market_cap: float | None) -> str:
    """Return the tier key ('mega' | 'large' | 'mid' | 'small' | 'micro' |
    'unclassified') for a given market cap in dollars.

    None or non-positive market_cap returns 'unclassified' — better to
    surface "we don't know" than guess wrong.
    """
    if market_cap is None:
        return "unclassified"
    try:
        mc = float(market_cap)
    except (TypeError, ValueError):
        return "unclassified"
    if mc <= 0:
        return "unclassified"
    if mc >= MEGA_CAP_THRESHOLD:
        return "mega"
    if mc >= LARGE_CAP_THRESHOLD:
        return "large"
    if mc >= MID_CAP_THRESHOLD:
        return "mid"
    if mc >= SMALL_CAP_THRESHOLD:
        return "small"
    return "micro"


def format_market_cap(market_cap: float | None) -> str:
    """Compact human-readable market cap. Examples:
      4_500_000_000_000  →  '$4.5T'
      125_000_000_000    →  '$125B'
      4_500_000_000      →  '$4.5B'
      450_000_000        →  '$450M'
      None               →  '—'
    """
    if market_cap is None:
        return "—"
    try:
        mc = float(market_cap)
    except (TypeError, ValueError):
        return "—"
    if mc <= 0:
        return "—"
    if mc >= 1e12:
        return f"${mc/1e12:.1f}T"
    if mc >= 1e9:
        return f"${mc/1e9:.0f}B" if mc >= 100e9 else f"${mc/1e9:.1f}B"
    if mc >= 1e6:
        return f"${mc/1e6:.0f}M"
    return f"${mc:.0f}"


def group_watchlist(watchlist: Iterable[str],
                     market_caps: dict[str, float | None]) -> dict[str, list[str]]:
    """Group tickers by market-cap tier.

    Args:
        watchlist: iterable of ticker symbols
        market_caps: dict mapping ticker → market cap in dollars (or None)

    Returns:
        Dict keyed by tier ('mega'/'large'/...) → list of tickers.
        Tickers within each tier are sorted by market cap DESCENDING
        (biggest names first within each tier) so the visual gradient
        within a section flows from "most likely to be familiar" to
        "more obscure."

    Missing/empty tiers are omitted from the result — no need to show
    "Micro-Cap (0)" headers cluttering up the page.
    """
    buckets: dict[str, list[tuple[str, float]]] = {}
    for t in watchlist:
        mc = market_caps.get(t)
        tier = classify_cap(mc)
        buckets.setdefault(tier, []).append((t, mc or 0.0))

    # Sort each bucket by market cap desc (biggest first), then alpha tiebreak
    result: dict[str, list[str]] = {}
    for tier, items in buckets.items():
        items.sort(key=lambda x: (-x[1], x[0]))
        result[tier] = [t for t, _ in items]
    return result
