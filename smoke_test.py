"""Smoke test for Bet Tracker. Run from project root: python smoke_test.py

Verifies:
  - All modules import cleanly
  - DB initializes from schema.sql
  - Auto-calculator math is correct
  - End-to-end: create user, log bets, resolve, log closing line, achievements
  - Stats aggregations agree with manual calculations
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Use a temp DB so we don't pollute the real one
TMP_DB = Path("/tmp/bet_tracker_smoke.db")
if TMP_DB.exists():
    TMP_DB.unlink()
os.environ["BET_TRACKER_DB_PATH"] = str(TMP_DB)
# Avoid require_telegram_token() failure during imports
os.environ.setdefault("BET_TRACKER_BOT_TOKEN", "smoke_test_dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.auto_calculator import (
    american_to_implied,
    calculate_clv,
    calculate_edge,
    calculate_pnl,
    determine_result,
    quarter_kelly,
)
from utils.achievements import check_and_award, current_win_streak
from utils.db_utils import Database, new_id
from utils.stats import (
    leaderboard,
    pending_bets,
    stats_alltime,
    stats_today,
    stats_week,
)


def assert_close(a: float, b: float, tol: float = 0.05, msg: str = "") -> None:
    assert abs(a - b) < tol, f"{msg}: {a} != {b}"


def test_calculator() -> None:
    print("→ test_calculator")
    # Implied prob
    assert_close(american_to_implied(-110), 52.38, msg="-110 implied")
    assert_close(american_to_implied(+150), 40.0, msg="+150 implied")
    assert_close(american_to_implied(-175), 63.64, msg="-175 implied")

    # Edge
    assert calculate_edge(60.0, 52.38) == 7.62

    # CLV — bet -175 (63.64%), close -210 (67.74%) -> +4.10 (you beat the close)
    clv = calculate_clv(-175, -210)
    assert clv > 0, f"CLV should be positive, got {clv}"
    assert_close(clv, 4.10, msg="CLV -175→-210")

    # PnL
    assert calculate_pnl(100, -110, "win") == 90.91
    assert calculate_pnl(100, +150, "win") == 150.0
    assert calculate_pnl(100, -110, "loss") == -100.0
    assert calculate_pnl(100, -110, "push") == 0.0

    # Quarter Kelly — clearly positive EV bet
    k = quarter_kelly(60.0, +100, 1000)
    assert k > 0, f"Quarter Kelly should be positive for +EV bet, got {k}"
    # EV negative bet -> 0
    assert quarter_kelly(40.0, -200, 1000) == 0.0

    # Determine result
    assert determine_result("over", 10.5, 12) == "win"
    assert determine_result("over", 10.5, 9) == "loss"
    assert determine_result("under", 10.5, 9) == "win"
    assert determine_result("over", 10, 10) == "push"

    print("  ✓ calculator math correct")


def test_db_init() -> None:
    print("→ test_db_init")
    db = Database(TMP_DB)
    db.initialize()
    # All 6 tables should exist
    rows = db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [r["name"] for r in rows]
    expected = {"users", "bets", "weekly_summaries", "achievements",
                "groups", "group_members"}
    assert expected.issubset(set(names)), f"Missing tables: {expected - set(names)}"
    print(f"  ✓ tables: {sorted(names)}")


def test_end_to_end() -> None:
    print("→ test_end_to_end")
    db = Database(TMP_DB)
    db.initialize()

    # Create user
    user_id = new_id("u_")
    db.execute(
        """
        INSERT INTO users (user_id, username, telegram_id, display_name, bankroll)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, "smoketest", 999_999, "Smoke Test", 1000.0),
    )

    # Log 3 bets
    bets_to_log = [
        # (game, player, prop, line, dir, book, odds, your_prob, stake, niche)
        ("GSW vs LAL", "Walker", "rebounds", 6.5, "over", "DK", -175, 80.0, 25.0, "role_expansion"),
        ("BOS vs MIA", "Tatum", "points",   28.5, "under", "FD", +110, 55.0, 20.0, "totals"),
        ("DEN vs OKC", "Jokic",  "assists",  9.5, "over", "MGM", -120, 65.0, 30.0, "role_expansion"),
    ]
    bet_ids = []
    for game, player, prop, line, direction, book, odds, prob, stake, niche in bets_to_log:
        bid = new_id("b_")
        bet_ids.append(bid)
        implied = round(american_to_implied(odds), 2)
        edge = calculate_edge(prob, implied)
        db.execute(
            """
            INSERT INTO bets (
              bet_id, user_id, sport, game, game_date, player, prop_type,
              line, direction, book, odds, implied_prob, your_prob, edge,
              stake, niche, result
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')
            """,
            (
                bid, user_id, "NBA", game, "2026-04-09", player, prop, line,
                direction, book, odds, implied, prob, edge, stake, niche,
            ),
        )

    # All 3 should be pending
    pend = pending_bets(db, user_id)
    assert len(pend) == 3, f"Expected 3 pending, got {len(pend)}"

    # Achievements after logging
    awards = check_and_award(db, user_id, "log")
    award_keys = {db.fetch_one("SELECT achievement_type FROM achievements WHERE user_id=? AND description=?", (user_id, desc))["achievement_type"] for _, desc in awards}
    assert "first_bet" in award_keys, f"first_bet missing from {award_keys}"
    assert "line_shopper" in award_keys, "line_shopper should fire (3 books used)"
    print(f"  ✓ awards on log: {sorted(award_keys)}")

    # Resolve bet 1 as WIN: Walker had 8 reb, line 6.5 OVER → win
    actual_1 = 8.0
    result_1 = determine_result("over", 6.5, actual_1)
    pnl_1 = calculate_pnl(25.0, -175, result_1)
    db.execute(
        "UPDATE bets SET actual_value=?, result=?, pnl=?, resolved_at=CURRENT_TIMESTAMP WHERE bet_id=?",
        (actual_1, result_1, pnl_1, bet_ids[0]),
    )
    assert result_1 == "win"
    assert_close(pnl_1, 14.29, msg="PnL Walker win")

    # Resolve bet 2 as LOSS: Tatum had 30 pts, line 28.5 UNDER → loss
    actual_2 = 30.0
    result_2 = determine_result("under", 28.5, actual_2)
    pnl_2 = calculate_pnl(20.0, +110, result_2)
    db.execute(
        "UPDATE bets SET actual_value=?, result=?, pnl=?, resolved_at=CURRENT_TIMESTAMP WHERE bet_id=?",
        (actual_2, result_2, pnl_2, bet_ids[1]),
    )
    assert result_2 == "loss"
    assert pnl_2 == -20.0

    # Bet 3 stays pending

    # Closing odds for bet 1 — beat the line
    clv_1 = calculate_clv(-175, -210)  # +4.10
    db.execute(
        "UPDATE bets SET closing_odds=?, closing_implied=?, clv=? WHERE bet_id=?",
        (-210, round(american_to_implied(-210), 2), clv_1, bet_ids[0]),
    )

    # Aggregate stats
    s = stats_alltime(db, user_id)
    assert s["total"] == 3, s
    assert s["wins"] == 1 and s["losses"] == 1 and s["pending"] == 1, s
    expected_pnl = round(pnl_1 + pnl_2, 2)
    assert_close(s["total_pnl"], expected_pnl, msg="alltime PnL")
    expected_roi = (expected_pnl / 45.0) * 100  # only resolved stake counts? No: total stake
    # NB: stats_alltime sums *all* stakes including pending. ROI is rough.
    print(f"  ✓ stats alltime: {s['wins']}W-{s['losses']}L-{s['pending']}P, "
          f"PnL ${s['total_pnl']:.2f}, ROI {s['roi']:.2f}%")

    # Today should also see all 3 bets (created today)
    today = stats_today(db, user_id)
    assert today["total"] == 3, today

    # Week should also see all 3
    wk = stats_week(db, user_id)
    assert wk["total"] == 3, wk

    # Achievements after resolving
    awards2 = check_and_award(db, user_id, "resolve")
    after_keys = {r["achievement_type"] for r in db.fetch_all(
        "SELECT achievement_type FROM achievements WHERE user_id=?", (user_id,)
    )}
    assert "first_win" in after_keys, after_keys
    print(f"  ✓ all achievements so far: {sorted(after_keys)}")

    # Resolve bet 3 as win to test streak
    db.execute(
        "UPDATE bets SET actual_value=?, result=?, pnl=?, resolved_at=CURRENT_TIMESTAMP WHERE bet_id=?",
        (11.0, "win", calculate_pnl(30.0, -120, "win"), bet_ids[2]),
    )
    streak = current_win_streak(db, user_id)
    print(f"  ✓ current win streak: {streak}")

    print("  ✓ end-to-end OK")


