"""One-time helper: create the Bet Tracker sync gist.

Usage:
    python scripts/create_gist.py

Prints the new gist ID. Paste it into .env as BET_TRACKER_GIST_ID.
Requires GITHUB_TOKEN (PAT with 'gist' scope) already set in .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.gist_sync import create_gist


def main() -> None:
    gist_id = create_gist("Bet Tracker state snapshot")
    print(f"\n✅ Gist created.\n")
    print(f"BET_TRACKER_GIST_ID={gist_id}\n")
    print(f"URL: https://gist.github.com/{gist_id}")
    print("\nPaste that line into ~/bet_tracker/.env")


if __name__ == "__main__":
    main()
