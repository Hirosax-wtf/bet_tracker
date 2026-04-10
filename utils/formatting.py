"""Display helpers shared by Telegram bot and Streamlit pages."""
from __future__ import annotations


def fmt_odds(odds: int | None) -> str:
    if odds is None:
        return "—"
    return f"+{odds}" if odds > 0 else str(odds)


def fmt_money(amount: float | None, *, sign: bool = False) -> str:
    if amount is None:
        return "—"
    s = f"${abs(amount):,.2f}"
    if sign:
        if amount > 0:
            return f"+{s}"
        if amount < 0:
            return f"-{s}"
    elif amount < 0:
        return f"-{s}"
    return s


def fmt_pct(value: float | None, *, sign: bool = False, digits: int = 1) -> str:
    if value is None:
        return "—"
    if sign:
        return f"{value:+.{digits}f}%"
    return f"{value:.{digits}f}%"


def fmt_record(wins: int, losses: int, pushes: int = 0) -> str:
    if pushes:
        return f"{wins}W-{losses}L-{pushes}P"
    return f"{wins}W-{losses}L"


def fmt_bet_line(bet: dict) -> str:
    """One-line summary used in confirmations and lists."""
    parts = []
    if bet.get("player"):
        parts.append(bet["player"])
    if bet.get("prop_type"):
        parts.append(str(bet["prop_type"]).title())
    if bet.get("line") is not None:
        parts.append(str(bet["line"]))
    if bet.get("direction"):
        parts.append(str(bet["direction"]).upper())
    return " ".join(parts) if parts else (bet.get("game") or "Bet")
