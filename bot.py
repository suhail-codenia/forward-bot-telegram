"""
Telegram Channel Mirror Bot (One-Source → Many-Targets)
========================================================
Forwards all messages from a single source channel to every registered
mirror destination.

Source channel: persisted in SQLite (`/set_source`) or bootstrapped from
optional `SOURCE_CHANNEL_ID` in `.env`. Targets: auto-registered when the bot
is added to other channels, or via `/add_target`.

Mirroring uses ``copyMessage`` (posts appear from the bot; text/caption edits
sync). Telegram does not tell bots when a channel message was deleted — use
``/delete_mirror <source_msg_id>`` after removing a post in the source channel.

Uses aiogram 3.x (async).
"""

from dataclasses import dataclass
import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import BotCommand, ChatMemberUpdated, Message
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL_ID = os.getenv("SOURCE_CHANNEL_ID")

DB_PATH = Path("message_map.db")

SOURCE_CFG_KEY = "source_channel_id"


@dataclass
class RuntimeConfig:
    """Source chat id used by handlers; set in main() and updated by /set_source."""

    source_chat_id: int | None = None


runtime = RuntimeConfig()


def _parse_optional_channel_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def is_source_chat(message: Message) -> bool:
    """True when this update is from the configured source chat."""
    return (
        runtime.source_chat_id is not None
        and message.chat.id == runtime.source_chat_id
    )

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


