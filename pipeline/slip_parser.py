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

# FD paste formats:
#
# Format 1 (inline): "Jalen Brunson Over 24.5 Points (-110)"
#
# Format 2 (SGP from app):
#   SGP
#   Same Game Parlay™
#   Houston Astros (T Imai) @ Seattle Mariners (E Hancock)
#   +169
#   9:41PM ET
#   Houston Astros          ← moneyline leg
#   MONEYLINE
#   Tatsuya Imai 5+ Strikeouts    ← prop leg (N+ format)
#   TATSUYA IMAI - ALT STRIKEOUTS ← label (skip)

_FD_SKIP_CI = re.compile(
    r"(?:^sgp$|same game parlay|total wager|^home$|all sports|my bets|casino)",
    re.IGNORECASE,
)
_FD_SKIP_CS = re.compile(
    r"(?:^\$[\d,.]+$|^[A-Z][A-Z\s-]{4,}$)"  # ALL-CAPS labels only (case-sensitive)
)

def _fd_skip(line: str) -> bool:
    return bool(_FD_SKIP_CI.match(line) or _FD_SKIP_CS.match(line))

# "Player N+ PropType" — e.g. "Tatsuya Imai 5+ Strikeouts"
_FD_PROP_LEG_RE = re.compile(
    r"^(.+?)\s+(\d+\.?\d*)\+\s+(.+)$",
)

# "Player Over/Under N.N PropType" — e.g. "Brunson Over 24.5 Points"
_FD_OU_LEG_RE = re.compile(
    r"^(.+?)\s+(over|under)\s+(\d+\.?\d*)\s+(.+?)(?:\s*\(([+-−–]?\d+)\))?$",
    re.IGNORECASE,
)

# Game line: "Team (Pitcher) @ Team (Pitcher)" or "Team @ Team"
_FD_GAME_RE = re.compile(
    r"^(.+?)\s+(?:@|vs\.?)\s+(.+)$",
    re.IGNORECASE,
)

# SGP odds: "+169" or "-134"
_FD_SGP_ODDS_RE = re.compile(r"^[+-−–]\d{2,}$")

# Time: "9:41PM ET"
_FD_TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)\s*ET", re.IGNORECASE)


def _try_fd(text: str) -> Optional[list[dict]]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return None

    legs = []
    current_odds = None
    current_game = None
    sgp_group = 0  # increments on each new SGP block

    for i, line in enumerate(lines):
        # New SGP block
        if line.strip().upper() == "SGP" or "same game parlay" in line.lower():
            sgp_group += 1
            continue

        # Skip boilerplate
        if _FD_TIME_RE.match(line):
            continue
        if _fd_skip(line):
            continue

        # SGP odds line
        if _FD_SGP_ODDS_RE.match(line):
            current_odds = _parse_odds(line)
            if sgp_group == 0:
                sgp_group = 1
            continue

        # Game line — extract for context
        game_m = _FD_GAME_RE.match(line)
        if game_m and "+" not in line and "over" not in line.lower() and "under" not in line.lower():
            current_game = line
            continue

        # MONEYLINE leg: team name followed by "MONEYLINE" on next line
        if i + 1 < len(lines) and lines[i + 1].strip().upper() == "MONEYLINE":
            legs.append({
                "player": line.strip(),  # team name
                "direction": "moneyline",
                "line": 0,
                "prop_type": "moneyline",
                "odds": current_odds,
                "book": "FD",
                "group": sgp_group,
                "game": current_game,
            })
            continue
        if line.strip().upper() == "MONEYLINE":
            continue

        # ALL CAPS label line (e.g. "TATSUYA IMAI - ALT STRIKEOUTS") — skip
        if line == line.upper() and len(line) > 5 and "-" in line:
            continue

        # Prop leg: "Player N+ PropType"
        prop_m = _FD_PROP_LEG_RE.match(line)
        if prop_m:
            legs.append({
                "player": prop_m.group(1).strip(),
                "direction": "over",
                "line": float(prop_m.group(2)),
                "prop_type": _normalize_prop(prop_m.group(3)),
                "odds": current_odds,
                "book": "FD",
                "group": sgp_group,
                "game": current_game,
            })
            continue

        # Prop leg: "Player Over/Under N.N PropType (odds)"
        ou_m = _FD_OU_LEG_RE.match(line)
        if ou_m:
            legs.append({
                "player": ou_m.group(1).strip(),
                "direction": _parse_direction(ou_m.group(2)),
                "line": float(ou_m.group(3)),
                "prop_type": _normalize_prop(ou_m.group(4)),
                "odds": _parse_odds(ou_m.group(5)) if ou_m.group(5) else current_odds,
                "book": "FD",
                "group": sgp_group,
                "game": current_game,
            })
            continue

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

