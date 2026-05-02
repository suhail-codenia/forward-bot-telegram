import asyncio
import logging
import os
import sqlite3
import sys
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
)
from telethon.tl.custom import Message
from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent

load_dotenv(_SCRIPT_DIR / ".env")

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE = os.getenv("TELEGRAM_PHONE")
PASSWORD = os.getenv("TELEGRAM_PASSWORD") or None
SOURCE_CHANNEL_ID = os.getenv("SOURCE_CHANNEL_ID")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SESSION_PATH = os.getenv("TELETHON_SESSION") or str(_SCRIPT_DIR / "telethon.session")
DB_PATH = _SCRIPT_DIR / "message_map.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class MirrorDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS message_map ("
                "source_id INTEGER NOT NULL, "
                "target_chat_id INTEGER NOT NULL, "
                "target_msg_id INTEGER NOT NULL, "
                "PRIMARY KEY (source_id, target_chat_id))"
            )
            conn.commit()

    async def set_mapping(self, source_id: int, target_chat_id: int, target_msg_id: int):
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO message_map (source_id, target_chat_id, target_msg_id) VALUES (?, ?, ?)",
                    (source_id, target_chat_id, target_msg_id),
                )
                conn.commit()

    async def get_mappings(self, source_id: int) -> list[tuple[int, int]]:
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute(
                    "SELECT target_chat_id, target_msg_id FROM message_map WHERE source_id = ?", (source_id,)
                ).fetchall()


def register_handlers(client: TelegramClient, db: MirrorDB, source_id: int, target_id: int):
    @client.on(events.NewMessage(chats=source_id))
    async def on_new_message(event):
        msg: Message = event.message
        try:
            if msg.media:
                copied = await client.send_file(
                    target_id, msg.media, caption=msg.message or None
                )
            else:
                copied = await client.send_message(
                    target_id, msg.text or msg.message or ""
                )
            await db.set_mapping(msg.id, target_id, copied.id)
            logger.info("Copied src=%s -> tgt=%s:%s", msg.id, target_id, copied.id)
        except Exception:
            logger.exception("Failed to copy %s to %s", msg.id, target_id)

    @client.on(events.MessageEdited(chats=source_id))
    async def on_edit_message(event):
        mappings = await db.get_mappings(event.message.id)
        if not mappings:
            return
        msg = event.message
        for target_chat_id, target_msg_id in mappings:
            try:
                if msg.text or msg.message:
                    await client.edit_message(
                        target_chat_id, target_msg_id, text=msg.text or msg.message
                    )
                    logger.info("Edited src=%s -> tgt=%s:%s", msg.id, target_chat_id, target_msg_id)
            except Exception:
                logger.exception(
                    "Failed to edit %s:%s for source %s", target_chat_id, target_msg_id, msg.id
                )


async def main():
    missing = []
    if not API_ID:
        missing.append("TELEGRAM_API_ID")
    if not API_HASH:
        missing.append("TELEGRAM_API_HASH")
    if not PHONE:
        missing.append("TELEGRAM_PHONE")
    if not SOURCE_CHANNEL_ID:
        missing.append("SOURCE_CHANNEL_ID")
    if not TARGET_CHANNEL_ID:
        missing.append("TARGET_CHANNEL_ID")
    if missing:
        logger.error("Set in .env: %s", ", ".join(missing))
        return

    source_id = int(SOURCE_CHANNEL_ID)
    target_id = int(TARGET_CHANNEL_ID)

    client = TelegramClient(SESSION_PATH, int(API_ID), API_HASH)
    await client.start(phone=PHONE, password=PASSWORD)

    db = MirrorDB(DB_PATH)
    register_handlers(client, db, source_id, target_id)

    me = await client.get_me()
    logger.info(
        "Logged in as %s (%s) | source=%s target=%s",
        me.first_name,
        me.id,
        source_id,
        target_id,
    )
    logger.info("DB: %s | session: %s", DB_PATH, SESSION_PATH)

    try:
        await client.run_until_disconnected()
    except (AuthKeyUnregisteredError, AuthKeyInvalidError, AuthKeyDuplicatedError) as e:
        _base = Path(SESSION_PATH).name
        logger.error(
            "Telegram rejected this session (%s). Revoked keys often happen after "
            "'Terminate other sessions', a second Telethon/Telegram client on the same file, "
            "or moderation. Stop other bots using this account; delete `%s` plus `%s-journal`, "
            "`%s-wal`, `%s-shm` if present; then run again to sign in.",
            type(e).__name__,
            SESSION_PATH,
            _base,
            _base,
            _base,
        )
        try:
            await client.disconnect()
        except Exception:
            logger.exception("disconnect() after auth error failed (ignored)")
        sys.exit(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
