"""SQLite persistence for mirror targets and message id mappings."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

DB_PATH = Path("message_map.db")
SOURCE_CFG_KEY = "source_channel_id"


class MirrorDB:
    """Async-safe SQLite store.

    Tables
    ------
    target_channels  – every channel the bot should mirror INTO
    message_map      – source_id  ->  (target_chat_id, target_message_id)
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS target_channels (
                    chat_id INTEGER PRIMARY KEY,
                    title   TEXT
                );

                CREATE TABLE IF NOT EXISTS message_map (
                    source_id     INTEGER NOT NULL,
                    target_chat_id INTEGER NOT NULL,
                    target_msg_id INTEGER NOT NULL,
                    PRIMARY KEY (source_id, target_chat_id)
                );

                CREATE TABLE IF NOT EXISTS bot_config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()

    async def get_source_channel_id(self) -> int | None:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM bot_config WHERE key = ?",
                    (SOURCE_CFG_KEY,),
                ).fetchone()
                if row is None:
                    return None
                try:
                    return int(row[0])
                except ValueError:
                    return None

    async def set_source_channel_id(self, chat_id: int) -> None:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bot_config (key, value) "
                    "VALUES (?, ?)",
                    (SOURCE_CFG_KEY, str(chat_id)),
                )
                conn.commit()

    async def add_target(self, chat_id: int, title: str | None = None):
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO target_channels (chat_id, title) "
                    "VALUES (?, ?)",
                    (chat_id, title),
                )
                conn.commit()

    async def remove_target(self, chat_id: int):
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM message_map WHERE target_chat_id = ?",
                    (chat_id,),
                )
                conn.execute(
                    "DELETE FROM target_channels WHERE chat_id = ?",
                    (chat_id,),
                )
                conn.commit()

    async def list_targets(self) -> list[tuple[int, str | None]]:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT chat_id, title FROM target_channels"
                ).fetchall()
                return [(r[0], r[1]) for r in rows]

    async def is_target(self, chat_id: int) -> bool:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM target_channels WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()
                return row is not None

    async def set_mapping(
        self, source_id: int, target_chat_id: int, target_msg_id: int
    ):
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO message_map "
                    "(source_id, target_chat_id, target_msg_id) "
                    "VALUES (?, ?, ?)",
                    (source_id, target_chat_id, target_msg_id),
                )
                conn.commit()

    async def get_mappings(self, source_id: int) -> list[tuple[int, int]]:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT target_chat_id, target_msg_id FROM message_map "
                    "WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
                return [(r[0], r[1]) for r in rows]

    async def delete_mappings_for_source(self, source_id: int) -> None:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM message_map WHERE source_id = ?",
                    (source_id,),
                )
                conn.commit()

    async def delete_mapping(self, source_id: int, target_chat_id: int) -> None:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM message_map WHERE source_id = ? "
                    "AND target_chat_id = ?",
                    (source_id, target_chat_id),
                )
                conn.commit()