# Real Fanatics paste format (from app share):
#   Fanatics Sportsbook
#   11 Leg SGP
#   Wager $25.00
#   Payout $371.88
#   +1387
#   ...
#   4+                              ← line (4+ means over 4)
#   Brandin Podziemski              ← player
#   - Rebounds                      ← prop type
#   Los Angeles Lakers at Golden    ← game (may wrap)
#   State Warriors
#   ...
#   MUST BE 21+. GAMBLING PROBLEM?  ← footer

# Threshold pattern: "4+" or "12+" or "0.5+" or "Under 3.5"
_FAN_THRESHOLD_RE = re.compile(r"^(\d+\.?\d*)\+$")
_FAN_UNDER_RE = re.compile(r"^(?:under|u)\s*(\d+\.?\d*)$", re.IGNORECASE)

# Prop line: "- Rebounds" or "- Points" etc.
_FAN_PROP_RE = re.compile(r"^-\s*(.+)$")

# Overall odds in header: "+1387" or "-250"
_FAN_PARLAY_ODDS_RE = re.compile(r"^[+-−–]\d{3,}$")

# Lines to skip
_FAN_SKIP = re.compile(
    r"(?:fanatics|sportsbook|leg sgp|wager|payout|fcash|must be 21|gambling problem|call 1-800|"
    r"rg$|betslip|share|placed|open|settled|won|lost|void)",
    re.IGNORECASE,
)


def _try_fanatics(text: str) -> Optional[list[dict]]:
    # Quick check: must contain "fanatics" or the N+ threshold pattern
    if "fanatics" not in text.lower() and not re.search(r"^\d+\+$", text, re.MULTILINE):
        return None

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return None

    # Extract overall parlay odds from header
    parlay_odds = None
    for line in lines[:10]:
        if _FAN_PARLAY_ODDS_RE.match(line):
            parlay_odds = _parse_odds(line)
            break

    # Parse legs by scanning for threshold → player → prop → game pattern
    legs = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip header/footer lines
        if _FAN_SKIP.search(line):
            i += 1
            continue

        # Check for threshold: "4+" or "12+"
        threshold_m = _FAN_THRESHOLD_RE.match(line)
        under_m = _FAN_UNDER_RE.match(line) if not threshold_m else None

        if threshold_m or under_m:
            if threshold_m:
                line_val = float(threshold_m.group(1))
                direction = "over"
            else:
                line_val = float(under_m.group(1))
                direction = "under"

            # Next line should be player name
            player = None
            prop_type = None
            if i + 1 < len(lines):
                # Player name: could be "Brandin Podziemski" or
                # "Brandin Podziemski\n- Rebounds" or "Brandin Podziemski - Rebounds"
                next_line = lines[i + 1]
                # Check if player + prop are on same line: "Player - PropType"
                dash_split = re.match(r"^(.+?)\s*-\s*(points|rebounds|assists|steals|blocks|threes|3-pointers|strikeouts|hits|runs|total bases|home runs|touchdowns|goals|passing yards|rushing yards|receiving yards).*$", next_line, re.IGNORECASE)
                if dash_split:
                    player = dash_split.group(1).strip()
                    prop_type = _normalize_prop(dash_split.group(2))
                    i += 2
                else:
                    player = next_line.strip()
                    i += 2
                    # Look for prop on next line: "- Rebounds"
                    if i < len(lines):
                        prop_m = _FAN_PROP_RE.match(lines[i])
                        if prop_m:
                            prop_type = _normalize_prop(prop_m.group(1))
                            i += 1

                # Skip game lines (contain "at" or known team words)
                while i < len(lines) and not _FAN_THRESHOLD_RE.match(lines[i]) and not _FAN_UNDER_RE.match(lines[i]):
                    if _FAN_SKIP.search(lines[i]):
                        i += 1
                        break
                    # Game lines: "Los Angeles Lakers at Golden" / "State Warriors"
                    if " at " in lines[i] or _is_team_continuation(lines[i], lines[i-1] if i > 0 else ""):
                        i += 1
                        continue
                    break

            if player:
                legs.append({
                    "player": player,
                    "direction": direction,
                    "line": line_val,
                    "prop_type": prop_type or "points",
                    "odds": parlay_odds,
                    "book": "FAN",
                })
            continue

        i += 1

    return legs if legs else None


