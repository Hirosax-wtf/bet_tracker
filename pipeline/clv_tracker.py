"""
CLV Tracker — automated closing-line value capture.

Runs every 10 minutes from 6 PM to midnight ET on game days via APScheduler.
For each pending bet in bet_tracker.db where closing_odds IS NULL:
  1. Check if the game is within 35 minutes of tip
  2. Fetch the current live line from DraftKings (primary) or PrizePicks (fallback)
  3. Store as closing_odds, compute CLV via auto_calculator
  4. DM the user with the CLV result
  5. If 5+ consecutive negative CLV bets, send a pattern warning

Run standalone test:
    cd ~/bet_tracker && python -m pipeline.clv_tracker
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

from pathlib import Path

# Project root imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import require_telegram_token
from pipeline.auto_calculator import american_to_implied, calculate_clv
from utils.db_utils import db
from utils.gist_sync import push_async

log = logging.getLogger("bet_tracker.clv")

# Sports DB for game tip times
try:
    sys.path.insert(0, os.path.expanduser("~/sports_db"))
    from query import get_games_by_date
    _HAS_SPORTS_DB = True
except ImportError:
    _HAS_SPORTS_DB = False
    log.warning("sports_db not available — CLV tracker won't have tip-off times")

# Line fetcher for current odds
try:
    sys.path.insert(0, os.path.expanduser("~/nba_quant_bot/prop_scanner"))
    from pipeline.line_fetcher import fetch_all_lines, get_best_line
    _HAS_LINE_FETCHER = True
except ImportError:
    _HAS_LINE_FETCHER = False
    log.warning("line_fetcher not available — CLV tracker can't fetch closing lines")


def _get_pending_bets_without_closing() -> list[dict]:
    """Return all pending bets that don't have closing odds yet."""
    db.initialize()
    rows = db.fetch_all(
        """
        SELECT b.*, u.telegram_id FROM bets b
        JOIN users u ON b.user_id = u.user_id
        WHERE b.result = 'pending'
          AND b.closing_odds IS NULL
          AND b.odds IS NOT NULL
        ORDER BY b.game_date ASC
        """
    )
    return [dict(r) for r in rows]


def _is_within_minutes(tip_time_str: str | None, minutes: int = 35) -> bool:
    """Check if a tip-off time string is within N minutes from now."""
    if not tip_time_str:
        return False
    try:
        # Handle ISO format with timezone
        tip = datetime.fromisoformat(tip_time_str.replace("Z", "+00:00"))
        now = datetime.now(tip.tzinfo) if tip.tzinfo else datetime.now()
        diff = (tip - now).total_seconds() / 60
        return 0 <= diff <= minutes
    except (ValueError, TypeError):
        return False


def _find_tip_time(game_desc: str, game_date: str) -> str | None:
    """Try to find tip-off time from sports_db."""
    if not _HAS_SPORTS_DB:
        return None
    try:
        games = get_games_by_date(game_date)
        if not games:
            return None
        game_lower = game_desc.lower()
        for g in games:
            # Match on team names in game description
            home = (g.get("home_team") or "").lower()
            away = (g.get("away_team") or "").lower()
            if home and away and (home in game_lower or away in game_lower):
                return g.get("tip_off_time") or g.get("game_time")
        return None
    except Exception as e:
        log.debug("Tip time lookup failed: %s", e)
        return None


def _fetch_closing_odds(player: str, prop_type: str, line: float,
                        direction: str, sport: str = "NBA") -> int | None:
    """Fetch current live odds for a bet. Returns American odds or None."""
    if not _HAS_LINE_FETCHER:
        return None
    try:
        lines = fetch_all_lines(sport)
        best = get_best_line(player, prop_type, direction, lines)
        if best and best.get("best_odds"):
            return best["best_odds"]
    except Exception as e:
        log.warning("Line fetch failed for %s %s: %s", player, prop_type, e)
    return None


def _send_telegram_dm(telegram_id: int, text: str) -> None:
    """Send a DM via the bet tracker bot."""
    import urllib.request
    import urllib.parse
    import json as _json

    token = require_telegram_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": telegram_id, "text": text}).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        log.warning("Failed to DM %s: %s", telegram_id, e)


def _check_negative_pattern(user_id: str) -> bool:
    """Return True if user has 5+ consecutive negative CLV bets."""
    rows = db.fetch_all(
        """
        SELECT clv FROM bets
        WHERE user_id = ? AND clv IS NOT NULL
        ORDER BY COALESCE(resolved_at, created_at) DESC
        LIMIT 5
        """,
        (user_id,),
    )
    if len(rows) < 5:
        return False
    return all(r["clv"] is not None and r["clv"] < 0 for r in rows)


def run_clv_capture() -> int:
    """
    Main CLV capture loop.

    For each pending bet without closing odds:
    1. Look up game tip time
    2. If within 35 min of tip, fetch current line
    3. Store closing odds + CLV
    4. DM the user
    5. Check for negative CLV pattern

    Returns number of bets captured.
    """
    db.initialize()
    pending = _get_pending_bets_without_closing()
    if not pending:
        log.info("CLV capture: no pending bets without closing odds")
        return 0

    captured = 0
    for bet in pending:
        # Check tip time
        tip_time = _find_tip_time(bet["game"], bet["game_date"])
        if tip_time and not _is_within_minutes(tip_time, 35):
            continue  # Not close enough to tip yet

        # If we can't determine tip time, try to capture anyway
        # (better to capture a slightly early closing line than miss it)

        # Fetch closing odds
        closing = _fetch_closing_odds(
            bet.get("player") or "",
            bet.get("prop_type") or "",
            bet.get("line") or 0,
            bet.get("direction") or "over",
            bet.get("sport") or "NBA",
        )
        if not closing:
            continue

        # Calculate CLV
        clv = calculate_clv(bet["odds"], closing)
        closing_implied = round(american_to_implied(closing), 2)

        # Store in DB
        db.execute(
            """
            UPDATE bets SET closing_odds = ?, closing_implied = ?, clv = ?
            WHERE bet_id = ?
            """,
            (closing, closing_implied, clv, bet["bet_id"]),
        )
        captured += 1

        # DM user
        if bet.get("telegram_id"):
            direction = "✅ You beat the market" if clv > 0 else "⚠️ Market moved against you"
            player_str = bet.get("player") or bet.get("game") or "Bet"
            prop_str = bet.get("prop_type") or ""
            dir_str = (bet.get("direction") or "").upper()
            line_str = bet.get("line") or ""

            msg = (
                f"⏱️ Closing line captured: {player_str} {prop_str} {dir_str} {line_str}\n"
                f"Your odds: {bet['odds']:+d} | Closing: {closing:+d}\n"
                f"CLV: {clv:+.1f}% {direction}"
            )
            _send_telegram_dm(bet["telegram_id"], msg)

            # Check negative pattern
            if _check_negative_pattern(bet["user_id"]):
                _send_telegram_dm(
                    bet["telegram_id"],
                    "⚠️ Pattern: 5 straight bets with negative CLV.\n"
                    "You may be betting too late into line moves.\n"
                    "Try placing bets earlier in the day."
                )

    if captured:
        push_async(db)
        log.info("CLV capture: %d bets captured", captured)
    return captured


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run_clv_capture()
    print(f"Captured closing lines for {n} bets")
