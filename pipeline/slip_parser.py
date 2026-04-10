"""
Sportsbook bet slip parser — extracts legs from pasted text.

Supported books: DraftKings, FanDuel, Fanatics, BetMGM.
Falls back to a generic regex parser for unknown formats.

Each parser returns a list of dicts, one per leg:
    {
        "player": str,
        "prop_type": str,        # points, rebounds, assists, threes, etc.
        "line": float,
        "direction": "over" | "under",
        "odds": int,             # American odds (-110, +150)
        "book": str,             # DK, FD, FAN, MGM
    }

Usage:
    from pipeline.slip_parser import parse_slip
    legs = parse_slip(text)
"""
from __future__ import annotations

import re
from typing import Optional


# ── prop type normalization ────────────────────────────────────────

_PROP_MAP = {
    "points": "points", "pts": "points", "point": "points",
    "rebounds": "rebounds", "reb": "rebounds", "rebs": "rebounds", "rebound": "rebounds",
    "assists": "assists", "ast": "assists", "asst": "assists", "assist": "assists",
    "3-pointers": "threes", "3-pt": "threes", "threes": "threes",
    "three pointers": "threes", "3pm": "threes", "three-pointers": "threes",
    "3-pointers made": "threes", "three pointers made": "threes",
    "steals": "steals", "stl": "steals", "steal": "steals",
    "blocks": "blocks", "blk": "blocks", "blks": "blocks", "block": "blocks",
    "turnovers": "turnovers", "to": "turnovers", "turnover": "turnovers",
    "pts+reb": "pts+reb", "pts + reb": "pts+reb", "points + rebounds": "pts+reb",
    "pts+ast": "pts+ast", "pts + ast": "pts+ast", "points + assists": "pts+ast",
    "reb+ast": "reb+ast", "reb + ast": "reb+ast", "rebounds + assists": "reb+ast",
    "pts+reb+ast": "pts+reb+ast", "pts + reb + ast": "pts+reb+ast", "pra": "pts+reb+ast",
    "fantasy score": "fantasy", "fantasy points": "fantasy",
    "strikeouts": "strikeouts", "ks": "strikeouts",
    "hits": "hits", "runs": "runs", "rbis": "rbis", "rbi": "rbis",
    "total bases": "total_bases", "tb": "total_bases",
    "home runs": "home_runs", "hr": "home_runs",
    "hits+runs+rbis": "h+r+rbi", "h+r+rbi": "h+r+rbi",
    "passing yards": "passing_yards", "pass yds": "passing_yards",
    "rushing yards": "rushing_yards", "rush yds": "rushing_yards",
    "receiving yards": "receiving_yards", "rec yds": "receiving_yards",
    "touchdowns": "touchdowns", "tds": "touchdowns", "anytime td": "anytime_td",
    "goals": "goals", "shots on goal": "sog", "saves": "saves",
}


def _normalize_prop(raw: str) -> str:
    key = raw.strip().lower()
    # try exact match
    if key in _PROP_MAP:
        return _PROP_MAP[key]
    # try removing trailing 's'
    if key.rstrip("s") in _PROP_MAP:
        return _PROP_MAP[key.rstrip("s")]
    # try substring match
    for k, v in _PROP_MAP.items():
        if k in key:
            return v
    return key


def _parse_odds(raw: str) -> Optional[int]:
    """Parse American odds string like '-110', '+150', '−110' (unicode minus)."""
    s = raw.strip().replace("−", "-").replace("–", "-").replace("\u2212", "-")
    s = s.replace(",", "")
    m = re.search(r"([+-]?\d+)", s)
    if not m:
        return None
    v = int(m.group(1))
    # Bare positive number without + (e.g. "150") → +150
    if v > 0 and "+" not in s and "-" not in s:
        return v
    return v


def _parse_line(raw: str) -> Optional[float]:
    m = re.search(r"(\d+\.?\d*)", raw)
    return float(m.group(1)) if m else None


def _parse_direction(raw: str) -> Optional[str]:
    low = raw.strip().lower()
    if "over" in low or low == "o":
        return "over"
    if "under" in low or low == "u":
        return "under"
    return None


# ── DraftKings ─────────────────────────────────────────────────────

