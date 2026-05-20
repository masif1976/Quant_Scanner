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


def inject_css():
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
        font-size: 0.7rem;
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
        font-size: 0.95rem;
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

    .stButton > button {{
        background: linear-gradient(135deg, {ACCENT}, {ACCENT2});
        color: #04121f;
        border: none;
        border-radius: 8px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 700;
        letter-spacing: 1px;
        padding: 0.5rem 1rem;
    }}
    .stButton > button:hover {{ filter: brightness(1.12); }}

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

    .tiny {{ font-size: 0.72rem; color: {MUTED};
             font-family: 'JetBrains Mono', monospace; }}
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
