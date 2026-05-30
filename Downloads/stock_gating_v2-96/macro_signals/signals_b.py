"""
Macro signals 4-7: Credit Spreads, VIX Momentum, Factor Crowding, Mega-Cap Rotation.
"""

import numpy as np
import pandas as pd
import data_utils as du


# ── 4. Credit Spreads ─────────────────────────────────────────────────────────
def credit_spreads() -> dict:
    name = "Credit Spreads"
    try:
        hyg = du.get_close_series("HYG", days=400)
        tlt = du.get_close_series("TLT", days=400)
        if len(hyg) < 60 or len(tlt) < 60:
            return _err(name, "HYG/TLT unavailable")

        aligned = pd.concat([hyg, tlt], axis=1, join="inner").dropna()
        aligned.columns = ["HYG", "TLT"]
        if len(aligned) < 60:
            return _err(name, "Insufficient aligned data")

        # TLT/HYG ratio rises as credit risk widens
        spread = aligned["TLT"] / aligned["HYG"]
        spread = spread.iloc[-252:] if len(spread) > 252 else spread

        cur = float(spread.iloc[-1])
        mean = float(spread.mean())
        std = float(spread.std())
        if std == 0:
            return _err(name, "Zero std in spread")

        z = (cur - mean) / std
        score = du.clamp(np.interp(z, [-2, 2], [100, 0]))
        regime = "Tight (risk-on)" if z < 0 else "Wide (risk-off)"

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "z_score": round(z, 2),
            "detail": f"TLT/HYG z-score {z:+.2f} — {regime}",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 5. VIX Momentum (named "Put/Call" originally; now honest) ─────────────────────────────────────────────────────
def vix_momentum(horizon: str = "Swing Trade System") -> dict:
    name = "VIX Momentum"
    # late import to avoid circular dep
    try:
        from scanner_factors.factors import lookback
        window = lookback(horizon, "vix_momentum")
    except ImportError:
        window = 20

    try:
        # need at least `window + 2` bars; pull a healthy buffer
        closes = du.get_close_series("^VIX", days=max(120, window + 30))
        if len(closes) < window + 2:
            return _err(name, "Insufficient VIX data")

        cur = float(closes.iloc[-1])
        past = float(closes.iloc[-(window + 1)])
        if past == 0:
            return _err(name, "Past VIX zero")

        roc = (cur - past) / past * 100
        score = du.clamp(np.interp(roc, [-30, 50], [100, 0]))
        mood = "Fear rising" if roc > 20 else "Neutral" if roc > -10 else "Complacent"

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "vix_roc": round(roc, 1),
            "window_days": window,
            "detail": f"VIX {window}d ROC {roc:+.1f}% — {mood}",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 6. Factor Crowding ────────────────────────────────────────────────────────
