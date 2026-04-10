"""Web form alternative to the Telegram /bet command.

Pre-fills from URL params (e.g. ?sport=NBA&player=Walker) so the prop
scanner could deep-link directly into a pre-populated form later.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from config import DEFAULT_BANKROLL
from pipeline.auto_calculator import (
    american_to_implied,
    calculate_edge,
    quarter_kelly,
)
from config import USE_REMOTE_DB
from theme import inject_theme
from utils.achievements import check_and_award, format_award_message
from utils.db_factory import get_db
from utils.db_utils import new_id
from utils.gist_sync import push_async

inject_theme("Log Bet — Bet Tracker", "📝")
db = get_db()

st.markdown("## 📝 Log a bet")

if USE_REMOTE_DB:
    st.warning(
        "This cloud dashboard is **read-only** for bet logging. "
        "The authoritative database lives with the Telegram bot — "
        "log bets via `/bet` in Telegram to keep state consistent. "
        "This page still works for previewing the edge/Kelly math below."
    )

users = db.fetch_all(
    "SELECT user_id, username, display_name, bankroll FROM users ORDER BY username"
)
if not users:
    st.warning("Create a user first via the Telegram bot (/start).")
    st.stop()

user = st.selectbox("User", users, format_func=lambda u: u["display_name"])

qp = st.query_params

with st.form("log_bet"):
    c1, c2 = st.columns(2)
    sport = c1.selectbox(
        "Sport", ["NBA", "MLB", "NFL", "NHL", "OTHER"],
        index=["NBA", "MLB", "NFL", "NHL", "OTHER"].index(qp.get("sport", "NBA").upper())
        if qp.get("sport", "NBA").upper() in ["NBA","MLB","NFL","NHL","OTHER"] else 0,
    )
    game = c2.text_input("Game", value=qp.get("game", ""))

    c3, c4 = st.columns(2)
    player = c3.text_input("Player (blank for game bet)", value=qp.get("player", ""))
    prop_type = c4.text_input("Prop type", value=qp.get("prop", "points"))

    c5, c6, c7 = st.columns(3)
    line = c5.number_input("Line", value=float(qp.get("line", 0.5)), step=0.5)
    direction = c6.selectbox("Direction", ["over", "under"])
    book = c7.text_input("Book", value=qp.get("book", "DK"))

    c8, c9, c10 = st.columns(3)
    odds = c8.number_input("Odds (American)", value=int(qp.get("odds", -110)), step=5)
    your_prob = c9.number_input(
        "Your probability %", min_value=0.1, max_value=99.9,
        value=float(qp.get("prob", 55.0)), step=0.5,
    )
    stake = c10.number_input("Stake $", min_value=0.0, value=25.0, step=1.0)

    c11, c12 = st.columns(2)
    niche = c11.text_input("Niche", value=qp.get("niche", "other"))
    injury_context = c12.text_input("Injury context", value="")

    notes = st.text_area("Notes", value="")

    implied = round(american_to_implied(int(odds)), 2)
    edge = calculate_edge(float(your_prob), implied)
    kelly = quarter_kelly(
        float(your_prob), int(odds),
        float(user["bankroll"] or DEFAULT_BANKROLL),
    )
    st.caption(
        f"Implied: {implied:.1f}% • Edge: {edge:+.2f}% • "
        f"Kelly suggests: ${kelly:.2f}"
    )

    submitted = st.form_submit_button(
        "Log bet", type="primary", disabled=USE_REMOTE_DB
    )

if submitted and not USE_REMOTE_DB:
    bet_id = new_id("b_")
    db.execute(
        """
        INSERT INTO bets (
            bet_id, user_id, sport, game, game_date, player, prop_type,
            line, direction, book, odds, implied_prob, your_prob, edge,
            stake, niche, injury_context, notes, result
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')
        """,
        (
            bet_id, user["user_id"], sport, game, date.today().isoformat(),
            player or None, prop_type or None, float(line), direction, book,
            int(odds), implied, float(your_prob), edge, float(stake),
            niche or None, injury_context or None, notes or None,
        ),
    )
    awards = check_and_award(db, user["user_id"], "log")
    msg = f"✅ Bet logged. ID: `{bet_id}`"
    if awards:
        msg += "\n\n" + format_award_message(awards)
    push_async(db)
    st.success(msg)
    st.cache_data.clear()
