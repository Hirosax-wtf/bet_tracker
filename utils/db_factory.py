"""Return the right Database for the current process.

- Telegram bot + scheduler (on Hiro's box): always file-backed. They are the
  authoritative writers and they push gist snapshots after each write.
- Streamlit dashboard (on Streamlit Cloud): gist-backed. Cached at the
  module level so every page hit within the TTL shares one snapshot load.
"""
from __future__ import annotations

import logging
import time

from config import USE_REMOTE_DB
from utils.db_utils import Database, db as _file_db

log = logging.getLogger("bet_tracker.db_factory")

_CACHE_TTL_SECONDS = 60
_cached: tuple[float, Database] | None = None


def _load_remote() -> Database:
    """Pull the gist and build an in-memory DB from it."""
    from utils.gist_sync import load_into_memory, pull_state

    snapshot = pull_state()
    return load_into_memory(snapshot)


def get_db() -> Database:
    """Return the right DB for this process.

    For the dashboard on Streamlit Cloud we cache for 60s so hammering the
    pages doesn't hammer the gist API. For local runs we return the file db
    directly — schema.initialize() is idempotent.
    """
    global _cached
    if not USE_REMOTE_DB:
        _file_db.initialize()
        return _file_db

    now = time.time()
    if _cached and (now - _cached[0]) < _CACHE_TTL_SECONDS:
        return _cached[1]

    try:
        mem = _load_remote()
    except Exception:  # noqa: BLE001
        log.exception("Failed to load remote gist — falling back to file db")
        _file_db.initialize()
        return _file_db

    _cached = (now, mem)
    return mem
