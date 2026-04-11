"""Generate and broadcast weekly review summaries.

Called once a week by pipeline/scheduler.py. For each user with bets in
the last 7 days we compute the week summary, store it, and DM them via
the bet-tracker bot. CLV trend is treated as the headline signal: if
average CLV is negative we add a warning about line-shopping and timing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from telegram import Bot

from config import require_telegram_token
from utils.db_utils import db, new_id
from utils.formatting import fmt_money, fmt_pct, fmt_record
from utils.gist_sync import push_async
from utils.stats import bets_in_range, roi_by_niche, hit_rate_by_prop, _summarize

log = logging.getLogger("bet_tracker.weekly")


def _build_summary(user_id: str, anchor: date) -> dict | None:
    """Compute the weekly summary dict for a user; None if no activity."""
    week_start = anchor - timedelta(days=anchor.weekday() + 7)  # last Mon
    week_end = week_start + timedelta(days=6)
    rows = bets_in_range(db, user_id, week_start, week_end)
    if not rows:
        return None

    s = _summarize(rows)

    niches = roi_by_niche(db, user_id)
    week_niches = [n for n in niches if n["n"] >= 1]
    best_niche = max(week_niches, key=lambda n: n["roi"], default=None)
    worst_niche = min(week_niches, key=lambda n: n["roi"], default=None)

    props = hit_rate_by_prop(db, user_id)
    best_prop = max(
        (p for p in props if p["decided"] >= 2), key=lambda p: p["hit_rate"], default=None
    )

    return {
        "user_id": user_id,
        "week_start": week_start,
        "week_end": week_end,
        **s,
        "best_niche": best_niche["niche"] if best_niche else None,
        "best_niche_rate": best_niche["win_rate"] if best_niche else None,
        "worst_niche": worst_niche["niche"] if worst_niche else None,
        "worst_niche_rate": worst_niche["win_rate"] if worst_niche else None,
        "best_prop": best_prop["prop_type"] if best_prop else None,
        "best_prop_rate": best_prop["hit_rate"] if best_prop else None,
    }


def _persist_summary(s: dict) -> None:
    db.execute(
        """
        INSERT INTO weekly_summaries (
            summary_id, user_id, week_start, week_end, total_bets,
            wins, losses, pushes, win_rate, total_staked, total_pnl,
            roi, avg_clv, best_niche, worst_niche
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            new_id("ws_"),
            s["user_id"],
            s["week_start"].isoformat(),
            s["week_end"].isoformat(),
            s["total"], s["wins"], s["losses"], s["pushes"],
            round(s["win_rate"], 2), s["total_staked"], s["total_pnl"],
            round(s["roi"], 2), round(s["avg_clv"], 2),
            s["best_niche"], s["worst_niche"],
        ),
    )


def _format_message(s: dict, streak: int) -> str:
    from utils.achievements import current_win_streak  # local to avoid cycle

    range_str = f"{s['week_start']:%b %d} – {s['week_end']:%b %d}"
    body = [
        f"📊 Weekly Review — {range_str}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Record: {fmt_record(s['wins'], s['losses'], s['pushes'])} "
        f"({s['win_rate']:.1f}%)",
        f"P&L: {fmt_money(s['total_pnl'], sign=True)} | "
        f"ROI: {fmt_pct(s['roi'], sign=True)}",
        f"Avg CLV: {fmt_pct(s['avg_clv'], sign=True)} "
        f"{'✅' if s['avg_clv'] >= 0 else '⚠️'}",
        "",
    ]
    if s["best_niche"]:
        body.append(
            f"Best niche: {s['best_niche']} ({s['best_niche_rate']:.0f}%)"
        )
    if s["worst_niche"] and s["worst_niche"] != s["best_niche"]:
        body.append(
            f"Avoid: {s['worst_niche']} ({s['worst_niche_rate']:.0f}%)"
        )
    if s["best_prop"]:
        body.append(f"Best prop: {s['best_prop']} ({s['best_prop_rate']:.0f}%)")

    body += ["", f"🔥 Streak: {streak}W", "━━━━━━━━━━━━━━━━━━━━"]
    if s["avg_clv"] >= 0:
        body.append("CLV positive — real edge found. ✅")
    else:
        body.append(
            "⚠️ Negative CLV. Betting into line moves. Try betting earlier."
        )
    return "\n".join(body)


async def _send(bot: Bot, telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to send weekly review to %s: %s", telegram_id, exc)


async def run_weekly_review_async(anchor: date | None = None) -> int:
    """Generate + send summaries. Returns count of messages sent."""
    db.initialize()
    anchor = anchor or date.today()
    bot = Bot(token=require_telegram_token())

    users = db.fetch_all(
        "SELECT user_id, telegram_id, username FROM users WHERE telegram_id IS NOT NULL"
    )
    sent = 0
    for u in users:
        s = _build_summary(u["user_id"], anchor)
        if not s:
            continue
        _persist_summary(s)
        from utils.achievements import current_win_streak, check_and_award

        streak = current_win_streak(db, u["user_id"])
        msg = _format_message(s, streak)
        await _send(bot, u["telegram_id"], msg)
        check_and_award(db, u["user_id"], "weekly")
        sent += 1
    if sent:
        push_async(db)
    log.info("Weekly review sent to %d users", sent)
    return sent


def run_weekly_review(anchor: date | None = None) -> int:
    """Sync wrapper for APScheduler."""
    result = asyncio.run(run_weekly_review_async(anchor))

    # --- shared_grading: import resolved bets + generate bet_tracker review ---
    try:
        import sys as _sg_sys, os as _sg_os
        _sg_sys.path.insert(0, _sg_os.path.expanduser("~"))
        from shared_grading.adapters.bet_tracker_adapter import import_resolved_bets
        from shared_grading.review.report_generator import generate_bot_review
        import_resolved_bets()
        review = generate_bot_review("bet_tracker")
        log.info("shared_grading bet_tracker review: %d picks, hit_rate=%s",
                 review.get("summary", {}).get("total_picks", 0),
                 review.get("summary", {}).get("hit_rate"))
    except Exception as _sg_err:
        log.debug("shared_grading review failed (non-fatal): %s", _sg_err)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekly_review()
