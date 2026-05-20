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

Same six **scored** factors, weighted by **Institutional Flow** (Options Flow 30%, Volume Surge 25%, Momentum 15%, Relative Strength 10%, Short Interest 10%, Range Proximity 10%). P/E, Volume Pace, and the 52W columns are shown for context but are **not** part of the score. Every factor is **regime-aware** (BULL / SIDEWAYS / BEAR), see below.

---

## Page 1 — Macro Gate (`run_macro_gate.py`)

Seven internal macro signals, each 0–100, blended into a composite:

VIX Level, VIX Term Structure, Watchlist Breadth, Credit Spreads, VIX Momentum (20-day ROC), Factor Crowding, and Mega-Cap Rotation (MAGS/SPY) — equally weighted (≈14.3% each). The CNN Fear & Greed Index is fetched and shown as a standalone gauge for reference but is **deliberately excluded** from the composite score.

**Regimes:** 70–100 🟢 BULL REGIME · 40–69 🟡 SIDEWAYS REGIME · 0–39 🔴 BEAR REGIME (the Scanner & Backtest pages stay enabled in all regimes; factors block longs in BEAR and shorts in BULL).

**Layout:**
- **Live price banner** (instant load) — `st.metric` tiles for the watchlist using yfinance `fast_info` (current price + daily % change only, no historical download).
- **On-demand button** — "Run Full Macro Analysis" gates the heavy calculation; the result is cached in `st.session_state` so switching pages never re-runs it.
- Composite score as a huge number **next to a decoupled Fear & Greed gauge**.
- A regime-aware **Strategy Recommendation** block (`st.success` / `st.warning` / `st.error`) advising primary & secondary engines for the current regime.
- A **Recent Composite Macro Score** table showing month-end snapshots for the past 4 months plus today.
- The 7 internal signal gauges (each tagged ·STALE if its underlying ticker fell back to last-known-good cache), a "📖 Metric Definitions" expander, a **benchmark selector** (SPY/QQQ/RSP/IWM) with a definitions expander, and the 180-day chart tinted by regime zone.
- A **last-known-good banner** appears at the top whenever yfinance is rate-limited — displayed values are real prior data with a staleness timestamp, never mocked.

## Page 2 — Custom Watchlist Scanner (`run_scanner.py`)

Six strategy-aware, **regime-aware** technical/institutional factors scored 0–100 (Institutional Flow weighted, see above) and ranked across the watchlist. The score maps to a **Directional Bias** that guides LONG / SHORT trade placement:

| Score | Directional Bias | Score | Directional Bias |
|-------|------------------|-------|------------------|
| 80–100 | 🟢 STRONG LONG | 35–49 | 🟠 WATCH SHORT |
| 65–79 | 🟢 LEAN LONG | 20–34 | 🔴 LEAN SHORT |
| 50–64 | 🟡 HOLD / CASH | 0–19 | 🔴 STRONG SHORT |

**Decoupled context (NOT scored):** Trailing & Forward P/E, 52W Low/High columns, and **Volume Pace** — a plain-English RVOL label (Heavy (Institutional) / Normal / Quiet (Retail)). All from live yfinance data.

**Display:** an expander with strategy, factor, **and Macro Regime / Capital Deployment** definitions; a Macro Regime banner; a ranked-composite bar chart (blocked rows excluded); and an interactive selectable table with columns: Ticker, Score, Directional Bias, **Tactical Allocation Action** (Tranche 1/2/3 LONG/SHORT or HOLD/CASH), Price, 52W Low, 52W High, Volume Pace, Trailing P/E, Fwd P/E, Next Earnings.

A ⚠️ icon next to the Ticker signals earnings within ~5 trading days (visual only; doesn't change the action). Blocked rows (regime forbids the direction) sort to the bottom with `❌ BLOCKED BY REGIME` and a sentinel `-1` score.

The first row is selected by default so a **1-year technical chart** (candlesticks, VWAP, floor pivots, color-coded volume bars, 50-bin volume profile + POC, and a 20-day ROC subplot with 9-EMA signal line) renders instantly. Below the table, a **CSV download button** exports the current scan for record-keeping.

## Page 3 — Visual Backtest & Audit (`run_backtest.py`)

Pick a ticker; the scanner score and directional bias are replayed across the last 252 trading days using the **currently active strategy engine**.

- **Main chart:** 12-month price line, background shaded green during LONG-bias periods and red during SHORT-bias periods.
- **Sub-chart:** the composite score (0–100) trend with directional reference bands.
- **Performance table:** average 20-day forward return per bias tier, the LONG-vs-SHORT directional edge, and the best/worst signal days.

**Methodology:** the historical score uses the **same Institutional Flow weighting** as the live scanner (Options Flow 30%, Volume Surge 25%, Momentum 15%, RS 10%, SI 10%, Range Proximity 10%). The four price/volume factors are replayed under the active strategy; Short Interest and Options Flow have no free historical series, so each is pinned to a neutral 50 and contributes its weight × 50 to every day's composite. The backtest is **regime-aware** — it pulls the historical macro-score series and applies the same per-day BULL/SIDEWAYS/BEAR rules (cap, block) as the live scanner. Blocked days appear as `❌ BLOCKED BY REGIME`.

---

## Project structure

```
stock_gating_v2/
├── app.py                  # Streamlit entry + sidebar + strategy radio + 3-page nav
├── theme.py                # Dark theme / CSS / chart styling
├── data_utils.py           # yfinance access, @st.cache_data(ttl=3600), indicators
├── run_macro_gate.py       # PAGE 1 — 7 macro signals
├── run_scanner.py          # PAGE 2 — dual-strategy, regime-aware scanner
├── run_backtest.py         # PAGE 3 — strategy-aware historical backtest
├── requirements.txt
├── run.sh                  # one-command launcher
├── macro_signals/
│   ├── __init__.py
│   ├── signals_a.py        # VIX Level, Term Structure, Breadth
│   └── signals_b.py        # Credit Spreads, VIX Momentum, Crowding, Mega-Cap Rotation
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
- **S&P 500 sample** (used for Watchlist Breadth and Factor Crowding) is a representative 60-stock subset for speed. Swap in full constituents in `data_utils.SP500_SAMPLE` for exactness.
- The **`scan_universe()`** function in `run_scanner.py` is deprecated (computed only 45% of the live engine's weights; no UI uses it) and is left in the file for future revival once free historical short-interest / options-flow data is available.
- **`VIX Momentum`** is the honest name for what used to be called "Put/Call Sentiment" — yfinance doesn't ship the real CBOE put/call ratio, so the metric tracks the 20-day rate of change of the VIX instead. It's a real metric, just clearly labeled now.
- **Rate-limit handling:** every yfinance download is wrapped with a last-known-good disk cache. When yfinance is rate-limited the dashboard serves the most recent successful fetch (real data, just stale), shows a banner with the staleness timestamp, and per-signal gauges get a ·STALE tag. No mock or placeholder values are ever fabricated.
- **Options data** from Yahoo can be flaky; every factor degrades gracefully to a neutral 50.
- **The on-demand technical chart** downloads 1 year of daily candles for the clicked ticker; VWAP, floor pivots, and the 20-day ROC are computed from that data with pandas/numpy.
- Switching the strategy engine invalidates cached backtests so Page 3 recomputes.

*Educational and research use only. Not financial advice.*
