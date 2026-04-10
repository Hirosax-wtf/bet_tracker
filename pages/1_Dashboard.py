"""Personal stats dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from theme import inject_theme, pnl_color
from utils.achievements import (
    ACHIEVEMENTS,
    consecutive_logging_days,
    current_win_streak,
)
from utils.db_factory import get_db
from utils.stats import (
    bets_all,
    cumulative_pnl_series,
    hit_rate_by_prop,
    pending_bets,
    roi_by_niche,
    rolling_clv,
    stats_alltime,
)

inject_theme("Dashboard — Bet Tracker", "📊")
db = get_db()

# ---------------------------------------------------------------------------
# User picker
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30)
def _users() -> list[dict]:
    return [dict(r) for r in db.fetch_all(
        "SELECT user_id, username, display_name FROM users ORDER BY username"
    )]

users = _users()
if not users:
    st.warning("No users yet. Start the Telegram bot and run /start.")
    st.stop()

# Pre-select via ?user=username if present
qp = st.query_params
default_idx = 0
if "user" in qp:
    for i, u in enumerate(users):
        if u["username"] == qp["user"]:
            default_idx = i
            break

choice = st.sidebar.selectbox(
    "User", users, format_func=lambda u: u["display_name"], index=default_idx
)
user_id = choice["user_id"]

st.markdown(f"## 📊 {choice['display_name']}'s dashboard")

# ---------------------------------------------------------------------------
# Row 1 — KPI cards
# ---------------------------------------------------------------------------
s = stats_alltime(db, user_id)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Record", f"{s['wins']}-{s['losses']}-{s['pushes']}",
          f"{s['win_rate']:.1f}% win rate")
c2.metric("Total P&L", f"${s['total_pnl']:+,.2f}")
c3.metric("ROI", f"{s['roi']:+.2f}%")
c4.metric("Avg CLV", f"{s['avg_clv']:+.2f}%",
          help=f"Across {s['clv_count']} bets with closing odds logged")

st.divider()

# ---------------------------------------------------------------------------
# Row 2 — Cum P&L + ROI by niche
# ---------------------------------------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("Cumulative P&L")
    series = cumulative_pnl_series(db, user_id)
    if series:
        df = pd.DataFrame(series)
        df["ts"] = pd.to_datetime(df["ts"])
        line_color = "#4ADE80" if df["cum_pnl"].iloc[-1] >= 0 else "#F87171"
        fig = px.area(
            df, x="ts", y="cum_pnl",
            color_discrete_sequence=[line_color],
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="rgba(255,255,255,0.75)",
            xaxis_title=None, yaxis_title=None,
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No resolved bets yet.")

with right:
    st.subheader("ROI by niche")
    nrows = roi_by_niche(db, user_id)
    if nrows:
        df = pd.DataFrame(nrows).sort_values("roi", ascending=True)
        fig = px.bar(
            df, x="roi", y="niche", orientation="h",
            color="roi",
            color_continuous_scale=[(0, "#F87171"), (0.5, "#9CA3AF"), (1, "#4ADE80")],
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="rgba(255,255,255,0.75)",
            xaxis_title="ROI %", yaxis_title=None,
            coloraxis_showscale=False,
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No niche data yet.")

# ---------------------------------------------------------------------------
# Row 3 — Rolling CLV
# ---------------------------------------------------------------------------
st.subheader("Rolling 20-bet CLV")
clv_rows = rolling_clv(db, user_id, window=20)
if clv_rows:
    df = pd.DataFrame(clv_rows)
    df["ts"] = pd.to_datetime(df["ts"])
    last_clv = df["rolling_clv"].iloc[-1]
    color = "#4ADE80" if last_clv >= 0 else "#F87171"
    fig = go.Figure()
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.2)")
    fig.add_trace(go.Scatter(
        x=df["ts"], y=df["rolling_clv"],
        mode="lines", line=dict(color=color, width=3),
        fill="tozeroy", fillcolor=color.replace(")", ", 0.10)").replace("rgb", "rgba"),
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="rgba(255,255,255,0.75)",
        xaxis_title=None, yaxis_title="CLV %",
        height=240,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("Log closing odds with /closing to populate this chart.")

# ---------------------------------------------------------------------------
# Row 4 — Hit rate by prop type
# ---------------------------------------------------------------------------
st.subheader("Hit rate by prop type")
prows = hit_rate_by_prop(db, user_id)
if prows:
    df = pd.DataFrame([p for p in prows if p["decided"] >= 1])
    if not df.empty:
        df = df.sort_values("hit_rate", ascending=True)
        fig = px.bar(
            df, x="hit_rate", y="prop_type", orientation="h",
            color="hit_rate",
            color_continuous_scale=[(0, "#F87171"), (0.5, "#9CA3AF"), (1, "#4ADE80")],
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="rgba(255,255,255,0.75)",
            xaxis_title="Hit rate %", yaxis_title=None,
            coloraxis_showscale=False,
            height=260,
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Row 5 — Achievements
# ---------------------------------------------------------------------------
st.subheader("🏅 Achievements")
earned_rows = db.fetch_all(
    "SELECT achievement_type FROM achievements WHERE user_id=?", (user_id,)
)
earned = {r["achievement_type"] for r in earned_rows}
badge_html = ""
for key, (icon, desc) in ACHIEVEMENTS.items():
    cls = "earned" if key in earned else "locked"
    lock = "" if key in earned else "🔒 "
    badge_html += (
        f'<span class="bt-badge {cls}" title="{desc}">{lock}{icon} {desc}</span>'
    )
st.markdown(badge_html, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Row 6 — Streak
# ---------------------------------------------------------------------------
streak = current_win_streak(db, user_id)
log_streak = consecutive_logging_days(db, user_id)
st.markdown(
    f'<div style="margin-top:14px;">'
    f'<span class="bt-streak">🔥 {streak}W win streak</span>'
    f'&nbsp;&nbsp;<span class="bt-streak">📅 {log_streak}d logging streak</span>'
    f'</div>',
    unsafe_allow_html=True,
)

st.divider()

# ---------------------------------------------------------------------------
# Row 7 — Last 20 bets
# ---------------------------------------------------------------------------
st.subheader("Last 20 bets")
all_bets = bets_all(db, user_id)[:20]
if all_bets:
    df = pd.DataFrame([dict(r) for r in all_bets])
    show_cols = [
        "created_at", "game", "player", "prop_type", "line", "direction",
        "odds", "your_prob", "edge", "stake", "clv", "result", "pnl",
    ]
    show_cols = [c for c in show_cols if c in df.columns]
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
else:
    st.caption("No bets yet.")

# ---------------------------------------------------------------------------
# Row 8 — Pending
# ---------------------------------------------------------------------------
st.subheader("Pending bets")
pend = pending_bets(db, user_id)
if pend:
    df = pd.DataFrame([dict(r) for r in pend])
    show_cols = [c for c in [
        "bet_id", "game", "player", "prop_type", "line", "direction",
        "odds", "stake",
    ] if c in df.columns]
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
    st.caption("Resolve from Telegram with `/close <bet_id> <actual>`")
else:
    st.caption("No pending bets.")
