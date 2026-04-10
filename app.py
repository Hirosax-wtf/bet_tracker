"""Bet Tracker — Streamlit homepage.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable when Streamlit runs from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from theme import inject_theme
from utils.db_factory import get_db
from utils.stats import leaderboard
from config import LEADERBOARD_MIN_BETS

inject_theme("Bet Tracker", "🎯")
db = get_db()

st.markdown(
    "<h1 style='font-family:Outfit,sans-serif;font-weight:800;'>"
    "🎯 Bet Tracker</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='font-size:18px;color:rgba(255,255,255,0.65);max-width:680px;'>"
    "Track every bet, calculate edge, follow your CLV, and compete on the "
    "leaderboard. Log bets via the Telegram bot or the <b>Log Bet</b> page."
    "</p>",
    unsafe_allow_html=True,
)

st.divider()

col1, col2 = st.columns(2)
with col1:
    st.subheader("Get started")
    st.markdown(
        "1. Open the **Bet Tracker** Telegram bot and send `/start`\n"
        "2. Pick a username\n"
        "3. Log your first bet with `/bet`\n"
        "4. View your stats here on the **Dashboard** page"
    )
with col2:
    st.subheader("Why CLV matters")
    st.markdown(
        "Closing line value (CLV) measures whether you got a better number "
        "than the market closed at. **Long-run CLV is the only proof of "
        "edge** — win rate alone is just variance. Log closing odds with "
        "`/closing <bet_id> <odds>` after each game."
    )

st.divider()
st.subheader("🏆 Top performers")

@st.cache_data(ttl=60)
def _top_users() -> list[dict]:
    # Show users with any bets on homepage; full leaderboard page uses stricter minimum
    return leaderboard(db, min_bets=1)[:5]

rows = _top_users()
if not rows:
    st.info(
        "No users with logged bets yet — "
        "log bets to be the first on the leaderboard."
    )
else:
    for i, r in enumerate(rows, 1):
        c1, c2, c3, c4 = st.columns([0.5, 2, 1, 1])
        c1.markdown(f"### {i}")
        c2.markdown(f"**{r['display_name']}**  \n{r['wins']}W-{r['losses']}L")
        c3.metric("ROI", f"{r['roi']:+.1f}%")
        c4.metric("CLV", f"{r['avg_clv']:+.2f}%")
