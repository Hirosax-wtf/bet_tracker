"""Achievement definitions and award logic.

The bot calls `check_and_award(db, user_id, trigger)` after every bet log,
resolution, and weekly review. Each check is a small SQL query — cheap
enough to run on every event. Awards are deduped by a unique index on
(user_id, achievement_type) so re-running is safe.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from utils.db_utils import Database, new_id

ACHIEVEMENTS: dict[str, tuple[str, str]] = {
    # Volume
    "first_bet":        ("🎯",  "Logged your first bet"),
    "ten_bets":         ("📝",  "10 bets logged"),
    "fifty_bets":       ("🎰",  "50 bets logged"),
    "hundred_bets":     ("💯",  "100 bets logged"),
    "daily_7":          ("📅",  "Logged bets 7 days in a row"),
    "daily_30":         ("🗓️", "30-day logging streak"),
    # Performance
    "first_win":        ("✅",  "First winning bet"),
    "winning_week":     ("💰",  "Positive P&L for a week"),
    "winning_month":    ("💎",  "Positive P&L for a month"),
    "streak_3":         ("🔥",  "3 wins in a row"),
    "streak_5":         ("🔥🔥","5 wins in a row"),
    "streak_10":        ("👑",  "10 wins in a row"),
    "sharp_eye":        ("📈",  "Avg CLV > +3% over 20 bets"),
    "value_hunter":     ("🎯",  "10 bets with edge > 10%"),
    "perfect_day":      ("⭐",  "All bets win on one day"),
    # Process
    "always_closing":   ("⏱️", "CLV logged for 20 consecutive bets"),
    "calibrated":       ("🧠",  "Prob estimate within 5% of actual hit rate (50 bets)"),
    "niche_master_50":  ("🎓",  "50 bets in one niche"),
    "niche_master_100": ("🏆",  "100 bets in one niche"),
    "line_shopper":     ("📚",  "Used 3+ different books"),
    # Social
    "first_group":      ("👥",  "Joined a betting group"),
    "group_leader":     ("👑",  "Top of group leaderboard"),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _has(db: Database, user_id: str, key: str) -> bool:
    row = db.fetch_one(
        "SELECT 1 FROM achievements WHERE user_id=? AND achievement_type=?",
        (user_id, key),
    )
    return row is not None


def _award(db: Database, user_id: str, key: str) -> tuple[str, str] | None:
    if key not in ACHIEVEMENTS or _has(db, user_id, key):
        return None
    icon, desc = ACHIEVEMENTS[key]
    db.execute(
        """
        INSERT OR IGNORE INTO achievements
            (achievement_id, user_id, achievement_type, description, icon)
        VALUES (?, ?, ?, ?, ?)
        """,
        (new_id("a_"), user_id, key, desc, icon),
    )
    return icon, desc


# ---------------------------------------------------------------------------
# Stat queries (also used by /record and dashboard)
# ---------------------------------------------------------------------------
def total_bets(db: Database, user_id: str) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS n FROM bets WHERE user_id=?", (user_id,)
    )
    return int(row["n"]) if row else 0


def current_win_streak(db: Database, user_id: str) -> int:
    rows = db.fetch_all(
        """
        SELECT result FROM bets
        WHERE user_id=? AND result IN ('win','loss')
        ORDER BY COALESCE(resolved_at, created_at) DESC
        LIMIT 50
        """,
        (user_id,),
    )
    streak = 0
    for r in rows:
        if r["result"] == "win":
            streak += 1
        else:
            break
    return streak


def consecutive_logging_days(db: Database, user_id: str) -> int:
    rows = db.fetch_all(
        """
        SELECT DISTINCT DATE(created_at, 'localtime') AS d FROM bets
        WHERE user_id=? ORDER BY d DESC LIMIT 60
        """,
        (user_id,),
    )
    if not rows:
        return 0
    days = [datetime.fromisoformat(r["d"]).date() for r in rows]
    today = date.today()
    # Allow streak to start "yesterday" (user hasn't bet today yet).
    if days[0] != today and days[0] != today - timedelta(days=1):
        return 0
    streak = 1
    for i in range(1, len(days)):
        if (days[i - 1] - days[i]).days == 1:
            streak += 1
        else:
            break
    return streak


def avg_clv_recent(db: Database, user_id: str, n: int = 20) -> tuple[float, int]:
    rows = db.fetch_all(
        """
        SELECT clv FROM bets
        WHERE user_id=? AND clv IS NOT NULL
        ORDER BY COALESCE(resolved_at, created_at) DESC LIMIT ?
        """,
        (user_id, n),
    )
    if not rows:
        return 0.0, 0
    vals = [r["clv"] for r in rows]
    return sum(vals) / len(vals), len(vals)


def consecutive_closing_logged(db: Database, user_id: str) -> int:
    rows = db.fetch_all(
        """
        SELECT closing_odds FROM bets
        WHERE user_id=?
        ORDER BY created_at DESC LIMIT 50
        """,
        (user_id,),
    )
    streak = 0
    for r in rows:
        if r["closing_odds"] is not None:
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def check_and_award(
    db: Database, user_id: str, trigger: str = "any"
) -> list[tuple[str, str]]:
    """Run all relevant checks and return any newly awarded (icon, desc)."""
    awarded: list[tuple[str, str]] = []

    def _try(key: str, condition: bool) -> None:
        if condition:
            result = _award(db, user_id, key)
            if result:
                awarded.append(result)

    n = total_bets(db, user_id)
    _try("first_bet", n >= 1)
    _try("ten_bets", n >= 10)
    _try("fifty_bets", n >= 50)
    _try("hundred_bets", n >= 100)

    log_streak = consecutive_logging_days(db, user_id)
    _try("daily_7", log_streak >= 7)
    _try("daily_30", log_streak >= 30)

    # Performance — only meaningful when something is resolved
    win_row = db.fetch_one(
        "SELECT 1 FROM bets WHERE user_id=? AND result='win' LIMIT 1",
        (user_id,),
    )
    _try("first_win", win_row is not None)

    streak = current_win_streak(db, user_id)
    _try("streak_3", streak >= 3)
    _try("streak_5", streak >= 5)
    _try("streak_10", streak >= 10)

    avg_clv, clv_n = avg_clv_recent(db, user_id, 20)
    _try("sharp_eye", clv_n >= 20 and avg_clv > 3.0)

    edge_count_row = db.fetch_one(
        "SELECT COUNT(*) AS n FROM bets WHERE user_id=? AND edge > 10",
        (user_id,),
    )
    _try("value_hunter", edge_count_row and edge_count_row["n"] >= 10)

    # Perfect day — every resolved bet today is a win
    today = date.today().isoformat()
    pd_row = db.fetch_one(
        """
        SELECT
          SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS w,
          SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END) AS rs
        FROM bets WHERE user_id=? AND DATE(created_at, 'localtime')=?
        """,
        (user_id, today),
    )
    if pd_row and pd_row["rs"] and pd_row["rs"] >= 1 and pd_row["w"] == pd_row["rs"]:
        _try("perfect_day", True)

    # Process
    _try("always_closing", consecutive_closing_logged(db, user_id) >= 20)

    niche_row = db.fetch_one(
        """
        SELECT niche, COUNT(*) AS n FROM bets
        WHERE user_id=? AND niche IS NOT NULL
        GROUP BY niche ORDER BY n DESC LIMIT 1
        """,
        (user_id,),
    )
    if niche_row:
        _try("niche_master_50", niche_row["n"] >= 50)
        _try("niche_master_100", niche_row["n"] >= 100)

    book_row = db.fetch_one(
        "SELECT COUNT(DISTINCT book) AS n FROM bets WHERE user_id=?",
        (user_id,),
    )
    _try("line_shopper", book_row and book_row["n"] >= 3)

    # Calibration — needs at least 50 resolved bets
    calib_row = db.fetch_one(
        """
        SELECT AVG(your_prob) AS avg_p,
               AVG(CASE WHEN result='win' THEN 100.0 ELSE 0 END) AS hit_rate,
               COUNT(*) AS n
        FROM bets
        WHERE user_id=? AND result IN ('win','loss')
        """,
        (user_id,),
    )
    if (
        calib_row
        and calib_row["n"] >= 50
        and calib_row["avg_p"] is not None
        and abs(calib_row["avg_p"] - calib_row["hit_rate"]) <= 5
    ):
        _try("calibrated", True)

    # Group membership
    grp_row = db.fetch_one(
        "SELECT 1 FROM group_members WHERE user_id=? LIMIT 1", (user_id,)
    )
    _try("first_group", grp_row is not None)

    return awarded


def format_award_message(awards: Iterable[tuple[str, str]]) -> str:
    """Pretty-print newly earned achievements for a Telegram reply."""
    awards = list(awards)
    if not awards:
        return ""
    lines = ["🏅 New achievement!" if len(awards) == 1 else "🏅 New achievements!"]
    for icon, desc in awards:
        lines.append(f"  {icon} {desc}")
    return "\n".join(lines)