class MirrorDB:
    """Async-safe SQLite store.

    Tables
    ------
    target_channels  – every channel the bot should mirror INTO
    message_map      – source_id  ->  (target_chat_id, target_message_id)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
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

    # -- target channels --------------------------------------------------

    async def add_target(self, chat_id: int, title: str | None = None):
        """Register a channel as a mirror destination."""
        async with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO target_channels (chat_id, title) "
                    "VALUES (?, ?)",
                    (chat_id, title),
                )
                conn.commit()

    async def remove_target(self, chat_id: int):
        """Remove a channel and all its message mappings."""
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
        """Return all registered target channels."""
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

    # -- message mappings -------------------------------------------------

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
        """Return list of (target_chat_id, target_msg_id) for a source message."""
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


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def mirror_message(
    bot: Bot, message: Message, db: MirrorDB, source_chat_id: int
):
    """Copy each source post into targets (bot-owned copies — editable/deletable).

    Uses ``copyMessage``, not ``forwardMessage``, so mirrored posts can be edited
    and deleted via the Bot API. Copies appear as the bot's posts (no forward header).
    """
    targets = await db.list_targets()
    if not targets:
        logger.info("No target channels registered – skipping mirror")
        return

    for target_chat_id, _title in targets:
        try:
            copied = await bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=source_chat_id,
                message_id=message.message_id,
            )
            await db.set_mapping(
                message.message_id, target_chat_id, copied.message_id
            )
            logger.info(
                "Mirrored  src=%s  ->  tgt=%s:%s  (type=%s)",
                message.message_id,
                target_chat_id,
                copied.message_id,
                message.content_type,
            )
        except Exception:
            logger.exception(
                "Failed to mirror %s to %s",
                message.message_id,
                target_chat_id,
            )


async def delete_mirrored_posts(bot: Bot, source_message_id: int, db: MirrorDB):
    """Delete bot-owned copies in all targets; clear DB mappings.

    Telegram does not send channel message-deletion events to bots. Use this when
    you removed a post in the source channel and want mirrors removed too.
    """
    mappings = await db.get_mappings(source_message_id)
    if not mappings:
        logger.warning(
            "No mirrored copies tracked for source message %s", source_message_id
        )
        return

    for target_chat_id, target_msg_id in mappings:
        try:
            await bot.delete_message(
                chat_id=target_chat_id, message_id=target_msg_id
            )
            await db.delete_mapping(source_message_id, target_chat_id)
            logger.info(
                "Deleted mirror  src=%s  ->  tgt=%s:%s",
                source_message_id,
                target_chat_id,
                target_msg_id,
            )
        except Exception:
            logger.exception(
                "Failed to delete mirror %s:%s for source %s",
                target_chat_id,
                target_msg_id,
                source_message_id,
            )


async def edit_message(bot: Bot, message: Message, db: MirrorDB):
    """Mirror text/caption edits to copied posts in targets."""
    mappings = await db.get_mappings(message.message_id)
    if not mappings:
        logger.warning(
            "No mappings for edited source message %s",
            message.message_id,
        )
        return

    for target_chat_id, target_msg_id in mappings:
        try:
            if message.text is not None:
                await bot.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    text=message.text,
                    entities=message.entities,
                    link_preview_options=message.link_preview_options,
                )
            elif message.caption is not None:
                await bot.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities,
                    show_caption_above_media=message.show_caption_above_media,
                )
            else:
                logger.debug(
                    "Edit has no text/caption to mirror for src=%s",
                    message.message_id,
                )
                continue

            logger.info(
                "Edited  src=%s  ->  tgt=%s:%s",
                message.message_id,
                target_chat_id,
                target_msg_id,
            )
        except Exception:
            logger.exception(
                "Failed to edit target %s:%s for source %s",
                target_chat_id,
                target_msg_id,
                message.message_id,
            )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def register_handlers(dp: Dispatcher, db: MirrorDB):
    """Attach all handlers."""

    # aiogram stops message propagation after the FIRST matching handler.
    # Register specific handlers (commands + source channel) BEFORE any catch-all.

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        targets = await db.list_targets()
        src = runtime.source_chat_id
        src_line = (
            f"Source channel: <code>{src}</code>"
            if src is not None
            else "Source channel: <i>not set</i> — use <code>/set_source</code>"
        )
        lines = [
            "Mirror bot is running.",
            src_line,
            f"Target channels: {len(targets)}",
        ]
        for chat_id, title in targets:
            lines.append(f"  • <code>{chat_id}</code>  ({title or 'unknown'})")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("set_source"), F.chat.type == "private")
    async def cmd_set_source(message: Message):
        """Set mirror source chat ID (persisted). Usage: /set_source -1001234567890"""
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Usage: <code>/set_source -100xxxxxx</code>\n"
                "Numeric ID of the channel or supergroup posts should be mirrored "
                "<b>from</b>. The bot must be an admin there.",
                parse_mode="HTML",
            )
            return
        try:
            chat_id = int(args[1].strip())
        except ValueError:
            await message.answer("Invalid chat ID.")
            return
        await db.set_source_channel_id(chat_id)
        runtime.source_chat_id = chat_id
        logger.info("Source channel set to %s (via /set_source)", chat_id)
        await message.answer(
            f"Source channel set to <code>{chat_id}</code>. "
            f"It was saved; restart not required.",
            parse_mode="HTML",
        )

    @dp.message(Command("targets"))
    async def cmd_targets(message: Message):
        """List all registered target channels."""
        targets = await db.list_targets()
        if not targets:
            await message.answer(
                "No target channels registered. Use "
                "<code>/add_target -100xxxxxxxxxx</code> with each destination "
                "channel’s numeric ID, or remove and re-add the bot to a channel "
                "so it can auto-register.",
                parse_mode="HTML",
            )
            return
        lines = ["Registered target channels:"]
        for chat_id, title in targets:
            lines.append(f"  <code>{chat_id}</code> – {title or 'unknown'}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("add_target"))
    async def cmd_add_target(message: Message):
        """Register a mirror destination by chat ID: /add_target -1001234567890"""
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Usage: <code>/add_target -100xxxxxx</code>\n"
                "Use each destination channel’s numeric ID (e.g. from @RawDataBot). "
                "The bot must be an admin there with permission to post.",
                parse_mode="HTML",
            )
            return
        try:
            chat_id = int(args[1].strip())
        except ValueError:
            await message.answer("Invalid chat ID.")
            return
        if (
            runtime.source_chat_id is not None
            and chat_id == runtime.source_chat_id
        ):
            await message.answer(
                "That ID is the source channel. Add destination channels only."
            )
            return
        await db.add_target(chat_id, None)
        logger.info("Manually added target channel %s", chat_id)
        await message.answer(
            f"Added <code>{chat_id}</code> as a target. "
            f"Use <code>/targets</code> to verify, then post in the source channel.",
            parse_mode="HTML",
        )

    @dp.message(Command("remove_target"))
    async def cmd_remove_target(message: Message):
        """Remove a target channel by ID: /remove_target -1001234567890"""
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Usage: <code>/remove_target -100xxxxxx</code>", parse_mode="HTML")
            return
        try:
            chat_id = int(args[1])
        except ValueError:
            await message.answer("Invalid chat ID.")
            return
        await db.remove_target(chat_id)
        await message.answer(f"Removed channel <code>{chat_id}</code> from targets.", parse_mode="HTML")

    @dp.message(Command("delete_mirror"), F.chat.type == "private")
    async def cmd_delete_mirror(message: Message):
        """Remove mirrored copies for one source message id.
        Telegram does not notify bots when a channel post is deleted — run this
        after deleting in the source channel. Usage: /delete_mirror 123"""
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Usage: <code>/delete_mirror &lt;source_message_id&gt;</code>\n"
                "Use the message id from the <b>source</b> channel (same id used when "
                "mirroring). Deletes bot-posted copies in every target channel.",
                parse_mode="HTML",
            )
            return
        try:
            mid = int(args[1].strip())
        except ValueError:
            await message.answer("Invalid message id.")
            return
        before = await db.get_mappings(mid)
        if not before:
            await message.answer(
                "No mirrored copies are tracked for that source message id.",
                parse_mode="HTML",
            )
            return
        await delete_mirrored_posts(message.bot, mid, db)
        await message.answer(
            f"Tried to delete mirrors for source message <code>{mid}</code> "
            f"({len(before)} channel(s)).",
            parse_mode="HTML",
        )

    # -- Source channel → targets (must run before any catch-all message handler)
    #
    # Telegram sends *channel* posts as `channel_post`, not `message`. Listening
    # only to `message` misses every post in a source channel.

    async def handle_source_new_post(message: Message, update_kind: str):
        logger.info(
            "Source new post (%s) chat_id=%s msg=%s",
            update_kind,
            message.chat.id,
            message.message_id,
        )
        if runtime.source_chat_id is None:
            return
        await mirror_message(
            message.bot, message, db, runtime.source_chat_id
        )

    @dp.message(is_source_chat)
    async def on_new_message(message: Message):
        """New posts when the source is a group/supergroup (message updates)."""
        await handle_source_new_post(message, "message")

    @dp.channel_post(is_source_chat)
    async def on_channel_post(message: Message):
        """New posts when the source is a channel (channel_post updates)."""
        await handle_source_new_post(message, "channel_post")

    # -- Edits in source ---------------------------------------------------

    @dp.edited_message(is_source_chat)
    async def on_edit_message(message: Message):
        await edit_message(message.bot, message, db)

    @dp.edited_channel_post(is_source_chat)
    async def on_edit_channel_post(message: Message):
        await edit_message(message.bot, message, db)

    # -- Auto-detect when bot is added to a channel ------------------------

    @dp.my_chat_member()
    async def on_chat_member_update(event: ChatMemberUpdated, bot: Bot):
        """Register when bot is added to a channel."""
        logger.info(
            "my_chat_member: chat_id=%s chat_type=%s old=%s new=%s",
            event.chat.id,
            event.chat.type,
            event.old_chat_member.status,
            event.new_chat_member.status,
        )
        chat = event.chat
        new_status = event.new_chat_member.status

        if new_status in ("member", "administrator") and chat.type in (
            "channel",
            "group",
            "supergroup",
        ):
            if (
                runtime.source_chat_id is not None
                and chat.id == runtime.source_chat_id
            ):
                logger.info(
                    "Bot in source channel %s — not registering as mirror target",
                    chat.id,
                )
            else:
                await db.add_target(chat.id, chat.title)
                logger.info(
                    "Auto-registered target channel %s (%s)", chat.id, chat.title
                )

        elif new_status in ("kicked", "restricted", "left"):
            await db.remove_target(chat.id)
            logger.info(
                "Removed target channel %s (%s)", chat.id, chat.title
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Missing required env var: BOT_TOKEN")
        return

    db = MirrorDB(DB_PATH)

    sid = await db.get_source_channel_id()
    if sid is None:
        sid = _parse_optional_channel_id(SOURCE_CHANNEL_ID)
        if sid is not None:
            await db.set_source_channel_id(sid)
            logger.info(
                "Bootstrapped source channel %s from SOURCE_CHANNEL_ID into DB",
                sid,
            )
    runtime.source_chat_id = sid

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    register_handlers(dp, db)

    targets = await db.list_targets()
    logger.info(
        "Config: BOT_TOKEN=%s... source_chat_id=%s",
        BOT_TOKEN[:10],
        runtime.source_chat_id or "(not set — use /set_source)",
    )
    logger.info("Registered target channels: %s", targets)

    # Set bot commands shown in Telegram client
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Bot info and target list"),
            BotCommand(command="set_source", description="Set source channel chat ID"),
            BotCommand(command="targets", description="List target channels"),
            BotCommand(command="add_target", description="Add a target channel by chat ID"),
            BotCommand(command="remove_target", description="Remove a target channel by ID"),
            BotCommand(
                command="delete_mirror",
                description="Delete mirrored copies by source message id",
            ),
        ]
    )

    logger.info(
        "Starting mirror bot (source: %s) ...",
        runtime.source_chat_id or "NOT SET",
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