def _is_team_continuation(line: str, prev: str) -> bool:
    """Heuristic: is this line a wrapped team name from the previous line?"""
    # Common team name fragments that appear on wrapped lines
    team_fragments = [
        "warriors", "lakers", "celtics", "nets", "knicks", "bucks", "heat",
        "76ers", "sixers", "suns", "nuggets", "clippers", "kings", "hawks",
        "bulls", "cavaliers", "mavericks", "rockets", "pacers", "grizzlies",
        "pelicans", "thunder", "magic", "pistons", "raptors", "jazz",
        "timberwolves", "blazers", "trail blazers", "spurs", "hornets", "wizards",
        "state warriors", "trail blazers",
        # MLB
        "yankees", "red sox", "dodgers", "mets", "cubs", "astros", "braves",
        "padres", "phillies", "orioles", "rangers", "guardians", "twins",
        "mariners", "rays", "blue jays", "white sox", "tigers", "royals",
        "pirates", "reds", "brewers", "cardinals", "giants", "rockies",
        "diamondbacks", "nationals", "marlins", "athletics",
    ]
    low = line.lower().strip()
    if any(t in low for t in team_fragments):
        return True
    # If prev line ended with "at" + partial city, this is continuation
    if prev.strip().endswith(("Golden", "Los Angeles", "San Antonio", "New York",
                              "Oklahoma City", "San Francisco", "New Orleans",
                              "Salt Lake", "Portland Trail")):
        return True
    return False


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
    Flexible last-resort parser for freeform pasted text.

    Catches all common formats users paste from screenshots:
      "Brunson 25+ pts"
      "Brunson over 24.5 points"
      "Brunson Over 24.5 Pts (-110)"
      "Jalen Brunson - Over 24.5 Points"
      "KAT 10+ reb"
      "Maxey o4.5 ast"
      "Warriors ML"
      "Warriors moneyline"
      "GSW -3.5"

    Odds are optional — most screenshot pastes don't include them.
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    legs = []

    # Skip lines that are clearly boilerplate
    skip_re = re.compile(
        r"(?:^(?:same game parlay|sgp|total wager|wager|payout|potential|"
        r"my bets|all sports|casino|must be 21|gambling|call 1-800|"
        r"draftkings|fanduel|fanatics|betmgm|bovada|espn bet|"
        r"placed|open|settled|won|lost|void|share|bet slip|receipt))",
        re.IGNORECASE,
    )
    time_re = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)
    money_re = re.compile(r"^\$[\d,.]+$")

    # Odds pattern
    odds_re = re.compile(r"([+-−–]\d{3,})")

    # Pattern 1: "Player N+ PropType" — e.g. "Brunson 25+ pts", "KAT 10+ reb"
    nplus_re = re.compile(
        r"^(.+?)\s+(\d+\.?\d*)\+\s*(.+?)(?:\s*\(([+-−–]?\d+)\))?$",
    )

    # Pattern 2: "Player over/under N.N PropType" — e.g. "Maxey over 4.5 ast"
    # Also handles "Player - Over 24.5 Points" and "Player o4.5 ast"
    ou_re = re.compile(
        r"^(.+?)\s*[-–—]?\s*(?:(over|under|o|u)\s*(\d+\.?\d*)\s*(.+?))(?:\s*\(([+-−–]?\d+)\))?$",
        re.IGNORECASE,
    )

    # Pattern 3: "Team ML" or "Team moneyline" or "Team -3.5"
    ml_re = re.compile(
        r"^(.+?)\s+(?:ML|moneyline)(?:\s*\(([+-−–]?\d+)\))?$",
        re.IGNORECASE,
    )
    spread_re = re.compile(
        r"^(.+?)\s+([+-]\d+\.?\d*)(?:\s*\(([+-−–]?\d+)\))?$",
    )

    for i, line in enumerate(lines):
        # Skip boilerplate
        if skip_re.match(line) or time_re.match(line) or money_re.match(line):
            continue
        # Skip standalone odds lines
        if re.match(r"^[+-−–]?\d+$", line):
            continue
        # Skip ALL-CAPS label lines (e.g. "TATSUYA IMAI - ALT STRIKEOUTS")
        if line == line.upper() and len(line) > 10 and "-" in line:
            continue

        # Try N+ format first: "Brunson 25+ pts"
        m = nplus_re.match(line)
        if m:
            legs.append({
                "player": m.group(1).strip(),
                "direction": "over",
                "line": float(m.group(2)),
                "prop_type": _normalize_prop(m.group(3)),
                "odds": _parse_odds(m.group(4)) if m.group(4) else None,
                "book": "unknown",
            })
            continue

        # Try over/under format: "Maxey over 4.5 ast"
        m = ou_re.match(line)
        if m:
            player = m.group(1).strip().rstrip("-–— :")
            dir_str = m.group(2)
            line_val = float(m.group(3))
            prop_raw = m.group(4).strip()
            # Clean trailing odds from prop: "Points (-110)" → "Points"
            prop_raw = re.sub(r"\s*\([+-−–]?\d+\)\s*$", "", prop_raw)
            prop = _normalize_prop(prop_raw) if prop_raw else "points"
            odds = _parse_odds(m.group(5)) if m.group(5) else None

            if player and not re.match(r"^[+-−]?\d+$", player):
                legs.append({
                    "player": player,
                    "direction": _parse_direction(dir_str),
                    "line": line_val,
                    "prop_type": prop,
                    "odds": odds,
                    "book": "unknown",
                })
                continue

        # Try moneyline: "Warriors ML"
        m = ml_re.match(line)
        if m:
            legs.append({
                "player": m.group(1).strip(),
                "direction": "moneyline",
                "line": 0,
                "prop_type": "moneyline",
                "odds": _parse_odds(m.group(2)) if m.group(2) else None,
                "book": "unknown",
            })
            continue

        # Try spread: "GSW -3.5"
        m = spread_re.match(line)
        if m and not re.match(r"^\d", m.group(1)):  # name shouldn't start with digit
            legs.append({
                "player": m.group(1).strip(),
                "direction": "spread",
                "line": float(m.group(2)),
                "prop_type": "spread",
                "odds": _parse_odds(m.group(3)) if m.group(3) else None,
                "book": "unknown",
            })
            continue

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
            book = detected_book or result[0].get("book", "unknown")
            if detected_book:
                for leg in result:
                    leg["book"] = detected_book
            # Merge in any legs the book-specific parser missed
            # (e.g. ML lines, spreads mixed with props)
            result = _merge_generic_extras(result, text, book)
            return result

    # Generic fallback — always run to catch lines other parsers missed
    result = _try_generic(text)
    if result:
        if detected_book:
            for leg in result:
                leg["book"] = detected_book
        return result

    return []


