"""Gist-backed state sync (option B architecture).

The Telegram bot/scheduler run on Hiro's local box and write to a SQLite
file. Streamlit Community Cloud can't see that file, so after every write
the bot PATCHes a snapshot of the full state into a private-ish Gist.
The dashboard GETs the snapshot and loads it into an in-memory SQLite
with the same schema — so every query in utils/stats.py and
utils/achievements.py works identically on both sides.

Security model: the Gist is public-by-obscurity (matches sports_dashboard).
The URL is unguessable. Bet data is not sensitive enough to warrant
hosted-DB infra for now.

Writes go through push_state() which is best-effort: a failing sync
logs a warning but never crashes the bot.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from config import (
    BET_TRACKER_GIST_ID,
    GIST_FILENAME,
    GITHUB_TOKEN,
    SCHEMA_PATH,
    require_github_token,
    require_gist_id,
)
from utils.db_utils import Database

log = logging.getLogger("bet_tracker.gist_sync")

# Tables that need to cross the sync boundary. Order matters for FK-friendly
# re-insertion into the in-memory clone.
SYNC_TABLES = [
    "users",
    "groups",
    "group_members",
    "bets",
    "weekly_summaries",
    "achievements",
]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def dump_state(db: Database) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of every sync table."""
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "tables": {},
    }
    for table in SYNC_TABLES:
        rows = db.fetch_all(f"SELECT * FROM {table}")
        snapshot["tables"][table] = [dict(r) for r in rows]
    return snapshot


def load_into_memory(snapshot: dict[str, Any]) -> Database:
    """Materialize a snapshot into a shared in-memory SQLite DB.

    Returns a Database instance that all dashboard queries can use.
    Uses `file::memory:?cache=shared` so the connection context managers
    in Database still work (each call opens a new connection to the same
    in-memory DB).
    """
    mem_uri = f"file:bet_tracker_mem_{id(snapshot):x}?mode=memory&cache=shared"

    class _MemDatabase(Database):
        def __init__(self, uri: str) -> None:
            self.db_path = uri
            self._initialized = False
            # Keep a persistent connection so the in-memory DB isn't torn down
            # between context-manager entries.
            self._keepalive = sqlite3.connect(uri, uri=True)

        def conn(self):  # type: ignore[override]
            from contextlib import contextmanager

            @contextmanager
            def _ctx():
                c = sqlite3.connect(self.db_path, uri=True)
                c.row_factory = sqlite3.Row
                try:
                    c.execute("PRAGMA foreign_keys=ON")
                    yield c
                    c.commit()
                finally:
                    c.close()

            return _ctx()

    mem_db = _MemDatabase(mem_uri)
    mem_db.initialize = lambda: None  # schema applied here, don't double-apply
    ddl = SCHEMA_PATH.read_text()
    with mem_db.conn() as c:
        c.executescript(ddl)
        for table in SYNC_TABLES:
            rows = snapshot.get("tables", {}).get(table, [])
            if not rows:
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join("?" for _ in cols)
            col_list = ",".join(cols)
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
            c.executemany(sql, [[r.get(k) for k in cols] for r in rows])
    return mem_db


# ---------------------------------------------------------------------------
# Gist I/O
# ---------------------------------------------------------------------------
_API = "https://api.github.com/gists"


def _request(url: str, *, method: str, token: str | None, body: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "bet-tracker-sync")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def create_gist(description: str = "Bet Tracker state") -> str:
    """One-time helper. Returns the new gist ID."""
    token = require_github_token()
    body = {
        "description": description,
        "public": False,  # "secret" gist — still accessible via URL
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(
                    {"schema_version": 1, "tables": {t: [] for t in SYNC_TABLES}},
                    indent=2,
                )
            }
        },
    }
    data = _request(_API, method="POST", token=token, body=json.dumps(body).encode())
    return json.loads(data)["id"]


def push_state(snapshot: dict[str, Any]) -> None:
    """PATCH the configured gist with a new snapshot."""
    token = require_github_token()
    gist_id = require_gist_id()
    body = {
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(snapshot, default=str)
            }
        }
    }
    _request(
        f"{_API}/{gist_id}",
        method="PATCH",
        token=token,
        body=json.dumps(body).encode(),
    )


def pull_state() -> dict[str, Any]:
    """GET the gist and return the decoded snapshot dict."""
    gist_id = require_gist_id()
    token = GITHUB_TOKEN or None  # optional but avoids rate limits
    data = _request(f"{_API}/{gist_id}", method="GET", token=token)
    gist = json.loads(data)
    content = gist["files"][GIST_FILENAME]["content"]
    return json.loads(content)


# ---------------------------------------------------------------------------
# Fire-and-forget helper used by the bot
# ---------------------------------------------------------------------------
def push_async(db: Database) -> None:
    """Background push. Errors are logged, never raised.

    Called from the Telegram handlers right after a successful write. Using a
    short-lived thread keeps the bot response latency flat — the user sees
    their confirmation message immediately and the sync happens in parallel.
    """
    if not BET_TRACKER_GIST_ID or BET_TRACKER_GIST_ID == "PASTE_GIST_ID_HERE":
        return  # gist not configured yet — silently no-op in local-only mode

    try:
        snapshot = dump_state(db)
    except Exception:  # noqa: BLE001
        log.exception("gist dump_state failed")
        return

    def _worker() -> None:
        try:
            push_state(snapshot)
            log.info("gist push ok (%d bets)", len(snapshot["tables"].get("bets", [])))
        except urllib.error.HTTPError as e:
            log.warning("gist push http error: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("gist push failed")

    threading.Thread(target=_worker, daemon=True, name="gist-push").start()
