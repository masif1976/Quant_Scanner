# 🛰️ Market Deployment Gating System & Quantitative Scanner

A three-page capital-deployment decision engine with a dual-strategy quant scanner and a Streamlit dashboard.

- **Page 1 — Macro Gate & Broad Market Setup:** *Should I be deploying capital, and what are the best broad-market setups?*
- **Page 2 — Custom Watchlist Scanner:** *Which of my stocks have the best setup for my active strategy?*
- **Page 3 — Visual Backtest & Audit:** *How did this strategy perform on this stock over the last year?*

---

## Quick start

### Easiest — one command (macOS / Linux)

```bash
cd stock_gating_v2
chmod +x run.sh
./run.sh
```

### Manual setup

```bash
cd stock_gating_v2
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

The dashboard opens at **http://localhost:8501**. In the sidebar, click **RUN FULL ANALYSIS**.

> Tip: use `python3 -m streamlit` rather than bare `streamlit` — it avoids macOS `PATH` issues.

---

## Global configuration

### Sidebar
1. **Watchlist tag box** — defaults to AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, SMCI, CRDO, MXL. Add (comma-separated text), remove (✕ on a tag), or reset.
2. **Active Strategy Engine radio** — `Trend-Following` (default) or `Mean Reversion`. This toggle drives the factor logic on Pages 1, 2, and 3.
3. **Navigation menu** — switch between the three pages.

### Caching
All yfinance OHLCV / options / short-interest downloads are wrapped with `@st.cache_data(ttl=3600)`, so reloads — including the S&P 500 universe scan — are fast for an hour. Outside the Streamlit runtime the same decorator falls back to an in-process TTL cache, keeping the engine modules independently testable.

---

## The two strategy engines

Every scanner factor branches on the active engine:

| Factor | Trend-Following | Mean Reversion |
|--------|-----------------|----------------|
| Momentum | 100 if 10-EMA > 50-EMA | 100 if price extended >10% **below** the 50-EMA (capitulation) |
| Volume Surge | volume confirms the move | volume spike on a downtrend = exhaustion |
| Relative Strength | outperformance vs SPY | extreme **under**performance vs SPY |
| Range Proximity | price / 52-week **high** | price / 52-week **low** |
| Short Interest | declining shorts = bullish | elevated/rising shorts = squeeze fuel |
| Options Flow | low IV + call-heavy OI = calm | high IV + put-heavy OI = peak fear |

Same six **scored** factors, equally weighted — opposite reward functions. P/E and the 52-week range position are shown for context but are **not** part of the score.

---

## Page 1 — Macro Gate (`run_macro_gate.py`)

Seven internal macro signals, each 0–100, blended into a composite:

VIX Level, VIX Term Structure, Watchlist Breadth, Credit Spreads, Put/Call ROC, Factor Crowding, and Mega-Cap Rotation (MAGS/SPY) — equally weighted (≈14.3% each). The CNN Fear & Greed Index is fetched and shown as a standalone gauge for reference but is **deliberately excluded** from the composite score.

**Regimes:** 70–100 FULL DEPLOY · 40–69 REDUCED · 0–39 DEFENSIVE (disables Pages 2 & 3).

**Layout:**
- **Live price banner** (instant load) — `st.metric` tiles for the watchlist using yfinance `fast_info` (current price + daily % change only, no historical download).
- **On-demand button** — "Run Full Macro Analysis" gates the heavy calculation; the result is cached in `st.session_state` so switching pages never re-runs it.
- Composite score as a huge number **next to a decoupled Fear & Greed gauge**, then 7 internal signal gauges, a "📖 Metric Definitions" expander, and the SPY 180-day chart tinted by regime zone.

## Page 2 — Custom Watchlist Scanner (`run_scanner.py`)

Six strategy-aware technical/institutional factors scored 0–100 (equal-weighted average) and ranked across the watchlist. The score maps to a **Directional Bias** that guides LONG / SHORT trade placement:

| Score | Directional Bias | Score | Directional Bias |
|-------|------------------|-------|------------------|
| 80–100 | 🟢 STRONG LONG | 35–49 | 🟠 WATCH SHORT |
| 65–79 | 🟢 LEAN LONG | 20–34 | 🔴 LEAN SHORT |
| 50–64 | 🟡 HOLD / CASH | 0–19 | 🔴 STRONG SHORT |

**Decoupled context (NOT scored):** Trailing & Forward P/E and a 52-Week Range Position — `(Price − 52W Low) / (52W High − 52W Low) × 100`, rendered as an in-table progress bar — all from live yfinance data.

**Display:** an expander with strategy & factor definitions, a Macro Regime banner, a ranked-composite bar chart, and an interactive selectable table — Ticker, Score, Directional Bias, Tactical Allocation Action, 52W Range Position (progress bar), Price, Trailing P/E, Fwd P/E, Next Earnings. Clicking a row loads an on-demand 1-year technical chart (candlesticks, VWAP overlay, floor pivots, and a 20-day ROC momentum subplot).

## Page 3 — Visual Backtest & Audit (`run_backtest.py`)

Pick a ticker; the scanner score and directional bias are replayed across the last 252 trading days using the **currently active strategy engine**.

- **Main chart:** 12-month price line, background shaded green during LONG-bias periods and red during SHORT-bias periods.
- **Sub-chart:** the composite score (0–100) trend with directional reference bands.
- **Performance table:** average 20-day forward return per bias tier, the LONG-vs-SHORT directional edge, and the best/worst signal days.

**Methodology:** the historical score is a 6-factor average matching the live scanner. The four price/volume factors (Momentum, Volume Surge, Relative Strength, Range Proximity) are replayed under the active strategy; Short Interest and Options Flow have no free historical series, so each is pinned to a neutral 50 every day (composite ÷ 6). The Page 3 score is therefore slightly compressed vs the live Page 2 score and is best read as a directional audit, not an exact replica.

---

## Project structure

```
stock_gating_v2/
├── app.py                  # Streamlit entry + sidebar + strategy radio + 3-page nav
├── theme.py                # Dark theme / CSS / chart styling
├── data_utils.py           # yfinance access, @st.cache_data(ttl=3600), indicators
├── run_macro_gate.py       # PAGE 1 — 7 macro signals
├── run_scanner.py          # PAGE 2 — dual-strategy scanner + S&P 500 universe scan
├── run_backtest.py         # PAGE 3 — strategy-aware historical backtest
├── requirements.txt
├── run.sh                  # one-command launcher
├── macro_signals/
│   ├── __init__.py
│   ├── signals_a.py        # VIX Level, Term Structure, Breadth
│   └── signals_b.py        # Credit, Put/Call, Crowding, Rotation
├── scanner_factors/
│   ├── __init__.py
│   └── factors.py          # 6 dual-strategy factors + earnings overlay
└── pages_lib/
    ├── __init__.py
    ├── page_macro.py       # Page 1
    ├── page_scanner.py     # Page 2
    └── page_backtest.py    # Page 3
```

---

## Notes & caveats

- **Live data** comes from Yahoo Finance via `yfinance` when you click RUN. Quotes are ~15-min delayed; daily bars update end-of-day.
- **S&P 500 scans** (breadth, factor crowding, Top 5) use a representative 60-stock sample for speed. Swap in full constituents in `data_utils.SP500_SAMPLE` for exactness.
- The **universe scan skips** short-interest and options factors (neutral 50) so the Page 1 Top 5 stays fast.
- **Options data** from Yahoo can be flaky; every factor degrades gracefully to a neutral 50.
- **The on-demand technical chart** downloads 1 year of daily candles for the clicked ticker; VWAP, floor pivots, and the 20-day ROC are computed from that data with pandas/numpy.
- Switching the strategy engine invalidates cached backtests so Page 3 recomputes.

*Educational and research use only. Not financial advice.*
