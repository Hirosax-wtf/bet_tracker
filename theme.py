"""Shared visual theme for the Bet Tracker Streamlit dashboard.

Mirrors the color tokens and font choices used by sports_dashboard so
the two apps feel like they belong to the same product family. Each page
calls inject_theme() once at the top.
"""
from __future__ import annotations

import streamlit as st


THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;500;700&family=Outfit:wght@600;700;800&display=swap');

:root {
  --bg: #0C0C0E;
  --accent: #00BAFF;
  --accent-dim: rgba(0, 186, 255, 0.10);
  --accent-border: rgba(0, 186, 255, 0.30);
  --card-bg: rgba(255, 255, 255, 0.02);
  --card-border: rgba(255, 255, 255, 0.06);
  --text-primary: #ffffff;
  --text-secondary: rgba(255, 255, 255, 0.7);
  --text-muted: rgba(255, 255, 255, 0.45);
  --win-green: #4ADE80;
  --loss-red: #F87171;
  --push-yellow: #FBBF24;
  --divider: rgba(255, 255, 255, 0.05);
}

.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background-color: var(--bg) !important;
}
[data-testid="stSidebar"] {
  background-color: #111113 !important;
  border-right: 1px solid var(--divider) !important;
}

.stApp, .stMarkdown, p, li {
  font-family: 'DM Sans', sans-serif !important;
  color: var(--text-secondary) !important;
}
h1, h2, h3 {
  font-family: 'Outfit', sans-serif !important;
  color: var(--text-primary) !important;
  font-weight: 700 !important;
}

[data-testid="stMetric"] {
  background: var(--card-bg) !important;
  border: 1px solid var(--card-border) !important;
  border-radius: 14px !important;
  padding: 18px !important;
}
[data-testid="stMetricValue"] {
  color: var(--text-primary) !important;
  font-weight: 700 !important;
}
[data-testid="stMetricLabel"] {
  color: var(--text-muted) !important;
  text-transform: uppercase !important;
  font-size: 11px !important;
  letter-spacing: 1.2px !important;
}

div[data-testid="stDataFrame"] {
  border: 1px solid var(--card-border) !important;
  border-radius: 12px !important;
  background: var(--card-bg) !important;
}

button[kind="primary"] {
  background: var(--accent) !important;
  color: #0C0C0E !important;
  font-weight: 700 !important;
  border-radius: 10px !important;
}

.bt-badge {
  display: inline-block;
  padding: 6px 12px;
  margin: 4px 6px 4px 0;
  border-radius: 999px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  font-size: 13px;
  font-family: 'DM Sans', sans-serif;
}
.bt-badge.earned { border-color: var(--accent-border); background: var(--accent-dim); color: #fff; }
.bt-badge.locked { color: var(--text-muted); opacity: 0.55; }

.bt-streak {
  display: inline-block;
  padding: 10px 18px;
  border-radius: 14px;
  background: rgba(251, 146, 60, 0.10);
  border: 1px solid rgba(251, 146, 60, 0.35);
  color: #FDBA74 !important;
  font-weight: 700;
  font-family: 'Outfit', sans-serif;
}
</style>
"""


def inject_theme(page_title: str, page_icon: str = "🎯") -> None:
    st.set_page_config(
        page_title=page_title,
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def pnl_color(value: float | None) -> str:
    if value is None:
        return "var(--text-muted)"
    if value > 0:
        return "var(--win-green)"
    if value < 0:
        return "var(--loss-red)"
    return "var(--text-secondary)"
