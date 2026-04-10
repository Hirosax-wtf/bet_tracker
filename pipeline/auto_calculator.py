"""Pure-function math helpers for bet tracking.

All probabilities here are expressed as 0-100 percentages, NOT 0-1 decimals,
because that's the format users type into Telegram. The dashboard and bot
both import from here so there's exactly one place that knows how to
convert between American odds, implied probability, edge, CLV, and Kelly.
"""
from __future__ import annotations


def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability 0-100."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100) * 100
    return 100 / (odds + 100) * 100


def calculate_edge(your_prob: float, implied_prob: float) -> float:
    """User's probability estimate minus the market's implied probability."""
    return round(your_prob - implied_prob, 2)


def calculate_clv(bet_odds: int, closing_odds: int) -> float:
    """Closing line value.

    Positive CLV means you got a better number than the market closed at:
    your bet's implied probability is *lower* than the closing implied
    probability, i.e. you locked in a longer-odds version of the same bet.
    """
    return round(
        american_to_implied(closing_odds) - american_to_implied(bet_odds), 2
    )


def calculate_pnl(stake: float, odds: int, result: str) -> float:
    """Profit/loss for a settled bet. Push and pending return 0."""
    if result == "win":
        if odds > 0:
            return round(stake * (odds / 100), 2)
        return round(stake * (100 / abs(odds)), 2)
    if result == "loss":
        return -float(stake)
    return 0.0


def quarter_kelly(your_prob: float, odds: int, bankroll: float) -> float:
    """Quarter Kelly stake suggestion. Returns dollars (>=0)."""
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    p = your_prob / 100
    q = 1 - p
    full_kelly = (b * p - q) / b
    return round(max(0.0, bankroll * full_kelly / 4), 2)


def determine_result(direction: str, line: float, actual: float) -> str:
    """Resolve over/under bets given the actual stat value."""
    direction = (direction or "").lower()
    if direction == "over":
        if actual > line:
            return "win"
        if actual == line:
            return "push"
        return "loss"
    if direction == "under":
        if actual < line:
            return "win"
        if actual == line:
            return "push"
        return "loss"
    return "pending"
