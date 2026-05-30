# Market Deployment Gating System

A self-contained Streamlit dashboard that combines a **macro-regime classifier** with a **dual-strategy, regime-aware stock scanner**, a **strategy-faithful backtest**, a **signal performance journal**, and a **paper-trading blotter** — all powered by free yfinance data with last-known-good rate-limit resilience.

The system is built around one organizing idea: **stock-level signals are only as good as the macro regime they're traded in.** Page 1 classifies the regime. Page 2 ranks tickers within that regime. Pages 3–5 measure whether any of it actually worked.

> ⚠️ **Educational tool, not financial advice.** Every line of math in this dashboard is documented in the UI alongside the engine's actual implementation. Read the Factor Definitions & Mathematical Formulas expander on Page 2 before trusting any output.

---

## Quick Start

```bash
# macOS / Linux
git clone <your-repo-url>
cd stock_gating_v2
chmod +x run.sh
./run.sh
# opens at http://localhost:8501
```

Or manually:

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 -m streamlit run app.py
```

**Requires:** Python 3.10+ (uses `|` union syntax, `dict[str, dict]` annotations, and `:=` walrus). Streamlit ≥ 1.32 for `st.dataframe` row-selection.

---

## The Five Pages

### Page 1 — Macro Gate

Classifies the current **market regime** (BULL / SIDEWAYS / BEAR) from seven systematic macro signals. The regime gates every other page in the app.

- **Live price banner** — instant `fast_info` quotes for the watchlist; tagged ·STALE when yfinance falls back to last-known-good cache
- **Composite Macro Score** — large 0–100 readout with regime pill, computed by the Institutional Flow-Weighted Macro Composite model (see below)
- **Strategy Recommendation block** — colored alert (green/yellow/red) advising primary/secondary engine for the current regime
- **Recent Composite Macro Score table** — four monthly snapshots plus today
- **CNN Fear & Greed gauge** — displayed alongside but **excluded** from the composite (reference only)
- **7 gauges** — one per macro signal; each tagged ·STALE if its underlying ticker fell back
- **📖 Metric Definitions expander** — definition cards in descending weight order, with red-light/green-light thresholds
- **Benchmark chart** — selectable SPY/QQQ/RSP/IWM 180-day chart tinted by historical regime; the macro composite renders as a stacked oscillator subplot beneath the price (shared x-axis, unified hover, dotted threshold lines at 70 and 40)

### Page 2 — Custom Watchlist Scanner

Scores each ticker in the watchlist 0–100 using the **strategy-specific Institutional Flow-Weighted Stock Score** model. The strategy toggle (sidebar) controls both *what each factor measures* and *the weight each factor gets*.

- **Active Engine indicator** — shows which strategy is in effect (TREND / MR)
- **Macro Regime banner** — pulls the regime from Page 1
- **📖 Strategy & Factor Definitions** — honest per-strategy descriptions of each factor; calls out where MR uses entirely different math than TREND
- **📚 Factor Definitions & Mathematical Formulas** — LaTeX rendering of the classical textbook formula AND the engine's actual formula for each of the 6 factors, with documented reasons where they differ
- **Ranked composite bar chart** — blocked rows excluded
- **Interactive scanner table** — single-row selection, columns: Ticker (with ⚠️ earnings-soon icon), Score, Directional Bias, Tactical Allocation Action, Price, 52W Low, 52W High, Volume Pace, Trailing P/E, Fwd P/E, Next Earnings
- **Dynamic footnote** — lists the six factors in the active strategy's weight order with their exact percentages
- **CSV download** — exports the current scan (with earnings flag column) for record-keeping
- **✋ Override system recommendation** — log a discretionary action different from the system's, with a free-text reason (audit trail on Page 5)
- **🟢 Paper Execution** — execute simulated 100-share trades against the current scan; persists to the Portfolio blotter
- **1-year technical chart** — on-demand candlestick + VWAP + floor pivots + 50-bin volume profile (with POC) + 20-day ROC subplot with 9-EMA signal line. The first row is auto-selected so a chart renders without clicking. Drag-to-zoom and pan are locked; use the modebar zoom buttons.

### Page 3 — Backtest & Audit

Replays the last 252 trading days through the **same** strategy-specific weighted model the live scanner uses today — including regime-blocking rules — so the historical edge is a faithful audit, not a different model.

- Replayed factors (4 of 6): Momentum, Volume Surge, Relative Strength, Range Proximity — replayed from OHLCV
- Pinned factors (2 of 6): Short Interest, Options Flow — pinned to neutral 50 each day (no free historical series exists; their **weights** still contribute, just at a neutral score)
- Per-day regime applied: BULL/SIDEWAYS/BEAR rules use a daily macro-score series; blocked days are sentinelled with `composite = -1` and labeled `❌ BLOCKED BY REGIME`
- Performance table: hit rate, edge, win/loss averages

### Page 5 — Performance Journal

Persistent record of **what the system said** and **what you actually did**. Stored in `~/.stock_gating_v2/journal.db` (SQLite, WAL mode, never uploaded anywhere).

- **Auto-logging** — every scan run captures every row: scan_id, timestamp, ticker, strategy, regime, macro_score, composite, factor breakdown, status label, tranche action, entry price, earnings flag
- **Override audit trail** — every override is linked to the scan_id + ticker it overrode, with a reason field
- **Forward returns at 5/10/20/60 days** — computed from real yfinance closes via `searchsorted(scan_date, side='right')-1` (no look-ahead). Immature signals stay `NaN` — never mocked
- **Performance by tier** — n, hit rate, avg win/loss, direction-adjusted edge (LONG tiers want positive returns, SHORT tiers want negative)
- **System vs Override comparison** — three side-by-side metrics: followed-edge, overrode-edge, and the spread. Warns when sample size < 20
- **Raw signal log + CSV export**
- **Danger zone reset** — requires typing `RESET` to confirm

### Page 6 — Portfolio Blotter

Paper-trading layer for simulated execution. Independent SQLite DB at `~/.stock_gating_v2/paper_trades.db` (so wiping the journal never touches trades, and vice versa).

- **Open positions** — marked to current live prices via `get_live_quotes` (with last-known-good fallback); unrealized P&L and Return% are direction-aware
- **Close-position form** — pre-fills exit price to current live price; atomic update (status, exit_price, exit_timestamp, realized_pnl in one transaction)
- **Closed trades** — full audit: entry/exit prices, return%, realized P&L, strategy engine, macro score at entry, both timestamps. Summary stats: hit rate, avg win, avg loss
- **CSV export** of closed-trade history
- **Danger zone wipe** — requires typing `WIPE` to confirm; resets AUTOINCREMENT so next trade starts at id=1

---

## Mathematical Models

The dashboard runs **two** Institutional Flow-Weighted models. They are different models that share an aesthetic — same weighting philosophy, different scopes.

### Model A — Composite Macro Score (Page 1)

Scores the **overall market regime** by combining 7 systematic macro signals.

$$
\text{Composite Macro} = \sum_{i=1}^{7} w_i \cdot S_i
$$

where each \\(S_i \in [0, 100]\\) and the result is clamped to \\([0, 100]\\) then rounded to an integer.

| Signal | Weight | What it measures |
|---|---:|---|
| **Credit Spreads** | 25% | HY/IG credit spread Z-score (high-yield distress vs Treasuries) |
| **VIX Term Structure** | 20% | Ratio of spot VIX to 3-month VIX (backwardation = crash warning) |
| **Sector Breadth** | 15% | Fraction of 11 SPDR sector ETFs above their 50-day SMA |
| **VIX Level** | 15% | Spot VIX (panic above 30, complacency below 15) |
| **VIX Momentum** | 10% | 20-day rate-of-change of VIX (fear velocity) |
| **Mega-Cap Rotation** | 10% | 20-day ROC of MAGS/SPY ratio (institutional flow into mega-caps) |
| **Factor Crowding** | 5% | 60-day correlation of momentum vs value factor baskets |

**Sector Breadth (the 11 SPDR ETFs):**

$$
\text{Sector Breadth} = \frac{\text{count}\big( P_t > \text{SMA}_{50}(P) \big)}{11} \times 100
$$

over the 11 Select Sector SPDR ETFs: XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY.

**Regime mapping:**
- `composite ≥ 70` → 🟢 **BULL REGIME** — full risk-on
- `40 ≤ composite < 70` → 🟡 **SIDEWAYS REGIME** — high selectivity
- `composite < 40` → 🔴 **BEAR REGIME** — defensive

### Model B — Multi-Factor Stock Score (Page 2)

Scores **individual stocks** within the active macro regime. Same equation as Model A but with **strategy-specific weights** and a different set of inputs.

$$
\text{Stock Score} = \sum_{j=1}^{6} w_j(\text{strategy}) \cdot F_j(\text{strategy}, \text{regime})
$$

with the same `[0, 100]` clamp + integer round; `-1` is the sentinel for regime-blocked rows.

#### Weights per strategy

Both weight dictionaries sum to exactly 1.0 (asserted at module load). All factor functions are direction-aware AND regime-aware.

**TREND (Trend-Following):** *"Riding the wave."*

| Factor | Weight |
|---|---:|
| Market Leader (Relative Strength) | 25% |
| Price Speed (Momentum) | 25% |
| Big Money Volume (Volume Surge) | 20% |
| Chart Position (Range Proximity) | 15% |
| Options Flow | 10% |
| Squeeze Fuel (Short Interest) | 5% |

**MR (Mean Reversion):** *"The rubber band effect."*

| Factor | Weight |
|---|---:|
| Chart Position (Range Proximity) | 30% |
| Options Flow | 25% |
| Big Money Volume (Volume Surge) | 20% |
| Squeeze Fuel (Short Interest) | 15% |
| Price Speed (Momentum) | 5% |
| Market Leader (Relative Strength) | 5% |

#### Factor formulas (engine's actual implementations)

The dashboard renders the **classical textbook formula** AND the **engine's actual formula** side-by-side in the Page 2 "📚 Factor Definitions & Mathematical Formulas" expander. The engine deviates from textbook in places for numerical robustness or strategy-specific clarity. The actual computations are:

**Price Speed (Momentum) — TREND:**

$$
\text{Momentum}_{\text{TREND}} = \frac{\text{EMA}_{10}(P) - \text{EMA}_{50}(P)}{P_{\text{current}}} \times 100
$$

EMA over SMA for faster reaction. Normalized by current price (not the slower EMA₅₀) for tighter score scaling.

**Price Speed (Momentum) — MR:** switches entirely to RSI + 20-day Bollinger Band z-score (different math, different intent):

$$
z_{20} = \frac{P_t - \text{SMA}_{20}}{\text{StdDev}_{20}}, \quad \text{score expresses buyable-dip strength}
$$

**Big Money Volume (Volume Surge):** matches textbook exactly.

$$
\text{Surge Ratio} = \frac{\overline{V}_{5\text{-day}}}{\overline{V}_{20\text{-day}}}
$$

Mapped linearly: 0.7× → 0, 2.0× → 100.

**Market Leader (Relative Strength vs SPY):**

$$
RS_{\text{engine}} = \%\Delta P_{\text{stock}}^{(20d)} - \%\Delta P_{\text{SPY}}^{(20d)} \quad (\text{in pp})
$$

Uses the **difference** of percentage returns, not the textbook **ratio**. The ratio is undefined when SPY's return crosses zero — the difference is well-defined across all market conditions and produces the same ranking on non-degenerate days. Under MR the score inverts: deep underperformance scores HIGH.

**Chart Position (Range Proximity):**

$$
\text{TREND:} \quad \frac{P_{\text{current}}}{\text{High}_{52w}} \quad\quad \text{MR:} \quad \frac{P_{\text{current}}}{\text{Low}_{52w}}
$$

Two single-anchor ratios (one per strategy), each linearly mapped to 0–100. Under TREND, proximity to the 52-week HIGH scores high; under MR, proximity to the 52-week LOW scores high.

**Squeeze Fuel (Short Interest):**

$$
\Delta SI = \frac{SI_{\text{current month}} - SI_{\text{prior month}}}{SI_{\text{prior month}}} \times 100
$$

Engine measures the **change**, not the level — levels vary enormously by ticker, change is a cleaner cross-sectional signal. **Data caveat:** FINRA publishes SI ~twice a month, so this factor has a built-in 1–14 day lag.

**Options Flow:**

$$
PCR_{\text{engine}} = \frac{\sum \text{Put OI (all strikes)}}{\sum \text{Call OI (all strikes)}}
$$

$$
IV\text{-pct} = P_{252}\big( IV_t, \{IV_{t-252} \ldots IV_t\} \big)
$$

Uses **open interest** (positioning) over **volume** (single-day flow). IV percentile over a rolling 252-day window. Combined into one factor with different weighting per strategy.

### Directional Bias tiers (purely score-driven)

Once the composite is computed, the tier is read from a fixed mapping:

| Score range | Tier | Color |
|---|---|---|
| 80–100 | 🟢 STRONG LONG | green |
| 65–79 | 🟢 LEAN LONG | green |
| 50–64 | 🟡 HOLD / CASH | yellow |
| 35–49 | 🟠 WATCH SHORT | orange |
| 20–34 | 🔴 LEAN SHORT | red |
| 0–19 | 🔴 STRONG SHORT | red |
| -1 (sentinel) | ❌ BLOCKED BY REGIME | grey |

### Tactical Allocation Action (Tranche Engine)

Combines the stock score with the macro regime to produce a deployable directive:

| Stock | Macro | Action |
|---|---|---|
| ≥ 80 | ≥ 70 (BULL) | 🟢 TRANCHE 3 (MAX LONG) |
| ≥ 80 | 40–69 (SIDEWAYS) | 🟢 TRANCHE 2 (MID LONG · A+ RS) |
| 65–79 | any | 🟢 TRANCHE 1 (PILOT LONG) |
| 50–64 | any | 🟡 HOLD CORE / CASH |
| 35–49 | any | 🟠 WATCH / NO TRADE |
| 20–34 | any | 🔴 TRANCHE 2 (LEAN SHORT) |
| 0–19 | any | 🔴 TRANCHE 3 (MAX SHORT) |

**Overriding rules:**
1. Macro `< 40` (BEAR) → all LONG actions forced to `❌ RISK OFF: COLD CASH`
2. Macro `≥ 70` (BULL) → all SHORT actions forced to `❌ SHORT BLOCKED: BULL REGIME`
3. `stock_score == -1` (sentinel) → `❌ BLOCKED BY REGIME` (regime blocked the underlying factors)

The MID LONG · A+ RS label only fires when a stock scores 80+ under SIDEWAYS regime. Under SIDEWAYS the engine caps non-RS factors at 70, so reaching 80 implies the stock has top-tier Relative Strength — hence the A+ tag.

### Forward returns (Journal evaluation)

For each journaled signal with entry date \\(t\\) and entry price \\(P_t\\), forward returns at horizon \\(h\\) (in trading days) are:

$$
r_h = \frac{P_{t+h} - P_t}{P_t} \times 100
$$

Computed in `signal_journal.attach_forward_returns()` using `prices.index.searchsorted(scan_date, side='right') - 1` to ensure the entry price is the close on-or-before the scan date and the forward price is strictly after. No look-ahead. Immature horizons (e.g. 60d on a 14-day-old signal) stay `NaN` — never mocked.

For SHORT-tier signals the system reports **direction-adjusted** edge: a SHORT signal counts a *negative* return as a win.

---

## Project Structure

```
stock_gating_v2/
├── app.py                       # Streamlit entry: 5-page nav, sidebar, strategy toggle
├── theme.py                     # Dark theme (#0b0e17), CSS, plotly_dark universal
├── data_utils.py                # yfinance fetch + @st.cache_data + LKG disk cache
├── signal_journal.py            # SQLite signal/override persistence (Page 5)
├── db_manager.py                # SQLite paper-trade persistence (Page 6)
│
├── run_macro_gate.py            # Composite Macro Score (Model A) — Page 1 engine
├── run_scanner.py               # Multi-Factor Stock Score (Model B) — Page 2 engine
├── run_backtest.py              # Strategy-faithful 252-day backtest — Page 3 engine
│
├── macro_signals/
│   ├── signals_a.py             # VIX Level, VIX Term Structure, Sector Breadth
│   ├── signals_b.py             # Credit Spreads, VIX Momentum, Factor Crowding,
│   │                            #   Mega-Cap Rotation
│   └── macro_history.py         # Daily regime time series for the chart + backtest
│
├── scanner_factors/
│   └── factors.py               # 6 factor functions (dual-strategy, regime-aware)
│
├── pages_lib/
│   ├── page_macro.py            # Page 1 renderer
│   ├── page_scanner.py          # Page 2 renderer
│   ├── page_backtest.py         # Page 3 renderer
│   ├── page_journal.py          # Page 5 renderer
│   └── page_portfolio.py        # Page 6 renderer
│
├── requirements.txt
├── run.sh                       # One-command launcher (creates venv, installs deps)
└── README.md                    # This file
```

**Naming note:** the page-renderer folder is `pages_lib/` (not `pages/`) deliberately — Streamlit auto-discovers `pages/` as a multi-page app and would interfere with the in-app routing.

---

## Data Integrity & Resilience

### Last-known-good rate-limit handling

yfinance is unofficial and rate-limited. Every download is wrapped with a **last-known-good disk cache** (`~/.stock_gating_v2_lkg/`). When yfinance fails or returns empty results:

- The dashboard serves the most-recent successful fetch
- A yellow banner appears at the top of Page 1 with the staleness timestamp
- Per-signal gauges get a tiny `·STALE` tag
- The live-prices banner gets a "(stale, last good: ...)" header
- **No mock or placeholder values are ever fabricated** — only real prior data, just stale

`du.fallback_for_ticker(ticker)` lets any UI component check whether a specific ticker fell back. Every fallback event is logged to `FALLBACK_LOG` keyed by fetch URL.

### Single source of truth for the composite

`run_macro_gate.compute_composite()` is the **canonical** formula. The historical engine (`macro_history.py`) calls it row-by-row via `score_df.apply(lambda row: rmg.compute_composite(row.to_dict()))` so Page 1's "today" reading and the historical chart can never drift apart.

### Persistence is local-only

Both SQLite databases live under `~/.stock_gating_v2/` and are **never uploaded anywhere**. The signal journal and paper-trade DB are independent files — resetting one does not touch the other.

### Look-ahead protection

All historical price lookups use `prices.index.searchsorted(scan_date, side='right') - 1` to guarantee the entry price is the close on-or-before the scan date and any forward price is strictly after. EMAs and rolling indicators are computed on the full series before slicing, which is mathematically safe since each window contains only past data.

### No mock data — anywhere

Verified by `grep -rn "import random\|np.random\|mock_data\|fake_data" --include="*.py" .` (clean as of this writing). Empty / failed fetches return empty DataFrames; the UI degrades gracefully but never invents numbers.

---

## Architectural Decisions Worth Knowing

- **`scan_universe()` is deprecated.** Originally built to scan the S&P 500 for a "Broad Market Top 5" panel, but it skipped Short Interest and Options Flow (55% of the live composite by weight) for speed, making the ranking incompatible with the live engine. No UI calls it. Left in the codebase with a `DeprecationWarning` for future revival once free historical SI/options data becomes available.

- **Backtest uses strategy-specific weights.** A TREND backtest uses `TREND_WEIGHTS`, an MR backtest uses `MR_WEIGHTS` — pulled by direct import from `run_scanner`, so the backtest automatically tracks any future weight changes. This is what makes the backtest a faithful audit instead of a different model.

- **Volume Surge vs Volume Pace are intentionally separate.** Volume Surge is the *scored* factor (precise quantitative 0–100). Volume Pace is the same RVOL rendered as a *display-only* categorical label ("Heavy (Institutional)" / "Normal" / "Quiet (Retail)"). They're the same underlying number at different granularities — Volume Pace is NOT a 7th factor, by design, to avoid double-counting.

- **VIX Momentum, not "Put/Call Sentiment".** An earlier version of the macro composite included a "Put/Call Sentiment" metric that was actually computing VIX 20-day ROC. The metric was renamed for honesty — no real CBOE put/call feed exists in yfinance.

- **Override panel logs to a fixed action vocabulary.** 8 choices (MAX LONG / MID LONG / PILOT LONG / HOLD / NO TRADE / PILOT SHORT / LEAN SHORT / MAX SHORT) so Page 5 can group overrides meaningfully.

- **Earnings warning is visual-only.** A ⚠️ icon next to the ticker means earnings within ~5 trading days. By explicit user instruction, this does NOT alter the tranche action — it's a heads-up so you can size around the event consciously.

- **Page-1 metric definitions render in descending weight order.** Credit Spreads (25%) appears first, Factor Crowding (5%) last. Readers learn the model better when big drivers are on top.

---

## Sample Sizes Matter

Every performance metric in this dashboard carries an honest caveat: **statistics on fewer than 50 signals are unreliable**. The Performance Journal page surfaces a yellow warning when matured-signal count is below 20 at the selected horizon. The override-vs-system comparison shows a warning below 20 overrides. A 70% hit rate over 8 signals is indistinguishable from luck. Use the journal to *measure*, not to *conclude*, until you have meaningful sample size.

---

## Known Limitations

- **yfinance is unofficial.** It works, but it's not a contract. Production trading systems should use a paid feed (Polygon, Tradier, Intrinio, IEX Cloud). This dashboard tolerates yfinance flakiness with the LKG cache but can't fix bad data at the source.

- **No transaction-cost modeling.** Tranche actions don't account for bid-ask spread, commissions, borrow cost for shorts, or market impact. A 65-score signal on a wide-spread name may be uneconomic after friction (see BR-1 in the audit history).

- **No position sizing engine.** "MAX LONG" is conviction-relative, not dollar-relative. Real position sizing requires account-equity input and risk-per-trade rules (see BR-2).

- **No correlation-aware portfolio heat.** Five "different" MAX LONG signals on correlated mega-caps is one bet sized 5×. The system doesn't currently warn about this (see BR-3).

- **Backtest does not include earnings reactions.** Signals into earnings get the visual ⚠️ flag but their forward returns include the earnings move, which is largely random.

- **Survivorship bias in default watchlist.** The default 10-ticker watchlist (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, SMCI, CRDO, MXL) is a *2026* watchlist. Backtesting these names over 2024–2025 implicitly assumes you'd have picked them in 2024 — which is hindsight.

Educational tool, not financial advice.

---

## Reset / Wipe Procedures

```bash
# Wipe last-known-good price cache (forces fresh yfinance fetches)
rm -rf ~/.stock_gating_v2_lkg/

# Wipe signal journal (Page 5 data)
rm ~/.stock_gating_v2/journal.db

# Wipe paper-trade blotter (Page 6 data)
rm ~/.stock_gating_v2/paper_trades.db

# In-app: Page 5 has a "type RESET to confirm" wipe for the journal;
#         Page 6 has a "type WIPE to confirm" wipe for paper trades.
```

The signal journal and paper-trade DB are intentionally separate files so wiping one does not touch the other.

---

## License & Credits

Built on top of: streamlit, yfinance, pandas, numpy, plotly, fear-and-greed.

Macro-regime taxonomy and Institutional Flow Weighting are based on standard buy-side risk-desk practice but adapted for retail-data realities (yfinance constraints, free EOD feeds, no real-time options chain). The engine documents every place where it deviates from textbook formulas, and why.

If you trade actual money based on what this dashboard says, that is on you. Educational tool, not financial advice.