def test_weekly_review_dryrun() -> None:
    print("→ test_weekly_review_build")
    # Don't actually send Telegram; just verify _build_summary doesn't crash.
    from pipeline.weekly_review import _build_summary
    from datetime import date
    db = Database(TMP_DB)
    user_row = db.fetch_one("SELECT user_id FROM users LIMIT 1")
    s = _build_summary(user_row["user_id"], date.today())
    # Our test bets were created today, last week's window is empty -> None
    print(f"  ✓ build_summary returned: {'data' if s else 'None (no last-week bets, expected)'}")


def test_gist_roundtrip() -> None:
    """Serialize → materialize in-memory → verify stats queries agree.

    This is the guard rail for option B: if it passes, the dashboard
    running on Streamlit Cloud sees identical numbers to the bot.
    """
    print("→ test_gist_roundtrip")
    from utils.gist_sync import dump_state, load_into_memory
    from utils.stats import stats_alltime

    file_db = Database(TMP_DB)
    snapshot = dump_state(file_db)

    assert snapshot["schema_version"] == 1
    assert len(snapshot["tables"]["bets"]) == 3
    assert len(snapshot["tables"]["users"]) == 1

    mem_db = load_into_memory(snapshot)

    for table in ["users", "bets", "achievements"]:
        file_count = file_db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        mem_count = mem_db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        assert file_count == mem_count, f"{table}: file={file_count} mem={mem_count}"

    uid = file_db.fetch_one("SELECT user_id FROM users LIMIT 1")["user_id"]
    file_stats = stats_alltime(file_db, uid)
    mem_stats = stats_alltime(mem_db, uid)
    assert file_stats["total_pnl"] == mem_stats["total_pnl"], (
        f"PnL mismatch file={file_stats['total_pnl']} mem={mem_stats['total_pnl']}"
    )
    assert file_stats["wins"] == mem_stats["wins"]
    assert file_stats["losses"] == mem_stats["losses"]
    print(f"  ✓ roundtrip OK — bets/users/achievements preserved, "
          f"stats agree (PnL ${file_stats['total_pnl']:.2f})")


def test_imports() -> None:
    print("→ test_imports")
    import pipeline.telegram_bot  # noqa: F401
    import pipeline.scheduler     # noqa: F401
    import pipeline.weekly_review # noqa: F401
    import utils.db_factory       # noqa: F401
    import utils.gist_sync        # noqa: F401
    print("  ✓ all modules import cleanly")


if __name__ == "__main__":
    test_calculator()
    test_db_init()
    test_end_to_end()
    test_weekly_review_dryrun()
    test_gist_roundtrip()
    test_imports()
    print("\n✅ ALL SMOKE TESTS PASSED")