# DK paste format (single leg):
#   Jalen Brunson
#   Over 24.5 Points
#   -110
#
# DK parlay paste:
#   Jalen Brunson - Over 24.5 Pts
#   Anthony Edwards - Over 22.5 Pts
#   Odds: +350

def _try_dk(text: str) -> Optional[list[dict]]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return None

    legs = []

    # Pattern 1: "Player - Over/Under X.X PropType" (parlay format)
    parlay_re = re.compile(
        r"^(.+?)\s*[-–—]\s*(over|under|o|u)\s+(\d+\.?\d*)\s+(.+?)(?:\s*$)",
        re.IGNORECASE,
    )
    parlay_odds_re = re.compile(r"(?:odds|total\s*odds)\s*[:\s]*([+-−–]?\d+)", re.IGNORECASE)

    parlay_mode = False
    final_odds = None
    for line in lines:
        m = parlay_re.match(line)
        if m:
            parlay_mode = True
            legs.append({
                "player": m.group(1).strip(),
                "direction": _parse_direction(m.group(2)),
                "line": float(m.group(3)),
                "prop_type": _normalize_prop(m.group(4)),
                "odds": None,
                "book": "DK",
            })
            continue
        om = parlay_odds_re.search(line)
        if om:
            final_odds = _parse_odds(om.group(1))

    if parlay_mode and legs:
        # Distribute parlay odds to the last leg or store on all
        for leg in legs:
            if leg["odds"] is None:
                leg["odds"] = final_odds
        return legs

    # Pattern 2: block format (3 lines per leg: name, "Over X.X Type", odds)
    i = 0
    legs = []
    while i < len(lines):
        # Name line: no digits typically, or at least not starting with Over/Under/odds
        name_line = lines[i]
        if re.match(r"^[+-−]?\d+$", name_line):
            i += 1
            continue
        if i + 1 >= len(lines):
            break
        prop_line = lines[i + 1]
        prop_m = re.match(
            r"^(over|under|o|u)\s+(\d+\.?\d*)\s+(.+)",
            prop_line,
            re.IGNORECASE,
        )
        if not prop_m:
            i += 1
            continue
        direction = _parse_direction(prop_m.group(1))
        line_val = float(prop_m.group(2))
        prop_type = _normalize_prop(prop_m.group(3))
        odds = None
        if i + 2 < len(lines):
            odds = _parse_odds(lines[i + 2])
            if odds is not None:
                i += 3
            else:
                i += 2
        else:
            i += 2
        legs.append({
            "player": name_line.strip(),
            "direction": direction,
            "line": line_val,
            "prop_type": prop_type,
            "odds": odds,
            "book": "DK",
        })

    return legs if legs else None


# ── FanDuel ────────────────────────────────────────────────────────

# FD paste format:
#   Jalen Brunson Over 24.5 Points (-110)
#   or
#   Jalen Brunson
#   Over 24.5 Points
#   (-110)

def _try_fd(text: str) -> Optional[list[dict]]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    legs = []

    # Single-line format: "Player Over/Under X.X PropType (odds)"
    single_re = re.compile(
        r"^(.+?)\s+(over|under)\s+(\d+\.?\d*)\s+(.+?)\s*\(([+-−–]?\d+)\)",
        re.IGNORECASE,
    )
    for line in lines:
        m = single_re.match(line)
        if m:
            legs.append({
                "player": m.group(1).strip(),
                "direction": _parse_direction(m.group(2)),
                "line": float(m.group(3)),
                "prop_type": _normalize_prop(m.group(4)),
                "odds": _parse_odds(m.group(5)),
                "book": "FD",
            })

    if legs:
        return legs

    # Multi-line block format (same as DK pattern 2 but tag as FD)
    result = _try_dk(text)
    if result:
        for leg in result:
            leg["book"] = "FD"
        return result

    return None


# ── Fanatics ───────────────────────────────────────────────────────

# Fanatics (formerly PointsBet) paste format varies, but common pattern:
#   Player Name
#   Over X.X Prop Type
#   odds
# or inline: "Player Name: Over X.X Points @ -110"

