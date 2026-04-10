"""Leaderboard — global and group views."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from config import LEADERBOARD_MIN_BETS
from theme import inject_theme
from utils.achievements import current_win_streak
from utils.db_factory import get_db
from utils.stats import leaderboard, roi_by_niche

inject_theme("Leaderboard — Bet Tracker", "🏆")
db = get_db()

st.markdown("## 🏆 Leaderboard")

tab_global, tab_groups = st.tabs(["🌍 Global", "👥 Groups"])


def _to_table(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows, 1):
        niches = roi_by_niche(db, r["user_id"])
        best = max(niches, key=lambda n: n["roi"], default=None)
        out.append({
            "Rank": i,
            "Username": r["display_name"],
            "Record": f"{r['wins']}-{r['losses']}",
            "ROI %": round(r["roi"], 2),
            "Avg CLV %": round(r["avg_clv"], 2),
            "Streak": current_win_streak(db, r["user_id"]),
            "Best niche": best["niche"] if best else "—",
        })
    return pd.DataFrame(out)


with tab_global:
    rows = leaderboard(db, min_bets=LEADERBOARD_MIN_BETS)
    if not rows:
        st.info(
            f"Nobody has logged {LEADERBOARD_MIN_BETS}+ bets yet. "
            "Be the first!"
        )
    else:
        st.dataframe(_to_table(rows), use_container_width=True, hide_index=True)

with tab_groups:
    groups = db.fetch_all("SELECT group_id, group_name FROM groups ORDER BY group_name")
    if not groups:
        st.info("No groups yet. Create one with `/group create <name>` in Telegram.")
    else:
        choice = st.selectbox(
            "Group", groups, format_func=lambda g: g["group_name"]
        )
        gid = choice["group_id"]
        rows = leaderboard(db, min_bets=1, group_id=gid)
        if not rows:
            st.caption("No members with bets yet.")
        else:
            st.dataframe(_to_table(rows), use_container_width=True, hide_index=True)
