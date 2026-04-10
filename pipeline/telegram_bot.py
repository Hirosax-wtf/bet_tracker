"""Bet Tracker Telegram bot.

Built on python-telegram-bot v21 (async). Uses ConversationHandler for the
guided /bet flow and a single CommandHandler for the shorthand form. The
bot is intentionally a separate process from the weekly scheduler so that
either can be restarted without affecting the other.

Run with:
    python -m pipeline.telegram_bot
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

# Allow running as a script (python pipeline/telegram_bot.py) by adding the
# project root to sys.path so absolute imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import DEFAULT_BANKROLL, LEADERBOARD_MIN_BETS, require_telegram_token
from pipeline.auto_calculator import (
    american_to_implied,
    calculate_clv,
    calculate_edge,
    calculate_pnl,
    determine_result,
    quarter_kelly,
)
from utils.achievements import check_and_award, format_award_message
from utils.db_utils import db, new_id
from utils.formatting import fmt_bet_line, fmt_money, fmt_odds, fmt_pct, fmt_record
from utils.gist_sync import push_async
from utils.stats import (
    leaderboard,
    pending_bets,
    stats_alltime,
    stats_today,
    stats_week,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bet_tracker.bot")


# ---------------------------------------------------------------------------
# Conversation states for guided /bet flow
# ---------------------------------------------------------------------------
(
    ASK_USERNAME,
    BET_SPORT,
    BET_GAME,
    BET_PLAYER,
    BET_PROP,
    BET_LINE,
    BET_DIRECTION,
    BET_BOOK,
    BET_ODDS,
    BET_PROB,
    BET_STAKE,
    BET_NICHE,
    BET_INJURY,
    BET_NOTES,
    SLIP_CONFIRM,
    SLIP_PROB,
    SLIP_STAKE,
) = range(17)


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------
def _get_user_by_tg(telegram_id: int):
    return db.fetch_one(
        "SELECT * FROM users WHERE telegram_id=?", (telegram_id,)
    )


def _ensure_user_or_prompt(update: Update) -> dict | None:
    user = _get_user_by_tg(update.effective_user.id)
    return dict(user) if user else None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id = update.effective_user.id
    existing = _get_user_by_tg(tg_id)
    if existing:
        await update.message.reply_text(
            f"Welcome back {existing['username']}!\n\n"
            "Use /bet to log a bet, /pending to see open bets, "
            "/today /week /record for stats."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Welcome to Bet Tracker!\n\n"
        "Pick a username (letters/numbers/underscore, 3-20 chars):"
    )
    return ASK_USERNAME


async def set_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not (3 <= len(name) <= 20) or not all(
        c.isalnum() or c == "_" for c in name
    ):
        await update.message.reply_text(
            "⚠️ Invalid. Use 3-20 chars, letters/numbers/underscore. Try again:"
        )
        return ASK_USERNAME

    if db.fetch_one("SELECT 1 FROM users WHERE username=?", (name,)):
        await update.message.reply_text("⚠️ Username taken. Try another:")
        return ASK_USERNAME

    user_id = new_id("u_")
    db.execute(
        """
        INSERT INTO users (user_id, username, telegram_id, display_name, bankroll)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, name, update.effective_user.id, name, DEFAULT_BANKROLL),
    )
    push_async(db)
    await update.message.reply_text(
        f"✅ Welcome, {name}! Bankroll set to ${DEFAULT_BANKROLL:.0f}.\n\n"
        "Try /bet to log your first bet, or /kelly -175 60 1000 for a quick stake calc."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /bet — guided + shorthand
# ---------------------------------------------------------------------------
SHORTHAND_HELP = (
    "4 ways to log a bet:\n\n"
    "1️⃣ /bet (guided — step by step)\n"
    "2️⃣ /bet NBA \"GSW vs LAL\" Curry pts over 26.5 DK -120 62 25 totals\n"
    "3️⃣ /template → fill & send back\n"
    "4️⃣ /slip → paste a bet slip from DK/FD/Fanatics/MGM\n\n"
    "Tip: /bet last — reuses last bet's game/book/niche"
)

TEMPLATE_MSG = (
    "📝 Copy this, fill it in, and send it back:\n\n"
    "```\n"
    "/bet\n"
    "Sport: NBA\n"
    "Game: GSW vs LAL\n"
    "Player: Curry\n"
    "Prop: points\n"
    "Line: 26.5\n"
    "Dir: over\n"
    "Book: DK\n"
    "Odds: -120\n"
    "Prob: 62\n"
    "Stake: 25\n"
    "Niche: totals\n"
    "Injury: skip\n"
    "Notes: skip\n"
    "```\n\n"
    "Fields are labeled so you can fill them in any order. "
    "'Injury' and 'Notes' are optional — leave as 'skip' or delete the line."
)

# Recognized labels for multi-line template parsing
_TEMPLATE_KEYS = {
    "sport": "sport", "game": "game", "player": "player",
    "prop": "prop_type", "type": "prop_type", "prop_type": "prop_type",
    "line": "line", "dir": "direction", "direction": "direction",
    "over/under": "direction", "book": "book", "odds": "odds",
    "prob": "your_prob", "your_prob": "your_prob", "probability": "your_prob",
    "stake": "stake", "niche": "niche", "injury": "injury_context",
    "injury_context": "injury_context", "notes": "notes",
}


async def cmd_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = _ensure_user_or_prompt(update)
    if not user:
        await update.message.reply_text("Run /start first to create your profile.")
        return ConversationHandler.END

    text = update.message.text or ""
    args = ctx.args or []

    # --- "last" shortcut: reuse last bet's game/book/niche context ------
    if args and args[0].lower() == "last":
        last = db.fetch_one(
            "SELECT * FROM bets WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user["user_id"],),
        )
        if not last:
            await update.message.reply_text("No previous bet to reuse.")
            return ConversationHandler.END
        pre = (
            "📝 Fill from last bet — change what you need:\n\n"
            f"```\n"
            f"/bet\n"
            f"Sport: {last['sport']}\n"
            f"Game: {last['game']}\n"
            f"Player: {last['player'] or 'game'}\n"
            f"Prop: {last['prop_type'] or 'points'}\n"
            f"Line: {last['line']}\n"
            f"Dir: {last['direction']}\n"
            f"Book: {last['book']}\n"
            f"Odds: {last['odds']}\n"
            f"Prob: \n"
            f"Stake: {last['stake']}\n"
            f"Niche: {last['niche'] or 'other'}\n"
            f"```\n"
            "Copy, fill in Prob (and adjust anything else), send it back."
        )
        await update.message.reply_text(pre, parse_mode="Markdown")
        return ConversationHandler.END

    # --- Multi-line template format (has ":" labeled fields) ------------
    if "\n" in text and ":" in text:
        try:
            bet = _parse_template(text)
            bet_id, msg = _save_bet(user, bet)
            awards = check_and_award(db, user["user_id"], "log")
            if awards:
                msg += "\n\n" + format_award_message(awards)
            push_async(db)
            await update.message.reply_text(msg)
            return ConversationHandler.END
        except ValueError:
            pass  # Fall through to shorthand / guided

    # --- Shorthand (single-line positional args) ------------------------
    if args:
        try:
            bet = _parse_shorthand(text)
        except ValueError as e:
            await update.message.reply_text(f"⚠️ {e}\n\n{SHORTHAND_HELP}")
            return ConversationHandler.END
        bet_id, msg = _save_bet(user, bet)
        awards = check_and_award(db, user["user_id"], "log")
        if awards:
            msg += "\n\n" + format_award_message(awards)
        push_async(db)
        await update.message.reply_text(msg)
        return ConversationHandler.END

    # --- Guided mode (no args) ------------------------------------------
    ctx.user_data["bet"] = {}
    await update.message.reply_text(
        "Sport? (NBA / MLB / NFL / NHL / other)\n"
        "(send /cancel to abort, or try /template for a fill-in form)"
    )
    return BET_SPORT


def _parse_template(text: str) -> dict:
    """Parse a multi-line labeled template message.

    Example input:
        /bet
        Sport: NBA
        Game: GSW vs LAL
        Player: Curry
        ...
    Labels are case-insensitive and flexible (Prop/Type/prop_type all work).
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("/bet"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        val = val.strip()
        mapped = _TEMPLATE_KEYS.get(key)
        if mapped and val:
            fields[mapped] = val

    required = {"sport", "game", "book", "odds", "your_prob", "stake"}
    missing = required - set(fields.keys())
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")

    injury = fields.get("injury_context")
    notes = fields.get("notes")
    player = fields.get("player")

    return {
        "sport": fields["sport"].upper(),
        "game": fields["game"],
        "player": None if (not player or player.lower() in ("game", "skip", "-")) else player,
        "prop_type": fields.get("prop_type", "points"),
        "direction": fields.get("direction", "over").lower(),
        "line": float(fields.get("line", "0")),
        "book": fields["book"],
        "odds": int(fields["odds"].replace("+", "")),
        "your_prob": float(fields["your_prob"]),
        "stake": float(fields["stake"].replace("$", "")),
        "niche": fields.get("niche", "other"),
        "injury_context": None if (not injury or injury.lower() == "skip") else injury,
        "notes": None if (not notes or notes.lower() == "skip") else notes,
        "game_date": date.today().isoformat(),
    }


def _parse_shorthand(text: str) -> dict:
    """Tokenize a /bet line, respecting double-quoted game strings."""
    # Strip the leading "/bet"
    body = text.split(None, 1)[1] if " " in text else ""
    if not body:
        raise ValueError("Missing arguments.")
    tokens: list[str] = []
    cur = ""
    in_q = False
    for ch in body:
        if ch == '"':
            in_q = not in_q
            continue
        if ch.isspace() and not in_q:
            if cur:
                tokens.append(cur)
                cur = ""
            continue
        cur += ch
    if cur:
        tokens.append(cur)

    if len(tokens) < 11:
        raise ValueError(f"Expected 11 fields, got {len(tokens)}.")
    sport, game, player, prop, direction, line, book, odds, yourprob, stake, niche = tokens[:11]
    return {
        "sport": sport.upper(),
        "game": game,
        "player": player,
        "prop_type": prop,
        "direction": direction.lower(),
        "line": float(line),
        "book": book,
        "odds": int(odds),
        "your_prob": float(yourprob),
        "stake": float(stake),
        "niche": niche,
        "injury_context": None,
        "notes": None,
        "game_date": date.today().isoformat(),
    }


async def bet_sport(update, ctx):
    ctx.user_data["bet"]["sport"] = update.message.text.strip().upper()
    await update.message.reply_text("Game? (e.g. GSW vs LAL)")
    return BET_GAME


async def bet_game(update, ctx):
    ctx.user_data["bet"]["game"] = update.message.text.strip()
    ctx.user_data["bet"]["game_date"] = date.today().isoformat()
    await update.message.reply_text("Player? (or 'game' for a game-level bet)")
    return BET_PLAYER


async def bet_player(update, ctx):
    p = update.message.text.strip()
    ctx.user_data["bet"]["player"] = None if p.lower() == "game" else p
    await update.message.reply_text(
        "Prop type? (points/rebounds/assists/total/spread/h1_total/q1_total)"
    )
    return BET_PROP


async def bet_prop(update, ctx):
    ctx.user_data["bet"]["prop_type"] = update.message.text.strip().lower()
    await update.message.reply_text("Line? (e.g. 12.5)")
    return BET_LINE


async def bet_line(update, ctx):
    try:
        ctx.user_data["bet"]["line"] = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Numeric please. Line?")
        return BET_LINE
    await update.message.reply_text("Over or under?")
    return BET_DIRECTION


async def bet_direction(update, ctx):
    d = update.message.text.strip().lower()
    if d not in {"over", "under"}:
        await update.message.reply_text("⚠️ Type 'over' or 'under'.")
        return BET_DIRECTION
    ctx.user_data["bet"]["direction"] = d
    await update.message.reply_text("Book? (DK / FD / MGM / PP / Bovada / other)")
    return BET_BOOK


async def bet_book(update, ctx):
    ctx.user_data["bet"]["book"] = update.message.text.strip()
    await update.message.reply_text("Odds? (e.g. -175 or +105)")
    return BET_ODDS


async def bet_odds(update, ctx):
    try:
        ctx.user_data["bet"]["odds"] = int(update.message.text.strip().replace("+", ""))
    except ValueError:
        await update.message.reply_text("⚠️ Integer odds (e.g. -175). Try again.")
        return BET_ODDS
    await update.message.reply_text("Your probability estimate? (0-100)")
    return BET_PROB


async def bet_prob(update, ctx):
    try:
        p = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Numeric please.")
        return BET_PROB
    if not (0 < p < 100):
        await update.message.reply_text("⚠️ Must be between 0 and 100.")
        return BET_PROB
    ctx.user_data["bet"]["your_prob"] = p
    await update.message.reply_text("Stake? ($)")
    return BET_STAKE


async def bet_stake(update, ctx):
    try:
        ctx.user_data["bet"]["stake"] = float(update.message.text.strip().replace("$", ""))
    except ValueError:
        await update.message.reply_text("⚠️ Numeric please.")
        return BET_STAKE
    await update.message.reply_text(
        "Niche? (role_expansion / totals / h1_props / spread / other)"
    )
    return BET_NICHE


async def bet_niche(update, ctx):
    ctx.user_data["bet"]["niche"] = update.message.text.strip().lower()
    await update.message.reply_text("Injury context? (who's out, or 'skip')")
    return BET_INJURY


async def bet_injury(update, ctx):
    txt = update.message.text.strip()
    ctx.user_data["bet"]["injury_context"] = None if txt.lower() == "skip" else txt
    await update.message.reply_text("Notes? (or 'skip')")
    return BET_NOTES


async def bet_notes(update, ctx):
    txt = update.message.text.strip()
    ctx.user_data["bet"]["notes"] = None if txt.lower() == "skip" else txt

    user = _ensure_user_or_prompt(update)
    bet_id, msg = _save_bet(user, ctx.user_data["bet"])
    awards = check_and_award(db, user["user_id"], "log")
    if awards:
        msg += "\n\n" + format_award_message(awards)
    push_async(db)
    await update.message.reply_text(msg)
    ctx.user_data.clear()
    return ConversationHandler.END


def _save_bet(user: dict, bet: dict) -> tuple[str, str]:
    bet_id = new_id("b_")
    implied = round(american_to_implied(bet["odds"]), 2)
    edge = calculate_edge(bet["your_prob"], implied)
    kelly = quarter_kelly(bet["your_prob"], bet["odds"], user["bankroll"] or DEFAULT_BANKROLL)

    db.execute(
        """
        INSERT INTO bets (
            bet_id, user_id, sport, game, game_date, player, prop_type,
            line, direction, book, odds, implied_prob, your_prob, edge,
            stake, niche, injury_context, notes, result
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')
        """,
        (
            bet_id, user["user_id"], bet["sport"], bet["game"], bet["game_date"],
            bet.get("player"), bet.get("prop_type"), bet.get("line"),
            bet.get("direction"), bet["book"], bet["odds"], implied,
            bet["your_prob"], edge, bet["stake"], bet.get("niche"),
            bet.get("injury_context"), bet.get("notes"),
        ),
    )

    summary = (
        "✅ Bet logged!\n"
        "─────────────────\n"
        f"{fmt_bet_line(bet)}\n"
        f"Book: {bet['book']} @ {fmt_odds(bet['odds'])}\n"
        f"Your edge: {fmt_pct(edge, sign=True)}\n"
        f"(You: {bet['your_prob']:.0f}% vs Implied: {implied:.1f}%)\n"
        f"Stake: {fmt_money(bet['stake'])} | Kelly suggests: {fmt_money(kelly)}\n"
        "─────────────────\n"
        f"Bet ID: {bet_id}\n"
        f"Use /close {bet_id} [actual] to grade"
    )
    return bet_id, summary


# ---------------------------------------------------------------------------
# /close, /closing
# ---------------------------------------------------------------------------
async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = _ensure_user_or_prompt(update)
    if not user:
        await update.message.reply_text("Run /start first.")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /close <bet_id> <actual_value>")
        return
    bet_id, actual_str = ctx.args[0], ctx.args[1]
    try:
        actual = float(actual_str)
    except ValueError:
        await update.message.reply_text("⚠️ Actual value must be numeric.")
        return

    bet = db.fetch_one(
        "SELECT * FROM bets WHERE bet_id=? AND user_id=?",
        (bet_id, user["user_id"]),
    )
    if not bet:
        await update.message.reply_text("⚠️ Bet not found.")
        return
    if bet["result"] != "pending":
        await update.message.reply_text(
            f"⚠️ Already graded: {bet['result'].upper()}"
        )
        return

    result = determine_result(bet["direction"], bet["line"], actual)
    pnl = calculate_pnl(bet["stake"], bet["odds"], result)
    db.execute(
        """
        UPDATE bets SET actual_value=?, result=?, pnl=?, resolved_at=CURRENT_TIMESTAMP
        WHERE bet_id=?
        """,
        (actual, result, pnl, bet_id),
    )

    icon = {"win": "✅ WIN", "loss": "❌ LOSS", "push": "➖ PUSH"}.get(result, result)
    today = stats_today(db, user["user_id"])
    body = (
        f"{icon} — {fmt_bet_line(dict(bet))} → {actual} actual\n"
        f"P&L: {fmt_money(pnl, sign=True)} | "
        f"Running today: {fmt_money(today['total_pnl'], sign=True)}"
    )
    awards = check_and_award(db, user["user_id"], "resolve")
    if awards:
        body += "\n\n" + format_award_message(awards)
    push_async(db)
    await update.message.reply_text(body)


async def cmd_closing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = _ensure_user_or_prompt(update)
    if not user:
        await update.message.reply_text("Run /start first.")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /closing <bet_id> <closing_odds>")
        return
    bet_id = ctx.args[0]
    try:
        closing = int(ctx.args[1].replace("+", ""))
    except ValueError:
        await update.message.reply_text("⚠️ Closing odds must be integer.")
        return

    bet = db.fetch_one(
        "SELECT * FROM bets WHERE bet_id=? AND user_id=?",
        (bet_id, user["user_id"]),
    )
    if not bet:
        await update.message.reply_text("⚠️ Bet not found.")
        return

    closing_implied = round(american_to_implied(closing), 2)
    clv = calculate_clv(bet["odds"], closing)
    db.execute(
        "UPDATE bets SET closing_odds=?, closing_implied=?, clv=? WHERE bet_id=?",
        (closing, closing_implied, clv, bet_id),
    )
    verdict = "✅ You beat the market" if clv > 0 else "⚠️ Market closed shorter — review timing"
    body = (
        f"📊 CLV: {fmt_pct(clv, sign=True)} {verdict}\n"
        f"(Bet {fmt_odds(bet['odds'])}, closed {fmt_odds(closing)})"
    )
    awards = check_and_award(db, user["user_id"], "closing")
    if awards:
        body += "\n\n" + format_award_message(awards)
    push_async(db)
    await update.message.reply_text(body)


# ---------------------------------------------------------------------------
# Read commands: /pending /today /week /record /streak /leaderboard /kelly
# ---------------------------------------------------------------------------
async def cmd_pending(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        await update.message.reply_text("Run /start first.")
        return
    rows = pending_bets(db, user["user_id"])
    if not rows:
        await update.message.reply_text("No pending bets. 🧘")
        return
    lines = ["📋 Pending bets:"]
    for r in rows:
        lines.append(
            f"  {r['bet_id']} — {fmt_bet_line(dict(r))} "
            f"@ {fmt_odds(r['odds'])} ({r['book']})"
        )
    await update.message.reply_text("\n".join(lines))


def _stat_block(title: str, s: dict) -> str:
    return (
        f"{title}\n"
        f"  Record: {fmt_record(s['wins'], s['losses'], s['pushes'])} "
        f"({s['win_rate']:.1f}%)\n"
        f"  P&L: {fmt_money(s['total_pnl'], sign=True)} | "
        f"ROI: {fmt_pct(s['roi'], sign=True)}\n"
        f"  Avg CLV: {fmt_pct(s['avg_clv'], sign=True)} "
        f"({s['clv_count']} closed)"
    )


async def cmd_today(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        return await update.message.reply_text("Run /start first.")
    s = stats_today(db, user["user_id"])
    await update.message.reply_text(_stat_block("📅 Today", s))


async def cmd_week(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        return await update.message.reply_text("Run /start first.")
    s = stats_week(db, user["user_id"])
    title = f"📅 Week of {s['week_start']}"
    await update.message.reply_text(_stat_block(title, s))


async def cmd_record(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        return await update.message.reply_text("Run /start first.")
    s = stats_alltime(db, user["user_id"])
    await update.message.reply_text(_stat_block("🏆 All-time", s))


async def cmd_streak(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        return await update.message.reply_text("Run /start first.")
    from utils.achievements import current_win_streak

    streak = current_win_streak(db, user["user_id"])
    # Compute best ever
    rows = db.fetch_all(
        """
        SELECT result FROM bets
        WHERE user_id=? AND result IN ('win','loss')
        ORDER BY COALESCE(resolved_at, created_at) ASC
        """,
        (user["user_id"],),
    )
    best = cur = 0
    for r in rows:
        if r["result"] == "win":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    await update.message.reply_text(
        f"🔥 Current streak: {streak}W\n🏅 Best ever: {best}W"
    )


async def cmd_leaderboard(update, ctx):
    rows = leaderboard(db, min_bets=LEADERBOARD_MIN_BETS)[:10]
    if not rows:
        await update.message.reply_text(
            f"No users with {LEADERBOARD_MIN_BETS}+ bets yet."
        )
        return
    lines = ["🏆 Leaderboard (Top 10 by ROI):"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r['display_name']} — "
            f"ROI {fmt_pct(r['roi'], sign=True)} | "
            f"{fmt_record(r['wins'], r['losses'])} | "
            f"CLV {fmt_pct(r['avg_clv'], sign=True)}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_kelly(update, ctx):
    if len(ctx.args) < 3:
        await update.message.reply_text(
            "Usage: /kelly <odds> <your_prob 0-100> <bankroll>\n"
            "Example: /kelly -175 80 1000"
        )
        return
    try:
        odds = int(ctx.args[0].replace("+", ""))
        prob = float(ctx.args[1])
        bankroll = float(ctx.args[2])
    except ValueError:
        await update.message.reply_text("⚠️ Bad arguments.")
        return
    stake = quarter_kelly(prob, odds, bankroll)
    pct = (stake / bankroll * 100) if bankroll else 0
    await update.message.reply_text(
        f"Kelly suggests: {fmt_money(stake)} ({pct:.2f}% of bankroll)"
    )


# ---------------------------------------------------------------------------
# /group create|join|stats
# ---------------------------------------------------------------------------
async def cmd_group(update, ctx):
    user = _ensure_user_or_prompt(update)
    if not user:
        return await update.message.reply_text("Run /start first.")
    if not ctx.args:
        return await update.message.reply_text(
            "Usage: /group create <name> | /group join <group_id> | /group stats"
        )
    sub = ctx.args[0].lower()

    if sub == "create" and len(ctx.args) >= 2:
        name = " ".join(ctx.args[1:])
        gid = new_id("g_")
        db.execute(
            """
            INSERT INTO groups (group_id, group_name, created_by, telegram_chat_id)
            VALUES (?, ?, ?, ?)
            """,
            (gid, name, user["user_id"], update.effective_chat.id),
        )
        db.execute(
            "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)",
            (gid, user["user_id"]),
        )
        push_async(db)
        await update.message.reply_text(f"✅ Group '{name}' created. ID: {gid}")
        return

    if sub == "join" and len(ctx.args) >= 2:
        gid = ctx.args[1]
        if not db.fetch_one("SELECT 1 FROM groups WHERE group_id=?", (gid,)):
            return await update.message.reply_text("⚠️ Group not found.")
        db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)",
            (gid, user["user_id"]),
        )
        awards = check_and_award(db, user["user_id"], "group")
        body = "✅ Joined group."
        if awards:
            body += "\n\n" + format_award_message(awards)
        push_async(db)
        return await update.message.reply_text(body)

    if sub == "stats":
        # Use the most recently joined group
        row = db.fetch_one(
            """
            SELECT g.group_id, g.group_name FROM groups g
            JOIN group_members gm ON gm.group_id = g.group_id
            WHERE gm.user_id=? ORDER BY gm.joined_at DESC LIMIT 1
            """,
            (user["user_id"],),
        )
        if not row:
            return await update.message.reply_text("You're not in any groups.")
        rows = leaderboard(db, min_bets=1, group_id=row["group_id"])[:10]
        lines = [f"👥 {row['group_name']}"]
        for i, r in enumerate(rows, 1):
            lines.append(
                f"{i}. {r['display_name']} — ROI {fmt_pct(r['roi'], sign=True)} "
                f"({fmt_record(r['wins'], r['losses'])})"
            )
        return await update.message.reply_text("\n".join(lines))

    await update.message.reply_text(
        "Usage: /group create <name> | /group join <group_id> | /group stats"
    )


# ---------------------------------------------------------------------------
# /slip — paste a sportsbook bet slip
# ---------------------------------------------------------------------------
SLIP_HELP = (
    "📋 Paste a bet slip from DraftKings, FanDuel, Fanatics, or BetMGM.\n\n"
    "Example — just copy from your sportsbook app and paste:\n"
    "```\n"
    "/slip\n"
    "Jalen Brunson\n"
    "Over 24.5 Points\n"
    "-110\n"
    "```\n"
    "I'll parse it and ask you to confirm before logging."
)


async def cmd_slip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = _ensure_user_or_prompt(update)
    if not user:
        await update.message.reply_text("Run /start first.")
        return ConversationHandler.END

    text = update.message.text or ""
    # Strip the /slip command prefix
    body = text.split(None, 1)[1] if " " in text or "\n" in text else ""
    # If /slip was on its own line, grab everything after it
    if not body and "\n" in text:
        body = text.split("\n", 1)[1]

    if not body.strip():
        await update.message.reply_text(SLIP_HELP, parse_mode="Markdown")
        await update.message.reply_text(
            "Paste your bet slip now (or /cancel to abort):"
        )
        return SLIP_CONFIRM

    # Text was included with the command
    return await _process_slip_text(update, ctx, body, user)


async def slip_receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the pasted slip text after a bare /slip."""
    user = _ensure_user_or_prompt(update)
    if not user:
        return ConversationHandler.END
    text = update.message.text or ""
    return await _process_slip_text(update, ctx, text, user)


async def _process_slip_text(update, ctx, text, user) -> int:
    from pipeline.slip_parser import parse_slip, format_confirmation

    legs = parse_slip(text)
    if not legs:
        await update.message.reply_text(
            "⚠️ Couldn't parse any bets from that text.\n\n"
            "Try the shorthand instead:\n"
            "/bet NBA \"GSW vs LAL\" Brunson pts over 24.5 DK -110 62 25 role_expansion"
        )
        return ConversationHandler.END

    ctx.user_data["slip_legs"] = legs
    ctx.user_data["slip_current"] = 0

    msg = format_confirmation(legs, parlay=len(legs) > 1)
    await update.message.reply_text(msg)
    return SLIP_CONFIRM


async def slip_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Yes/Edit/Cancel after showing parsed slip."""
    text = (update.message.text or "").strip().lower()
    legs = ctx.user_data.get("slip_legs")

    if not legs:
        # They pasted text after bare /slip — try to parse it
        return await slip_receive_text(update, ctx)

    if text in ("yes", "y", "✅", "confirm", "ok", "log"):
        # Need prob and stake before logging — ask for both
        await update.message.reply_text(
            "What's your probability estimate? (0-100)\n"
            "(This is YOUR confidence the bet hits)"
        )
        return SLIP_PROB

    if text in ("cancel", "no", "n", "❌", "abort"):
        ctx.user_data.pop("slip_legs", None)
        await update.message.reply_text("Cancelled.")
        return ConversationHandler.END

    if text in ("edit", "✏️", "e"):
        legs_data = ctx.user_data.get("slip_legs", [])
        if legs_data:
            # Show as a pre-filled template they can edit
            leg = legs_data[0]
            tmpl = (
                "📝 Edit and send back:\n\n"
                "```\n"
                "/bet\n"
                f"Sport: NBA\n"
                f"Game: ?\n"
                f"Player: {leg.get('player', '?')}\n"
                f"Prop: {leg.get('prop_type', 'points')}\n"
                f"Line: {leg.get('line', '?')}\n"
                f"Dir: {leg.get('direction', 'over')}\n"
                f"Book: {leg.get('book', '?')}\n"
                f"Odds: {leg.get('odds', '?')}\n"
                f"Prob: \n"
                f"Stake: \n"
                f"Niche: \n"
                "```"
            )
            ctx.user_data.pop("slip_legs", None)
            await update.message.reply_text(tmpl, parse_mode="Markdown")
            return ConversationHandler.END
        await update.message.reply_text("Nothing to edit. Send a new /slip.")
        return ConversationHandler.END

    # Unrecognized response — might be paste text after bare /slip
    return await slip_receive_text(update, ctx)


async def slip_prob(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        prob = float(update.message.text.strip())
        if not (0 < prob <= 100):
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Enter a number between 1-100:")
        return SLIP_PROB
    ctx.user_data["slip_prob"] = prob
    await update.message.reply_text("Stake amount ($)?")
    return SLIP_STAKE


async def slip_stake(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        stake = float(update.message.text.strip().replace("$", "").replace(",", ""))
        if stake <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Enter a positive dollar amount:")
        return SLIP_STAKE

    user = _ensure_user_or_prompt(update)
    if not user:
        return ConversationHandler.END

    prob = ctx.user_data.get("slip_prob", 50)
    legs = ctx.user_data.get("slip_legs", [])

    msgs = []
    for leg in legs:
        bet = {
            "sport": "NBA",  # default, user can fix via /close
            "game": "?",
            "game_date": date.today().isoformat(),
            "player": leg.get("player"),
            "prop_type": leg.get("prop_type"),
            "line": leg.get("line"),
            "direction": leg.get("direction"),
            "book": leg.get("book", "?"),
            "odds": leg.get("odds") or -110,
            "your_prob": prob,
            "stake": stake,
            "niche": None,
            "injury_context": None,
            "notes": "logged via /slip",
        }
        bet_id, summary = _save_bet(user, bet)
        awards = check_and_award(db, user["user_id"], "log")
        if awards:
            summary += "\n\n" + format_award_message(awards)
        msgs.append(summary)

    push_async(db)
    await update.message.reply_text("\n\n".join(msgs))
    ctx.user_data.pop("slip_legs", None)
    ctx.user_data.pop("slip_prob", None)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /template
# ---------------------------------------------------------------------------
async def cmd_template(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TEMPLATE_MSG, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------
async def cmd_cancel(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_app() -> Application:
    db.initialize()
    token = require_telegram_token()
    app = Application.builder().token(token).build()

    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_username)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    bet_conv = ConversationHandler(
        entry_points=[CommandHandler("bet", cmd_bet)],
        states={
            BET_SPORT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_sport)],
            BET_GAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_game)],
            BET_PLAYER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_player)],
            BET_PROP:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_prop)],
            BET_LINE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_line)],
            BET_DIRECTION:[MessageHandler(filters.TEXT & ~filters.COMMAND, bet_direction)],
            BET_BOOK:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_book)],
            BET_ODDS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_odds)],
            BET_PROB:     [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_prob)],
            BET_STAKE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_stake)],
            BET_NICHE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_niche)],
            BET_INJURY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_injury)],
            BET_NOTES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_notes)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    slip_conv = ConversationHandler(
        entry_points=[CommandHandler("slip", cmd_slip)],
        states={
            SLIP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, slip_confirm)],
            SLIP_PROB:    [MessageHandler(filters.TEXT & ~filters.COMMAND, slip_prob)],
            SLIP_STAKE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, slip_stake)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(start_conv)
    app.add_handler(bet_conv)
    app.add_handler(slip_conv)
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("closing", cmd_closing))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("record", cmd_record))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("kelly", cmd_kelly))
    app.add_handler(CommandHandler("group", cmd_group))
    app.add_handler(CommandHandler("template", cmd_template))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    return app


def main() -> None:
    # Python 3.14 removed implicit loop creation in asyncio.get_event_loop().
    # python-telegram-bot 21.4's run_polling() still calls get_event_loop()
    # internally, so we create and install one before it does.
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = build_app()
    log.info("Bet Tracker bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
