"""
theme.py — Shared dark theme, CSS, and chart styling for the dashboard.
"""

import streamlit as st

BG = "#0b0e17"
PANEL = "#121724"
PANEL_HI = "#1a2133"
BORDER = "#1f2940"
ACCENT = "#3d8bff"
ACCENT2 = "#00d4c8"
TEXT = "#e6ebf5"
MUTED = "#7d8aa5"

GREEN = "#22e08a"
YELLOW = "#f5c344"
ORANGE = "#ff9442"
RED = "#ff5d6c"


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
    """Inject the global stylesheet.

    `text_scale` multiplies the body-text rules (.tiny, .kicker, and the
    related explanatory-text sizes) without touching headings, gauges,
    scores, or tables. Scope A of the text-size feature — solves the
    readability complaint without risking chart/gauge layout breakage.

    Pass 1.0 for default (medium), 0.88 for small, 1.18 for large.
    """
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
        background: #0e131f;
        border-right: 1px solid {BORDER};
    }}

    /* ── Sidebar text contrast ─────────────────────────────────────────
       Make radio labels, page links, and widget labels readable on the
       dark sidebar. Default = light gray; hover = pure white; the
       currently-selected radio item = pure white + bolder weight.
    ─────────────────────────────────────────────────────────────────── */
    section[data-testid="stSidebar"] [role="radiogroup"] label,
    section[data-testid="stSidebar"] [role="radiogroup"] label p,
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] a {{
        color: #A3A8B8 !important;
        transition: color 0.15s ease-in-out, font-weight 0.15s ease-in-out;
    }}
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover,
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover p,
    section[data-testid="stSidebar"] a:hover {{
        color: #FFFFFF !important;
    }}
    /* Radio item whose underlying input is checked = pure white, semibold.
       BaseWeb hides the real radio input via opacity:0 but keeps it in the
       DOM with :checked state, which lets us :has() its parent label. */
    section[data-testid="stSidebar"] [role="radiogroup"]
        label:has(input:checked),
    section[data-testid="stSidebar"] [role="radiogroup"]
        label:has(input:checked) p {{
        color: #FFFFFF !important;
        font-weight: 600 !important;
    }}

    h1, h2, h3, h4 {{ font-family: 'Sora', sans-serif; font-weight: 800; }}

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

    /* ── BUTTON STYLING ────────────────────────────────────────────────
       Per user request: secondary buttons (↻ Refresh) render as black text
       on white background. Maximum readability — no theme color conflicts,
       no dependence on which Streamlit version is rendering the page.

       This explicitly targets the button AND any inner span/p/div elements
       that Streamlit may put inside. Some versions wrap button text in a
       <div data-testid="..."> that has its own background, which is why
       earlier "background: dark !important" attempts still showed white. */
    .stButton > button,
    .stButton > button[kind="secondary"],
    .stButton > button[data-testid*="secondary"] {{
        background: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #ffffff !important;
        border-radius: 8px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        font-size: 0.85rem;
        letter-spacing: 0.5px;
        padding: 0.4rem 0.9rem;
        transition: all 0.15s ease;
    }}
    /* Force text color on any element inside the button — some Streamlit
       versions render the label inside a <div> or <p> with its own color. */
    .stButton > button p,
    .stButton > button span,
    .stButton > button div,
    .stButton > button[kind="secondary"] p,
    .stButton > button[kind="secondary"] span,
    .stButton > button[kind="secondary"] div {{
        color: #000000 !important;
        background: transparent !important;
    }}
    .stButton > button:hover,
    .stButton > button[kind="secondary"]:hover,
    .stButton > button[data-testid*="secondary"]:hover {{
        background: #f0f0f0 !important;
        border-color: #d0d0d0 !important;
        color: #000000 !important;
    }}

    /* Primary buttons — full-width gradient calls-to-action. Match BOTH
       Streamlit's older 'kind="primary"' attribute AND newer DOM patterns. */
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
    }}

    .dot {{
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 7px;
    }}

    hr {{ border-color: {BORDER}; }}

    .stDataFrame {{ border: 1px solid {BORDER}; border-radius: 10px; }}

    /* tag chips */
    [data-baseweb="tag"] {{
        background: {ACCENT} !important;
        border-radius: 6px !important;
    }}

    /* small-text class — bumped from 0.72rem to 0.85rem so it's readable
       on a 13" laptop without browser zoom. Still compact enough to keep
       the dashboard information-dense on larger screens. */
    .tiny {{ font-size: {tiny_size}rem; color: {MUTED};
             font-family: 'JetBrains Mono', monospace;
             line-height: 1.45; }}
    </style>
    """, unsafe_allow_html=True)


def plotly_layout_dark(fig, height=None):
    """
    Apply the universal dark-mode chart styling: plotly_dark template plus
    explicit light (#e0e0e0) text on every axis, tick, legend, and annotation
    so all chart text is legible against the dark background.
    """
    LIGHT = "#e0e0e0"
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=LIGHT, family="Sora"),
        legend=dict(font=dict(color=LIGHT)),
        margin=dict(l=20, r=20, t=30, b=20),
    )
    # force light text on every axis (titles + tick labels), both 2D and subplots
    fig.update_xaxes(color=LIGHT, title_font=dict(color=LIGHT),
                     tickfont=dict(color=LIGHT))
    fig.update_yaxes(color=LIGHT, title_font=dict(color=LIGHT),
                     tickfont=dict(color=LIGHT))
    # any annotations (e.g. chart captions) -> light text
    try:
        anns = fig.layout.annotations
        if anns:
            for ann in anns:
                if ann.font is None or ann.font.color is None:
                    ann.font = dict(color=LIGHT)
    except (AttributeError, TypeError):
        pass  # stub/figure without a layout.annotations accessor
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
