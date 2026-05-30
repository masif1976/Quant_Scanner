"""
theme.py — Shared theme, CSS, and chart styling for the dashboard.

Supports a light/dark toggle. The module-level colour constants (BG, PANEL,
TEXT, etc.) reflect the ACTIVE theme and are swapped by set_mode(); existing
pages that reference theme.TEXT etc. keep working without changes. Call
set_mode() once at the top of app.py (before inject_css) based on the user's
selection.
"""

import streamlit as st

# ── Theme palettes ──
_DARK_PALETTE = {
    "BG": "#0b0e17", "PANEL": "#121724", "PANEL_HI": "#1a2133",
    "BORDER": "#1f2940", "ACCENT": "#3d8bff", "ACCENT2": "#00d4c8",
    "TEXT": "#e6ebf5", "MUTED": "#7d8aa5",
    "SIDEBAR_BG": "#0e131f",
}
_LIGHT_PALETTE = {
    "BG": "#f4f6fa", "PANEL": "#ffffff", "PANEL_HI": "#eef2f7",
    "BORDER": "#d8dee8", "ACCENT": "#2e6fdb", "ACCENT2": "#0bb5ac",
    "TEXT": "#1a2230", "MUTED": "#5b6675",
    "SIDEBAR_BG": "#e9edf3",
}

# Active theme — defaults to dark (the app's original look). Module-level
# constants below mirror this and are refreshed by set_mode().
_MODE = "dark"
_ACTIVE = dict(_DARK_PALETTE)

BG = _ACTIVE["BG"]
PANEL = _ACTIVE["PANEL"]
PANEL_HI = _ACTIVE["PANEL_HI"]
BORDER = _ACTIVE["BORDER"]
ACCENT = _ACTIVE["ACCENT"]
ACCENT2 = _ACTIVE["ACCENT2"]
TEXT = _ACTIVE["TEXT"]
MUTED = _ACTIVE["MUTED"]
SIDEBAR_BG = _ACTIVE["SIDEBAR_BG"]

# Status colours are theme-independent (they read on both backgrounds).
GREEN = "#22e08a"
YELLOW = "#f5c344"
ORANGE = "#ff9442"
RED = "#ff5d6c"


def set_mode(mode: str) -> None:
    """Switch the active theme palette ('light' or 'dark'). Refreshes the
    module-level colour constants so all pages pick up the new theme."""
    global _MODE, _ACTIVE, BG, PANEL, PANEL_HI, BORDER, ACCENT, ACCENT2
    global TEXT, MUTED, SIDEBAR_BG
    _MODE = "light" if str(mode).lower() == "light" else "dark"
    _ACTIVE = dict(_LIGHT_PALETTE if _MODE == "light" else _DARK_PALETTE)
    BG = _ACTIVE["BG"]
    PANEL = _ACTIVE["PANEL"]
    PANEL_HI = _ACTIVE["PANEL_HI"]
    BORDER = _ACTIVE["BORDER"]
    ACCENT = _ACTIVE["ACCENT"]
    ACCENT2 = _ACTIVE["ACCENT2"]
    TEXT = _ACTIVE["TEXT"]
    MUTED = _ACTIVE["MUTED"]
    SIDEBAR_BG = _ACTIVE["SIDEBAR_BG"]


def get_mode() -> str:
    """Return the active theme mode ('light' or 'dark')."""
    return _MODE


def hero_gradient() -> str:
    """CSS gradient for the macro hero cards.

    These are intentional dark 'feature cards' (Stripe/Linear pattern) — a
    deliberate dark focal point that stays dark in BOTH themes, because the
    big colored score reads best against a dark surface. The text inside is
    set to light explicitly (see hero_text/hero_muted) so it stays legible
    on the dark card regardless of the page theme.
    """
    return "linear-gradient(160deg,#121724,#0d1320)"


# Fixed light text colours for use INSIDE the dark hero cards, so they stay
# readable even when the rest of the page is light.
HERO_TEXT = "#e6ebf5"
HERO_MUTED = "#9aa6bd"


def score_color(score: float) -> str:
    if score >= 70:
        return GREEN
    if score >= 40:
        return YELLOW
    return RED


def factor_color(score: float) -> str:
    if score >= 75:
        return GREEN
    if score >= 55:
        return "#9fe06a"
    if score >= 40:
        return YELLOW
    if score >= 25:
        return ORANGE
    return RED