def _merge_generic_extras(primary: list[dict], text: str, book: str) -> list[dict]:
    """Run generic parser and merge in any legs the primary parser missed."""
    generic = _try_generic(text)
    if not generic:
        return primary

    # Build set of players already parsed (lowercase for matching)
    existing_players = {leg["player"].lower().strip() for leg in primary}

    extras = []
    for leg in generic:
        player_low = leg["player"].lower().strip()
        # Only add if this player isn't already in primary results
        if player_low not in existing_players:
            leg["book"] = book
            extras.append(leg)
            existing_players.add(player_low)

    return primary + extras


def format_confirmation(legs: list[dict], parlay: bool = False) -> str:
    """Format parsed legs for user confirmation in Telegram."""
    if not legs:
        return "Could not parse any bets from that text."

    # Group by SGP group
    from collections import defaultdict
    groups: dict[int, list] = defaultdict(list)
    for leg in legs:
        groups[leg.get("group", 0)].append(leg)

    lines = []
    n_bets = len(groups)
    if n_bets > 1:
        lines.append(f"🎯 Parsed {n_bets} SGP bet(s) ({len(legs)} total legs):")
    elif len(legs) > 1:
        lines.append(f"🎯 Parsed {len(legs)}-leg parlay:")
    else:
        lines.append(f"🎯 Parsed 1 bet:")

    for gid, group_legs in sorted(groups.items()):
        if n_bets > 1:
            odds = group_legs[0].get("odds")
            odds_str = f" ({odds:+d})" if odds is not None else ""
            game = group_legs[0].get("game") or ""
            lines.append(f"─────────────────")
            lines.append(f"SGP {gid}{odds_str}: {game}")
        else:
            lines.append("─────────────────")

        for i, leg in enumerate(group_legs, 1):
            player = leg.get("player") or "?"
            prop = leg.get("prop_type") or "?"
            direction = (leg.get("direction") or "?").upper()
            line_val = leg.get("line")

            line_str = f"{line_val}" if line_val is not None else ""
            if prop == "moneyline":
                lines.append(f"  {player} ML")
            else:
                lines.append(f"  {player} {prop.title()} {direction} {line_str}")

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
