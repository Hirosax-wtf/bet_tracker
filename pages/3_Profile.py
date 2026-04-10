"""Public profile page — read by visiting ?user=<username>."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from theme import inject_theme
from utils.achievements import ACHIEVEMENTS, current_win_streak
from utils.db_factory import get_db
from utils.stats import bets_all, roi_by_niche, stats_alltime

inject_theme("Profile — Bet Tracker", "👤")
db = get_db()

qp = st.query_params
username = qp.get("user")
if not username:
    users = db.fetch_all(
        "SELECT username FROM users WHERE is_public=1 ORDER BY username"
    )
    if not users:
        st.info("No public users yet.")
        st.stop()
    username = st.selectbox("User", [u["username"] for u in users])

user = db.fetch_one(
    "SELECT * FROM users WHERE username=? AND is_public=1", (username,)
)
if not user:
    st.error("User not found or profile is private.")
    st.stop()

st.markdown(f"## 👤 {user['display_name'] or user['username']}")
st.caption(f"@{user['username']}")

s = stats_alltime(db, user["user_id"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("Record", f"{s['wins']}-{s['losses']}-{s['pushes']}")
c2.metric("ROI", f"{s['roi']:+.2f}%")
c3.metric("Avg CLV", f"{s['avg_clv']:+.2f}%")
c4.metric("Streak", f"{current_win_streak(db, user['user_id'])}W")

st.divider()
st.subheader("🏅 Achievements")
earned = {
    r["achievement_type"]
    for r in db.fetch_all(
        "SELECT achievement_type FROM achievements WHERE user_id=?",
        (user["user_id"],),
    )
}
badge_html = ""
for key, (icon, desc) in ACHIEVEMENTS.items():
    if key in earned:
        badge_html += f'<span class="bt-badge earned">{icon} {desc}</span>'
st.markdown(badge_html or "<i>None earned yet.</i>", unsafe_allow_html=True)

st.subheader("Best niche")
niches = roi_by_niche(db, user["user_id"])
if niches:
    best = max(niches, key=lambda n: n["roi"])
    st.markdown(
        f"**{best['niche']}** — {best['win_rate']:.0f}% win rate, "
        f"{best['roi']:+.1f}% ROI over {best['n']} bets"
    )

st.subheader("Recent bets")
recent = bets_all(db, user["user_id"])[:15]
if recent:
    df = pd.DataFrame([dict(r) for r in recent])
    show_cols = [c for c in [
        "created_at", "game", "player", "prop_type", "line", "direction",
        "odds", "result", "pnl",
    ] if c in df.columns]
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