def _try_fanatics(text: str) -> Optional[list[dict]]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    legs = []

    # Inline with @ separator: "Player: Over X.X PropType @ odds"
    inline_re = re.compile(
        r"^(.+?)\s*[:]\s*(over|under)\s+(\d+\.?\d*)\s+(.+?)\s*[@]\s*([+-−–]?\d+)",
        re.IGNORECASE,
    )
    for line in lines:
        m = inline_re.match(line)
        if m:
            legs.append({
                "player": m.group(1).strip(),
                "direction": _parse_direction(m.group(2)),
                "line": float(m.group(3)),
                "prop_type": _normalize_prop(m.group(4)),
                "odds": _parse_odds(m.group(5)),
                "book": "FAN",
            })

    if legs:
        return legs

    # Fall back to DK-style block parser
    result = _try_dk(text)
    if result:
        for leg in result:
            leg["book"] = "FAN"
        return result

    return None


# ── BetMGM ─────────────────────────────────────────────────────────

# MGM paste format:
#   Jalen Brunson - Points - Over 24.5
#   -110
# or
#   Jalen Brunson Points Over 24.5 @ -110

def _try_mgm(text: str) -> Optional[list[dict]]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    legs = []

    # "Player - PropType - Over/Under X.X" with odds on next line or inline
    dash_re = re.compile(
        r"^(.+?)\s*[-–—]\s*(.+?)\s*[-–—]\s*(over|under)\s+(\d+\.?\d*)",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        m = dash_re.match(line)
        if m:
            odds = None
            # Check for inline odds: "... @ -110" or trailing odds
            at_m = re.search(r"[@]\s*([+-−–]?\d+)", line)
            if at_m:
                odds = _parse_odds(at_m.group(1))
            elif i + 1 < len(lines):
                odds = _parse_odds(lines[i + 1])
            legs.append({
                "player": m.group(1).strip(),
                "prop_type": _normalize_prop(m.group(2)),
                "direction": _parse_direction(m.group(3)),
                "line": float(m.group(4)),
                "odds": odds,
                "book": "MGM",
            })

    if legs:
        return legs

    # "Player PropType Over/Under X.X @ odds" (no dashes)
    inline_re = re.compile(
        r"^(.+?)\s+(points|rebounds|assists|threes|pts|reb|ast|steals|blocks|strikeouts|hits|runs|total bases)"
        r"\s+(over|under)\s+(\d+\.?\d*)\s*(?:[@]\s*)?([+-−–]?\d+)?",
        re.IGNORECASE,
    )
    for line in lines:
        m = inline_re.match(line)
        if m:
            legs.append({
                "player": m.group(1).strip(),
                "prop_type": _normalize_prop(m.group(2)),
                "direction": _parse_direction(m.group(3)),
                "line": float(m.group(4)),
                "odds": _parse_odds(m.group(5)) if m.group(5) else None,
                "book": "MGM",
            })

    if legs:
        return legs

    # Final fallback to DK block parser
    result = _try_dk(text)
    if result:
        for leg in result:
            leg["book"] = "MGM"
        return result

    return None


# ── Generic fallback ───────────────────────────────────────────────

def _try_generic(text: str) -> Optional[list[dict]]:
    """
    Last resort: scan for any line containing over/under + a number,
    with a name on the same or preceding line.
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    legs = []

    # Pattern: anything with "over/under X.X" somewhere
    ou_re = re.compile(r"(over|under)\s+(\d+\.?\d*)", re.IGNORECASE)
    # Odds pattern: standalone American odds
    odds_re = re.compile(r"([+-−–]\d{3,})")

    for i, line in enumerate(lines):
        m = ou_re.search(line)
        if not m:
            continue

        direction = _parse_direction(m.group(1))
        line_val = float(m.group(2))

        # Try to extract player name: text before "over/under"
        before = line[:m.start()].strip().rstrip("-–— :").strip()
        # If before is empty, check previous line
        if not before and i > 0:
            before = lines[i - 1].strip()
        # Skip if "before" looks like odds or a number
        if before and re.match(r"^[+-−]?\d+$", before):
            before = ""

        # Try to extract prop type: text after the number
        after = line[m.end():].strip()
        prop = _normalize_prop(after) if after else "points"

        # Scan for odds in this line or the next
        odds = None
        om = odds_re.search(line[m.end():])
        if om:
            odds = _parse_odds(om.group(1))
        elif i + 1 < len(lines):
            om2 = odds_re.search(lines[i + 1])
            if om2:
                odds = _parse_odds(om2.group(1))
        # Also check for parenthesized odds
        paren = re.search(r"\(([+-−–]?\d+)\)", line)
        if paren and odds is None:
            odds = _parse_odds(paren.group(1))

        if before:
            legs.append({
                "player": before,
                "direction": direction,
                "line": line_val,
                "prop_type": prop,
                "odds": odds,
                "book": "unknown",
            })

    return legs if legs else None


# ── Book detection ─────────────────────────────────────────────────

def _detect_book(text: str) -> Optional[str]:
    low = text.lower()
    if "draftkings" in low or "dk " in low or "draft kings" in low:
        return "DK"
    if "fanduel" in low or "fd " in low or "fan duel" in low:
        return "FD"
    if "fanatics" in low:
        return "FAN"
    if "betmgm" in low or "mgm" in low or "bet mgm" in low:
        return "MGM"
    if "bovada" in low:
        return "BOV"
    if "espn" in low or "espn bet" in low:
        return "ESPN"
    return None


# ── Main entry point ───────────────────────────────────────────────

def parse_slip(text: str) -> list[dict]:
    """
    Try each book-specific parser in order, then generic fallback.
    Returns list of parsed legs (may be empty).

    Each leg has: player, prop_type, line, direction, odds, book.
    Any field may be None if not parseable.
    """
    if not text or not text.strip():
        return []

    detected_book = _detect_book(text)

    # Try book-specific parsers based on detected book
    parsers = [
        ("DK", _try_dk),
        ("FD", _try_fd),
        ("FAN", _try_fanatics),
        ("MGM", _try_mgm),
    ]

    # Try detected book first
    if detected_book:
        for name, fn in parsers:
            if name == detected_book:
                result = fn(text)
                if result:
                    return result

    # Try all parsers in order
    for name, fn in parsers:
        result = fn(text)
        if result:
            # Override book if we detected one from the text
            if detected_book:
                for leg in result:
                    leg["book"] = detected_book
            return result

    # Generic fallback
    result = _try_generic(text)
    if result:
        if detected_book:
            for leg in result:
                leg["book"] = detected_book
        return result

    return []


def format_confirmation(legs: list[dict], parlay: bool = False) -> str:
    """Format parsed legs for user confirmation in Telegram."""
    if not legs:
        return "Could not parse any bets from that text."

    lines = []
    if parlay and len(legs) > 1:
        lines.append(f"🎯 Parsed {len(legs)}-leg parlay:")
    else:
        lines.append(f"🎯 Parsed {len(legs)} bet(s):")

    lines.append("─────────────────")
    for i, leg in enumerate(legs, 1):
        player = leg.get("player") or "?"
        prop = leg.get("prop_type") or "?"
        direction = (leg.get("direction") or "?").upper()
        line_val = leg.get("line")
        odds = leg.get("odds")
        book = leg.get("book") or "?"

        line_str = f"{line_val}" if line_val is not None else "?"
        odds_str = f"{odds:+d}" if odds is not None else "?"

        prefix = f"  Leg {i}: " if len(legs) > 1 else "  "
        lines.append(f"{prefix}{player}")
        lines.append(f"    {prop.title()} {direction} {line_str} ({odds_str}) [{book}]")

    lines.append("─────────────────")
    lines.append("Reply: ✅ Yes  |  ✏️ Edit  |  ❌ Cancel")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    tests = [
        # DK block format
        "Jalen Brunson\nOver 24.5 Points\n-110",
        # DK parlay
        "Jalen Brunson - Over 24.5 Pts\nAnthony Edwards - Over 22.5 Pts\nOdds: +350",
        # FD inline
        "Jalen Brunson Over 24.5 Points (-110)",
        # MGM dash format
        "Jalen Brunson - Points - Over 24.5\n-110",
        # Fanatics inline
        "Brunson: Over 24.5 Points @ -110",
        # Generic
        "some random format Brunson over 24.5 pts -110",
    ]
    for t in tests:
        print(f"INPUT: {t!r}")
        legs = parse_slip(t)
        for leg in legs:
            print(f"  → {leg}")
        print()