def factor_crowding() -> dict:
    """
    Build momentum & value long/short baskets from the S&P sample,
    then measure their 60-day rolling return correlation.
    Highly negative correlation => crowded factor positioning => low score.
    """
    name = "Factor Crowding"
    try:
        data = du.get_bulk_history(du.SP500_SAMPLE, days=320)
        # build a clean close-price panel
        closes = {}
        for t, df in data.items():
            if not df.empty and "Close" in df and len(df["Close"].dropna()) > 150:
                closes[t] = df["Close"].dropna()
        if len(closes) < 20:
            return _err(name, "Insufficient panel for factor baskets")

        panel = pd.DataFrame(closes).dropna()
        if len(panel) < 130:
            return _err(name, "Panel too short")

        # --- Momentum factor: rank by 120-day return ---
        mom_ret = panel.iloc[-1] / panel.iloc[-120] - 1
        mom_sorted = mom_ret.sort_values()
        n = max(3, len(mom_sorted) // 3)  # top/bottom third (proxy for top/bottom 50)
        mom_long = mom_sorted.index[-n:]
        mom_short = mom_sorted.index[:n]

        # --- Value factor proxy: inverse 250-day return (mean-reversion stand-in) ---
        lookback_v = min(250, len(panel) - 1)
        val_ret = panel.iloc[-1] / panel.iloc[-lookback_v] - 1
        val_sorted = val_ret.sort_values()
        val_long = val_sorted.index[:n]    # cheap / laggard = "value long"
        val_short = val_sorted.index[-n:]  # expensive = "value short"

        daily = panel.pct_change().dropna()

        def basket_ls(long_ids, short_ids):
            return daily[long_ids].mean(axis=1) - daily[short_ids].mean(axis=1)

        mom_ls = basket_ls(mom_long, mom_short)
        val_ls = basket_ls(val_long, val_short)

        joined = pd.concat([mom_ls, val_ls], axis=1).dropna()
        joined.columns = ["mom", "val"]
        if len(joined) < 60:
            return _err(name, "Not enough overlap for correlation")

        corr = float(joined["mom"].iloc[-60:].corr(joined["val"].iloc[-60:]))
        # corr +0.3 -> 100 ; corr -0.8 -> 0
        score = du.clamp(np.interp(corr, [-0.8, 0.3], [0, 100]))
        regime = "Crowded (factors fighting)" if corr < -0.3 else "Healthy dispersion"

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "correlation_60d": round(corr, 3),
            "detail": f"Mom/Val L-S corr {corr:+.2f} — {regime}",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 7. Mega-Cap Rotation (MAGS/SPY) ───────────────────────────────────────────
def megacap_rotation() -> dict:
    """
    Mega-Cap Rotation: 20-day ROC of the MAGS/SPY ratio (MAGS = Magnificent 7
    ETF). Rising ratio = institutional capital flowing into mega-caps.
    """
    name = "Mega-Cap Rotation"
    try:
        mags = du.get_close_series("MAGS", days=60)
        spy = du.get_close_series("SPY", days=60)
        if len(mags) < 25 or len(spy) < 25:
            return _err(name, "MAGS/SPY unavailable")

        aligned = pd.concat([mags, spy], axis=1, join="inner").dropna()
        aligned.columns = ["MAGS", "SPY"]
        ratio = aligned["MAGS"] / aligned["SPY"]
        if len(ratio) < 22:
            return _err(name, "Insufficient ratio history")

        cur = float(ratio.iloc[-1])
        past = float(ratio.iloc[-21])
        roc = (cur - past) / past * 100  # % change of MAGS/SPY ratio over 20d

        # Map ROC: mega-caps outperforming -> high. Symmetric band of +/-5%.
        score = du.clamp(np.interp(roc, [-5, 5], [0, 100]))
        flow = "Into Mega-Caps (MAGS)" if roc > 0 else "Out of Mega-Caps"

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "mags_spy_roc_20d": round(roc, 2),
            "detail": f"MAGS/SPY 20d ROC {roc:+.2f}% — flow {flow}",
        }
    except Exception as e:
        return _err(name, str(e))


# ── 8. CNN Fear & Greed Index ─────────────────────────────────────────────────
def fear_and_greed() -> dict:
    """
    CNN Fear & Greed Index via the `fear-and-greed` library.
    Raw value 0-100 IS the score: 0 = Extreme Fear, 100 = Extreme Greed.
    """
    name = "Fear & Greed"
    try:
        import fear_and_greed
        idx = fear_and_greed.get()
        value = float(idx.value)
        score = max(0.0, min(100.0, value))

        desc = idx.description.title() if getattr(idx, "description", None) \
            else ("Extreme Greed" if score >= 75 else
                  "Greed" if score >= 55 else
                  "Neutral" if score >= 45 else
                  "Fear" if score >= 25 else "Extreme Fear")

        return {
            "name": name, "score": round(score, 1), "status": "ok",
            "raw_value": round(value, 1),
            "detail": f"CNN F&G index {value:.0f} — {desc}",
        }
    except Exception as e:
        return _err(name, str(e))


def _err(name, msg):
    return {"name": name, "score": 50.0, "status": "error",
            "detail": f"Error: {msg}", "error": msg}
