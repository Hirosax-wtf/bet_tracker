"""Aggregate stat queries shared by Telegram bot and Streamlit dashboard.

Keeping these in one place means /today, /week, /record and the dashboard
metric cards are guaranteed to agree.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from utils.db_utils import Database


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row) if row else {}


def _summarize(rows: list) -> dict[str, Any]:
    """Compute aggregate stats from a list of bet rows."""
    wins = losses = pushes = pending = 0
    staked = pnl = 0.0
    clvs: list[float] = []
    for r in rows:
        result = r["result"]
        if result == "win":
            wins += 1
        elif result == "loss":
            losses += 1
        elif result == "push":
            pushes += 1
        else:
            pending += 1
        staked += float(r["stake"] or 0)
        if r["pnl"] is not None:
            pnl += float(r["pnl"])
        if r["clv"] is not None:
            clvs.append(float(r["clv"]))

    resolved = wins + losses + pushes
    decided = wins + losses
    return {
        "total": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "pending": pending,
        "resolved": resolved,
        "win_rate": (wins / decided * 100) if decided else 0.0,
        "total_staked": round(staked, 2),
        "total_pnl": round(pnl, 2),
        "roi": (pnl / staked * 100) if staked else 0.0,
        "avg_clv": (sum(clvs) / len(clvs)) if clvs else 0.0,
        "clv_count": len(clvs),
    }


# ---------------------------------------------------------------------------
# Range queries
# ---------------------------------------------------------------------------
def bets_in_range(
    db: Database, user_id: str, start: date, end: date
) -> list:
    # SQLite stores CURRENT_TIMESTAMP as UTC; convert to local before
    # comparing so /today and /week match the user's wall-clock day.
    return db.fetch_all(
        """
        SELECT * FROM bets
        WHERE user_id=?
          AND DATE(created_at, 'localtime') >= ?
          AND DATE(created_at, 'localtime') <= ?
        ORDER BY created_at DESC
        """,
        (user_id, start.isoformat(), end.isoformat()),
    )


def bets_all(db: Database, user_id: str) -> list:
    return db.fetch_all(
        "SELECT * FROM bets WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    )


def stats_today(db: Database, user_id: str) -> dict[str, Any]:
    today = date.today()
    return _summarize(bets_in_range(db, user_id, today, today))


def stats_week(db: Database, user_id: str, anchor: date | None = None) -> dict[str, Any]:
    """Stats for the ISO week containing `anchor` (defaults to today)."""
    a = anchor or date.today()
    start = a - timedelta(days=a.weekday())  # Monday
    end = start + timedelta(days=6)
    summary = _summarize(bets_in_range(db, user_id, start, end))
    summary["week_start"] = start
    summary["week_end"] = end
    return summary


def stats_alltime(db: Database, user_id: str) -> dict[str, Any]:
    return _summarize(bets_all(db, user_id))


def pending_bets(db: Database, user_id: str) -> list:
    return db.fetch_all(
        """
        SELECT * FROM bets WHERE user_id=? AND result='pending'
        ORDER BY game_date ASC, created_at ASC
        """,
        (user_id,),
    )


# ---------------------------------------------------------------------------
# Niche / prop breakdowns
# ---------------------------------------------------------------------------
def roi_by_niche(db: Database, user_id: str) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT niche,
               COUNT(*) AS n,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END) AS decided,
               SUM(stake) AS staked,
               SUM(COALESCE(pnl,0)) AS pnl
        FROM bets
        WHERE user_id=? AND niche IS NOT NULL
        GROUP BY niche
        """,
        (user_id,),
    )
    out = []
    for r in rows:
        staked = float(r["staked"] or 0)
        pnl = float(r["pnl"] or 0)
        decided = int(r["decided"] or 0)
        out.append(
            {
                "niche": r["niche"],
                "n": int(r["n"]),
                "wins": int(r["w"] or 0),
                "decided": decided,
                "win_rate": (r["w"] / decided * 100) if decided else 0.0,
                "staked": round(staked, 2),
                "pnl": round(pnl, 2),
                "roi": (pnl / staked * 100) if staked else 0.0,
            }
        )
    return out


def hit_rate_by_prop(db: Database, user_id: str) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT prop_type,
               COUNT(*) AS n,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END) AS decided
        FROM bets
        WHERE user_id=? AND prop_type IS NOT NULL
        GROUP BY prop_type
        """,
        (user_id,),
    )
    return [
        {
            "prop_type": r["prop_type"],
            "n": int(r["n"]),
            "wins": int(r["w"] or 0),
            "decided": int(r["decided"] or 0),
            "hit_rate": (r["w"] / r["decided"] * 100) if r["decided"] else 0.0,
        }
        for r in rows
    ]


def cumulative_pnl_series(db: Database, user_id: str) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT created_at, COALESCE(pnl,0) AS pnl
        FROM bets
        WHERE user_id=? AND result IN ('win','loss','push')
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    running = 0.0
    out = []
    for r in rows:
        running += float(r["pnl"])
        out.append({"ts": r["created_at"], "cum_pnl": round(running, 2)})
    return out


def rolling_clv(db: Database, user_id: str, window: int = 20) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT created_at, clv FROM bets
        WHERE user_id=? AND clv IS NOT NULL
        ORDER BY created_at ASC
        """,
        (user_id,),
    )
    vals = [(r["created_at"], float(r["clv"])) for r in rows]
    out = []
    for i in range(len(vals)):
        lo = max(0, i - window + 1)
        chunk = [v for _, v in vals[lo : i + 1]]
        out.append({"ts": vals[i][0], "rolling_clv": sum(chunk) / len(chunk)})
    return out


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def leaderboard(
    db: Database, *, min_bets: int = 20, group_id: str | None = None
) -> list[dict[str, Any]]:
    if group_id:
        sql = """
            SELECT u.user_id, u.username, u.display_name
            FROM users u
            JOIN group_members gm ON gm.user_id = u.user_id
            WHERE gm.group_id = ?
        """
        users = db.fetch_all(sql, (group_id,))
    else:
        users = db.fetch_all(
            "SELECT user_id, username, display_name FROM users WHERE is_public=1"
        )

    rows = []
    for u in users:
        s = stats_alltime(db, u["user_id"])
        if s["total"] < min_bets:
            continue
        rows.append(
            {
                "user_id": u["user_id"],
                "username": u["username"],
                "display_name": u["display_name"] or u["username"],
                **s,
            }
        )
    rows.sort(key=lambda r: r["roi"], reverse=True)
    return rows