def inject_css(text_scale: float = 1.0):
    """Inject the global stylesheet for the ACTIVE theme.

    `text_scale` multiplies the body-text rules (.tiny, .kicker, and the
    related explanatory-text sizes) without touching headings, gauges,
    scores, or tables.

    Pass 1.0 for default (medium), 0.88 for small, 1.18 for large.
    """
    # Light mode needs dark button text on light buttons inverted; build the
    # button palette from the active theme so contrast always works.
    is_light = (_MODE == "light")
    btn_bg = "#1a2230" if is_light else "#ffffff"
    btn_fg = "#ffffff" if is_light else "#000000"
    btn_hover = "#2a3550" if is_light else "#f0f0f0"
    sidebar_text = "#3a424f" if is_light else "#A3A8B8"
    sidebar_text_active = "#000000" if is_light else "#FFFFFF"
    # Card shadows: soft and visible on light (gives float/separation that a
    # dark bg provides for free); near-invisible on dark (borders do the work).
    if is_light:
        card_shadow = "0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)"
        card_shadow_hover = "0 4px 12px rgba(16,24,40,0.10)"
    else:
        card_shadow = "none"
        card_shadow_hover = "0 0 0 1px rgba(61,139,255,0.25)"
    # Compute the scaled sizes once so the f-string below stays readable.
    kicker_size  = round(0.92 * text_scale, 3)
    tiny_size    = round(0.85 * text_scale, 3)
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap');

    html, body, [class*="css"], .stApp {{
        background: {BG};
        color: {TEXT};
        font-family: 'Sora', sans-serif;
    }}
    .main .block-container {{ padding-top: 1.6rem; max-width: 1300px; }}

    section[data-testid="stSidebar"] {{
        background: {SIDEBAR_BG};
        border-right: 1px solid {BORDER};
    }}

    /* ── Sidebar text contrast ───────────────────────────────────────── */
    section[data-testid="stSidebar"] [role="radiogroup"] label,
    section[data-testid="stSidebar"] [role="radiogroup"] label p,
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] a {{
        color: {sidebar_text} !important;
        transition: color 0.15s ease-in-out, font-weight 0.15s ease-in-out;
    }}
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover,
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover p,
    section[data-testid="stSidebar"] a:hover {{
        color: {sidebar_text_active} !important;
    }}
    section[data-testid="stSidebar"] [role="radiogroup"]
        label:has(input:checked),
    section[data-testid="stSidebar"] [role="radiogroup"]
        label:has(input:checked) p {{
        color: {sidebar_text_active} !important;
        font-weight: 600 !important;
    }}

    h1, h2, h3, h4 {{ font-family: 'Sora', sans-serif; font-weight: 800;
                      color: {TEXT}; }}

    .mono {{ font-family: 'JetBrains Mono', monospace; }}

    .panel {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 14px;
        padding: 20px 24px;
        margin-bottom: 14px;
    }}

    .kicker {{
        font-family: 'JetBrains Mono', monospace;
        font-size: {kicker_size}rem;
        letter-spacing: 2.5px;
        color: {MUTED};
        text-transform: uppercase;
    }}

    .big-score {{
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        line-height: 1;
    }}

    .regime-pill {{
        display: inline-block;
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.05rem;
        font-weight: 700;
        letter-spacing: 2px;
        padding: 8px 22px;
        border-radius: 6px;
    }}

    .signal-row {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-left: 3px solid {ACCENT};
        border-radius: 10px;
        padding: 12px 18px;
        margin-bottom: 8px;
    }}

    /* ── BUTTON STYLING (theme-aware) ──────────────────────────────────── */
    .stButton > button,
    .stButton > button[kind="secondary"],
    .stButton > button[data-testid*="secondary"] {{
        background: {btn_bg} !important;
        color: {btn_fg} !important;
        border: 1px solid {btn_bg} !important;
        border-radius: 8px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        font-size: 0.85rem;
        letter-spacing: 0.5px;
        padding: 0.4rem 0.9rem;
        transition: all 0.15s ease;
    }}
    .stButton > button p,
    .stButton > button span,
    .stButton > button div,
    .stButton > button[kind="secondary"] p,
    .stButton > button[kind="secondary"] span,
    .stButton > button[kind="secondary"] div {{
        color: {btn_fg} !important;
        background: transparent !important;
    }}
    .stButton > button:hover,
    .stButton > button[kind="secondary"]:hover,
    .stButton > button[data-testid*="secondary"]:hover {{
        background: {btn_hover} !important;
        border-color: {btn_hover} !important;
        color: {btn_fg} !important;
    }}

    /* Primary buttons — gradient CTAs (same on both themes) */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid*="primary"] {{
        background: linear-gradient(135deg, {ACCENT}, {ACCENT2});
        color: #04121f;
        border: none;
        font-size: 0.92rem;
        font-weight: 700;
        letter-spacing: 1px;
        padding: 0.55rem 1.1rem;
    }}
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid*="primary"]:hover {{
        filter: brightness(1.12);
        background: linear-gradient(135deg, {ACCENT}, {ACCENT2});
        color: #04121f;
        border: none;
    }}

    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input {{
        background: {PANEL_HI};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        font-family: 'JetBrains Mono', monospace;
    }}

    [data-testid="stMetricValue"] {{
        font-family: 'JetBrains Mono', monospace;
        color: {TEXT};
    }}

    .dot {{
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 7px;
    }}

    hr {{ border-color: {BORDER}; }}

    .stDataFrame {{ border: 1px solid {BORDER}; border-radius: 10px; }}

    /* Bordered containers (st.container(border=True)) — soft light card look.
       (The gauge cards get their dark surface from the Plotly figure's own
       paper_bgcolor, not from this CSS, so they don't merge into the page.) */
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background: {PANEL};
        border: 1px solid {BORDER} !important;
        border-radius: 14px;
        box-shadow: {card_shadow};
        transition: box-shadow 0.15s ease, transform 0.15s ease;
    }}
    [data-testid="stVerticalBlockBorderWrapper"]:hover {{
        box-shadow: {card_shadow_hover};
    }}

    /* tag chips */
    [data-baseweb="tag"] {{
        background: {ACCENT} !important;
        border-radius: 6px !important;
    }}

    .tiny {{ font-size: {tiny_size}rem; color: {MUTED};
             font-family: 'JetBrains Mono', monospace;
             line-height: 1.45; }}
    </style>
    """, unsafe_allow_html=True)


def plotly_layout_dark(fig, height=None):
    """Apply universal chart styling matched to the ACTIVE theme.

    (Name kept for backward compatibility with existing callers.) In dark
    mode this is the original light-text-on-transparent styling; in light
    mode it switches to dark text + a light template so charts stay legible
    on a light page. Backgrounds stay transparent so the page surface shows
    through either way.
    """
    if _MODE == "light":
        txt = "#1a2230"
        template = "plotly_white"
    else:
        txt = "#e0e0e0"
        template = "plotly_dark"
    fig.update_layout(
        template=template,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=txt, family="Sora"),
        legend=dict(font=dict(color=txt)),
        margin=dict(l=20, r=20, t=30, b=20),
    )
    fig.update_xaxes(color=txt, title_font=dict(color=txt),
                     tickfont=dict(color=txt))
    fig.update_yaxes(color=txt, title_font=dict(color=txt),
                     tickfont=dict(color=txt))
    try:
        anns = fig.layout.annotations
        if anns:
            for ann in anns:
                if ann.font is None or ann.font.color is None:
                    ann.font = dict(color=txt)
    except (AttributeError, TypeError):
        pass
    if height:
        fig.update_layout(height=height)
    return fig


def horizon_pill_html(horizon: str) -> str:
    """Small badge showing the active Trading Horizon. Sits below the page
    kicker on Pages 1 and 2 so the user can see at a glance which lookback
    family is driving the numbers."""
    # accent the badge by horizon: Swing = ACCENT (cyan-blue), Long-Term = a
    # warmer hue so they're visually distinguishable at a glance
    swing = horizon.lower().startswith("swing")
    color = ACCENT if swing else "#f5c344"
    label = "SWING TRADE SYSTEM" if swing else "LONG-TERM SYSTEM"
    detail = ("Lookbacks: Momentum 10/50 · Volume 5d/20d · RS 20d"
              if swing else
              "Lookbacks: Momentum 50/200 · Volume 20d/60d · RS 60d")
    return (
        f"<div style='display:inline-flex;align-items:center;gap:12px;"
        f"margin:6px 0 14px;padding:8px 18px;border-radius:8px;"
        f"background:{color}1c;border:1px solid {color}55'>"
        f"<span style='font-family:JetBrains Mono;font-size:0.95rem;"
        f"font-weight:700;letter-spacing:1px;color:{color}'>⏱ {label}</span>"
        f"<span style='font-family:JetBrains Mono;font-size:0.88rem;"
        f"color:#a3a8b8'>{detail}</span>"
        f"</div>"
    )
