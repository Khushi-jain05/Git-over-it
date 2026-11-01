"""LLM response cache in the SQLite index (table llm_cache)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


class Cache:
    def __init__(self, db: sqlite3.Connection, enabled: bool = True):
        self.db, self.enabled = db, enabled

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        row = self.db.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, model: str, response: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                        (key, model, response, datetime.now(UTC).isoformat()))
        self.db.commit()
