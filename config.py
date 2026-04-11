"""Bet Tracker — central config loader.

Loads environment variables from .env and exposes typed constants.
Mirrors the dotenv pattern from nba_quant_bot/prop_scanner/config.py.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# On Streamlit Cloud, env vars don't auto-populate from the secrets UI —
# they live in st.secrets. Copy them into os.environ so the rest of this
# module (and everything downstream) can use the same lookup path.
try:
    import streamlit as _st  # type: ignore

    if hasattr(_st, "secrets"):
        for _key in (
            "BET_TRACKER_BOT_TOKEN",
            "BET_TRACKER_TZ",
            "BET_TRACKER_DB_PATH",
            "GITHUB_TOKEN",
            "BET_TRACKER_GIST_ID",
        ):
            try:
                if _key in _st.secrets and not os.environ.get(_key):
                    os.environ[_key] = str(_st.secrets[_key])
            except Exception:  # noqa: BLE001 — st.secrets raises if file missing
                pass
except ImportError:
    pass

# --- Telegram ---------------------------------------------------------------
BET_TRACKER_BOT_TOKEN: str = os.getenv("BET_TRACKER_BOT_TOKEN", "").strip()

# --- Timezone (used by APScheduler weekly review) --------------------------
BET_TRACKER_TZ: str = os.getenv("BET_TRACKER_TZ", "America/New_York")

# --- Database ---------------------------------------------------------------
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "bet_tracker.db"
BET_TRACKER_DB_PATH: Path = Path(
    os.getenv("BET_TRACKER_DB_PATH", str(DEFAULT_DB_PATH))
)
SCHEMA_PATH: Path = PROJECT_ROOT / "db" / "schema.sql"

# --- Gist sync (option B) ---------------------------------------------------
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "").strip()
BET_TRACKER_GIST_ID: str = os.getenv("BET_TRACKER_GIST_ID", "").strip()
GIST_FILENAME: str = "bet_tracker_state.json"

# Dashboard uses remote mode when a gist ID is configured.
USE_REMOTE_DB: bool = bool(BET_TRACKER_GIST_ID) and BET_TRACKER_GIST_ID != "PASTE_GIST_ID_HERE"

# --- Misc -------------------------------------------------------------------
DEFAULT_BANKROLL: float = 1000.0
LEADERBOARD_MIN_BETS: int = 1


def require_github_token() -> str:
    if not GITHUB_TOKEN or GITHUB_TOKEN == "PASTE_YOUR_PAT_HERE":
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Needed for gist sync. "
            "Create a PAT at https://github.com/settings/tokens with 'gist' scope."
        )
    return GITHUB_TOKEN


def require_gist_id() -> str:
    if not BET_TRACKER_GIST_ID or BET_TRACKER_GIST_ID == "PASTE_GIST_ID_HERE":
        raise RuntimeError(
            "BET_TRACKER_GIST_ID is not set. "
            "Run `python scripts/create_gist.py` to create one."
        )
    return BET_TRACKER_GIST_ID


def require_telegram_token() -> str:
    """Return the bot token or raise if missing.

    Use this in entrypoints (telegram_bot.py, scheduler.py) so the
    Streamlit pages can still import config without crashing when the
    token is unset.
    """
    if not BET_TRACKER_BOT_TOKEN or BET_TRACKER_BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError(
            "BET_TRACKER_BOT_TOKEN is not set. "
            "Edit ~/bet_tracker/.env and paste your @BotFather token."
        )
    return BET_TRACKER_BOT_TOKEN
