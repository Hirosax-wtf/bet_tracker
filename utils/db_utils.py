"""SQLite helper for Bet Tracker.

Mirrors the prop_scanner Database pattern: single class with a context-manager
connection, WAL mode, and idempotent schema initialization. The schema lives
as a separate file (db/schema.sql) so the dashboard, bot, and scheduler all
share one source of truth.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from config import BET_TRACKER_DB_PATH, SCHEMA_PATH


def new_id(prefix: str = "") -> str:
    """Short unique id (8 hex chars). Prefix is optional, e.g. 'b_'."""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class Database:
    """Thin sqlite3 wrapper with context-manager connection."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or BET_TRACKER_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    # ------------------------------------------------------------------
    # Connection / lifecycle
    # ------------------------------------------------------------------
    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with row_factory=Row and WAL enabled."""
        connection = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Apply schema.sql idempotently. Safe to call repeatedly."""
        if self._initialized:
            return
        ddl = SCHEMA_PATH.read_text()
        with self.conn() as c:
            c.executescript(ddl)
        self._initialized = True

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def fetch_all(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> list[sqlite3.Row]:
        with self.conn() as c:
            cur = c.execute(sql, params or [])
            return cur.fetchall()

    def fetch_one(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> sqlite3.Row | None:
        with self.conn() as c:
            cur = c.execute(sql, params or [])
            return cur.fetchone()

    def execute(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> int:
        """Execute a write and return rowcount."""
        with self.conn() as c:
            cur = c.execute(sql, params or [])
            return cur.rowcount

    def execute_many(
        self, sql: str, rows: Iterable[Sequence[Any]]
    ) -> None:
        with self.conn() as c:
            c.executemany(sql, rows)


# Module-level singleton — import-and-go style used by the bot/scheduler.
# Streamlit pages should call get_db() through @st.cache_resource instead.
db = Database()
